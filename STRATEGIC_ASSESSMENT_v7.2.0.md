# DBDE AI Assistant — Avaliação Estratégica v7.2.0
## Arquiteto: Claude Opus 4.6 | Data: 2026-02-24
## Para: Pedro Mousinho (Product Owner) e Codex (Lead Developer)

---

## 1. ESTADO ACTUAL — DIAGNÓSTICO COMPLETO

### 1.1 O que está sólido

| Componente | Estado | Notas |
|-----------|--------|-------|
| Core (chat, agent, tools) | ✅ Estável | 10 tools builtin funcionais |
| Segurança (11/11 findings) | ✅ Fechado | WIQL sanitization, DOMPurify, httpOnly cookies, CORS whitelist, rate limiting |
| Persistência | ✅ Funcional | Write-through + lazy-load ChatHistory |
| Charts (Plotly.js) | ✅ Deployado | 6 tipos, multi-series, SVG/PNG export |
| US Writer Pro | ✅ Deployado | Draft→Review→Final, WriterProfiles |
| Tool Registry | ✅ Deployado | Dinâmico, auto-registo no import |
| Figma/Miro (código) | ✅ Corrigido | Fixes locais aplicados (VFS PUT temporário) |

### 1.2 Problemas Identificados

| # | Problema | Severidade | Impacto |
|---|---------|-----------|---------|
| P1 | **Fixes Figma/Miro em VFS PUT** — não persistem após restart | 🔴 ALTA | Próximo restart reverte para código antigo |
| P2 | **Tokens Figma/Miro no runtime** — App Settings podem não estar a chegar ao container | 🟠 MÉDIA | Tools retornam "token em falta" |
| P3 | **Git sem commits** — toda a evolução v7.0.5→v7.2.0 está em ficheiros modificados sem commit | 🟠 MÉDIA | Risco de perda de código |
| P4 | **Handoff desactualizado** — o .md no folder é v7.0.5, o Rmd é v7.2.0 mas não em formato útil | 🟡 BAIXA | Confusão em onboarding |
| P5 | **startup.sh** diz "v7.0..." | 🟡 COSMÉTICO | Sem impacto funcional |
| P6 | **antenv/** contém .so de pptx/lxml para plataforma errada | 🟡 BAIXA | Oryx deve sobrescrever; pptx shelved |

### 1.3 Versão em Produção

- **Código deployado:** v7.2.0 (ZIP deploy)
- **Patches temporários:** tools_figma.py e tools_miro.py via VFS PUT (NÃO persistente)
- **App Settings pendentes de validação:** FIGMA_ACCESS_TOKEN, MIRO_ACCESS_TOKEN

---

## 2. DECISÃO ESTRATÉGICA — PLANO DE ACÇÃO IMEDIATO

### Prioridade 1: Consolidar o que temos (ANTES de qualquer feature nova)

**Objectivo:** Tornar os fixes permanentes e garantir que a v7.2.0 está completa e estável.

#### Tarefa C.1 — ZIP Deploy Permanente com Fixes Figma/Miro

**O que:** Criar novo ZIP com os ficheiros corrigidos (tools_figma.py, tools_miro.py) e fazer deploy permanente.

**Porquê:** Os fixes via VFS PUT desaparecem no próximo restart. O ZIP deploy é a source of truth do Oryx.

**Como (Pedro):**
1. No Mac, na pasta `dbde-ai-v7-patched/`, criar ZIP limpo:
```bash
cd /path/to/dbde-ai-v7-patched
zip -r ../dbde-ai-v7.2.0-final.zip \
  app.py agent.py auth.py config.py export_engine.py \
  learning.py llm_provider.py models.py storage.py \
  tools.py tool_registry.py tools_figma.py tools_miro.py \
  requirements.txt startup.sh start_server.py \
  static/ antenv/ \
  -x "*.md" "__pycache__/*" ".git/*" ".DS_Store" "*.zip" "*.pdf" "*.html" "*.log" "*.Rmd" "*.pptx" ".tmp_wheels/*" "codex_upload_test.pptx"
```
2. Deploy via Kudu zipdeploy:
```bash
curl -X POST \
  "https://millennium-ai-assistant-epa7d7b4defabwbn.scm.swedencentral-01.azurewebsites.net/api/zipdeploy" \
  -u "\$millennium-ai-assistant:uq6Gt0NK9L9pBGiJ0r7Wl2FfG0c6vMExcwyPf76HBTiDulJ1qp2rQm7AYLyM" \
  -H "Content-Type: application/zip" \
  --data-binary @../dbde-ai-v7.2.0-final.zip
```
3. Aguardar 2-5 min para Oryx build
4. Validar: `GET /health` → 200, `GET /api/info` → "7.2.0" + `active_tools` com search_figma/search_miro

#### Tarefa C.2 — Validar App Settings no Azure Portal

**O que:** Confirmar que FIGMA_ACCESS_TOKEN e MIRO_ACCESS_TOKEN estão configurados nas App Settings E que o container os recebe.

**Como (Pedro):**
1. Azure Portal → App Service → Configuration → Application settings
2. Verificar que ambos existem e têm valores (não vazios)
3. Após o ZIP deploy (C.1), testar no chat: "Quais os meus ficheiros no Figma?" → deve chamar search_figma
4. Se retornar "token em falta": ir a Advanced Tools → Kudu → Environment e verificar se as vars estão visíveis no runtime. Se não estiverem, fazer restart manual (Overview → Restart)

#### Tarefa C.3 — Git Commit de Consolidação

**O que:** Fazer commit de todo o código actual para preservar o estado v7.2.0.

**Como (Pedro, no Mac):**
```bash
cd /path/to/dbde-ai-v7-patched
git add app.py agent.py auth.py config.py export_engine.py \
  learning.py storage.py tools.py tool_registry.py \
  tools_figma.py tools_miro.py requirements.txt startup.sh \
  static/index.html TEAM_PROTOCOL.md
git commit -m "v7.2.0: Fases 1A-5 completas (security, charts, US Writer, Figma/Miro, registry)"
```

---

### Prioridade 2: Actualizar Documentação

#### Tarefa C.4 — Sincronizar Handoff

**O que:** O DBDE_AI_ASSISTANT_V7_HANDOFF.md no folder precisa de ser substituído pelo conteúdo do Handoffafter5.Rmd (que é o actualizado).

**Decisão tomada:** Vou gerar o ficheiro actualizado directamente.

#### Tarefa C.5 — Actualizar startup.sh

**O que:** Corrigir a mensagem de log "v7.0..." para "v7.2.0".

```bash
echo "Starting DBDE AI Agent v7.2.0..."
```

---

### Prioridade 3: Próxima Fase (após consolidação)

#### Opções avaliadas:

| Opção | Descrição | Esforço | Valor | Risco |
|-------|-----------|---------|-------|-------|
| **Fase 6** | PDF chunking + search_uploaded_document + daily digest | 1 semana | Alto | Baixo |
| **Fase 3.4/3.5** | Validar export charts + file generation | 2-3h | Baixo | Nenhum |
| **Anthropic** | Activar quando quota aprovada | 30min | Alto | Nenhum |
| **Excel Add-in** | Sidebar nativa no Excel | 2-4 semanas | Muito Alto | Alto |

#### Decisão: **Fase 6 (Análise Profunda)** após consolidação

**Justificação:**
- Fase 6 é o melhor rácio valor/esforço
- PDF chunking resolve um problema real (docs >50K chars são truncados)
- Daily digest é uma feature diferenciadora (zero interacção manual)
- Risco baixo (não toca em código core)
- Pode ser feito inteiramente pelo Codex via instruções

---

## 3. MENSAGENS PARA O CODEX

### Quando: APÓS Pedro completar C.1 (ZIP deploy) e C.2 (validar tokens)

As instruções para o Codex seguem abaixo, organizadas por tarefa.

---

> **MENSAGEM PARA O CODEX — Tarefa C.5 (Quick Fix)**
>
> **Ficheiro:** `startup.sh`
> **O que fazer:** Alterar a linha `echo "Starting DBDE AI Agent v7.0..."` para `echo "Starting DBDE AI Agent v7.2.0..."`
> **Regras:** Não alterar mais nada no ficheiro.
> **Validação:** `cat startup.sh` mostra v7.2.0
> **Deploy:** Incluir no próximo ZIP deploy

---

> **MENSAGEM PARA O CODEX — Tarefa 6.1 (PDF Chunking + Indexação)**
>
> **Contexto:** Ler DBDE_AI_ASSISTANT_V7_HANDOFF.md para arquitectura completa. A app é FastAPI + React CDN, deployada como Azure App Service Linux.
>
> **Ficheiros:** `app.py`, `tools.py`
>
> **O que fazer:**
> 1. No endpoint `/upload` em `app.py`, quando o ficheiro é PDF e o texto extraído >50K chars:
>    - Dividir em chunks de ~4000 chars com overlap de 200 chars
>    - Para cada chunk, calcular embedding via `get_embedding()` de `tools.py`
>    - Guardar chunks + embeddings em `uploaded_files_store[conv_id]["chunks"]`
>    - Manter o texto completo truncado a 50K para o contexto do LLM
>
> 2. Em `tools.py`, nova tool `search_uploaded_document`:
>    - Registar via `register_tool()` de `tool_registry.py`
>    - Parâmetros: `query` (string), `conv_id` (string, opcional — inferido pelo agent)
>    - Execução: calcular embedding da query, comparar com chunks guardados (cosine similarity), retornar top 5 chunks mais relevantes
>    - Se não houver documento carregado, retornar erro controlado
>    - Tool definition com description: "Pesquisa semântica no documento carregado pelo utilizador. Usar quando o utilizador perguntar sobre conteúdos específicos de um documento que fez upload."
>
> 3. System prompt (em `get_agent_system_prompt()` de `tools.py`):
>    - Adicionar regra: "Quando o utilizador faz upload de um documento e pergunta sobre secções específicas, usa search_uploaded_document para encontrar o conteúdo relevante"
>    - A regra só aparece se `has_tool("search_uploaded_document")` for true
>
> **Regras:**
> - NÃO alterar `agent.py`, `learning.py`, `auth.py`, `models.py`, `storage.py`
> - NÃO instalar dependências novas — `get_embedding()` já existe em `tools.py`
> - A cosine similarity pode ser calculada inline (numpy não está disponível — usar math.sqrt e sum)
> - Chunks são IN-MEMORY por conversa (não no AI Search) — desaparecem quando a conversa expira do ConversationStore
>
> **Validação:**
> 1. Upload de PDF longo (>50K chars) → sem erro
> 2. "O que diz o capítulo 3?" → usa search_uploaded_document → retorna chunks relevantes
> 3. Upload de PDF curto (<50K chars) → comportamento normal (sem chunking)
> 4. Sem upload → search_uploaded_document retorna erro controlado
> 5. Chat normal sem upload → todas as tools existentes funcionam (zero regressão)
>
> **Deploy:** Incluir no ZIP deploy. Não há dependências novas.

---

> **MENSAGEM PARA O CODEX — Tarefa 6.3 (Daily Digest Endpoint)**
>
> **Contexto:** Ler DBDE_AI_ASSISTANT_V7_HANDOFF.md para arquitectura.
>
> **Ficheiro:** `app.py`
>
> **O que fazer:**
> 1. Novo endpoint `GET /api/digest`:
>    - Requer autenticação (admin only ou qualquer user autenticado — decisão: qualquer user autenticado)
>    - Faz 4 queries WIQL ao Azure DevOps (reutilizar `_devops_request_with_retry` de `tools.py` — importar se necessário):
>      a. USs criadas ontem: `[System.CreatedDate] >= @Today-1 AND [System.CreatedDate] < @Today`
>      b. Bugs abertos >7 dias: `[System.WorkItemType] = 'Bug' AND [System.State] = 'Active' AND [System.CreatedDate] < @Today-7`
>      c. Items sem assignee: `[System.AssignedTo] = '' AND [System.State] <> 'Closed'`
>      d. Items fechados esta semana: `[System.State] = 'Closed' AND [Microsoft.VSTS.Common.ClosedDate] >= @StartOfWeek`
>    - Todas as queries filtram por `[System.TeamProject] = 'IT.DIT'`
>    - Retorna JSON estruturado (não HTML):
>      ```json
>      {
>        "date": "2026-02-24",
>        "created_yesterday": { "count": N, "items": [...] },
>        "old_bugs": { "count": N, "items": [...] },
>        "unassigned": { "count": N, "items": [...] },
>        "closed_this_week": { "count": N, "items": [...] }
>      }
>      ```
>    - Cada item: `{ "id", "title", "state", "type", "assigned_to", "created_date", "url" }`
>    - Rate limit: partilhado com os endpoints normais (chat_budget)
>
> **Regras:**
> - NÃO alterar `tools.py`, `agent.py`, `auth.py`, `models.py`
> - Usar `_devops_request_with_retry` ou criar helper local em `app.py` para queries WIQL com retry
> - WIQL deve ser sanitizada (usar constantes, não input do user)
> - Se DevOps falhar, retornar JSON com `"error"` em vez de 500
>
> **Validação:**
> 1. `GET /api/digest` (com auth) → 200 + JSON com 4 secções
> 2. `GET /api/digest` (sem auth) → 401/403
> 3. Se DevOps indisponível → JSON com erro gracioso
> 4. Todos os outros endpoints continuam a funcionar
>
> **Deploy:** Incluir no ZIP deploy.

---

## 4. TIMELINE ACTUALIZADA

```
AGORA (24 Fev)         Semana 1 (24-28 Fev)    Semana 2 (3-7 Mar)
─────────────────      ────────────────────      ──────────────────
C.1 ZIP Deploy         6.1 PDF Chunking         6.3 Daily Digest
C.2 Validar Tokens     6.2 search_uploaded_doc   Bump → v7.2.1
C.3 Git Commit         C.5 startup.sh fix        Validação final
C.4 Handoff sync
```

---

## 5. CONSTRAINTS OPERACIONAIS

| Constraint | Impacto | Mitigação |
|-----------|---------|-----------|
| Mac sem Python/exe local | Não pode testar backend localmente | Tudo testado em produção após deploy |
| Lenovo sem permissões | Não pode correr scripts | Usa Azure Portal + Kudu + browser |
| ZIP deploy obrigatório | VFS PUT não persiste | Sempre ZIP para produção |
| Anthropic sem quota | Standard/Pro correm em GPT-4.1-mini | Fallback automático funciona |
| pptx/lxml incompatível | Upload .pptx indisponível | Import guard retorna 503 |

---

— Claude (Arquiteto), 2026-02-24
