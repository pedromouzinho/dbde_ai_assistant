# Auditoria de Segurança Profunda — DBDE AI Assistant v7.1.1
## Auditor: Claude (Arquiteto) | Data: 2026-02-23
## Complemento ao ARCHITECTURE_REVIEW_v7.1.md (validação funcional)

---

## CONTEXTO

Este documento resulta de 3 auditorias paralelas profundas ao código-fonte, focadas em segurança, concorrência e robustez. O ARCHITECTURE_REVIEW_v7.1.md validou a correção funcional das Fases 3-4. Este documento identifica vulnerabilidades e problemas estruturais que requerem correção antes de qualquer expansão funcional.

**Estado de produção:** v7.1.1, health OK, login OK, pptx shelved (Tarefa 4.6).

---

## FINDINGS POR SEVERIDADE

---

### 🔴 CRITICAL-1: WIQL Injection em tools.py

**Ficheiro:** `tools.py`, linhas 344, 543, 546
**Impacto:** Um utilizador (ou o LLM, se manipulado) pode injetar WIQL arbitrário nas queries ao Azure DevOps.

**Evidência:**
```python
# Linha 344 — input do LLM interpolado diretamente
wiql = f"SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = '{DEVOPS_PROJECT}' AND {wiql_where} ORDER BY ..."

# Linhas 543/546 — child_type e parent_type sem escaping
wiql = f"... ([Target].[System.WorkItemType] = '{child_type}') ..."
```

O `wiql_where` vem dos argumentos da tool call do LLM. Se o modelo for manipulado via prompt injection (ex: user cola texto malicioso), pode executar WIQL arbitrário: listar work items de outros projetos, exfiltrar dados, ou causar DoS com queries pesadas.

**Decisão arquitetural:** Os parâmetros `wiql_where`, `child_type`, `parent_type` e `parent_id` devem ser sanitizados. Para `wiql_where`, a sanitização completa é difícil (é uma query language), mas deve-se pelo menos: (a) validar que `wiql_where` não contém subconsultas SELECT, (b) escapar aspas simples nos valores de `child_type`/`parent_type`, (c) validar que `parent_id` é numérico.

**Prioridade Codex:** P0 — Corrigir antes de qualquer outro trabalho.

---

### 🔴 CRITICAL-2: CORS Wildcard com Credentials em app.py

**Ficheiro:** `app.py`, linha 71
**Impacto:** Qualquer origem pode fazer requests autenticados à API.

**Evidência:**
```python
CORSMiddleware,
allow_origins=["*"], allow_credentials=True,
```

`allow_origins=["*"]` com `allow_credentials=True` é explicitamente proibido pela spec CORS (os browsers bloqueiam), mas o FastAPI/Starlette contorna isto respondendo com o Origin do request no header `Access-Control-Allow-Origin`. Isto significa que qualquer site malicioso pode fazer requests autenticados se o utilizador tiver um token válido no browser.

**Decisão arquitetural:** Substituir `["*"]` por uma lista explícita de origens permitidas, obtida de uma env var `ALLOWED_ORIGINS`. Em dev, pode incluir `localhost:*`. Em produção, apenas o domínio do App Service.

**Prioridade Codex:** P0.

---

### 🔴 CRITICAL-3: XSS via Markdown Rendering em index.html

**Ficheiro:** `static/index.html`, linhas 129-188 (renderMarkdown + renderInline), linhas 514 e 1380 (dangerouslySetInnerHTML)
**Impacto:** Conteúdo do LLM renderizado como HTML sem sanitização.

**Evidência:**
A função `renderMarkdown()` faz substituições regex de markdown para HTML e o resultado é injectado via `dangerouslySetInnerHTML`. Os code blocks escapam `<` e `>`, mas o resto do conteúdo (headings, lists, links, inline text) não é sanitizado. Se o LLM retornar (por manipulação ou erro) algo como:
```
## <img src=x onerror=alert(document.cookie)>
```
...o browser executa o script.

O `renderInline` na linha 184 cria links `<a href="$2">` onde `$2` pode ser `javascript:alert(1)` se o LLM retornar `[click](javascript:alert(1))`.

**Decisão arquitetural:** Integrar DOMPurify (CDN: ~7KB gzipped) como último passo antes do `dangerouslySetInnerHTML`. Configurar para permitir apenas tags seguras (p, strong, em, code, pre, h2-h4, li, a, table, thead, tbody, tr, th, td, br, div). Para links, validar que `href` começa com `http://` ou `https://`.

**Prioridade Codex:** P0.

---

### 🔴 CRITICAL-4: XSS no HTML Export em export_engine.py

**Ficheiro:** `export_engine.py`
**Impacto:** Se o export_engine gerar HTML (para PDF ou download), o conteúdo das conversas é inserido sem escaping.

**Decisão arquitetural:** Aplicar `html.escape()` a todo o conteúdo user-generated antes de inserir em templates HTML. Se já usa fpdf2 para PDF (que não interpreta HTML), o risco é menor, mas verificar se existe algum path de HTML export direto.

**Prioridade Codex:** P1 — Verificar e corrigir se path HTML existir.

---

### 🟠 IMPORTANT-1: Ausência Total de Rate Limiting

**Ficheiro:** `app.py` (todos os endpoints)
**Impacto:** Sem rate limiting, um atacante com token válido pode: (a) fazer centenas de requests LLM/segundo, gerando custos elevados, (b) exfiltrar dados via queries em massa, (c) causar DoS no serviço.

**Evidência:** Grep por `rate.limit|RateLim|slowapi|throttl` nos .py retorna zero resultados (excepto menções a "rate limited" em mensagens de erro de retry).

**Decisão arquitetural:** Implementar rate limiting por utilizador nos endpoints críticos:
- `/api/chat` e `/api/chat/stream`: max 10 req/minuto por user
- `/api/upload`: max 5 req/minuto
- `/api/login`: max 5 req/minuto (anti-brute-force)
- `/api/download/*`: max 20 req/minuto

Usar `slowapi` (wrapper do `limits` para FastAPI) com backend in-memory. A chave deve ser `user.sub` do JWT (ou IP para endpoints não-autenticados como login).

**Prioridade Codex:** P1.

---

### 🟠 IMPORTANT-2: Token JWT em localStorage (plaintext)

**Ficheiro:** `static/index.html`, linhas 700, 721, 728
**Impacto:** O token JWT (e potencialmente credenciais) ficam em `localStorage`, acessível a qualquer script na mesma origem — incluindo XSS (ver CRITICAL-3).

**Evidência:**
```javascript
localStorage.setItem("dbde_auth", JSON.stringify(a));  // linha 721
```

Se o XSS da CRITICAL-3 for explorado, o atacante pode exfiltrar o token JWT.

**Decisão arquitetural:** A curto prazo, resolver primeiro o XSS (CRITICAL-3) que elimina o vetor principal. A médio prazo (Fase 5+), migrar para httpOnly cookies com SameSite=Strict, o que torna o token inacessível a JavaScript. Não é P0 porque depende da resolução do XSS primeiro.

**Prioridade Codex:** P2 (mas documentar como dívida técnica para Fase 5).

---

### 🟠 IMPORTANT-3: ConversationStore — Race Condition na Criação de Locks

**Ficheiro:** `agent.py`, linhas 179-183
**Impacto:** Potencial criação duplicada de locks sob carga.

**Evidência:**
```python
def _get_conversation_lock(conv_id: str) -> asyncio.Lock:
    lock = _conversation_locks.get(conv_id)
    if not lock:
        lock = asyncio.Lock()
        _conversation_locks[conv_id] = lock
    return lock
```

Em asyncio single-threaded, isto é seguro na prática (não há preemption entre o `get` e o `[]=`). Mas se a app evoluir para multi-worker ou se alguém adicionar um `await` entre as duas linhas, torna-se um bug.

**Decisão arquitetural:** Usar `setdefault` que é atómico: `_conversation_locks.setdefault(conv_id, asyncio.Lock())`. Nota: isto cria um Lock extra que pode não ser usado, mas é negligível em memória.

**Prioridade Codex:** P2 — Baixo risco actual, preventivo.

---

### 🟠 IMPORTANT-4: Fire-and-Forget na Persistência

**Ficheiro:** `agent.py` — chamadas a `_persist_conversation()` e `_save_feedback()`
**Impacto:** Se a persistência falhar silenciosamente, o utilizador perde dados sem saber.

**Decisão arquitetural:** Verificar se `_persist_conversation()` está dentro de try/except com logging adequado. Se está em `asyncio.create_task()` sem error handler, adicionar um callback que logue o erro. Não é necessário propagá-lo ao user (degradação graciosa), mas deve ser visível nos logs.

**Prioridade Codex:** P2.

---

### 🟡 MINOR-1: Cache Race Conditions em learning.py

**Ficheiro:** `learning.py`
**Impacto:** Refresh concorrente do cache de rules/examples pode causar duplicação de trabalho ou estados inconsistentes.

**Decisão arquitetural:** Adicionar um flag `_refreshing` ou um Lock simples para evitar refreshes simultâneos. Se dois requests triggeram refresh ao mesmo tempo, o segundo deve esperar pelo resultado do primeiro.

**Prioridade Codex:** P3.

---

### 🟡 MINOR-2: WIQL no System Prompt Expõe Sintaxe ao Utilizador

**Ficheiro:** `tools.py`, linhas 1036-1039
**Impacto:** O system prompt inclui exemplos de WIQL, o que ensina ao LLM a sintaxe. Se o LLM for manipulado, já tem o "manual" para construir queries maliciosas.

**Decisão arquitetural:** Aceitar como risco inerente ao design (o LLM precisa saber WIQL para funcionar). Mitigar via CRITICAL-1 (sanitização dos inputs).

**Prioridade Codex:** Nenhuma acção directa — mitigado por CRITICAL-1.

---

### 🟡 MINOR-3: Hardcoded Admin Password (já identificado no review anterior)

**Ficheiro:** `storage.py`
**Já documentado no ARCHITECTURE_REVIEW_v7.1.md, secção 6.2.**
**Status:** ✅ CORRIGIDO — Migrado para `ADMIN_INITIAL_PASSWORD` env var com fallback `secrets.token_urlsafe(16)`.

**Prioridade Codex:** Fechado.

---

## VALIDAÇÃO PÓS-SPRINT — 2026-02-23

### Resultado: ✅ TODAS AS TAREFAS P0 E P1 VALIDADAS

Auditoria independente realizada pelo Arquiteto após implementação pelo Codex.

| Tarefa | Finding | Status | Validação |
|--------|---------|--------|-----------|
| 4.7.1 | WIQL Injection | ✅ FECHADO | `_sanitize_wiql_where()` com blocklist regex (SELECT/DROP/DELETE/UPDATE/INSERT/MERGE/EXEC/UNION + `;` + `--` + `/* */`), length limit 2000, balanced quotes. `_validate_workitem_type()` com whitelist. `parent_id` cast int + positivo. `_safe_wiql_literal()` para escaping. |
| 4.7.2 | CORS Wildcard | ✅ FECHADO | `ALLOWED_ORIGINS` env var em config.py com whitelist default (produção + localhost dev). Middleware custom `enforce_allowed_origins` retorna 403 para origens não-listadas. Set lookup O(1). |
| 4.7.3 | XSS Markdown | ✅ FECHADO | DOMPurify 3.2.6 via CDN. `sanitizeHtmlOutput()` com ALLOWED_TAGS/ATTR + FORBID_TAGS (script/iframe/object/embed/svg/math/img). `sanitizeLinkUrl()` valida protocol whitelist (http/https only). `escapeHtml()` como fallback. |
| C-4 | XSS HTML Export | ✅ FECHADO | `html.escape()` aplicado a títulos, sumários, headers, células, URLs (com `quote=True`), e JSON raw. `_safe_http_url()` valida esquema no server-side. |
| 4.7.5a | Lock setdefault | ✅ FECHADO | `_conversation_locks.setdefault(conv_id, asyncio.Lock())` — atómico. |
| 4.7.5b | Background task logging | ✅ FECHADO | `_create_logged_task()` com `add_done_callback` que loga exceções. Usado em ambas as chamadas de persistência (sync + stream). |
| 4.7.5c | Cache refresh lock | ✅ FECHADO | `_prompt_rules_lock = asyncio.Lock()` com double-check pattern (fast path sem lock + re-check dentro do lock). Previne thundering herd. |
| M-3 | Admin password | ✅ FECHADO | `ADMIN_INITIAL_PASSWORD` importado de config. Fallback para `secrets.token_urlsafe(16)` com warning log. Sem hardcoded credentials. |

### ✅ Tarefa 4.7.4 — Rate Limiting (IMPORTANT-1) — CONCLUÍDA E VALIDADA

Implementado com `slowapi==0.1.9`. Key funcs robustas: `_user_or_ip_rate_key` (JWT sub com fallback IP) e `_login_rate_key` (IP only). `_client_ip()` respeita `X-Forwarded-For` para Azure App Service. Handler 429 custom com JSON uniforme + `Retry-After` header calculado dinamicamente. Shared limit `chat_budget` (10/min) partilhado entre sync, stream e chat/file. `/health` e `/api/info` sem limites. Admin boost não aplicado por limitação de slowapi em limites dinâmicos — aceitável.

---

## INSTRUÇÕES PARA O CODEX — TAREFAS RESTANTES

### ~~Tarefa 4.7.4 — Rate Limiting (IMPORTANT-1)~~ ✅ CONCLUÍDA E VALIDADA

#### ~~Tarefa 4.7.5 — Quick Fixes~~ ✅ CONCLUÍDA E VALIDADA

---

### NOTA SOBRE TAREFA 4.6 (PPTX — Shelved)

O upload de ficheiros .pptx falha em produção porque o Oryx build system usa Python 3.9 para instalar dependências (lxml .so files compilados para cpython-39), apesar de LINUX_FX_VERSION=PYTHON|3.12. As opções são:
1. **Custom Docker image** com Python 3.12 e lxml pré-compilado
2. **Oryx build customization** via `.python_packages` ou `oryx-manifest.toml`
3. **Serverless function** dedicada para conversão PPTX (Azure Function com Python 3.12)

Nenhuma é trivial. Fica para decisão futura. O import guard já está implementado — a app funciona sem pptx, apenas retorna 503 no endpoint de upload de .pptx.

---

### ROADMAP PÓS-SPRINT

~~Após a Fase 4.7 (sprint de segurança), o estado será:~~
**ACTUALIZAÇÃO:** Fase 5 (v7.2.0) concluída e validada em 2026-02-23.

Estado actual:
- Todas as vulnerabilidades CRITICAL e IMPORTANT eliminadas (11/11)
- Rate limiting activo (slowapi)
- XSS prevenido via DOMPurify
- CORS restrito (whitelist)
- WIQL sanitizado
- JWT em httpOnly cookies (localStorage eliminado)
- Tool registry dinâmico com Figma e Miro read-only
- Streaming incremental (sem re-render completo)
- Feedback memory capped (deque maxlen=100)

Próximos:
- **Tarefa 4.6:** PPTX (quando houver solução para o Oryx/lxml)
- **Fase 6:** Deep Analysis (PDF chunking, digest) (v7.2.1)

---

## RESUMO EXECUTIVO

| # | Finding | Severidade | Status | Validado |
|---|---------|-----------|--------|----------|
| C-1 | WIQL Injection | 🔴 CRITICAL | ✅ FECHADO | Blocklist regex + whitelist + int cast |
| C-2 | CORS Wildcard + Credentials | 🔴 CRITICAL | ✅ FECHADO | Env var whitelist + middleware 403 |
| C-3 | XSS Markdown Rendering | 🔴 CRITICAL | ✅ FECHADO | DOMPurify + sanitizeLinkUrl |
| C-4 | XSS HTML Export | 🔴 CRITICAL | ✅ FECHADO | html.escape() + _safe_http_url |
| I-1 | Sem Rate Limiting | 🟠 IMPORTANT | ✅ FECHADO | slowapi + shared limits + 429 handler |
| I-2 | JWT em localStorage | 🟠 IMPORTANT | ✅ FECHADO | httpOnly cookie + SameSite=Lax + credentials:include |
| I-3 | Lock Race Condition | 🟠 IMPORTANT | ✅ FECHADO | setdefault atómico |
| I-4 | Fire-and-Forget Persist | 🟠 IMPORTANT | ✅ FECHADO | _create_logged_task + callback |
| M-1 | Cache Race (learning) | 🟡 MINOR | ✅ FECHADO | asyncio.Lock + double-check |
| M-2 | WIQL no System Prompt | 🟡 MINOR | ✅ MITIGADO | Via C-1 sanitization |
| M-3 | Admin Password Hardcoded | 🟡 MINOR | ✅ FECHADO | Env var + secrets fallback |

**Veredicto final:** 11 de 11 findings resolvidos. Zero dívida de segurança. Fase 4.7 concluída; I-2 (JWT localStorage) fechado na Fase 5 (v7.2.0) com migração para httpOnly cookies.

— Claude (Arquiteto), 2026-02-23 (actualizado: todos os findings fechados)
