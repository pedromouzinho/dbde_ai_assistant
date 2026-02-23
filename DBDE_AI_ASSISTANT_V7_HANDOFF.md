
---
title: "My Document"
output:
  pdf_document:
    latex_engine: xelatex
---



# DBDE AI Assistant v7.0.4 — Complete Project Handoff

> **Documento de contexto completo para continuacao do projecto.**
> Ultima actualizacao: 2026-02-23
> Autor: Pedro Mousinho, DIT/ADMChannels, Millennium BCP
> Versao actual em producao: **v7.0.4** (Fase 1B Quick Wins Completa)

---

## 1. O QUE E ESTE PROJECTO

Assistente de IA interno do Millennium BCP para a equipa DIT/ADMChannels. Permite consultar Azure DevOps (backlog, work items, user stories) via linguagem natural, pesquisar documentacao interna, analisar padroes de escrita, gerar User Stories, e exportar dados. Corre como Azure App Service (Linux, Python 3.12).

**URL de producao:** `https://dbdeai.pt` (custom domain) -> `https://millennium-ai-assistant-epa7d7b4defabwbn.swedencentral-01.azurewebsites.net`

**Stack:** FastAPI + React (CDN, sem build step) + Azure OpenAI + Anthropic Claude + Azure AI Search + Azure Table Storage + Azure DevOps REST API.

---

## 2. ARQUITECTURA v7.0.2

### 2.1 Modulos Python (backend)

```
app.py (487 linhas)        -- FastAPI routes, wiring, feedback indexing, endpoints
agent.py (391 linhas)      -- Agent loop (sync + SSE streaming), ephemeral injection, tool execution
auth.py (103 linhas)       -- JWT encode/decode, password hashing, FastAPI dependency
config.py (142 linhas)     -- TODAS as env vars e constantes centralizadas
export_engine.py (331)     -- Export CSV/XLSX/PDF/SVG/HTML
learning.py (119 linhas)   -- Aprendizagem adaptativa: regras, few-shot, cache (NOVO em v7.0.2)
llm_provider.py (691)      -- Abstraccao multi-modelo (Azure OpenAI + Anthropic)
models.py (175 linhas)     -- Pydantic models (request/response)
storage.py (206 linhas)    -- Azure Table Storage REST API (sem SDK)
tools.py (411 linhas)      -- 7 tools + system prompts + tool definitions
```

**Cadeia de imports (sem circularidades):**
```
app.py -> agent.py -> learning.py -> tools.py -> llm_provider.py -> config.py
                                  -> storage.py -> config.py
```

### 2.2 Frontend

```
static/index.html (1256 linhas) -- React app, CDN-loaded, zero build step
```

React 18 via CDN (`unpkg.com`), Babel standalone, Montserrat font. Componentes: LoginScreen, UserMenu, FeedbackWidget (1-10), MessageBubble, ToolBadges, ModelTierSelector. SSE streaming com fallback sync.

### 2.3 Fluxo de dados

```
Browser -> POST /chat/agent (ou /chat/agent/stream para SSE)
  -> agent.py: _ensure_conversation() -> _build_llm_messages() [efemero] -> llm_with_fallback()
    -> llm_provider.py: resolve tier -> AzureOpenAIProvider ou AnthropicProvider
      -> tool_calls? -> tools.py: execute_tool() -> Azure DevOps / AI Search
      -> loop ate max 5 iteracoes ou resposta final
  -> resposta ao browser (JSON ou SSE events)
```

**NOTA v7.0.2:** A injecao de regras aprendidas e few-shot examples e feita via `_build_llm_messages()` que cria uma **copia efemera** do historico. As mensagens de learning NUNCA persistem em `conversations[]` -- sao recalculadas a cada chamada ao LLM. Isto unifica o comportamento entre sync e streaming e elimina o bloating do historico.

### 2.4 Multi-modelo (LLM Tiers)

| Tier | Provider:Model | Uso |
|------|---------------|-----|
| `fast` | `azure_openai:gpt-4.1-mini` | Analises internas, classificacao |
| `standard` | `anthropic:claude-sonnet-4-6` | Default para chat |
| `pro` | `anthropic:claude-opus-4-6` | Qualidade maxima |
| `fallback` | `azure_openai:dbde_access_chatbot` | Se provider primario falhar |

**NOTA IMPORTANTE:** Os modelos Anthropic requerem `ANTHROPIC_API_KEY` configurado. Actualmente **nao esta activo** -- a quota no Azure AI Foundry ainda nao foi aprovada. O sistema faz fallback para Azure OpenAI (GPT-4.1-mini). Quando a key estiver disponivel, basta adicionar `ANTHROPIC_API_KEY` nas App Settings.

### 2.5 7 Tools disponiveis

| Tool | Funcao |
|------|--------|
| `query_workitems` | Query WIQL directa ao Azure DevOps. Contagens, listagens, filtros. |
| `search_workitems` | Pesquisa semantica vectorial (Azure AI Search, index `millennium-devops-index`) |
| `search_website` | Pesquisa no conteudo do site MSE (index `millennium-omni-index`) |
| `analyze_patterns` | Busca exemplos + analise LLM de padroes de escrita |
| `generate_user_stories` | Gera USs novas baseadas em padroes reais |
| `query_hierarchy` | Hierarquias parent/child (Epic->Feature->US->Task) |
| `compute_kpi` | KPIs, rankings, distribuicoes, tendencias (ate 1000 items) |

---

## 3. AZURE -- RECURSOS E CONFIGURACAO

### 3.1 Resource Group

Tudo no resource group do projecto DBDE em Sweden Central.

### 3.2 App Service

- **Nome:** `millennium-ai-assistant`
- **Plan:** Linux, Python 3.12
- **Regiao:** Sweden Central
- **URL:** `millennium-ai-assistant-epa7d7b4defabwbn.swedencentral-01.azurewebsites.net`
- **Custom domain:** `dbdeai.pt`
- **Startup command:** `startup.sh` (usa uvicorn, port 8000, 1 worker)

### 3.3 App Settings (Variaveis de Ambiente)

Estas sao as variaveis que DEVEM estar configuradas no Azure App Service -> Configuration -> Application settings:

```
AZURE_OPENAI_ENDPOINT     = https://dbdeaccess.openai.azure.com
AZURE_OPENAI_KEY          = (no Azure portal)
CHAT_DEPLOYMENT           = dbde_access_chatbot
EMBEDDING_DEPLOYMENT      = text-embedding-3-small
API_VERSION_CHAT          = 2024-02-15-preview

SEARCH_SERVICE            = dbdeacessrag
SEARCH_KEY                = (no Azure portal)

DEVOPS_PAT                = (Azure DevOps Personal Access Token)
DEVOPS_ORG                = ptbcp
DEVOPS_PROJECT            = IT.DIT

STORAGE_ACCOUNT           = dbdeaccessstorage
STORAGE_KEY               = (no Azure portal)

JWT_SECRET                = (custom secret)

# Opcional (quando Anthropic estiver disponivel):
ANTHROPIC_API_KEY         = (API key directa ou Azure Foundry key)
ANTHROPIC_FOUNDRY_RESOURCE = (nome do recurso Foundry, se aplicavel)
```

### 3.4 Azure AI Search (Cognitive Search)

- **Service:** `dbdeacessrag`
- **Indexes:**
  - `millennium-devops-index` -- work items indexados com embeddings
  - `millennium-omni-index` -- conteudo do site MSE
  - `millennium-examples-index` -- exemplos de feedback para few-shot learning

### 3.5 Azure Table Storage

- **Account:** `dbdeaccessstorage`
- **Tables criadas automaticamente no startup:**
  - `Users` -- autenticacao (PartitionKey: "user", RowKey: username)
  - `ChatHistory` -- conversas persistidas (PartitionKey: user_id, RowKey: conversation_id)
  - `feedback` -- ratings 1-10 com notas
  - `examples` -- exemplos positivos/negativos indexados
  - `PromptRules` -- regras aprendidas pelo admin
  - `AuditLog` -- log de utilizacao

### 3.6 Azure OpenAI

- **Resource:** `dbdeaccess`
- **Deployments:**
  - `dbde_access_chatbot` -- GPT-4.1 (ou GPT-4.1-mini, conforme configurado)
  - `text-embedding-3-small` -- embeddings para pesquisa semantica

---

## 4. DEPLOY

### 4.1 Deploy via ZIP (recomendado para novas dependencias)

O ZIP deve conter o virtualenv `antenv/` completo (bin/, lib/, pyvenv.cfg) para que o Oryx do Azure o reconheca e nao tente fazer `pip install`:

```bash
curl -X POST \
  "https://millennium-ai-assistant-epa7d7b4defabwbn.scm.swedencentral-01.azurewebsites.net/api/zipdeploy" \
  -u "\$millennium-ai-assistant:<deploy-password>" \
  -H "Content-Type: application/zip" \
  --data-binary @./dbde-ai-v7-patched.zip
```

### 4.2 Deploy de ficheiros individuais (via Kudu VFS)

Para updates rapidos sem restart de dependencias:

```bash
BASE="https://millennium-ai-assistant-epa7d7b4defabwbn.scm.swedencentral-01.azurewebsites.net/api/vfs/site/wwwroot"
AUTH="\$millennium-ai-assistant:<deploy-password>"

# Backend
curl -X PUT -u "$AUTH" --data-binary @./app.py "$BASE/app.py"
curl -X PUT -u "$AUTH" --data-binary @./agent.py "$BASE/agent.py"
curl -X PUT -u "$AUTH" --data-binary @./learning.py "$BASE/learning.py"
curl -X PUT -u "$AUTH" --data-binary @./tools.py "$BASE/tools.py"

# Frontend
curl -X PUT -u "$AUTH" --data-binary @./index.html "$BASE/static/index.html"
```

**Deploy password (Kudu):** `uq6Gt0NK9L9pBGiJ0r7Wl2FfG0c6vMExcwyPf76HBTiDulJ1qp2rQm7AYLyM`
**Deploy username:** `$millennium-ai-assistant`

### 4.3 Restart apos deploy (KuduLite Linux)

**IMPORTANTE:** O App Service Linux usa KuduLite, que **NAO** tem `/api/restart` nem suporta `DELETE /api/processes/0`. Metodos de restart que funcionam:

**Metodo 1 -- restartTrigger (recomendado, via Kudu):**
```bash
# ATENCAO: No KuduLite Linux, o path correcto e /site/wwwroot/ (NAO /site/config/)
# Confirmado em producao em 2026-02-22 (deploy Fase 1A v7.0.3)
curl -X PUT -u "$AUTH" \
  --data "$(date)" \
  "https://millennium-ai-assistant-epa7d7b4defabwbn.scm.swedencentral-01.azurewebsites.net/api/vfs/site/wwwroot/restartTrigger.txt"
```

**Metodo 2 -- Azure CLI:**
```bash
az webapp restart --name millennium-ai-assistant --resource-group <RESOURCE_GROUP_NAME>
```

**Metodo 3 -- Azure Portal:**
Overview -> botao "Restart", ou Configuration -> General settings -> "Save" (sem alterar nada).

### 4.4 Notas importantes de deploy

- O ZIP DEVE conter `antenv/` com o virtualenv completo (~11MB)
- NAO criar ZIPs no macOS com o Finder -- causa corruption no virtualenv
- Usar `zip -r` no terminal
- Apos ZIP deploy, o Oryx detecta `antenv/` e arranca o uvicorn via `startup.sh`
- Frontend individual pode ser atualizado sem restart (so Kudu PUT + Ctrl+Shift+R no browser)
- Backend individual requer restart apos PUT
- **Validacao pos-restart:** `GET /health` (200=healthy) + `GET /api/info` (200=import chain OK)

---

## 5. AUTENTICACAO

### 5.1 JWT

- Tokens JWT com HMAC-SHA256, implementacao manual (sem bibliotecas externas)
- Expira em 10 horas (`JWT_EXPIRATION_HOURS`)
- O `get_current_user()` e uma funcao SYNC (nao async) -- dependency do FastAPI

### 5.2 Users

- Admin default: `pedro.mousinho` / `Millennium2026!` (criado no startup se nao existir)
- Gestao de users via endpoints `/api/auth/create-user`, `/api/auth/users`, etc.
- Passwords hasheadas com PBKDF2-SHA256, 100k iteracoes

---

## 6. FUNCIONALIDADES DO FRONTEND

### 6.1 Chat

- SSE streaming com fallback para request/response sincrono
- Model tier selector (Fast / Standard / Pro)
- Tool badges visuais (mostra quais ferramentas foram usadas)
- Suporte a imagens (paste, drag & drop, file input)
- Upload de ficheiros (Excel, CSV, PDF, texto)
- Modo dual: "general" e "userstory" (User Story Writer)

### 6.2 Feedback Widget

Rating 1-10 com circulos coloridos:

- Verde (7-10): submete directamente
- Amarelo (5-6): submete directamente
- Vermelho (1-4): abre campo de texto "O que correu mal?" com opcao Enviar/Saltar

Ratings <=3 e >=7 sao automaticamente indexados no AI Search como exemplos negativos/positivos para few-shot learning.

### 6.3 Exports

- **HTML:** Export client-side da conversa completa (frontend)
- **CSV/XLSX/PDF/SVG/HTML:** Export server-side dos dados de tool results (backend `export_engine.py`)
- O frontend envia os dados da tool result directamente no pedido de export (nao depende da memoria do servidor)

### 6.4 Persistencia de conversas

- Conversas guardadas no Azure Table Storage (`ChatHistory`)
- Auto-save com debounce de 3 segundos
- Carregamento ao login via `/api/chats/{user_id}`
- Mensagens limitadas a 60KB JSON por conversa (trim automatico)

---

## 7. SISTEMA DE APRENDIZAGEM ADAPTATIVA

### 7.1 Arquitectura (v7.0.2)

O sistema de aprendizagem adaptativa vive no modulo `learning.py` e e invocado de forma **efemera** pelo `agent.py`. Antes de cada chamada ao LLM (tanto no path sync como no streaming), o `_build_llm_messages()` cria uma copia do historico e insere:

1. **Regras aprendidas** -- da tabela `PromptRules`, com cache de 1h em memoria. Admin pode criar/apagar via API. Cache invalidado automaticamente nos endpoints `POST/DELETE /api/learning/rules`.
2. **Exemplos semanticos** -- pesquisa vectorial no index `millennium-examples-index` com 3 exemplos positivos (rating >=7) e 2 negativos (rating <=3) mais similares a pergunta actual. Embedding calculado via `get_embedding()` do `tools.py`.

**Principio chave:** As mensagens de learning sao inseridas na posicao 1 da lista de mensagens (logo apos o system prompt, antes do historico conversacional) e **nunca persistem** em `conversations[]`. Cada chamada ao LLM recebe context fresco. Isto evita bloating acumulativo e garante comportamento identico entre sync e streaming.

### 7.2 Feedback pipeline

```
User clica rating -> POST /feedback -> guarda em Table Storage
  -> se rating >=7 ou <=3:
    -> guarda em tabela 'examples'
    -> indexa no AI Search com embedding da pergunta
    -> fica disponivel como few-shot example para futuras perguntas
```

### 7.3 Performance (v7.0.3)

O `get_few_shot_examples(question)` usa cache local em memoria (MD5 hash da pergunta normalizada, TTL 30min, max 50 entradas). Cache hit evita completamente as 3 chamadas HTTP (1 embedding + 2 AI Search). Cache miss: ~130ms. Implementado na Tarefa 1.4 da Fase 1A. A funcao `invalidate_few_shot_cache()` permite limpeza explicita.

---

## 8. LIMITACOES ACTUAIS E PROBLEMAS CONHECIDOS

### 8.1 Anthropic nao activo

Os modelos Claude (Sonnet/Opus) requerem `ANTHROPIC_API_KEY`. A quota no Azure AI Foundry ainda nao foi aprovada. Ate la, tudo corre em GPT-4.1-mini via Azure OpenAI.

### 8.2 Conversas in-memory (parcialmente resolvido em v7.0.3)

As conversas do agent vivem em memoria via `ConversationStore` (implementado na Tarefa 1.3). O store tem MAX_CONVERSATIONS=200, TTL=4h e eviction LRU. Ao reiniciar a app, conversas perdem-se (write-through para Table Storage planeado na Fase 2, Tarefa 2.2-2.3). O frontend envia dados directamente no pedido de export como fallback.

### 8.3 Rate limiting (resolvido em v7.0.3)

A API do DevOps tem retry com backoff exponencial (ate 5 tentativas, wait max 30s). **Desde v7.0.3**, o Azure AI Search tambem tem retry resiliente via `_search_request_with_retry()` (3 tentativas, 429 com Retry-After, 5xx backoff) — implementado na Tarefa 1.1. Falhas sao agora logadas em vez de silenciosas (Tarefa 1.2).

### 8.4 Streaming com tool calls

O streaming SSE funciona para texto puro, mas quando ha tool calls, o fluxo e: chamada non-streaming para detectar tools -> executar tools -> nova chamada -> eventualmente streaming do texto final. Nao e token-a-token puro durante a fase de tool calling.

---

## 9. ROADMAP / FUNCIONALIDADES PLANEADAS

### 9.1 Sprint 1 — Quick Wins (dias)

- **Activar Anthropic** -- quando a quota for aprovada, adicionar `ANTHROPIC_API_KEY` e testar os 3 tiers
- **System prompt mais abrangente** -- permitir respostas directas sem tools para perguntas gerais, conceptuais, redaccao de emails/textos. ~1h, zero codigo, valor alto
- **Largura variavel no chat** -- texto a 900px (de 800px), tabelas/graficos expandiveis a full-width. Toggle "expandir" por mensagem. ~2-3h
- **Polish dos exports** -- CSV, XLSX, PDF, SVG ja existem no `export_engine.py`; melhorar integracao com frontend (botoes visiveis junto de cada tabela/resultado)
- **Cache de few-shot por request** -- optimizacao menor para evitar embedding redundante no tool loop

### 9.2 Sprint 2 — Visualizacao e Dados (1 semana)

- **Charts interactivos no frontend** -- Plotly.js ou Chart.js via CDN. O objectivo NAO e so Sankey -- e ser um sistema generico de visualizacao: o utilizador faz upload de um Excel, pede "faz um pie chart de X por Y" e o assistente gera o grafico directamente no chat. Inclui: pie, bar, line, scatter, sankey, heatmap, etc. O `compute_kpi` e file uploads ja devolvem dados estruturados; o frontend renderiza. Zero peso no backend.
- **SVG como input e output** -- Aceitar ficheiros SVG no upload (adicionar `.svg` aos tipos aceites) para o assistente analisar diagramas, fluxos, arquitecturas. Como output, SVG e nativo do Plotly.js. Bidirecional.
- **File generation via prompts** -- gerar CSV/XLSX/PDF directamente a partir de perguntas ("gera-me um Excel com todas as USs activas do RevampFEE")

### 9.3 Sprint 3 — Integracoes Externas Leves (2-3 semanas)

- **Integracoes read-only com plataformas externas** -- O conceito e simples: adicionar tools que fazem GET a APIs externas para trazer contexto para o assistente, sem modificar nada nessas plataformas. NAO e um sistema de plugins rebuscado -- sao integracoes leves, read-only, com APIs que ja expoe REST/webhooks. Exemplos concretos:
  - **Figma** (API REST) -- buscar metadados de ficheiros, listar frames/componentes, trazer contexto de design para conversas sobre USs. O assistente pode responder "esta US refere-se a este ecra no Figma" com link directo.
  - **Miro** (API REST) -- ler boards, sticky notes, mindmaps. Util para contexto de workshops e planning sessions. O assistente sabe o que foi discutido no Miro sem o utilizador ter de copiar tudo.
  - Outros candidatos futuros: SharePoint (documentacao), Teams (mensagens de canais), Confluence.
- **Arquitectura:** Cada integracao e um modulo Python independente em `tools.py` (ou ficheiro separado) com a sua tool definition e implementacao. O `agent.py` carrega tools activas dinamicamente. Credenciais via App Settings (API keys/tokens). Sem OAuth complexo -- tokens estaticos ou PATs.

### 9.4 Backlog (longo prazo)

- **Criar work items no DevOps** -- actualmente so le, nao cria. Requer cuidado com permissoes e validacao
- **Excel Add-in** -- sidebar nativa no Excel que chama a API do assistente. Requer deployment AppSource ou sideloading corporativo. MVP: 2-4 semanas
- **Dashboard admin** -- visualizacao de metricas de utilizacao, feedback trends, gestao de regras
- **Multi-tenant** -- suporte para multiplas equipas com areas DevOps diferentes
- **Persistent conversations server-side** -- migrar de in-memory para Table Storage no backend

### 9.5 Descartado / Baixo ROI

- **Plotly+Kaleido no backend** -- +110MB no ZIP de deploy, inaceitavel. Charts ficam no frontend via CDN
- **WeasyPrint para PDFs** -- +100MB, problemas em App Service. `export_engine.py` ja resolve
- **Selector manual de modelo OpenAI** -- os tiers Fast/Standard/Pro ja cobrem isto; quando Anthropic activar, Standard e Pro sao Claude automaticamente
- **VBA macros para Excel** -- demasiado nicho, ninguem vai usar
- **Web search generico** -- complexidade alta para valor marginal fora do scope do assistente

---

## 10. HISTORICO DE VERSOES

### v7.0 -> v7.0.1 (Bug fixes)

| # | Bug | Causa | Fix |
|---|-----|-------|-----|
| 1 | Ecra branco apos login | React hooks (useEffect/useRef) declarados apos early return `if(!auth)` -- violacao das regras do React | Movidos todos os hooks para antes do early return |
| 2 | 500 em `/api/chats/` | `get_current_user` era `async def` mas chamado sem `await` nos endpoints | Convertido para `def` sync |
| 3 | Feedback era so thumbs up/down | v7 simplificou demais o widget | Restaurado widget v6 com 1-10, nota quando <=4 |
| 4 | Nao mostrava Descricao/AC | `query_workitems` ignorava o parametro `fields` | Usa `fields` custom quando fornecido |
| 5 | LLM nao pedia Descricao/AC | Tool definition dizia "campos ignorado, usa default" | Actualizada tool definition e system prompt |
| 6 | System prompt muito curto | v7 condensou prompt de ~60 para ~15 linhas | Restaurado prompt completo do v6 |
| 7 | Sem few-shot examples | Agent loop v7 nao chamava `get_learned_rules()` nem `get_few_shot_examples()` | Adicionada injeccao no agent.py |
| 8 | Export "Erro export" | Dependia de conversa in-memory (perde-se com restart) | Frontend envia dados directamente |

### v7.0.1 -> v7.0.2 (Ephemeral Learning Injection)

| # | Problema | Causa | Fix |
|---|----------|-------|-----|
| 9 | Bloating do historico com few-shot | `agent_chat()` fazia `.append()` de regras e few-shot em `conversations[]` -- acumulava system messages a cada turno | Criado `_build_llm_messages()` que constroi copia efemera; `conversations[]` so contem mensagens reais (user/assistant/tool) |
| 10 | Streaming sem aprendizagem adaptativa | `agent_chat_stream()` nao tinha injeccao de regras nem few-shot | `_build_llm_messages()` usado nos 4 pontos de chamada LLM (sync initial, sync loop, stream loop, stream final) |
| 11 | Circular import `from app import ...` | `agent.py` importava `get_learned_rules` e `get_few_shot_examples` de `app.py` dentro de try/except | Criado `learning.py` como modulo dedicado; imports limpos sem circularidade |

**Ficheiros alterados:** `agent.py` (modificado), `app.py` (modificado), `learning.py` (novo)
**Ficheiros intocados:** `tools.py`, `llm_provider.py`, `models.py`, `config.py`, `storage.py`, `export_engine.py`, `auth.py`, `index.html`
**Deploy:** Kudu VFS PUT (3 ficheiros) + restart via `restartTrigger.txt`
**Validacao:** `/health` 200, `/api/info` 200, chat sync OK, chat stream OK, bloating test PASS (+2 msgs/turno)

### v7.0.2 -> v7.0.3 (Fase 1A — Bug Fixes da Auditoria)

| # | Tarefa | Prioridade | Fix | Ficheiros |
|---|--------|-----------|-----|-----------|
| 1.0 | **Tier selector fix** — frontend enviava `tier` em vez de `model_tier`, Pydantic ignorava e usava default | P1 | Corrigido request body no POST do frontend: `tier` → `model_tier` | `static/index.html` |
| 1.1 | **AI Search retry** — search functions falhavam silenciosamente no primeiro 429/timeout | P2 | Criado `_search_request_with_retry()` em `tools.py` (3 retries, 429/Retry-After, 5xx backoff). Aplicado a `tool_search_workitems`, `tool_search_website` e `learning._search_examples_semantic` | `tools.py`, `learning.py` |
| 1.2 | **Silent failure logging** — 17 blocos except engoliam erros sem logging | P2 | Adicionado `logging.error`/`logging.warning` a 15 blocos em 5 ficheiros. `agent.py` main catch com `exc_info=True`. `storage.py` migrado de `print` para `logger.error` | `app.py`, `tools.py`, `agent.py`, `storage.py`, `auth.py` |
| 1.3 | **Memory eviction** — conversas em memoria cresciam sem limite | P1 | `ConversationStore` com MAX=200, TTL=4h, LRU eviction. Limpa `conversation_meta` e `uploaded_files_store` junto | `agent.py` |
| 1.4 | **Few-shot cache** — 3 HTTP calls por mensagem sem cache | P3 | Cache local por MD5 hash (TTL 30min, cap 50 entradas). `invalidate_few_shot_cache()` exposta | `learning.py` |

**Ficheiros alterados:** `static/index.html`, `tools.py`, `learning.py`, `app.py`, `agent.py`, `storage.py`, `auth.py`
**Ficheiros intocados:** `llm_provider.py`, `models.py`, `config.py`, `export_engine.py`
**Deploy:** Kudu VFS PUT (7 ficheiros) + restart via `restartTrigger.txt`
**Validacao:** Tier selector funcional (Fast→GPT-4.1-mini, Standard, Pro), AI Search retry com logging, falhas visiveis nos logs, memoria controlada com eviction, few-shot cache hit em perguntas repetidas
**Data:** 2026-02-22

### v7.0.3 -> v7.0.4 (Fase 1B — Quick Wins e UX)

| # | Tarefa | Fix | Ficheiros |
|---|--------|-----|-----------|
| 1.5 | **System prompt mais abrangente** — LLM chamava tools para perguntas conceptuais/redaccao | Expandido bloco "RESPOSTA DIRECTA SEM FERRAMENTAS" com 6 categorias e exemplos concretos | `tools.py` |
| 1.6 | **Largura variavel no chat** — tabelas e code blocks limitados a 900px | `.table-wrapper` e `pre` com `max-width: calc(100vw - 340px)`. Bolha streaming com `maxWidth: "min(900px, 100%)"` | `static/index.html` |
| 1.7 | **Export buttons por mensagem** — exports so no header, dificeis de encontrar | Botoes CSV/XLSX/PDF/HTML junto de cada mensagem com dados. Nova funcao `exportMessageData()`. Hotfix: SSE path agora persiste `tool_details`/`tool_results` no evento `done` | `static/index.html` |
| 1.8 | **SVG como input** — upload de SVGs nao aceite | Adicionado `.svg` ao `accept` do file input. Handler explicito em `app.py` com `col_names=["svg"]` | `static/index.html`, `app.py` |
| 1.9 | **Ativar Anthropic** | CONGELADA — quota nao aprovada | — |

**Ficheiros alterados:** `static/index.html`, `tools.py`, `app.py`, `config.py`
**Ficheiros intocados:** `agent.py`, `learning.py`, `llm_provider.py`, `models.py`, `storage.py`, `export_engine.py`, `auth.py`
**Deploy:** Kudu VFS PUT (4 ficheiros) + restart via `restartTrigger.txt` em `/site/wwwroot/`
**Validacao:** `/api/info` v7.0.4, `/health` healthy, resposta directa sem tools para perguntas conceptuais, upload SVG aceite, tabelas com scroll horizontal, export por mensagem funcional (sync e streaming)
**Nota:** Hotfix aplicado pelo Pedro durante validacao — SSE path nao persistia tool_details na mensagem final
**Data:** 2026-02-23

---

## 11. ESTRUTURA DOS ENDPOINTS

### Chat/Agent
- `POST /chat/agent` -- Chat sincrono (retorna `AgentChatResponse`)
- `POST /chat/agent/stream` -- Chat SSE streaming
- `POST /chat/file` -- Backward compat (redirige para /chat/agent)

### Auth
- `POST /api/auth/login` -- Login (retorna JWT)
- `POST /api/auth/create-user` -- Criar user (admin only)
- `GET /api/auth/users` -- Listar users (admin only)
- `DELETE /api/auth/users/{username}` -- Desactivar user
- `POST /api/auth/change-password` -- Mudar password
- `POST /api/auth/reset-password/{username}` -- Reset (admin)
- `GET /api/auth/me` -- Info do user actual

### Mode
- `POST /api/mode/switch` -- Mudar modo (general/userstory)

### File Upload
- `POST /upload` -- Upload ficheiro (Excel, CSV, PDF, texto)

### Export
- `POST /api/export` -- Export dados (CSV, XLSX, PDF, SVG, HTML)

### Feedback
- `POST /feedback` -- Submeter rating 1-10 + nota
- `GET /feedback/stats` -- Estatisticas

### Chat Persistence
- `POST /api/chats/save` -- Guardar conversa
- `GET /api/chats/{user_id}` -- Listar conversas
- `GET /api/chats/{user_id}/{conversation_id}` -- Obter conversa
- `DELETE /api/chats/{user_id}/{conversation_id}` -- Apagar conversa

### Learning
- `POST /api/learning/rules` -- Adicionar regra (admin)
- `GET /api/learning/rules` -- Listar regras
- `DELETE /api/learning/rules/{rule_id}` -- Apagar regra
- `POST /api/learning/analyze` -- Analisar feedback (admin)

### Info/Debug
- `GET /api/info` -- Info da app, modelos, capabilities
- `GET /health` -- Health check com status dos servicos
- `GET /debug/conversations` -- Debug conversas (admin)
- `GET /` -- Serve o frontend

---

## 12. REGRAS IMPORTANTES PARA O AGENTE

### 12.1 Workflow de deploy

Para updates rapidos (sem novas dependencias):
1. Output APENAS os ficheiros alterados
2. Deploy via Kudu VFS PUT
3. Restart via `restartTrigger.txt` (ver seccao 4.3)
4. NAO criar ZIP packages desnecessarios

Para updates com novas dependencias:
1. Criar virtualenv `antenv/` com todas as deps
2. ZIP tudo incluindo `antenv/`
3. Deploy via zipdeploy

### 12.2 React no frontend

- Sem build step -- React 18 via CDN + Babel standalone
- `React.createElement()` em vez de JSX
- Hooks DEVEM ser chamados ANTES de qualquer early return
- A ordem dos hooks deve ser identica em todos os renders

### 12.3 Azure DevOps WIQL

- Project e sempre `IT.DIT`
- Areas sob `IT.DIT\DIT\ADMChannels\DBKS\AM24\`
- Nomes de pessoas sao completos (ex: "Jorge Eduardo Rodrigues")
- Campos extra (Description, AcceptanceCriteria) devem ser pedidos explicitamente no parametro `fields`
- Rate limits: max 200 IDs por batch, retry com backoff

### 12.4 LLM Provider

- Formato canonico das mensagens: OpenAI format
- Para Anthropic: traducao automatica em `llm_provider.py`
- Tool definitions em formato OpenAI, traduzidas on-the-fly para Anthropic
- Fallback automatico: se provider primario falhar, tenta fallback (Azure OpenAI)

### 12.5 Learning / Aprendizagem Adaptativa

- Toda a logica de learning vive em `learning.py` (NAO em `app.py` nem `agent.py`)
- A injeccao e sempre efemera via `_build_llm_messages()` -- nunca mutar `conversations[]` com dados de learning
- Cache de regras invalida-se automaticamente via `invalidate_prompt_rules_cache()` nos endpoints de regras
- O `app.py` mantem apenas `_index_example()` (indexacao de feedback no AI Search) porque depende dos endpoints de feedback

---

## 13. ROADMAP DE 1-2 MESES (Fev-Abr 2026)

> Plano de execucao unificado: bugs da auditoria + features novas + melhorias de arquitectura.
> Cada fase so avanca quando a anterior esta estavel em producao.
> Datas sao estimativas — dependem de aprovacoes (Anthropic quota) e disponibilidade.
> Origem: merge da auditoria Codex (findings P1-P3) + investigacao de melhorias + novas features.

### FASE 1A — Bug Fixes da Auditoria ~~(Semana 1, ~24 Fev - 28 Fev)~~ COMPLETA 2026-02-22

**Objectivo:** Corrigir bugs reais identificados na auditoria. PRIORITARIO — fazer ANTES de qualquer feature nova.
**STATUS: COMPLETA** — Todas as 5 tarefas (1.0-1.4) implementadas e validadas. Ver secção 10 para detalhes.

| # | Tarefa | Prioridade | Ficheiros | Esforco | Notas |
|---|--------|-----------|-----------|---------|-------|
| 1.0 | **Fix tier selector** — frontend envia `tier` em vez de `model_tier`, o Pydantic ignora e usa sempre default. Mudar `tier: modelTier` para `model_tier: modelTier` no POST do frontend | P1 | `static/index.html` (~linha 754) | 15min | Sem isto, o seletor Fast/Standard/Pro nao funciona — e decorativo |
| 1.1 | **AI Search retry** — criar `_search_with_retry()` em `tools.py` com 3 tentativas e backoff (1-3-5s para 429). Usar em `search_workitems`, `search_website`, e em `learning.py` no `_search_examples_semantic` | P2 | `tools.py`, `learning.py` | 2h | Sem isto, um 429 do AI Search devolve resultado vazio silenciosamente |
| 1.2 | **Logging de falhas silenciosas** — substituir todos os `except: pass` e `except Exception: return []` por logging com `print(f"[modulo] descricao: {e}")`. Especialmente em `app.py` (`_index_example`) e `learning.py` (`_search_examples_semantic`) | P2 | `app.py`, `learning.py` | 1h | Sem isto, nao sabemos quando o learning falha |
| 1.3 | **Eviction de conversas em memoria** — substituir os 3 dicts globais em `agent.py` por um `ConversationStore` com max 200 entries, TTL 4h, cleanup a cada 30min. Background task no `app.py` startup | P1 | `agent.py`, `app.py` | 3h | Sem isto, memoria cresce sem limite. Com ~10-20 users nao e OOM iminente mas conversas abandonadas nunca limpam e impede scale-out |
| 1.4 | **Cache de few-shot por request** — cachear resultado de `get_few_shot_examples(question)` na primeira chamada dentro do mesmo request, reutilizar nas iteracoes seguintes | P3 | `agent.py` | 1-2h | Optimizacao de ~100ms por iteracao extra (~650ms worst case com 5 iteracoes) |

**Deploy:** Kudu VFS PUT (index.html, tools.py, learning.py, app.py, agent.py) + restart
**Validacao:** Seletor de tiers funcional (Fast devolve GPT-4.1-mini), AI Search retry nao falha silenciosamente, logs visiveis no App Service, memoria controlada
**Resultado:** v7.0.3

---

### FASE 1B — Quick Wins e UX ~~(Semana 2, ~3 Mar - 7 Mar)~~ COMPLETA 2026-02-23

**Objectivo:** Melhorias de UX e funcionalidade sem tocar na arquitectura. Zero risco.
**STATUS: COMPLETA** — Tarefas 1.5-1.8 implementadas e validadas. Tarefa 1.9 congelada (quota Anthropic). Ver secção 10 para detalhes.

| # | Tarefa | Ficheiros | Esforco | Dependencia |
|---|--------|-----------|---------|-------------|
| 1.5 | System prompt mais abrangente — adicionar regras para respostas directas sem tools (perguntas gerais, conceptuais, redaccao de emails/textos) | `tools.py` (system prompt) | 1h | Nenhuma |
| 1.6 | Largura variavel no chat — `maxWidth` de 800→900px para texto, tabelas/codigo expandiveis a full-width, toggle "expandir" por mensagem | `static/index.html` | 2-3h | Nenhuma |
| 1.7 | Polish dos export buttons — botoes de export visiveis junto de cada tabela/resultado no chat (CSV, XLSX, PDF, HTML). O backend ja suporta, falta o frontend mostrar | `static/index.html` | 3-4h | Nenhuma |
| 1.8 | SVG como input — adicionar `.svg` aos tipos aceites no upload. O conteudo SVG e texto, basta passar ao LLM como contexto | `app.py` (upload endpoint), `static/index.html` (file filter) | 1h | Nenhuma |
| 1.9 | Activar Anthropic — adicionar `ANTHROPIC_API_KEY` nas App Settings e testar os 3 tiers | `config.py` (ja preparado), Azure Portal | 30min | Quota aprovada |

**Deploy:** Kudu VFS PUT (index.html, tools.py, app.py) + restart
**Validacao:** Testar chat geral sem tools, testar exports, testar upload SVG, testar tiers
**Resultado:** v7.0.4

---

### FASE 2 — Criar no DevOps + Memoria Persistente (Semana 3, ~10-14 Mar)

**Objectivo:** O agente passa de leitor a escritor. Conversas sobrevivem a restarts.

| # | Tarefa | Ficheiros | Esforco | Dependencia |
|---|--------|-----------|---------|-------------|
| 2.1 | **Nova tool `create_workitem`** — POST para DevOps API com campos (Title, Description, AcceptanceCriteria, AreaPath, WorkItemType, AssignedTo, Tags). Usa JSON Patch format. O LLM pede confirmacao ao user antes de criar ("Vou criar esta US. Confirmas?") | `tools.py` | 3h | Nenhuma |
| 2.2 | Tool definition no TOOLS + instrucao no system prompt: "Quando o user confirmar criacao, usa create_workitem. Pergunta SEMPRE antes de criar" | `tools.py` | 1h | 2.1 |
| 2.3 | **Write-through para Table Storage** — apos cada turno do agent (par user+assistant), serializar `conversations[conv_id]` para `ChatHistory`. Mesmo formato que o frontend usa | `agent.py` | 3h | Fase 1A (ConversationStore) |
| 2.4 | **Lazy-load em `_ensure_conversation()`** — se conv_id nao esta em memoria mas existe na tabela, fazer load. Conversas sobrevivem a restarts e prepara multi-worker | `agent.py` | 2h | 2.3 |

**Deploy:** Kudu VFS PUT (tools.py, agent.py) + restart
**Validacao:** "Gera uma US sobre notificacoes push e cria no board" → work item criado no DevOps. Restart app → conversa anterior carrega do storage.
**Resultado:** v7.0.5

---

### FASE 3 — Charts e Visualizacao (Semana 4-5, ~17-28 Mar)

**Objectivo:** Sistema generico de visualizacao. Upload de dados → pedir qualquer tipo de grafico → renderizado no chat.

| # | Tarefa | Ficheiros | Esforco | Dependencia |
|---|--------|-----------|---------|-------------|
| 3.1 | Adicionar Plotly.js via CDN ao frontend — script tag defer + funcao `renderPlotlyChart(containerId, chartSpec)` generica | `static/index.html` | 3-4h | Nenhuma |
| 3.2 | Nova tool `generate_chart` — o LLM decide tipo (pie/bar/line/scatter/sankey/heatmap), eixos, dados e devolve JSON chart_spec. Frontend renderiza. A tool NAO gera imagem — so devolve spec | `tools.py`, `static/index.html` | 6-8h | 3.1 |
| 3.3 | Charts a partir de file upload — enriquecer contexto de upload com nomes de colunas e amostra (10 linhas). User pede "faz um pie chart da coluna X por Y" | `app.py`, `agent.py` | 4-6h | 3.1, 3.2 |
| 3.4 | Export de charts — botoes "Download SVG" e "Download PNG" em cada grafico (Plotly.downloadImage nativo) | `static/index.html` | 2h | 3.1 |
| 3.5 | File generation via prompts — tool `generate_file` que o LLM invoca para gerar CSV/XLSX/PDF a partir de dados estruturados. Backend usa `export_engine.py` | `tools.py`, `app.py`, `export_engine.py`, `static/index.html` | 4-6h | Nenhuma |

**Deploy:** Kudu VFS PUT (todos) + restart. Nao ha dependencias novas (Plotly.js e CDN)
**Validacao:** Upload Excel → pedir pie chart → ver grafico → download SVG. Query DevOps → pedir bar chart. "Gera-me um Excel com..." → download ficheiro.
**Resultado:** v7.1.0 (major: novas tools e frontend charting)

---

### FASE 4 — US Writer Pro (Semana 6, ~31 Mar - 4 Abr)

**Objectivo:** Modo userstory torna-se inteligente — ciclo de refinamento, perfis de escritor, visual parsing.

| # | Tarefa | Ficheiros | Esforco | Dependencia |
|---|--------|-----------|---------|-------------|
| 4.1 | **System prompt rewrite do `get_userstory_system_prompt()`** — ciclo Draft→Review→Final: LLM gera draft, apresenta, se user dá feedback re-chama generate_user_stories com feedback como context. Instrucoes de visual parsing (imagens→CTAs, inputs, labels) | `tools.py` | 2h | Nenhuma |
| 4.2 | **WriterProfiles table** — nova tabela no Table Storage. Cache de estilos por autor (output do analyze_patterns com analysis_type="author_style"). Carregamento em `generate_user_stories` para "gera USs como o Jorge escreve" sem refazer analyze do zero | `tools.py`, `storage.py` | 3h | Nenhuma |
| 4.3 | **Upload pre-processing inteligente** — quando modo e userstory, pre-processar conteudo: Excel→lista estruturada de requisitos, PDF→seccoes e headings como contexto hierarquico | `agent.py` | 3h | Nenhuma |
| 4.4 | **PPT support no upload** — adicionar `python-pptx` para extrair texto de slides como sequencia de requisitos | `app.py` | 2h | ZIP deploy (nova dependencia) |

**Deploy:** ZIP deploy (python-pptx e nova dependencia)
**Validacao:** Upload Figma screenshot + Excel requisitos → "gera USs como o Jorge escreve" → USs com estilo correcto e referencia a componentes visuais.
**Resultado:** v7.1.1

---

### FASE 5 — Integracoes Externas + Frontend Polish (Semana 7-8, ~7-18 Abr)

**Objectivo:** Trazer contexto de Figma e Miro. Optimizar frontend.

| # | Tarefa | Ficheiros | Esforco | Dependencia |
|---|--------|-----------|---------|-------------|
| 5.1 | **Registry de tools dinamico** — refactor de `tools.py` para suportar TOOL_REGISTRY. Cada integracao e um ficheiro separado. `agent.py` usa `get_tool_handler()` em vez de dispatch manual | `tools.py` (refactor), `agent.py` | 4-6h | Nenhuma |
| 5.2 | **Integracao Figma (read-only)** — tool `search_figma` via Figma REST API v1. Buscar ficheiros, listar frames, devolver metadados + thumbnails + links directos | `tools_figma.py` (novo), `config.py` | 8-12h | 5.1 |
| 5.3 | **Integracao Miro (read-only)** — tool `search_miro` via Miro REST API v2. Listar boards, ler sticky notes/shapes/text, devolver conteudo + links | `tools_miro.py` (novo), `config.py` | 8-12h | 5.1 |
| 5.4 | System prompt update para Figma/Miro — "quando mencionar designs/mockups → Figma"; "quando mencionar workshops/brainstorms → Miro" | `tools.py` | 1-2h | 5.2, 5.3 |
| 5.5 | **Streaming optimization** — `React.memo` no MessageBubble, so re-render do ultimo bloco durante streaming. Com respostas >2000 chars o re-render de `renderMarkdown()` fica visivelmente lento | `static/index.html` | 2h | Nenhuma |
| 5.6 | **feedback_memory cap** — limitar a 100 entries para evitar crescimento ilimitado | `app.py` | 30min | Nenhuma |

**Deploy:** ZIP deploy (novos ficheiros Python). Nao ha dependencias pip novas (usa httpx que ja existe)
**Validacao:** "Mostra-me os ecras do Figma do RevampFEE" → lista de frames. "O que foi discutido no Miro?" → conteudo. Chat fluido com respostas longas.
**Resultado:** v7.2.0
**Pre-requisito Pedro:** Configurar FIGMA_ACCESS_TOKEN e MIRO_ACCESS_TOKEN nas App Settings.

---

### FASE 6 — Analise Profunda (Semana 9-10, se houver tempo)

**Objectivo:** O agente analisa documentos completos e gera digests.

| # | Tarefa | Ficheiros | Esforco | Dependencia |
|---|--------|-----------|---------|-------------|
| 6.1 | **PDF chunking + indexacao temporaria** — documentos >50K chars sao divididos em chunks e indexados no AI Search (index temporario ou no `millennium-omni-index`). Permite pesquisa semantica em docs de 100+ paginas | `app.py`, `tools.py` | 4h | Nenhuma |
| 6.2 | **Nova tool `search_uploaded_document`** — pesquisa semantica no documento carregado. O user pergunta "o que diz o capitulo 3?" e o agente encontra | `tools.py` | 2h | 6.1 |
| 6.3 | **Daily digest endpoint** — `/api/digest` que gera HTML com: USs criadas ontem, bugs abertos >7 dias, items sem assignee, KPIs da semana | `app.py` | 3h | Nenhuma |

**Deploy:** Kudu VFS PUT + restart
**Validacao:** Upload PDF longo → perguntar sobre seccoes especificas → respostas precisas. GET /api/digest → HTML com resumo.
**Resultado:** v7.2.1

---

### TIMELINE VISUAL

```
Sem 1        Sem 2        Sem 3         Sem 4-5        Sem 6         Sem 7-8        Sem 9-10
Fev 24-28    Mar 3-7      Mar 10-14     Mar 17-28      Mar 31-Abr4   Abr 7-18       Abr 21+

 FASE 1A      FASE 1B      FASE 2        FASE 3         FASE 4        FASE 5         FASE 6
 Bug Fixes    UX Wins      DevOps Write  Charts &       US Writer     Figma+Miro     Deep
 v7.0.3       v7.0.4       + Memoria     Visualizacao   Pro           + Polish       Analysis
                           v7.0.5        v7.1.0         v7.1.1        v7.2.0         v7.2.1

 [tier fix]   [prompt]     [create_wi]   [Plotly CDN]   [draft→final] [tool registry] [PDF chunk]
 [retry]      [largura]    [write-thru]  [gen_chart]    [profiles]    [figma tool]    [search_doc]
 [logging]    [exports]    [lazy-load]   [upload→chart]  [upload pre] [miro tool]     [digest]
 [eviction]   [SVG input]                [chart export] [PPT]         [streaming opt]
 [fs cache]   [Anthropic?]               [gen_file]                   [fb_mem cap]
```

### NOTAS

- **Fases 1A-3 sao as criticas.** Corrigem bugs, adicionam DevOps write, memoria persistente, e charts.
- **Fases 4-5 sao diferenciadoras.** US Writer Pro e integracoes Figma/Miro tornam o agente unico.
- **Fase 6 e bonus.** So se houver tempo. Pode ser adiada sem impacto.
- **Cada fase e independente apos a 1A.** Se ficares atrasado, podes saltar fases sem quebrar as anteriores.
- **A regra de ouro:** uma fase, um deploy, uma validacao, proxima fase.

---

### 9.5 Descartado / Baixo ROI

- **Plotly+Kaleido no backend** -- +110MB no ZIP de deploy, inaceitavel. Charts ficam no frontend via CDN
- **WeasyPrint para PDFs** -- +100MB, problemas em App Service. `export_engine.py` ja resolve
- **Selector manual de modelo OpenAI** -- os tiers Fast/Standard/Pro ja cobrem isto; quando Anthropic activar, Standard e Pro sao Claude automaticamente
- **VBA macros para Excel** -- demasiado nicho, ninguem vai usar
- **Web search generico** -- complexidade alta para valor marginal fora do scope do assistente

---

## 14. MENSAGEM PARA O CODEX

> Instrucoes estruturadas para o agente de implementacao. Executar por ordem, uma tarefa de cada vez.
> **REGRA FUNDAMENTAL:** Nunca implementar mais do que uma tarefa por sessao. Validar, testar, e so depois avancar para a proxima.

---

### CONTEXTO OBRIGATORIO

Antes de qualquer implementacao, o agente DEVE:
1. Ler este ficheiro completo (`DBDE_AI_ASSISTANT_V7_HANDOFF.md`) para entender a arquitectura
2. Ler os ficheiros que vai alterar para entender o codigo actual
3. Respeitar as regras da seccao 12 (deploy workflow, React hooks, WIQL, etc.)
4. NAO alterar ficheiros que nao estao listados na tarefa
5. NAO instalar dependencias pip a menos que explicitamente indicado
6. Cada output deve incluir APENAS os ficheiros alterados, prontos para deploy via Kudu VFS PUT

---

### FASE 1A — BUG FIXES DA AUDITORIA (executar por ordem, ANTES de tudo o resto)

#### TAREFA 1.0 — Fix Tier Selector [P1]
```
FICHEIROS: static/index.html
ACCAO: Localizar o POST para /chat/agent e /chat/agent/stream (zona ~linha 754).
        O frontend envia { tier: modelTier } mas o Pydantic espera { model_tier: ... }.
        Mudar para { model_tier: modelTier }.
VALIDACAO: Selecionar "Fast", enviar mensagem. No response JSON, model_used deve ser "gpt-4.1-mini".
           Selecionar "Standard", enviar mensagem. model_used deve mudar.
DEPLOY: Kudu VFS PUT static/index.html (nao precisa restart)
```

#### TAREFA 1.1 — AI Search Retry Helper [P2]
```
FICHEIROS: tools.py, learning.py
ACCAO: 1. Em tools.py, criar funcao _search_with_retry(client, url, body, headers, max_retries=3)
           com retry para 429 (backoff 2-4-6s) e TimeoutException (backoff 2-4-6s).
           Return {"value": []} se todas as tentativas falharem.
        2. Usar em tool_search_workitems (substituir o client.post directo)
        3. Usar em tool_search_website (idem)
        4. Em learning.py, usar em _search_examples_semantic (importar de tools.py ou duplicar)
VALIDACAO: Simular 429 — o retry deve funcionar sem erro visivel ao utilizador.
DEPLOY: Kudu VFS PUT tools.py + learning.py + restart
```

#### TAREFA 1.2 — Logging de Falhas Silenciosas [P2]
```
FICHEIROS: app.py, learning.py
ACCAO: 1. Em app.py, localizar _index_example ou equivalente. Substituir qualquer
           "except: pass" ou "except Exception: pass" por:
           except Exception as e: print(f"[Learning] Index example failed: {e}")
        2. Em learning.py, localizar _search_examples_semantic. Substituir
           "except Exception: return []" por:
           except Exception as e: print(f"[Learning] Search examples failed: {e}"); return []
        3. Verificar se ha outros except silenciosos nos mesmos ficheiros e corrigir.
VALIDACAO: Provocar um erro e verificar que aparece nos logs do App Service.
DEPLOY: Kudu VFS PUT app.py + learning.py + restart
```

#### TAREFA 1.3 — Eviction de Conversas em Memoria [P1]
```
FICHEIROS: agent.py, app.py
ACCAO: 1. Em agent.py, substituir os 3 dicts globais (conversations, conversation_modes,
           file_contexts) por uma class ConversationStore:
           - MAX_CONVERSATIONS = 200, TTL_SECONDS = 4 * 3600 (4h)
           - OrderedDict com timestamps de last_accessed
           - get() actualiza timestamp, set() faz eviction se > MAX, cleanup() remove expirados
           - A interface publica deve manter mesma API para nao quebrar nada
        2. Em app.py, background task cleanup a cada 30min
CUIDADO: API publica do ConversationStore compativel com codigo existente.
VALIDACAO: GET /debug/conversations mostra contagem. Apos cleanup, entries antigas removidas.
DEPLOY: Kudu VFS PUT agent.py + app.py + restart
```

#### TAREFA 1.4 — Cache de Few-Shot por Request [P3]
```
FICHEIROS: agent.py
ACCAO: Cachear resultado de get_few_shot_examples(question) e get_learned_rules()
        na primeira chamada dentro do mesmo request (variavel local, nao global).
VALIDACAO: Performance — deve poupar ~100ms por iteracao extra.
DEPLOY: Kudu VFS PUT agent.py + restart
```

**~~Apos completar TODAS as tarefas 1.0-1.4: Bump APP_VERSION para "7.0.3" em config.py.~~** DONE — v7.0.3 (2026-02-22). PENDENTE: bump APP_VERSION em config.py no proximo deploy.

---

### FASE 1B — QUICK WINS E UX (executar por ordem)

#### TAREFA 1.5 — System Prompt Mais Abrangente
```
FICHEIROS: tools.py
ACCAO: Adicionar regras ao system prompt ANTES das regras de tools:
        "REGRA IMPORTANTE: NEM TODAS as perguntas precisam de ferramentas.
        - Perguntas gerais, conceptuais, opiniao → responde directamente sem tools
        - Redaccao de emails, textos, documentacao → responde directamente
        - Explicacoes de conceitos (WIQL, Agile, etc.) → responde directamente
        - So usa ferramentas para dados ESPECIFICOS do DevOps, pesquisa, ou analise de padroes"
VALIDACAO: "O que e uma user story?" → sem tool badges. "Quantas USs ativas?" → usa query_workitems.
DEPLOY: Kudu VFS PUT tools.py + restart
```

#### TAREFA 1.6 — Largura Variavel no Chat
```
FICHEIROS: static/index.html
ACCAO: maxWidth 800→900 para texto. Tabelas: max-width: calc(100vw - 320px) + overflow-x: auto.
        OPCIONAL: toggle "expandir" por mensagem.
CUIDADO: NAO quebrar React hooks. Testar login → chat → enviar → sem ecra branco.
DEPLOY: Kudu VFS PUT static/index.html (nao precisa restart)
```

#### TAREFA 1.7 — Polish dos Export Buttons
```
FICHEIROS: static/index.html
ACCAO: Quando resposta contem dados tabulares, renderizar barra [CSV] [Excel] [PDF] [HTML].
        Cada botao chama POST /api/export. Backend ja suporta via export_engine.py.
CUIDADO: NAO remover export HTML da conversa completa que ja existe.
DEPLOY: Kudu VFS PUT static/index.html (nao precisa restart)
```

#### TAREFA 1.8 — SVG como Input
```
FICHEIROS: app.py, static/index.html
ACCAO: Adicionar ".svg" e "image/svg+xml" aos tipos aceites. Ler como texto, passar ao LLM.
DEPLOY: Kudu VFS PUT app.py + static/index.html + restart
```

**Apos completar 1.5-1.8:** Bump APP_VERSION para "7.0.4" em config.py.

---

### FASE 2 — DEVOPS WRITE + MEMORIA (executar por ordem)

#### TAREFA 2.1 — Tool create_workitem
```
FICHEIROS: tools.py
ACCAO: Nova tool create_workitem:
        - POST _apis/wit/workitems/$User%20Story?api-version=7.1
        - Body: JSON Patch format [{"op":"add","path":"/fields/System.Title","value":title}, ...]
        - Parametros: work_item_type, title, description, acceptance_criteria, area_path, assigned_to, tags
        - Usa _devops_request_with_retry
        - Retorna: {"id": 12345, "url": "https://dev.azure.com/...", "title": "..."}
        - System prompt: "Quando o user confirmar criacao, usa create_workitem. Pergunta SEMPRE antes de criar."
VALIDACAO: "Gera uma US sobre notificacoes push e cria no board" → WI criado no DevOps.
DEPLOY: Kudu VFS PUT tools.py + restart
RISCO: Medio — escreve no DevOps. Testar com work item type de teste primeiro.
```

#### TAREFA 2.2 — Write-Through para Table Storage
```
FICHEIROS: agent.py
ACCAO: Apos cada turno do agent (par user+assistant), serializar conversations[conv_id]
        para ChatHistory table. Mesmo formato JSON que o frontend usa.
        Chamar await _persist_conversation(conv_id) apos conversations[conv_id].append(assistant msg).
VALIDACAO: Restart app → conversa anterior carrega do storage.
DEPLOY: Kudu VFS PUT agent.py + restart
```

#### TAREFA 2.3 — Lazy-Load de Conversas
```
FICHEIROS: agent.py
ACCAO: Em _ensure_conversation(), antes de criar nova conversa, tentar table_query("ChatHistory", ...)
        com o conv_id. Se encontrar, deserializar e popular conversations[conv_id].
VALIDACAO: Restart → enviar mensagem na mesma conversa → contexto preservado.
DEPLOY: Kudu VFS PUT agent.py + restart
```

**Apos completar 2.1-2.3:** Bump APP_VERSION para "7.0.5" em config.py.

---

### FASE 3 — CHARTS E VISUALIZACAO (executar por ordem)

#### TAREFA 3.1 — Plotly.js no Frontend
```
FICHEIROS: static/index.html
ACCAO: Script tag Plotly.js CDN (defer). Funcao renderPlotlyChart(). Componente React ChartBlock
        com botoes Download SVG/PNG. MessageBubble detecta campo "_chart" no tool_result.
DEPLOY: Kudu VFS PUT static/index.html (nao precisa restart)
```

#### TAREFA 3.2 — Tool generate_chart
```
FICHEIROS: tools.py, static/index.html
ACCAO: Nova tool "generate_chart" — parametros: chart_type, title, data.
        Devolve { "_chart": { type, data, layout } }. Frontend renderiza via Plotly.js.
        System prompt: "Para graficos/charts/visualizacoes de qualquer tipo, usa generate_chart."
DEPLOY: Kudu VFS PUT tools.py + index.html + restart
```

#### TAREFA 3.3 — Charts a Partir de File Upload
```
FICHEIROS: app.py, agent.py
ACCAO: Enriquecer contexto de upload com nomes de colunas e amostra (10 linhas).
        O LLM usa generate_chart com os dados do upload.
DEPLOY: Kudu VFS PUT app.py + agent.py + restart
```

#### TAREFA 3.4 — File Generation via Prompts
```
FICHEIROS: tools.py, app.py, export_engine.py, static/index.html
ACCAO: Nova tool "generate_file" (format, title, data, columns).
        Usa export_engine.py. Frontend detecta "_file_download" e mostra botao.
DEPLOY: Kudu VFS PUT (4 ficheiros) + restart
```

**Apos completar 3.1-3.4:** Bump APP_VERSION para "7.1.0" em config.py.

---

### FASE 4 — US WRITER PRO (executar por ordem)

#### TAREFA 4.1 — System Prompt Rewrite Modo Userstory
```
FICHEIROS: tools.py
ACCAO: Reescrever get_userstory_system_prompt() com ciclo Draft→Review→Final.
        Instrucoes de visual parsing (imagens→CTAs, inputs, labels).
        Refinamento iterativo: se user da feedback, re-chamar generate_user_stories com context.
DEPLOY: Kudu VFS PUT tools.py + restart
```

#### TAREFA 4.2 — WriterProfiles Table
```
FICHEIROS: tools.py, storage.py
ACCAO: Nova tabela WriterProfiles (PartitionKey: username, RowKey: "profile").
        Cache de estilos por autor. Carregamento em generate_user_stories.
        "Gera USs como o Jorge escreve" carrega perfil em vez de refazer analyze.
DEPLOY: Kudu VFS PUT tools.py + storage.py + restart
```

#### TAREFA 4.3 — Upload Pre-Processing Inteligente
```
FICHEIROS: agent.py
ACCAO: Quando modo e userstory, pre-processar: Excel→lista requisitos, PDF→seccoes hierarquicas.
DEPLOY: Kudu VFS PUT agent.py + restart
```

#### TAREFA 4.4 — PPT Support no Upload
```
FICHEIROS: app.py
ACCAO: Adicionar python-pptx. Extrair texto de slides como sequencia de requisitos.
DEPLOY: ZIP deploy (nova dependencia pip)
```

**Apos completar 4.1-4.4:** Bump APP_VERSION para "7.1.1" em config.py.

---

### FASE 5 — INTEGRACOES EXTERNAS + FRONTEND POLISH (executar por ordem)

#### TAREFA 5.1 — Registry de Tools Dinamico
```
FICHEIROS: tools.py (refactor), agent.py
ACCAO: TOOL_REGISTRY dict. register_tool(name, definition, handler).
        get_all_tool_definitions(), get_tool_handler(name). Agent usa registry.
CUIDADO: NAO quebrar tools existentes. Refactor interno.
DEPLOY: Kudu VFS PUT tools.py + agent.py + restart
```

#### TAREFA 5.2 — Integracao Figma (Read-Only)
```
FICHEIROS: tools_figma.py (novo), config.py, tools.py (import)
ACCAO: Tool "search_figma" via Figma REST API v1. Metadados, frames, thumbnails, links.
PRE-REQUISITO: Pedro configura FIGMA_ACCESS_TOKEN nas App Settings.
DEPLOY: ZIP deploy ou Kudu VFS PUT + restart
```

#### TAREFA 5.3 — Integracao Miro (Read-Only)
```
FICHEIROS: tools_miro.py (novo), config.py, tools.py (import)
ACCAO: Tool "search_miro" via Miro REST API v2. Boards, sticky notes, links.
PRE-REQUISITO: Pedro configura MIRO_ACCESS_TOKEN nas App Settings.
DEPLOY: ZIP deploy ou Kudu VFS PUT + restart
```

#### TAREFA 5.4 — Streaming Optimization
```
FICHEIROS: static/index.html
ACCAO: React.memo no MessageBubble. So re-render do ultimo bloco durante streaming.
DEPLOY: Kudu VFS PUT static/index.html (nao precisa restart)
```

**Apos completar 5.1-5.4:** Bump APP_VERSION para "7.2.0" em config.py.

---

### FASE 6 — ANALISE PROFUNDA (bonus, se houver tempo)

#### TAREFA 6.1 — PDF Chunking + Indexacao
```
FICHEIROS: app.py, tools.py
ACCAO: Docs >50K chars divididos em chunks, indexados no AI Search.
DEPLOY: Kudu VFS PUT + restart
```

#### TAREFA 6.2 — Tool search_uploaded_document
```
FICHEIROS: tools.py
ACCAO: Pesquisa semantica no documento carregado.
DEPLOY: Kudu VFS PUT tools.py + restart
```

#### TAREFA 6.3 — Daily Digest Endpoint
```
FICHEIROS: app.py
ACCAO: GET /api/digest — HTML com USs criadas ontem, bugs >7 dias, items sem assignee.
DEPLOY: Kudu VFS PUT app.py + restart
```

**Apos completar 6.1-6.3:** Bump APP_VERSION para "7.2.1" em config.py.

---

### REGRAS PARA O CODEX

1. **UMA tarefa de cada vez.** Nao agrupar. Nao antecipar.
2. **Ler antes de escrever.** Sempre ler o ficheiro completo antes de o alterar.
3. **Minimo de ficheiros.** So alterar o que a tarefa pede. Nada mais.
4. **Testar mentalmente.** Antes de entregar, simular o fluxo completo.
5. **Nao inventar.** Se algo nao esta claro, perguntar. Nao assumir.
6. **React hooks.** No index.html, NUNCA colocar hooks depois de early returns. Ordem identica em todos os renders.
7. **Sem dependencias novas** (pip install) a menos que explicitamente indicado na tarefa.
8. **Output limpo.** Entregar APENAS os ficheiros alterados, completos, prontos para Kudu VFS PUT.
9. **Versioning.** Apos cada fase: 1A→7.0.3, 1B→7.0.4, 2→7.0.5, 3→7.1.0, 4→7.1.1, 5→7.2.0, 6→7.2.1.
10. **Nao tocar em:** `auth.py`, `models.py` a menos que explicitamente pedido. `storage.py` so na tarefa 4.2. `learning.py` so nas tarefas 1.1 e 1.2.
