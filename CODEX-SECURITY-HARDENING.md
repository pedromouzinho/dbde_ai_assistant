# Codex Task: Security Hardening — SWOT Rec 8 (W7) + W10

**Branch**: create `codex/security-hardening` from `main`
**Scope**: llm_provider.py, storage.py, tools_knowledge.py, tools_figma.py, tools_miro.py, http_helpers.py, code_interpreter.py, tests

---

## Contexto

Dois gaps de seguranca identificados na SWOT:
1. **W7 / Rec 8**: Logs podem expor API keys, tokens e secrets em mensagens de erro HTTP
2. **W10**: Code Interpreter tem gaps de hardening (PATH, CPU/mem limits, symlinks)

---

## Task 1 — Redactar secrets em error responses logados

**Ficheiros**: `llm_provider.py`, `storage.py`, `tools_knowledge.py`, `tools_figma.py`, `tools_miro.py`, `http_helpers.py`

### Problema

Multiplos ficheiros logam `resp.text[:200]` ou `resp.text[:300]` em erros HTTP.
Error responses de APIs podem ecoar headers, API keys, ou tokens no body.

### O que fazer

Criar uma funcao utilitaria `_sanitize_error_response(text: str, max_len: int = 200) -> str`
num ficheiro apropriado (pode ser `http_helpers.py` ou um novo `log_sanitizer.py`).

```python
import re

# Patterns que devem ser redactados em error responses
_SECRET_PATTERNS = [
    re.compile(r'(?i)(api[_-]?key|authorization|x-api-key|ocp-apim-subscription-key|bearer)\s*[:=]\s*["\']?[\w\-\.]+', re.IGNORECASE),
    re.compile(r'(?i)(key|token|secret|password|pat)\s*[:=]\s*["\']?[\w\-\.]{8,}', re.IGNORECASE),
    re.compile(r'[A-Za-z0-9+/]{40,}={0,2}'),  # Base64 strings >= 40 chars (likely tokens)
    re.compile(r'eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}'),  # JWT tokens
]

def _sanitize_error_response(text: str, max_len: int = 200) -> str:
    """Redacta potenciais secrets de error responses antes de logar."""
    if not text:
        return ""
    truncated = text[:max_len]
    for pattern in _SECRET_PATTERNS:
        truncated = pattern.sub("[REDACTED]", truncated)
    return truncated
```

### Pontos a substituir

Substituir TODOS os `resp.text[:N]` por `_sanitize_error_response(resp.text, N)`:

| Ficheiro | Linha(s) | Actual | Novo |
|---|---|---|---|
| `llm_provider.py` | ~364 | `resp.text[:200]` | `_sanitize_error_response(resp.text, 200)` |
| `llm_provider.py` | ~543 | `resp.text[:300]` | `_sanitize_error_response(resp.text, 300)` |
| `llm_provider.py` | ~582 | `body_preview` (500 bytes) | `_sanitize_error_response(body_preview, 300)` |
| `storage.py` | ~262 | `resp.text[:200]` | `_sanitize_error_response(resp.text, 200)` |
| `storage.py` | ~303 | `resp.text[:200]` | `_sanitize_error_response(resp.text, 200)` |
| `storage.py` | ~333 | `resp.text[:200]` | `_sanitize_error_response(resp.text, 200)` |
| `storage.py` | ~357 | `resp.text[:200]` | `_sanitize_error_response(resp.text, 200)` |
| `storage.py` | ~383 | `resp.text[:200]` | `_sanitize_error_response(resp.text, 200)` |
| `tools_knowledge.py` | ~105 | `resp.text[:300]` | `_sanitize_error_response(resp.text, 300)` |
| `tools_knowledge.py` | ~290 | `resp.text[:200]` | `_sanitize_error_response(resp.text, 200)` |
| `tools_knowledge.py` | ~346 | `resp.text[:200]` | `_sanitize_error_response(resp.text, 200)` |
| `tools_figma.py` | ~92 | `resp.text[:200]` | `_sanitize_error_response(resp.text, 200)` |
| `tools_miro.py` | ~90 | `resp.text[:200]` | `_sanitize_error_response(resp.text, 200)` |
| `http_helpers.py` | ~68 | `resp.text[:200]` | `_sanitize_error_response(resp.text, 200)` |

### Regras

1. A funcao `_sanitize_error_response` deve ser importavel de um local central
   (ex: `http_helpers.py` ou novo `log_sanitizer.py`)
2. NAO alterar o nivel de logging (info/warning/error) — apenas o conteudo
3. NAO truncar MAIS do que o actual — manter os mesmos limites de chars
4. NAO remover a informacao de status_code — e segura e util para debug
5. O pattern de Base64 (40+ chars) pode ter false positives — aceitavel,
   prefere-se redactar a mais do que a menos

---

## Task 2 — Code Interpreter: PATH hardcoded minimo

**Ficheiro**: `code_interpreter.py`, linhas 340-341

### Problema

```python
env={
    "PATH": os.environ.get("PATH", ""),  # <-- herda PATH completo do host
    ...
}
```

O PATH do host pode conter binarios que o utilizador nao deveria poder chamar.

### O que fazer

Substituir por PATH minimo hardcoded:

```python
env={
    "PATH": "/usr/local/bin:/usr/bin:/bin",  # PATH minimo para Python + pip
    "HOME": tmpdir,
    ...
}
```

### Regras

1. Manter `/usr/local/bin` porque e onde o Python e pip costumam estar em containers
2. NAO incluir `/sbin` ou `/usr/sbin`
3. Se Python nao for encontrado com PATH minimo, testar com o real e voltar atras
   (mas o `-I` flag ja garante que `sys.executable` e usado directamente, nao via PATH)
4. Na verdade, como usamos `sys.executable` (caminho absoluto) para invocar o
   subprocess, o PATH so afecta imports de packages nativos e sub-shells — logo
   o PATH minimo e seguro.

---

## Task 3 — Code Interpreter: resource limits (CPU/memoria)

**Ficheiro**: `code_interpreter.py`, linhas 333-348

### Problema

Sem limites de CPU ou memoria, um loop infinito ou alocacao massiva pode
esgotar recursos do host. O timeout de 240s so mata o processo no fim,
nao previne memory exhaustion.

### O que fazer

Adicionar `preexec_fn` com `resource.setrlimit()`:

```python
import resource

_CODE_CPU_LIMIT_SECONDS = 120     # max CPU time (wall time handled by asyncio timeout)
_CODE_MEMORY_LIMIT_BYTES = 512 * 1024 * 1024  # 512 MB max RSS

def _set_resource_limits():
    """Limita CPU e memoria do subprocess do code interpreter."""
    try:
        # CPU time limit (hard kill at limit)
        resource.setrlimit(resource.RLIMIT_CPU, (_CODE_CPU_LIMIT_SECONDS, _CODE_CPU_LIMIT_SECONDS))
        # Virtual memory limit
        resource.setrlimit(resource.RLIMIT_AS, (_CODE_MEMORY_LIMIT_BYTES, _CODE_MEMORY_LIMIT_BYTES))
    except (ValueError, OSError):
        pass  # Some platforms don't support these limits

proc = await asyncio.create_subprocess_exec(
    sys.executable,
    "-I",
    str(script_path),
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    cwd=tmpdir,
    env={
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        ...
    },
    preexec_fn=_set_resource_limits,    # <-- NOVO
)
```

### Regras

1. `preexec_fn` corre no child process ANTES do exec — e o local correcto
2. `RLIMIT_AS` limita virtual memory (inclui mmap) — 512MB e generoso para
   pandas/numpy mas previne DoS
3. `RLIMIT_CPU` limita CPU time consumido — 120s e conservador (timeout e 240s
   de wall time, mas CPU time pode ser menos)
4. Usar `try/except` porque `RLIMIT_AS` nao existe em todas as plataformas
5. NAO usar `RLIMIT_NPROC` — pode interferir com o event loop do parent

---

## Task 4 — Code Interpreter: validar symlinks na criacao

**Ficheiro**: `code_interpreter.py`, linhas 175-186

### Problema

Na montagem de ficheiros para o sandbox:
```python
for _name in list(os.listdir(TMPDIR)):
    _src = os.path.join(TMPDIR, _name)
    if not os.path.isfile(_src) or _name.startswith("_"):
        continue
    _dst = os.path.join(TMPDIR, "mnt", "data", _name)
    os.symlink(_src, _dst)   # <-- Se _src for ele proprio um symlink, segue-o
```

Se `_src` for um symlink que aponta para fora do TMPDIR, o codigo cria
um symlink para um ficheiro arbitrario do host.

### O que fazer

Adicionar validacao de `os.path.islink()` e `os.path.realpath()`:

```python
for _name in list(os.listdir(TMPDIR)):
    _src = os.path.join(TMPDIR, _name)
    if not os.path.isfile(_src) or _name.startswith("_"):
        continue
    # Validar que _src nao e um symlink para fora do TMPDIR
    _real_src = os.path.realpath(_src)
    _real_root = os.path.realpath(TMPDIR)
    if not (_real_src == _real_root or _real_src.startswith(_real_root + os.sep)):
        continue  # Skip: symlink aponta para fora do sandbox
    _dst = os.path.join(TMPDIR, "mnt", "data", _name)
    try:
        os.symlink(_src, _dst)
    except Exception:
        try:
            shutil.copy2(_src, _dst)
        except Exception:
            pass
```

### Regras

1. Usar `os.path.realpath()` para resolver symlinks recursivamente
2. Comparar contra `os.path.realpath(TMPDIR)` (nao TMPDIR raw)
3. Se o symlink aponta para fora: `continue` (skip silenciosamente, nao crash)
4. Manter o fallback para `shutil.copy2` (copia fisica se symlink falha)

---

## Task 5 — Code Interpreter: bloquear `from os import system` e getattr bypass

**Ficheiro**: `code_interpreter.py`, linhas 110-119

### Problema 1: ImportFrom nao verifica nomes importados

```python
elif isinstance(node, ast.ImportFrom):
    mod = str(node.module or "").strip()
    if not _is_import_allowed(mod):
```

Isto permite `from os import system` porque `os` e allowed. O `system` nao e verificado.

### Problema 2: getattr bypass

`getattr(__builtins__, 'exec')()` bypassa o check de `_BLOCKED_CALLS` porque
o AST ve um `ast.Call` com func = `ast.Attribute(value=Name('getattr'))` e
o `_call_name` retorna `"getattr"` que nao esta em `_BLOCKED_CALLS`.

### O que fazer

```python
# Novos blocked imports (funcoes perigosas importadas directamente)
_BLOCKED_IMPORT_NAMES = {
    "system", "popen", "exec", "eval", "remove", "unlink", "rmdir",
    "rmtree", "Popen", "run", "call", "check_output", "check_call",
}

# Adicionar "getattr" e "setattr" a _BLOCKED_CALLS
_BLOCKED_CALLS = {
    "exec", "eval", "compile", "__import__", "globals", "locals",
    "getattr", "setattr", "delattr",  # <-- NOVOS
}

# No AST walker, verificar nomes importados em ImportFrom:
elif isinstance(node, ast.ImportFrom):
    mod = str(node.module or "").strip()
    if not _is_import_allowed(mod):
        return f"Import bloqueado por seguranca: {mod}"
    # Verificar nomes individuais importados
    for alias in (node.names or []):
        name = str(alias.name or "").strip()
        if name in _BLOCKED_IMPORT_NAMES:
            return f"Import de funcao bloqueada por seguranca: from {mod} import {name}"
```

### Regras

1. `_BLOCKED_IMPORT_NAMES` deve conter funcoes perigosas que existem em modulos allowed
2. Bloquear `getattr`/`setattr`/`delattr` nos calls — previne reflexao
3. NAO bloquear `hasattr` — e read-only e seguro
4. Manter os `_BLOCKED_ATTR_CALLS` existentes (sao complementares)
5. Testar que `import pandas` e `from pandas import DataFrame` continuam a funcionar

---

## Task 6 — Tests

**Ficheiro**: criar `tests/test_security_hardening.py`

### Tests obrigatorios (minimo 10)

```python
class TestSanitizeErrorResponse:
    """Verifica que secrets sao redactados em error responses."""

    def test_redacts_api_key_in_response(self):
        text = '{"error": "Invalid api-key: sk-abc123def456"}'
        result = _sanitize_error_response(text, 200)
        assert "sk-abc123def456" not in result
        assert "[REDACTED]" in result

    def test_redacts_bearer_token(self):
        text = 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.xxx'
        result = _sanitize_error_response(text, 300)
        assert "eyJhbGciOiJIUzI1NiJ9" not in result

    def test_redacts_base64_token(self):
        text = f'Basic {base64.b64encode(b":my-secret-pat-token-value-here").decode()}'
        result = _sanitize_error_response(text, 200)
        assert "my-secret-pat" not in result

    def test_preserves_status_code(self):
        text = "HTTP 401 Unauthorized: invalid credentials"
        result = _sanitize_error_response(text, 200)
        assert "401" in result
        assert "Unauthorized" in result

    def test_truncates_to_max_len(self):
        text = "x" * 500
        result = _sanitize_error_response(text, 200)
        assert len(result) <= 200

    def test_empty_input(self):
        assert _sanitize_error_response("", 200) == ""
        assert _sanitize_error_response(None, 200) == ""


class TestCodeInterpreterHardening:
    """Verifica hardening do Code Interpreter."""

    def test_path_is_minimal(self):
        # Verificar que o PATH nao inclui /sbin
        from code_interpreter import _runner_script  # ou ler o env dict
        # O teste deve verificar que o env dict nao herda os.environ PATH

    def test_blocked_from_os_import_system(self):
        from code_interpreter import _validate_code
        err = _validate_code("from os import system")
        assert err is not None
        assert "bloqueado" in err.lower() or "blocked" in err.lower()

    def test_blocked_getattr_builtins(self):
        from code_interpreter import _validate_code
        err = _validate_code("getattr(__builtins__, 'exec')()")
        assert err is not None

    def test_allowed_pandas_import(self):
        from code_interpreter import _validate_code
        assert _validate_code("import pandas as pd") is None

    def test_allowed_from_pandas_import(self):
        from code_interpreter import _validate_code
        assert _validate_code("from pandas import DataFrame") is None

    def test_symlink_validation_blocks_escape(self):
        # Testar que _safe_path bloqueia symlinks para fora do sandbox
        # (este teste ja existe parcialmente, verificar)
        pass

    def test_resource_limits_function_exists(self):
        from code_interpreter import _set_resource_limits
        # Deve existir e nao crashar
        # NAO chamar directamente (altera limites do processo de teste)
        assert callable(_set_resource_limits)
```

### Regras

1. Usar `monkeypatch` para evitar dependencias externas
2. NAO executar `_set_resource_limits()` no processo de teste (alteraria os limites)
3. Testar `_validate_code()` directamente — nao precisa de subprocess
4. Importar `_sanitize_error_response` de onde for definida
5. Manter testes deterministic

---

## Restricoes Globais

1. **NAO alterar a interface publica** dos endpoints FastAPI
2. **NAO modificar testes existentes** — todos os 209 testes actuais devem passar
3. **NAO adicionar dependencias** — `resource` e `re` sao stdlib
4. **Manter logging existente** — apenas redactar o conteudo, nao os niveis
5. Branch criada a partir de `main` (commit actual)
6. Correr `python -m pytest tests/ -x --tb=short -q` — todos os testes devem passar

---

## Ficheiros a modificar

| Ficheiro | Alteracao |
|---|---|
| `http_helpers.py` ou novo `log_sanitizer.py` | `_sanitize_error_response()` |
| `llm_provider.py` | Substituir `resp.text[:N]` por `_sanitize_error_response()` (3 pontos) |
| `storage.py` | Substituir `resp.text[:N]` por `_sanitize_error_response()` (5 pontos) |
| `tools_knowledge.py` | Substituir `resp.text[:N]` por `_sanitize_error_response()` (3 pontos) |
| `tools_figma.py` | Substituir `resp.text[:N]` por `_sanitize_error_response()` (1 ponto) |
| `tools_miro.py` | Substituir `resp.text[:N]` por `_sanitize_error_response()` (1 ponto) |
| `http_helpers.py` | Substituir `resp.text[:N]` por `_sanitize_error_response()` (1 ponto) |
| `code_interpreter.py` | PATH minimo, `_set_resource_limits`, symlink validation, AST hardening |
| `tests/test_security_hardening.py` | NOVO — minimo 10 tests |

---

## Commit History esperado

```
commit 1: feat: add _sanitize_error_response utility for log redaction
commit 2: refactor: replace raw resp.text logging with sanitized version
commit 3: feat: harden Code Interpreter PATH and add resource limits
commit 4: feat: add symlink validation and AST import hardening
commit 5: test: add security hardening tests (10+ tests)
```
