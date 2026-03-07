# Codex Task: Concurrency Locks — SWOT W1 (CRITICAL)

**Branch**: create `codex/concurrency-locks` from `main`
**Priority**: CRITICAL — unico item CRITICAL na SWOT
**Scope**: agent.py, llm_provider.py, pii_shield.py (tools.py ja tem lock parcial)

---

## Contexto

O backend corre com Uvicorn (async). Embora Python tenha GIL, as coroutines async
podem ser interrompidas em qualquer `await`, criando race conditions classicas de
check-then-act (TOCTOU). Com ~20 utilizadores concorrentes, qualquer partilha de
estado mutavel entre coroutines precisa de `asyncio.Lock`.

**Regra**: Toda escrita a estado partilhado (dicts globais, atributos de instancia
partilhada) deve ser protegida por `asyncio.Lock`. Leituras simples de referencia
sao seguras em CPython (GIL), mas check-then-act NAO e seguro.

---

## Task 1 — ConversationStore: lock interno

**Ficheiro**: `agent.py`, class `ConversationStore` (linhas 64-167)

### O que fazer

Adicionar um `asyncio.Lock` interno ao `ConversationStore`. Converter os metodos
que modificam estado para `async` e proteger com lock.

```python
class ConversationStore(MutableMapping[str, List[dict]]):
    def __init__(self, max_conversations, ttl_seconds, on_evict=None):
        self._data: Dict[str, List[dict]] = {}
        self._last_accessed: Dict[str, datetime] = {}
        self.max_conversations = max_conversations
        self.ttl_seconds = ttl_seconds
        self._on_evict = on_evict
        self._lock = asyncio.Lock()          # <-- NOVO

    async def async_get(self, key: str, default=None):
        """Thread-safe get com touch."""
        async with self._lock:
            if key in self._data:
                self._touch(key)
                return self._data[key]
            return default

    async def async_set(self, key: str, value: List[dict]) -> None:
        """Thread-safe set com capacity check."""
        async with self._lock:
            if key not in self._data:
                self.cleanup_expired()
                while len(self._data) >= self.max_conversations:
                    if self._evict_lru(exclude_key=key) is None:
                        break
            self._data[key] = value
            self._touch(key)

    async def async_delete(self, key: str) -> None:
        """Thread-safe delete."""
        async with self._lock:
            if key in self._data:
                self._evict(key, reason="manual")

    async def async_contains(self, key: str) -> bool:
        """Thread-safe contains check."""
        async with self._lock:
            return key in self._data

    async def async_cleanup_expired(self) -> List[str]:
        """Thread-safe cleanup."""
        async with self._lock:
            return self.cleanup_expired()
```

### Regras

1. **Manter os metodos sync existentes** (`__getitem__`, `__setitem__`, etc.) para
   compatibilidade — NAO os remover. Adicionar os metodos `async_*` como API preferida.
2. Os metodos sync internos (`cleanup_expired`, `_evict_lru`, `_evict`, `_touch`)
   continuam sync — sao chamados DENTRO do lock (ja protegidos).
3. **Migrar chamadores**: nos endpoints HTTP (funcs async), substituir:
   - `conversations[conv_id]` por `await conversations.async_get(conv_id)`
   - `conversations[conv_id] = msgs` por `await conversations.async_set(conv_id, msgs)`
   - `conv_id in conversations` por `await conversations.async_contains(conv_id)`
   - `del conversations[conv_id]` por `await conversations.async_delete(conv_id)`
4. **NAO migrar** usos que ja estao dentro de `async with _get_conversation_lock(conv_id):`
   — esses ja estao protegidos pelo lock da conversacao. O lock interno do store
   protege a estrutura interna (eviction, capacity); o lock externo protege a logica
   de negocio da conversacao.

---

## Task 2 — conversation_meta e uploaded_files_store: locks dedicados

**Ficheiro**: `agent.py`, linhas 169-170

### O que fazer

Criar locks dedicados para estes dois dicts globais:

```python
conversation_meta: Dict[str, Dict] = {}
_conversation_meta_lock = asyncio.Lock()      # <-- NOVO

uploaded_files_store: Dict[str, Dict] = {}
_uploaded_files_lock = asyncio.Lock()          # <-- NOVO
```

### Pontos de escrita a proteger

**conversation_meta** — proteger com `_conversation_meta_lock`:
- `_inject_file_context()` → `conversation_meta.setdefault(conv_id, {})["file_injected"] = True`
- `_cleanup_conversation_related_state()` → `conversation_meta.pop(conv_id, None)`
- Qualquer `.get()` seguido de escrita condicional

**uploaded_files_store** — proteger com `_uploaded_files_lock`:
- `_ensure_uploaded_files_loaded()` → `uploaded_files_store[conv_id] = {...}`
- `_normalize_uploaded_files_entry()` → `uploaded_files_store[conv_id] = normalized`
- `_cleanup_conversation_related_state()` → `uploaded_files_store.pop(conv_id, None)`
- `_get_uploaded_files()` → leitura, proteger para consistencia

### Regras

1. Converter `_ensure_uploaded_files_loaded` para usar lock no check-then-act:
   ```python
   async def _ensure_uploaded_files_loaded(conv_id, user_sub=""):
       async with _uploaded_files_lock:
           current_files = _get_uploaded_files(conv_id)
           if current_files:
               return
       # ... I/O pesado (table query, blob download) FORA do lock ...
       async with _uploaded_files_lock:
           # Double-check apos I/O
           if _get_uploaded_files(conv_id):
               return
           uploaded_files_store[conv_id] = {...}
   ```
2. Usar pattern **double-checked locking**: check → release lock → I/O → re-acquire → re-check → write.
   Isto evita bloquear todas as coroutines durante I/O lento.
3. `_inject_file_context()` e sync mas escreve em `conversation_meta` — como e
   chamada dentro de `_get_conversation_lock(conv_id)`, o risco e baixo. Ainda assim,
   proteger a escrita a `conversation_meta` com `_conversation_meta_lock`.
   Se necessario, tornar `_inject_file_context` async.

---

## Task 3 — HTTP client lazy init: lock nos providers

**Ficheiro**: `llm_provider.py`, linhas 288-293 (AzureOpenAI) e 462-465 (Anthropic)

### O que fazer

Adicionar `asyncio.Lock` a cada provider para proteger `_get_client()`:

```python
class AzureOpenAIProvider(LLMProvider):
    def __init__(self, deployment=None):
        ...
        self._http_client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()      # <-- NOVO

    async def _get_client(self) -> httpx.AsyncClient:     # <-- agora async
        async with self._client_lock:
            if self._http_client is None or self._http_client.is_closed:
                self._http_client = httpx.AsyncClient(timeout=180)
            return self._http_client
```

Repetir o mesmo pattern para `AnthropicProvider`.

### Regras

1. `_get_client()` passa a ser `async` — actualizar todos os chamadores:
   - `self._get_client()` → `await self._get_client()`
   - Aparece em `chat()`, `chat_stream()`, e qualquer metodo que faca requests
2. `close()` tambem deve adquirir o lock antes de fechar o client:
   ```python
   async def close(self):
       async with self._client_lock:
           if self._http_client and not self._http_client.is_closed:
               await self._http_client.aclose()
               self._http_client = None
   ```
3. NAO tocar no `_get_http_client()` de `pii_shield.py` — esse ja foi corrigido
   na Phase 2 com shared client pattern. Se quiser adicionar lock, pode, mas e
   de prioridade mais baixa (singleton module-level, GIL protege a atribuicao).

---

## Task 4 — _get_conversation_lock: eviction safety

**Ficheiro**: `agent.py`, linhas 171, 174-177, 275-276

### O que fazer

O `_conversation_locks` dict pode ter entries evicted enquanto uma coroutine
ainda segura o lock. Corrigir:

```python
_conversation_locks: Dict[str, asyncio.Lock] = {}
_conversation_locks_guard = asyncio.Lock()      # <-- NOVO

async def _get_conversation_lock(conv_id: str) -> asyncio.Lock:   # <-- agora async
    async with _conversation_locks_guard:
        if conv_id not in _conversation_locks:
            _conversation_locks[conv_id] = asyncio.Lock()
        return _conversation_locks[conv_id]
```

### Regras

1. **NAO evict um lock** enquanto ele esta acquired. Modificar
   `_cleanup_conversation_related_state()`:
   ```python
   def _cleanup_conversation_related_state(conv_id: str) -> None:
       conversation_meta.pop(conv_id, None)
       uploaded_files_store.pop(conv_id, None)
       lock = _conversation_locks.get(conv_id)
       if lock and not lock.locked():
           _conversation_locks.pop(conv_id, None)
       # Se lock.locked(), deixar para cleanup posterior
   ```
2. Actualizar todos os `async with _get_conversation_lock(conv_id):` para
   `async with await _get_conversation_lock(conv_id):` — porque agora retorna
   uma coroutine.

---

## Task 5 — Tests

**Ficheiro**: criar `tests/test_concurrency_locks.py`

### Tests obrigatorios (minimo 8)

```python
class TestConversationStoreLocking:
    """Verifica que ConversationStore e thread-safe."""

    @pytest.mark.asyncio
    async def test_concurrent_set_no_data_loss(self):
        """Dois async_set concorrentes para keys diferentes nao perdem dados."""
        store = ConversationStore(max_conversations=10, ttl_seconds=3600)
        async def writer(key):
            await store.async_set(key, [{"role": "user", "content": f"msg-{key}"}])
        await asyncio.gather(*(writer(f"conv-{i}") for i in range(10)))
        assert len(store) == 10

    @pytest.mark.asyncio
    async def test_concurrent_set_same_key_last_wins(self):
        """Dois async_set para a mesma key: ultimo a adquirir lock ganha."""
        store = ConversationStore(max_conversations=10, ttl_seconds=3600)
        results = []
        async def writer(val):
            await store.async_set("k", [{"v": val}])
            results.append(val)
        await asyncio.gather(writer("a"), writer("b"))
        final = await store.async_get("k")
        assert final == [{"v": results[-1]}]

    @pytest.mark.asyncio
    async def test_capacity_eviction_under_concurrency(self):
        """Capacidade nao e excedida mesmo com writes concorrentes."""
        store = ConversationStore(max_conversations=5, ttl_seconds=3600)
        async def writer(key):
            await store.async_set(key, [{"role": "user", "content": key}])
        await asyncio.gather(*(writer(f"c-{i}") for i in range(20)))
        assert len(store) <= 5

    @pytest.mark.asyncio
    async def test_async_delete_removes_entry(self):
        store = ConversationStore(max_conversations=10, ttl_seconds=3600)
        await store.async_set("x", [{"m": 1}])
        await store.async_delete("x")
        assert not await store.async_contains("x")

class TestHTTPClientLocking:
    """Verifica que HTTP clients nao leakam sob concorrencia."""

    @pytest.mark.asyncio
    async def test_azure_provider_single_client(self, monkeypatch):
        """Multiplas chamadas concorrentes a _get_client() devolvem o mesmo client."""
        from llm_provider import AzureOpenAIProvider
        monkeypatch.setattr("llm_provider.AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
        monkeypatch.setattr("llm_provider.AZURE_OPENAI_KEY", "test-key")
        provider = AzureOpenAIProvider(deployment="test")
        clients = await asyncio.gather(*(provider._get_client() for _ in range(10)))
        unique = set(id(c) for c in clients)
        assert len(unique) == 1, f"Expected 1 client, got {len(unique)} different instances"
        await provider.close()

    @pytest.mark.asyncio
    async def test_anthropic_provider_single_client(self, monkeypatch):
        from llm_provider import AnthropicProvider
        monkeypatch.setattr("llm_provider.ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr("llm_provider.ANTHROPIC_API_BASE", "https://test.anthropic.com")
        provider = AnthropicProvider(model="test")
        clients = await asyncio.gather(*(provider._get_client() for _ in range(10)))
        unique = set(id(c) for c in clients)
        assert len(unique) == 1
        await provider.close()

class TestConversationLockSafety:
    """Verifica que locks de conversacao sao safe."""

    @pytest.mark.asyncio
    async def test_concurrent_lock_acquisition_same_conv(self):
        """Dois acquires da mesma conv_id devolvem o mesmo Lock."""
        from agent import _get_conversation_lock
        locks = await asyncio.gather(
            _get_conversation_lock("conv-1"),
            _get_conversation_lock("conv-1"),
        )
        assert locks[0] is locks[1]

    @pytest.mark.asyncio
    async def test_lock_not_evicted_while_held(self):
        """Lock nao e removido do dict enquanto esta acquired."""
        from agent import _get_conversation_lock, _cleanup_conversation_related_state
        lock = await _get_conversation_lock("conv-held")
        async with lock:
            _cleanup_conversation_related_state("conv-held")
            # Lock still exists because it's held
            from agent import _conversation_locks
            # Lock should still be referenced or cleanup should skip it
```

### Regras para testes

1. Usar `asyncio.gather()` para simular concorrencia real
2. Cada test deve ser **deterministic** — nao depender de timing
3. Nao mockar locks — testar a concorrencia real
4. Importar de `agent` e `llm_provider` directamente
5. Usar `monkeypatch` para endpoints/keys (nao fazer requests reais)

---

## Restricoes Globais

1. **NAO alterar a interface publica** dos endpoints FastAPI (rotas, request/response schemas)
2. **NAO modificar testes existentes** — os 150+ testes actuais devem continuar a passar
3. **NAO adicionar dependencias** — usar apenas `asyncio.Lock` (stdlib)
4. **NAO tocar em pii_shield.py** — ja tem shared client (Phase 2). O `_get_http_client()`
   la e module-level singleton, GIL protege a atribuicao de referencia. Se quiser
   adicionar lock, criar uma task separada.
5. **Manter logging existente** — nao alterar niveis de log
6. Branch deve ser criada a partir de `main` (commit actual: `4677fc3`)
7. Correr `python -m pytest tests/ -x --tb=short -q` antes de submeter — todos os testes
   (existentes + novos) devem passar

---

## Ficheiros a modificar

| Ficheiro | Alteracao |
|---|---|
| `agent.py` | ConversationStore lock interno, conversation_meta lock, uploaded_files_store lock, _get_conversation_lock async |
| `llm_provider.py` | AzureOpenAIProvider._get_client lock, AnthropicProvider._get_client lock |
| `tests/test_concurrency_locks.py` | NOVO — minimo 8 tests |

---

## Commit History esperado

```
commit 1: feat: add asyncio.Lock to ConversationStore (async_get/set/delete/contains)
commit 2: feat: add locks for conversation_meta and uploaded_files_store
commit 3: feat: add lock to HTTP client initialization in LLM providers
commit 4: feat: make _get_conversation_lock async with eviction safety
commit 5: test: add concurrency lock tests (8+ tests)
```
