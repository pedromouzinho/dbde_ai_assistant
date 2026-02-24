# FASE 5 — Instruções para o Codex
## Emitido por: Claude (Arquiteto) | Data: 2026-02-23
## Referência: FASE5_PLAN.md (plano estratégico completo)

---

## REGRAS GERAIS (relembrar)

1. Tu decides o COMO. Eu defini o QUÊ e PORQUÊ no FASE5_PLAN.md.
2. Zero regressão — todas as funcionalidades existentes devem continuar a funcionar.
3. Um ficheiro, um propósito. Não juntar lógica de domínios diferentes.
4. Testar cada alteração antes de avançar para a próxima tarefa.
5. Os tokens abaixo são para configuração nas App Settings do Azure APENAS. Nunca os colocar em código, ficheiros de config no repo, ou logs.

---

## ORDEM DE EXECUÇÃO

### 1. Tarefa 5.6 — Feedback Memory Cap (30 min)

**Ficheiro:** `app.py`

**Objectivo:** O `feedback_memory` (lista global, linha ~184) deve ter cap de 100 entries FIFO.

**O que fazer:** Substituir a lista por `collections.deque(maxlen=100)`. Verificar que os locais que fazem `.append()` e iteração continuam a funcionar com deque (a API é compatível).

**Critério de sucesso:** `len(feedback_memory)` nunca excede 100 mesmo após 200+ feedbacks.

---

### 2. Tarefa 5.5 — Streaming Performance (2h)

**Ficheiro:** `static/index.html`

**Objectivo:** Eliminar re-render completo do markdown a cada token durante streaming.

**O que fazer:** Durante streaming activo, manter os blocos já renderizados no DOM e apenas re-renderizar o bloco de texto em progresso (após o último `\n\n`). Quando o streaming termina, fazer um render final completo para garantir consistência. O DOMPurify (`sanitizeHtmlOutput`) deve continuar a ser aplicado a cada bloco renderizado.

**Critério de sucesso:** Resposta com 5000+ chars em streaming sem stuttering visível. Output final visualmente idêntico ao actual.

---

### 3. Tarefa 5.1 — Tool Registry Dinâmico (4-6h)

**Ficheiros:** `tools.py` (refactor), `tool_registry.py` (novo, se fizer sentido), `agent.py` (adaptar)

**Objectivo:** Substituir o dispatch monolítico em `execute_tool()` por um registry onde cada tool se auto-regista.

**O que fazer:**
- Criar um mecanismo de registo (decorador, classe, ou dict global) onde cada tool declara: nome, definição OpenAI-format, e handler async
- `execute_tool(name, args)` faz lookup no registry
- `get_all_tool_definitions()` retorna tools activas (substitui a constante `TOOLS`)
- As tools existentes (DevOps, charts, file generation, etc.) registam-se usando o novo mecanismo
- Idealmente separar em ficheiros: `tools_devops.py`, `tools_charts.py`, etc. — mas se preferires manter num ficheiro só com o registry, aceitável desde que o pattern esteja lá para futuras separações
- O `agent.py` importa `execute_tool` e `get_all_tool_definitions` do registry em vez de directamente do tools.py

**Critério de sucesso:** A app arranca. Todas as tools existentes funcionam exactamente como antes. Adicionar uma nova tool requer apenas registá-la, sem alterar `execute_tool()` nem `agent.py`.

**Risco:** Alto — ficheiro core de 1100 linhas. Testar CADA tool individualmente.

---

### 4. Tarefa 5.7 — Migração JWT para httpOnly Cookies (4h)

**Ficheiros:** `auth.py`, `app.py`, `static/index.html`

**Objectivo:** O token JWT passa a ser gerido via cookie httpOnly em vez de localStorage.

**O que fazer:**
- `/api/login`: retornar o token JWT num `Set-Cookie: dbde_token=<jwt>; HttpOnly; Secure; SameSite=Lax; Path=/api; Max-Age=86400`
  - Usar `SameSite=Lax` (não Strict) para compatibilidade com redirects
  - `Secure` flag obrigatório em produção (HTTPS). Em dev local (HTTP), pode ser condicional via env var
- Endpoints autenticados: ler o token do cookie `dbde_token` PRIMEIRO, com fallback para `Authorization: Bearer` header (período de transição)
- Frontend (`index.html`): remover toda a lógica de `localStorage.getItem/setItem/removeItem("dbde_auth")`. Os requests fetch devem incluir `credentials: "include"` para enviar cookies
- Logout: `Set-Cookie: dbde_token=; HttpOnly; Secure; SameSite=Lax; Path=/api; Max-Age=0`
- A resposta JSON do login pode continuar a incluir dados do user (nome, role) mas NÃO o token — esse vai apenas no cookie

**Critério de sucesso:** Após login, `localStorage` não contém token. DevTools → Application → Cookies mostra `dbde_token` com HttpOnly. Toda a API funciona. Refresh da página mantém sessão (cookie enviado automaticamente).

---

### 5. Tarefa 5.2 — Integração Figma Read-Only (8-12h)

**Ficheiros:** `tools_figma.py` (novo), `config.py`

**Pré-requisito:** Tarefa 5.1 concluída (tool registry) + token configurado nas App Settings.

**Objectivo:** Tool `search_figma` registada no TOOL_REGISTRY que busca metadados de ficheiros/frames no Figma.

**O que fazer:**
- Usar Figma REST API v1 (base: `https://api.figma.com/v1`)
- Endpoints úteis: `GET /v1/me` (testar token), `GET /v1/files/:file_key` (metadados), `GET /v1/files/:file_key/nodes?ids=X` (nós específicos), `GET /v1/files/recent` (ficheiros recentes)
- A tool deve aceitar: query (texto livre), file_key (opcional), node_id (opcional)
- Retornar: lista de ficheiros ou frames com nome, thumbnail URL, link directo, última modificação
- Auth: header `X-Figma-Token: <token>` em cada request
- Credencial via `FIGMA_ACCESS_TOKEN = os.getenv("FIGMA_ACCESS_TOKEN", "")` em config.py
- Se token vazio, a tool NÃO se regista no registry (graceful disable)
- Cache de resultados: 5 minutos em memória para evitar rate limits da API
- Usar `httpx` (já existe como dependência) para requests async

**Critério de sucesso:** "Mostra-me os ficheiros recentes do Figma" → lista com nomes e links. "Detalhe do ficheiro X" → lista de frames/pages.

---

### 6. Tarefa 5.3 — Integração Miro Read-Only (8-12h)

**Ficheiros:** `tools_miro.py` (novo), `config.py`

**Pré-requisito:** Tarefa 5.1 concluída (tool registry) + token configurado nas App Settings.

**Objectivo:** Tool `search_miro` registada no TOOL_REGISTRY que lê boards e conteúdo no Miro.

**O que fazer:**
- Usar Miro REST API v2 (base: `https://api.miro.com/v2`)
- Endpoints úteis: `GET /v2/boards` (listar boards), `GET /v2/boards/:id` (detalhes), `GET /v2/boards/:id/items` (conteúdo: sticky notes, shapes, text)
- A tool deve aceitar: query (texto livre), board_id (opcional)
- Retornar: lista de boards ou items de um board (texto, tipo, cor, autor se disponível)
- Auth: header `Authorization: Bearer <token>` em cada request
- Credencial via `MIRO_ACCESS_TOKEN = os.getenv("MIRO_ACCESS_TOKEN", "")` em config.py
- Se token vazio, a tool NÃO se regista no registry (graceful disable)
- Cache de resultados: 5 minutos em memória
- Usar `httpx` para requests async

**Critério de sucesso:** "Lista os boards do Miro" → lista com nomes e links. "O que foi discutido no board X?" → conteúdo dos sticky notes.

---

### 7. Tarefa 5.4 — System Prompt Awareness (1-2h)

**Ficheiros:** `tools.py` (ou ficheiro de prompts), `tools_figma.py`, `tools_miro.py`

**Pré-requisito:** 5.2 e 5.3 concluídas.

**Objectivo:** O LLM sabe quando usar Figma vs Miro vs DevOps.

**O que fazer:**
- As tool definitions de Figma e Miro devem ter descriptions claras: "Usa esta tool quando o utilizador mencionar designs, mockups, ecrãs, UI, protótipos" (Figma) e "Usa esta tool quando o utilizador mencionar workshops, brainstorms, boards, sticky notes, planning sessions" (Miro)
- No system prompt geral, adicionar routing hints — mas APENAS se os tokens respectivos estiverem configurados (verificar via registry)
- As tool descriptions são a primeira linha de routing para o LLM. O system prompt é reforço.

**Critério de sucesso:** Perguntas sobre design → Figma. Perguntas sobre brainstorms → Miro. Perguntas sobre work items → DevOps (sem regressão).

---

### 8. Configuração de Tokens nas App Settings (pré-Bloco B)

**Quando:** Antes de iniciar as tarefas 5.2 e 5.3.

**O que fazer:** Configurar as seguintes App Settings no Azure App Service via CLI ou Kudu:

```
FIGMA_ACCESS_TOKEN = figd_4PQ2K9RLheWD3Z6I3_eBulCOW6gh-ZAuuNJGiabm
MIRO_ACCESS_TOKEN = eyJtaXJvLm9yaWdpbiI6ImV1MDEifQ_eaJKa6zqOQx7Gph68TDlErWB2tQ
```

Usar o método que já usas para configurar App Settings (az CLI ou Kudu API). Estes valores são segredos — não os colocar em código, logs, ou outputs.

Após configurar, verificar que estão activos:
- Kudu console: `echo $FIGMA_ACCESS_TOKEN` deve mostrar o valor
- Ou via `/api/info` se adicionares um flag de integrações activas

---

### 9. Finalização — Version Bump

**Quando:** Após TODAS as tarefas concluídas e validadas.

**O que fazer:**
- Bump `APP_VERSION` para `"7.2.0"` em `config.py`
- Actualizar label de versão no frontend (`index.html`) se aplicável
- ZIP deploy final

---

## DEPLOY

ZIP deploy com todos os ficheiros alterados + novos (`tool_registry.py`, `tools_figma.py`, `tools_miro.py`). Sem dependências pip novas — `httpx` e `slowapi` já existem.

Se o deploy da Fase 4.7 (rate limiting com slowapi) ainda não foi para produção, incluir no mesmo deploy.

---

## CRITÉRIO DE ABORT

Parar e reportar se:
- O refactor 5.1 quebrar qualquer tool existente e não conseguires recuperar em 30 min
- A migração httpOnly 5.7 quebrar o login e não conseguires reverter
- A API do Figma ou Miro retornar 401/403 com os tokens fornecidos (reportar para eu verificar com o Pedro)
- Qualquer tarefa causar crash da app em produção — reverter imediatamente

---

— Claude (Arquiteto), 2026-02-23
