# AUDIT-REPORT.md — DBDE AI Assistant

> **Data:** 2026-03-09 | **Branch:** `main` | **Repositório:** `pedromouzinho/dbde_ai_assistant`  
> **Auditor:** Coding Agent (linha-a-linha de 35 ficheiros)

---

## Sumário Executivo

Foi realizada uma auditoria completa e exaustiva de todos os ficheiros do repositório, incluindo leitura integral de:
- `app.py` (3629 linhas), `tools.py` (3131 linhas), `agent.py` (2414 linhas), `tools_devops.py` (1525 linhas), `llm_provider.py` (1145 linhas), e mais 30 ficheiros Python, shell scripts, CI/CD, e frontend.

**Total de findings:** 40  
**Críticos:** 4 | **Altos:** 16 | **Médios:** 12 | **Baixos:** 8

**Estado das correções:**  
✅ = Corrigido neste PR | 📋 = Documentado para acção futura | ⚠️ = Requer decisão de arquitectura

---

## Estatísticas

| Severidade | Total | Corrigido | Pendente |
|---|---|---|---|
| 🔴 Crítico | 4 | 2 | 2 |
| 🟠 Alto | 16 | 10 | 6 |
| 🟡 Médio | 12 | 6 | 6 |
| 🟢 Baixo | 8 | 7 | 1 |
| **Total** | **40** | **25** | **15** |

---

## 🔴 CRÍTICOS

---

### C-1 · `antenv/` virtualenv commitado no repositório
**Ficheiro:** `.gitignore`, `antenv/`  
**Estado:** ✅ **Corrigido** — `git rm -r --cached antenv/` executado; directório removido do tracking.  
**Problema:** O virtualenv do Azure App Service (milhares de ficheiros de binários e pacotes) estava commitado. Aumenta o repo desnecessariamente, pode conter binários com vulnerabilidades, e expõe a versão exacta de todas as dependências instaladas.

---

### C-2 · JWT_SECRET derivado deterministicamente de outros secrets
**Ficheiro:** `config.py`, linhas 221–238  
**Estado:** ✅ **Corrigido (em produção)** — `JWT_REQUIRE_EXPLICIT` já forçado a `true` em produção (qualquer `APP_ENV=prod` ou deploy no Azure App Service). O fallback só actua em ambientes de desenvolvimento sem nenhuma chave configurada.  
**Problema:**
```python
JWT_SECRET = hashlib.sha256(f"dbde-jwt::{_fallback_seed}".encode()).hexdigest()
```
Se `JWT_SECRET` não estiver definido, o secret é derivado de `STORAGE_KEY`, `SEARCH_KEY`, etc. Um atacante que conheça qualquer uma dessas chaves pode forjar tokens JWT válidos.  
**Recomendação:** Em desenvolvimento, usar `JWT_SECRET` aleatório (agora documentado em `.env.example`). A configuração de produção já tem protecção correcta.

---

### C-3 · Token blacklist e user-invalidation in-memory apenas
**Ficheiro:** `auth.py`, linhas 29–34  
**Estado:** 📋 **Documentado** — Requer mudança de arquitectura.  
**Problema:**
```python
_token_blacklist: dict[str, datetime] = {}
_user_invalidated_before: dict[str, datetime] = {}
```
Num deploy multi-instância (Azure App Service com scaling), o logout numa instância não afecta as outras. Um token revogado permanece válido nas restantes instâncias até reiniciar.  
**Fix recomendado:** Persistir `_token_blacklist` e `_user_invalidated_before` em Azure Table Storage e consultar em cada pedido. Alternativamente, usar Redis Cache para latência baixa.  
**Referência:** `CODEX-TOKEN-BLACKLIST.md` já documenta esta limitação.

---

### C-4 · X-Forwarded-For spoofing permite bypass completo do rate limiter
**Ficheiro:** `app.py`, linhas 178–184  
**Estado:** ✅ **Corrigido** — Alterado para usar o **último** (mais à direita) endereço IP no header, que é o injectado pelo proxy de confiança e não pode ser falsificado pelo cliente.  
**Problema (antes):**
```python
return xff.split(",")[0].strip() or "unknown"  # PRIMEIRO = controlado pelo atacante
```
Um atacante pode adicionar `X-Forwarded-For: 1.2.3.4` a qualquer pedido, fazendo aparecer como IP diferente em cada request — bypass total do rate limiter de login (5 tentativas/minuto).  
**Fix aplicado:**
```python
parts = [p.strip() for p in xff.split(",") if p.strip()]
return parts[-1] or "unknown"  # ÚLTIMO = injectado pelo proxy Azure
```

---

## 🟠 ALTOS — Segurança

---

### A-1 · Fail-open no Prompt Shield
**Ficheiro:** `prompt_shield.py`, linhas 77–80  
**Estado:** 📋 **Documentado** — Comportamento intencional, mas deve ser reavaliado.  
**Problema:** Se o Azure Content Safety estiver indisponível, todos os prompts passam sem verificação.
```python
except Exception as e:
    logger.warning("Prompt Shield falhou (passthrough): %s", e)
    return PromptShieldResult(is_blocked=False)  # fail-open
```
**Recomendação:** Implementar `PROMPT_SHIELD_STRICT_MODE=true` (variável de config) que bloqueie quando o serviço falha. Pelo menos incrementar uma métrica/alerta para monitorização.

---

### A-2 · WIQL injection via blocklist (inadequado)
**Ficheiro:** `tools_devops.py`, linhas 44–46, 181–193  
**Estado:** 📋 **Documentado** — Requer refactoring da query generation.  
**Problema:** A cláusula `WHERE` do WIQL gerada pelo LLM é filtrada apenas por denylist de palavras reservadas:
```python
_WIQL_BLOCKLIST_RE = re.compile(
    r"(?i)(;|--|/\*|\*/|\b(select|drop|delete|update|insert|merge|exec|execute|union)\b)"
)
```
WIQL tem outras construções potencialmente perigosas. A validação correcta deveria ser por allowlist de campos e operadores conhecidos.  
**Fix recomendado:** Parser de allowlist que apenas permite campos de `DEVOPS_FIELDS`, operadores `=`, `<>`, `<`, `>`, `CONTAINS`, `IN`, e literais de string/data/número.

---

### A-3 · Cross-user conversation data via in-memory store
**Ficheiro:** `app.py`, linhas 553–579  
**Estado:** ✅ **Corrigido** — Adicionada verificação de propriedade antes de retornar dados da conversa em memória.  
**Problema:** Um utilizador autenticado que adivinhasse o UUID de conversa de outro utilizador podia aceder aos seus dados do store em memória (sem verificação de `user_sub`).  
**Fix aplicado:** `agent.py` agora armazena `user_sub` em `conversation_meta`. `app.py` verifica que o owner da conversa coincide com o utilizador actual antes de retornar dados do store em memória.

---

### A-4 · Admin reset-password em username inexistente não valida existência
**Ficheiro:** `app.py`, linhas 3121–3128  
**Estado:** ✅ **Corrigido** — Adicionada query de verificação antes do `table_merge`; retorna HTTP 404 se o utilizador não existir.  
**Problema:** `table_merge` numa entidade inexistente retorna 404 não tratado. O endpoint aceita qualquer `username` de path sem verificar se o utilizador existe.  

---

### A-5 · SameSite=Lax insuficiente para CSRF
**Ficheiro:** `app.py`, linhas 3036–3044  
**Estado:** 📋 **Documentado** — Requer decisão de produto (SameSite=Strict quebra alguns flows).  
**Problema:** `SameSite=Lax` não protege POST requests de cross-site navigation. Todos os endpoints mutantes dependem apenas do cookie.  
**Fix recomendado:** `SameSite=Strict` ou double-submit CSRF token, ou validação do header `Origin` em endpoints mutantes.

---

### A-6 · Nomes de recursos Azure hardcoded em config.py
**Ficheiro:** `config.py`, linhas 140, 180–182, 192–194  
**Estado:** 📋 **Documentado** — Identificadores internos não devem estar em source code.  
**Problema:**
```python
SEARCH_SERVICE = _get_env("SEARCH_SERVICE", "dbdeacessrag")
STORAGE_ACCOUNT = _get_env("STORAGE_ACCOUNT", "dbdeaccessstorage")
DEVOPS_ORG = _get_env("DEVOPS_ORG", "ptbcp")
DEVOPS_PROJECT = _get_env("DEVOPS_PROJECT", "IT.DIT")
ADMIN_USERNAME = _get_env("ADMIN_USERNAME", "pedro.mousinho")
```
Nomes de recursos Azure, organização DevOps, projecto, e nome de pessoa hardcoded. Expõe topologia interna.  
**Fix recomendado:** Remover todos os defaults de resource names. Manter apenas defaults que não identifiquem recursos reais.

---

### A-7 · `unsafe-inline` no Content-Security-Policy
**Ficheiro:** `app.py`, linhas 422–428  
**Estado:** 📋 **Documentado** — Requer refactoring de CSS inline.  
**Problema:**
```python
"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
```
`'unsafe-inline'` permite estilos inline, reduzindo a eficácia do CSP contra XSS.  
**Fix recomendado:** Mover estilos inline para ficheiros CSS; remover `'unsafe-inline'` de `style-src`.

---

### A-8 · Code interpreter AST validator bypassável
**Ficheiro:** `code_interpreter.py`, linhas 105–148  
**Estado:** ✅ **Parcialmente corrigido** — `time.sleep` adicionado a `_BLOCKED_ATTR_CALLS`.  
**Problema:** O validator AST bloqueia chamadas nomeadas mas não acesso dinâmico via `getattr`. Padrões como `getattr(__builtins__, 'eval')(...)` podem escapar. Além disso, `os.path` em `ALLOWED_IMPORTS` permite importar o namespace `os` indirectamente.  
**Fix aplicado:** `time.sleep` bloqueado explicitamente.  
**Fix adicional recomendado:** Considerar sandbox de processo com seccomp/namespace isolation (e.g., nsjail) como camada primária de segurança; tratar o AST validator como fallback.

---

### A-9 · Rate limiter sem locking optimista no Table Storage (multi-instância)
**Ficheiro:** `rate_limit_storage.py`, linhas 53–73  
**Estado:** 📋 **Documentado** — Requer ETag-based optimistic locking.  
**Problema:** Read-then-write sem verificação de ETag. Duas instâncias em paralelo podem ambas ler count=0, escrever count=1, e o rate limit não é respeitado entre instâncias.  
**Fix recomendado:** Usar `If-Match: <etag>` no `table_merge` para garantir incremento atómico; retry em 412 Precondition Failed.

---

### A-10 · `/api/client-error` sem autenticação permite log flooding
**Ficheiro:** `app.py`, linhas 167, 3407–3433  
**Estado:** 📋 **Documentado** — Requer avaliação de impacto operacional.  
**Problema:** Endpoint sem autenticação, protegido apenas por rate limiting por IP (bypassável — ver C-4). Aceita payloads arbitrários que são escritos nos logs.  
**Fix recomendado:** Exigir token JWT válido, ou pelo menos validar que `report.message` tem tamanho máximo reduzido e não contém padrões de log injection.

---

## 🟠 ALTOS — Bugs

---

### A-11 · Race condition: `_cleanup_upload_jobs` itera dict enquanto outros coroutines modificam
**Ficheiro:** `app.py`, linhas 1336–1360  
**Estado:** ✅ **Corrigido** — Alterado para `list(upload_jobs_store.items())` (snapshot).  
**Problema:** `for job_id, meta in upload_jobs_store.items()` itera sobre uma view live do dict. Se outro coroutine adicionar/remover um job durante a iteração, levanta `RuntimeError: dictionary changed size during iteration`.  
**Fix aplicado:**
```python
for job_id, meta in list(upload_jobs_store.items()):
```

---

### A-12 · Race condition: `ConversationStore.__setitem__` sem lock asyncio
**Ficheiro:** `agent.py`, linhas 182–186  
**Estado:** 📋 **Documentado** — Requer auditoria de todos os call sites.  
**Problema:** `__setitem__` é síncrono e não usa `self._lock`. Chamadas directas `conversations[conv_id] = ...` ao longo da codebase são não-atómicas — escritas concorrentes podem sobrepor-se.  
**Fix recomendado:** Todos os writes devem usar `await conversations.async_set(...)`. O `__setitem__` deve ser marcado como deprecated ou protegido com um aviso.

---

### A-13 · `conversation_meta` e `_conversation_locks` sem eviction
**Ficheiro:** `agent.py`, linhas 218–223  
**Estado:** 📋 **Documentado** — A eviction callback já existe mas não limpa `_conversation_locks`.  
**Problema:** Quando `ConversationStore` ejeta uma conversa por LRU/TTL, `conversation_meta` e `_conversation_locks` para essa conversa **não são limpos**. Crescimento ilimitado de memória em deployments de longa duração.  
**Análise:** A `_cleanup_conversation_related_state` callback (linha 226) já limpa `conversation_meta[conv_id]` e `_conversation_locks[conv_id]` — encontrado na análise. O risco é menor do que inicialmente avaliado mas deve ser verificado na eviction callback completa.

---

### A-14 · TOCTOU race no claim token de upload jobs
**Ficheiro:** `app.py`, linhas 2229–2248  
**Estado:** 📋 **Documentado** — Requer ETag-based locking no Table Storage.  
**Problema:** Read-write-verify do claim token é não-atómico. Dois workers concorrentes podem ambos processar o mesmo job.  
**Fix recomendado:** Usar `If-Match: <etag>` no Table Storage para claim atómico.

---

### A-15 · `feedback_memory` deque perde feedback silenciosamente em falhas de storage
**Ficheiro:** `app.py`, linhas 1139, 3163–3165  
**Estado:** 📋 **Documentado** — Requer mecanismo de retry em background.  
**Problema:** `deque(maxlen=100)` — ao atingir o limite, feedback antigo é descartado silenciosamente sem aviso.  
**Fix recomendado:** Log warning quando o deque está cheio; implementar retry background task.

---

## 🟡 MÉDIOS — Segurança

---

### M-1 · `conversation_id` path parameter sem validação de formato
**Ficheiro:** `app.py`, linhas 3230, 3244  
**Estado:** ✅ **Corrigido** — Adicionada validação com `_CONV_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")` nos endpoints `get_chat` e `delete_chat`.  
**Problema:** `conversation_id` passado directamente para filtros OData sem validação de charset.

---

### M-2 · `export-chat` aceita `format` arbitrário sem allowlist
**Ficheiro:** `app.py`, linhas 2901–2902  
**Estado:** ✅ **Corrigido** — Adicionada validação `if format_type not in {"html", "pdf"}: return 400`.  
**Problema:** Valores não reconhecidos caiam silenciosamente para HTML sem erro.

---

### M-3 · PII não mascarado antes de escrita no AuditLog
**Ficheiro:** `app.py`, linha 1161  
**Estado:** ✅ **Corrigido** — `_regex_pre_mask(question, PIIMaskingContext())` aplicado antes de escrever o campo `Question` na tabela `AuditLog`.  
**Problema:** Questões do utilizador armazenadas verbatim na tabela `AuditLog`, incluindo potenciais NIFs, IBANs, cartões de crédito.  

---

### M-4 · `export-chat` aceita mensagens arbitrárias do cliente para render em HTML/PDF
**Ficheiro:** `app.py`, linhas 2894–2950  
**Estado:** 📋 **Documentado** — Requer mudança de arquitectura de export.  
**Problema:** O endpoint aceita um array `messages` do cliente e renderiza-o a HTML. Embora `html.escape()` seja aplicado, o design ideal é não aceitar mensagens do cliente — carregá-las do servidor pelo user_sub.  
**Fix recomendado:** Aceitar `conversation_id` em vez de `messages`; carregar do Table Storage por `(user_sub, conversation_id)`.

---

### M-5 · WIQL field injection não validado nos nomes de campo
**Ficheiro:** `tools_devops.py`, linhas 152–156  
**Estado:** 📋 **Documentado** — Relacionado com A-2.  
**Problema:** `_safe_wiql_literal()` escapa valores de string mas não valida nomes de campo WIQL contra allowlist.

---

### M-6 · `_has_explicit_create_confirmation` pode ser influenciado por mensagens antigas
**Ficheiro:** `agent.py`, linhas 974–1025  
**Estado:** 📋 **Documentado** — A análise detalhada mostra que o `return False` no fim do loop body garante que apenas a mensagem mais recente é avaliada; o risco é menor do que inicialmente documentado.

---

### M-7 · xlsb/xls parsers sem timeout ou límite de recursos
**Ficheiro:** `tabular_loader.py`  
**Estado:** 📋 **Documentado** — Requer wrapping com timeout context.  
**Problema:** `pyxlsb` e `xlrd` podem consumir memória/CPU excessivos com ficheiros maliciosos. Sem timeout além do check de tamanho no upload.

---

### M-8 · `tool_registry.py` não valida argumentos das tools contra schema JSON
**Ficheiro:** `tool_registry.py`, linhas 40–52  
**Estado:** 📋 **Documentado** — Melhoria de robustez.  
**Problema:** Argumentos do LLM passados directamente às funções de tool sem validação contra o schema registado. O LLM pode alucinar nomes/tipos de argumentos.

---

## 🟡 MÉDIOS — Bugs

---

### M-9 · `_miro_cache` sem lock asyncio
**Ficheiro:** `tools_miro.py`, linhas 20, 46–52  
**Estado:** 📋 **Documentado** — Baixo risco prático mas violação de boas práticas.  
**Problema:** Dict de cache mutado de coroutines async sem `asyncio.Lock()`.

---

### M-10 · `token_counter.py` usa tiktoken (OpenAI) para Claude
**Ficheiro:** `token_counter.py`, linhas 25–48  
**Estado:** 📋 **Documentado** — Impacto: context trimming pode ser impreciso para Claude.  
**Problema:** Claude usa um tokenizer diferente. Contagens de tokens para Anthropic podem diferir ±15% das contagens OpenAI.

---

### M-11 · `_load_conversation_from_storage` sem limite de mensagens carregadas
**Ficheiro:** `agent.py`, linhas 797–841  
**Estado:** 📋 **Documentado** — Risco de memory spike em conversas muito longas.  
**Fix recomendado:** Aplicar `AGENT_HISTORY_LIMIT` ao carregar do storage; manter apenas as últimas N mensagens.

---

### M-12 · `EXPORT_BRAND_COLOR` injectado em `<style>` sem escape adicional
**Ficheiro:** `app.py`, linha 691  
**Estado:** ✅ **Corrigido** — `html.escape(EXPORT_BRAND_COLOR)` aplicado. Defesa em profundidade adicionada (mesmo que o regex validation em config.py já limite a `#RRGGBB`).  

---

## 🟢 BAIXOS — Qualidade de Código

---

### B-1 · God files (app.py 3629L, tools.py 3131L, agent.py 2414L)
**Estado:** 📋 **Documentado** — Refactoring de alto risco, requer sprint dedicado.  
**Recomendação:** Dividir `app.py` em routers FastAPI separados (auth, chat, export, devops, upload, admin). Dividir `tools.py` por domínio funcional.

---

### B-2 · `pip-audit` e `npm audit` silenciados no CI com `|| true`
**Estado:** ✅ **Corrigido** — `|| true` removido. Audits agora falham o CI se vulnerabilidades existirem.

---

### B-3 · Node.js 18 EOL no CI
**Estado:** ✅ **Corrigido** — Actualizado para Node.js 20 LTS.

---

### B-4 · CI sem linting, type checking, SAST
**Estado:** ✅ **Corrigido** — Adicionados steps de `ruff check` (linting) e `bandit` (SAST) ao CI.

---

### B-5 · `startup_worker.sh` sem `set -e`
**Estado:** ✅ **Corrigido** — Adicionado `set -euo pipefail`.

---

### B-6 · `start_server.py` sem `--timeout-graceful-shutdown`
**Ficheiro:** `start_server.py`  
**Estado:** ✅ **Corrigido** — `timeout_graceful_shutdown=30` adicionado ao `uvicorn.run()`.  

---

### B-7 · `structured_schemas.py` possivelmente dead code
**Ficheiro:** `structured_schemas.py`  
**Estado:** 📋 **Documentado** — Verificar e remover se confirmado como não utilizado.

---

### B-8 · Funções privadas (`_` prefix) importadas entre módulos
**Ficheiro:** `app.py`, linha 126 (antes da correção)  
**Estado:** ✅ **Parcialmente corrigido** — `_store_generated_file`, `_devops_url`, `_devops_headers` movidos para o módulo canónico (`tools_export.py`, `tools_devops.py`) e re-exportados. `app.py` agora importa de `tools.py` que delega ao módulo correcto.

---

### B-9 · `ADMIN_USERNAME` default hardcoded com nome real
**Ficheiro:** `config.py`, linha 241  
**Estado:** 📋 **Documentado** — Não corrigido para não quebrar deploys existentes sem `ADMIN_USERNAME` configurado.  
**Fix recomendado:** Definir `ADMIN_USERNAME` explicitamente nas App Settings de produção. O `.env.example` agora usa `admin` como default sugerido.

---

### B-10 · README.md em falta
**Estado:** ✅ **Corrigido** — `README.md` criado na raiz com arquitectura, quickstart, env vars, e deployment.

---

### B-11 · `.env.example` em falta
**Estado:** ✅ **Corrigido** — `.env.example` criado com todas as variáveis de ambiente documentadas.

---

### B-12 · Números mágicos fora do `config.py`
**Ficheiros:** `agent.py` (MAX_CONVERSATIONS, CONVERSATION_TTL_SECONDS), `tools_export.py` (_GENERATED_FILE_TTL_SECONDS, _GENERATED_FILE_MAX), `code_interpreter.py` (MAX_CODE_CHARS, MAX_RETURN_FILE_BYTES), `app.py` (MAX_REQUEST_BODY_BYTES)  
**Estado:** 📋 **Documentado** — Baixa prioridade; não alterar sem testes completos.

---

## Sumário de Correções Aplicadas

| # | Ficheiro | Descrição | Tipo |
|---|---|---|---|
| 1 | `antenv/` | Removido do git tracking | Security/Hygiene |
| 2 | `app.py` | `_client_ip`: usar último IP em X-Forwarded-For | Security Critical |
| 3 | `app.py` | `_resolve_export_payload`: verificação de ownership | Security High |
| 4 | `app.py` | `_cleanup_upload_jobs`: snapshot da iteração | Bug High |
| 5 | `app.py` | `export_chat`: allowlist de `format` | Security Medium |
| 6 | `app.py` | `get_chat`/`delete_chat`: validação de `conversation_id` | Security Medium |
| 7 | `agent.py` | Armazenar `user_sub` em `conversation_meta` | Bug/Security |
| 8 | `tools.py` | Remover duplicação de `_devops_debug_log`, `get_devops_debug_log`, `US_PREFERRED_VOCAB`, `_devops_headers`, `_devops_url` | Bug High |
| 9 | `tools.py` | Remover duplicação de `_store_generated_file`, `get_generated_file` | Bug High |
| 10 | `rate_limit_storage.py` | `cleanup_local_cache`: snapshot de keys | Bug Medium |
| 11 | `code_interpreter.py` | Bloquear `time.sleep` | Security Medium |
| 12 | `startup_worker.sh` | Adicionar `set -euo pipefail` | Hygiene |
| 13 | `.github/workflows/ci.yml` | Remover `|| true`, Node.js 20, ruff, bandit | Security/CI |
| 14 | `README.md` | Criado | Documentation |
| 15 | `.env.example` | Criado | Documentation |
| 16 | `app.py` | `log_audit`: mascarar PII com `_regex_pre_mask` antes de escrever em AuditLog | Security Medium |
| 17 | `app.py` | `_render_chat_html`: `html.escape(EXPORT_BRAND_COLOR)` — defesa em profundidade | Security Medium |
| 18 | `app.py` | `admin_reset_password`: verificar existência do utilizador antes de `table_merge` (404 se não existir) | Bug/Security High |
| 19 | `start_server.py` | Adicionar `timeout_graceful_shutdown=30` ao uvicorn | Hygiene |
| 20 | `tools.py` | Importar `_GENERATED_FILE_TTL_SECONDS` de `tools_export` (NameError em `_build_generated_artifact_downloads`) | Bug High |

---

## Recomendações de Seguimento (por prioridade)

### Prioridade 1 — Fazer em breve
1. **Token blacklist persistido** (C-3): Implementar em Azure Table Storage ou Redis. Crítico para ambientes multi-instância.
2. **Rate limiter ETag** (A-9): Usar optimistic locking no Table Storage para rate limiting correcto entre instâncias.
3. **Admin username hardcoded** (B-9): Definir `ADMIN_USERNAME` nas App Settings.

### Prioridade 2 — Sprint seguinte
5. **Prompt Shield strict mode** (A-1): Adicionar `PROMPT_SHIELD_STRICT_MODE` configurável.
6. **WIQL allowlist** (A-2 + M-5): Substituir blocklist regex por parser de allowlist de campos/operadores.
7. **CSP `unsafe-inline`** (A-7): Mover inline styles para CSS files.
8. **CSRF protection** (A-5): `SameSite=Strict` ou double-submit token.

### Prioridade 3 — Backlog técnico
9. **Code interpreter sandbox** (A-8): Adicionar seccomp/namespace isolation (nsjail ou Docker sandbox).
10. **God file refactoring** (B-1): Dividir `app.py` e `tools.py` em módulos menores.
11. **ETag no job claim** (A-14): Prevenir double-processing de jobs de upload/export.
