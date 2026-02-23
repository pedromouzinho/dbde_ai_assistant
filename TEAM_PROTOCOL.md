# DBDE AI Assistant — Protocolo da Equipa e Regras de Operação

> **Ficheiro de referência rápida para recuperação de contexto.**
> Usar quando: chat perdido, context window cheia, novo agente precisa de onboarding.
> Última atualização: 2026-02-23 (Fase 3 em progresso — 3.1/3.2/3.3 em produção + hotfixes pós-auditoria; faltam 3.4/3.5)
> Versão atual em produção: **v7.0.5** (com hotfixes de segurança/robustez deployados em 2026-02-23)

---

## 1. A EQUIPA

| Papel | Quem | Foco | Onde opera |
|-------|------|------|------------|
| **Product Owner / Orquestrador** | Pedro Mousinho | Decisões de produto, prioridades, deploys via Kudu VFS, ponte entre Claude e Codex | Chat com Claude + Chat com Codex |
| **Arquiteto de Sistemas (Pro Tier)** | Claude Opus 4.6 | Raciocínio profundo, escalabilidade, segurança, lógica de sistemas, review de código, planeamento estratégico | Chat com Pedro (este chat) |
| **Lead Developer** | Codex | Escrita de código tático, implementação, sintaxe, APIs, rate limits | Chat com Pedro (outra janela) |

**Fluxo de trabalho:**
```
Pedro define tarefa → Claude analisa e gera "MENSAGEM PARA O CODEX" → Pedro copia/cola para o Codex → Codex implementa → Pedro reporta resultado ao Claude via "MENSAGEM PARA O CLAUDE" → Claude re-lê código e faz review → Ciclo repete
```

---

## 2. REGRAS DO CLAUDE (Arquiteto)

### 2.1 Identidade e Foco
- Foco em raciocínio profundo, escalabilidade, segurança e lógica de sistemas a longo prazo
- O papel é dizer ao Codex O QUE fazer e COMO estruturar — não cuspir blocos gigantes de código
- Parceiro do Codex (Lead Dev) — Claude planeia, Codex executa

### 2.2 Re-leitura Constante
- **OBRIGATÓRIO:** Sempre que Pedro pedir análise, re-ler os ficheiros relevantes ANTES de responder
- Assume que o código pode ter sido alterado pelo Codex desde a última vez
- Nunca aprovar uma tarefa sem verificar o código diretamente nos ficheiros

### 2.3 Handoff Técnico Obrigatório
- No final de cada análise estratégica ou decisão de design, gerar OBRIGATORIAMENTE um bloco:
  ```
  > **MENSAGEM PARA O CODEX**
  > [instruções técnicas precisas para Pedro copiar/colar]
  ```
- As instruções devem ser auto-contidas: o Codex não vê o histórico do chat do Claude

### 2.4 Guardião da Versão
- Proteger correções já feitas — nunca sugerir mudanças que:
  - Quebrem os React Hooks no frontend (hooks ANTES de early returns)
  - Reintroduzam bloating no histórico (injeção efémera é sagrada)
  - Alterem ficheiros fora do escopo da tarefa
- Validar que o Codex não tocou em ficheiros que não devia

### 2.5 Protocolo de Review
- Re-ler TODOS os ficheiros alterados antes de marcar tarefa como FECHADA
- Confirmar que a interface pública das funções não mudou (salvo quando pedido)
- Verificar zero regressões nos ficheiros protegidos

### 2.6 Última Tarefa de Cada Fase
- O handoff da última tarefa de cada fase DEVE incluir:
  1. Indicação clara de que é a última tarefa da fase
  2. Instrução para bump de `APP_VERSION` em `config.py`
  3. Lista de validações em produção para o Pedro executar após deploy
- Formato: "Esta é a ÚLTIMA TAREFA da Fase X. Após implementar, faz deploy e testa: [lista]"

### 2.7 Atualização de Documentos
- Atualizar `DBDE_AI_ASSISTANT_V7_HANDOFF.md` no final de cada fase completa
- Atualizar este ficheiro (`TEAM_PROTOCOL.md`) quando há mudanças de protocolo ou fim de fase

---

## 3. REGRAS DO CODEX (Lead Developer)

1. **UMA tarefa de cada vez.** Não agrupar. Não antecipar.
2. **Ler antes de escrever.** Sempre ler o ficheiro completo antes de o alterar.
3. **Mínimo de ficheiros.** Só alterar o que a tarefa pede. Nada mais.
4. **Testar mentalmente.** Antes de entregar, simular o fluxo completo.
5. **Não inventar.** Se algo não está claro, perguntar. Não assumir.
6. **React hooks.** No index.html, NUNCA colocar hooks depois de early returns. Ordem idêntica em todos os renders.
7. **Sem dependências novas** (pip install) a menos que explicitamente indicado na tarefa.
8. **Output limpo.** Entregar APENAS os ficheiros alterados, completos, prontos para Kudu VFS PUT.
9. **Versioning.** Após cada fase: 1A→7.0.3, 1B→7.0.4, 2→7.0.5, 3→7.1.0, 4→7.1.1, 5→7.2.0, 6→7.2.1.
10. **Não tocar em:** `auth.py`, `models.py` a menos que explicitamente pedido. `storage.py` só quando explicitamente pedido. `learning.py` só quando explicitamente pedido.

---

## 4. REGRAS OPERACIONAIS DE DEPLOY

### 4.1 Deploy via Kudu VFS (ficheiros individuais)
```bash
BASE="https://millennium-ai-assistant-epa7d7b4defabwbn.scm.swedencentral-01.azurewebsites.net/api/vfs/site/wwwroot"
AUTH="$millennium-ai-assistant:<deploy-password>"

curl -X PUT -u "$AUTH" --data-binary @./ficheiro.py "$BASE/ficheiro.py"
```

### 4.2 Restart
```bash
# Path CORRETO no KuduLite Linux (NÃO /site/config/)
# Confirmado em produção 2026-02-22
curl -X PUT -u "$AUTH" \
  --data "$(date)" \
  "https://millennium-ai-assistant-epa7d7b4defabwbn.scm.swedencentral-01.azurewebsites.net/api/vfs/site/wwwroot/restartTrigger.txt"
```

### 4.3 Validação pós-deploy
- `GET /health` → 200 + status "healthy"
- `GET /api/info` → 200 + version correta

### 4.4 Regras
- Frontend (index.html) pode ser atualizado sem restart (Kudu PUT + Ctrl+Shift+R no browser)
- Backend (.py) requer restart após PUT
- ZIP deploy só quando há dependências pip novas (inclui `antenv/`)

---

## 5. ARQUITECTURA PROTEGIDA (Não Quebrar)

### 5.1 Injeção Efémera (SAGRADA)
- `_build_llm_messages()` em `agent.py` cria cópia efémera do histórico
- Regras e few-shot são inseridos na posição 1 (após system prompt)
- NUNCA persistem em `conversations[]`
- Recalculados a cada chamada ao LLM

### 5.2 React Hooks (CRÍTICO)
- Hooks declarados ANTES de qualquer early return
- Ordem idêntica em todos os renders
- Violação = ecrã branco após login

### 5.3 ConversationStore (v7.0.3) + Persistent Memory (v7.0.5)
- MAX_CONVERSATIONS = 200, TTL = 4h, LRU eviction
- `on_evict` limpa `conversation_meta` e `uploaded_files_store`
- Interface MutableMapping — código existente trata como dict
- **v7.0.5+hotfix:** Write-through para ChatHistory com isolamento por utilizador (PK=user_id, RK=conv_id) via `_persist_conversation()` fire-and-forget
- **v7.0.5:** Lazy-load via `_load_conversation_from_storage()` — system prompt sempre substituído pelo actual
- **v7.0.5:** `_ensure_conversation()` agora é `async` — todos os call sites usam `await`
- **ATENÇÃO:** `table_merge` do `storage.py` NÃO faz upsert de criação — `_persist_conversation` usa query→insert/merge

### 5.4 Cache de Few-Shot (v7.0.3)
- MD5 hash normalizado, TTL 30min, cap 50 entradas
- `invalidate_few_shot_cache()` para limpeza explícita
- Cache hit evita 3 chamadas HTTP (1 embedding + 2 search)

---

## 6. ESTADO DO ROADMAP

### Fase 1A — Bug Fixes da Auditoria → v7.0.3 ✅ COMPLETA (2026-02-22)
| Tarefa | Descrição | Status |
|--------|-----------|--------|
| 1.0 | Tier selector fix (`tier` → `model_tier`) | DONE |
| 1.1 | AI Search retry (`_search_request_with_retry`) | DONE |
| 1.2 | Silent failure logging (15 blocos, 5 ficheiros) | DONE |
| 1.3 | Memory eviction (ConversationStore TTL+LRU) | DONE |
| 1.4 | Few-shot cache (MD5 hash, TTL 30min, cap 50) | DONE |

**Deploy:** v7.0.3 em produção, confirmado via `/api/info` e `/health`

### Fase 1B — Quick Wins e UX → v7.0.4 ✅ COMPLETA (2026-02-23)
| Tarefa | Descrição | Status |
|--------|-----------|--------|
| 1.5 | System prompt mais abrangente (6 categorias resposta directa) | DONE |
| 1.6 | Largura variável (tabelas/code calc(100vw-340px)) | DONE |
| 1.7 | Export buttons por mensagem + hotfix SSE tool_details | DONE |
| 1.8 | SVG como input (.svg no accept + handler explícito) | DONE |
| 1.9 | Ativar Anthropic | CONGELADA (quota não aprovada) |

**Deploy:** v7.0.4 em produção, confirmado via `/api/info` e `/health`
**Nota:** Hotfix SSE aplicado pelo Pedro — streaming path não persistia tool_details na mensagem final

### Fase 2 — DevOps Write + Memória Persistente → v7.0.5 ✅ COMPLETA (2026-02-23)
| Tarefa | Descrição | Status |
|--------|-----------|--------|
| 2.1 | Tool create_workitem (JSON Patch, retry, confirmação, 8 tools no TOOLS) | DONE |
| 2.2 | Write-through Table Storage (_persist_conversation, fire-and-forget, compressão) | DONE |
| 2.3 | Lazy-load conversas (_load_conversation_from_storage, async _ensure_conversation) | DONE |
| — | Hotfix PDF 500 (_latin1_safe, try/except fallback) | DONE |
| — | Hotfix export inteligente (messageHasExportableData, remoção header export) | DONE |
| — | Hotfix XLSX sheet title (_safe_sheet_title) | DONE |

**Deploy:** v7.0.5 em produção, 7 testes PASS
**Nota:** table_merge não é upsert — _persist_conversation usa query→insert/merge. _ensure_conversation agora async.

### Fase 3 — Charts e Visualização → v7.1.0 ⏳ EM PROGRESSO
| Tarefa | Descrição | Estado |
|--------|-----------|--------|
| 3.1 | Plotly.js CDN (2.35.2 defer) + renderPlotlyChart + ChartBlock + getChartSpecs | ✅ FECHADA |
| 3.2 | Tool generate_chart (6 tipos + multi-series, _chart Plotly spec, regra 9 system prompt, TOOLS 9 entries) | ✅ FECHADA |
| 3.3 | Upload enrichment: col_analysis (numeric/text) + _inject_file_context com instrução generate_chart | ✅ FECHADA |
| 3.4 | Export de charts (SVG/PNG) — parcialmente feito no ChartBlock da 3.1, validar | PENDENTE |
| 3.5 | File generation via prompts — tool generate_file para CSV/XLSX/PDF | PENDENTE |

**Nota:** 3.1-3.3 implementadas, auditadas e **deployadas em produção** (Kudu VFS PUT + restart em 2026-02-23). Hotfixes pós-auditoria também deployados: confirmação robusta no `create_workitem`, parsing de delimitador dinâmico no `col_analysis`, locks por conversa e hardening de persistência multimodal.
**Detalhe técnico 3.2:** `tool_generate_chart()` aceita chart_type, title, x_values, y_values, labels, values, series, x_label, y_label. Multi-series via `series` param (array de objetos). Retorna `{ _chart: { data, layout, config } }`.
**Detalhe técnico 3.3:** `col_analysis` inicializado como `[]` antes do bloco if (defensivo). Heurística >60% numérico para tipo. Guardado em `uploaded_files_store[conv_id]["col_analysis"]`. `_inject_file_context` enriquecida com bloco "ANÁLISE DE COLUNAS".

### Fase 4 — US Writer Pro → v7.1.1
### Fase 5 — Integrações + Polish → v7.2.0
### Fase 6 — Análise Profunda → v7.2.1

---

## 7. COMUNICAÇÃO: FORMATO DAS MENSAGENS

### Pedro → Claude (reportar resultado do Codex)
```
MENSAGEM PARA O CLAUDE
* [bullet points do que foi feito]
* [ficheiros alterados]
* [ficheiros NÃO alterados]
```

### Claude → Codex (via Pedro)
```
> **MENSAGEM PARA O CODEX**
> **Tarefa X.Y — Nome da Tarefa**
> **Ficheiro(s):** [lista]
> **O que fazer:** [instruções precisas]
> **Regras:** [o que NÃO tocar]
> **Validação:** [como confirmar que funciona]
```

---

## 8. NOTAS OPERACIONAIS APRENDIDAS

| Data | Nota |
|------|------|
| 2026-02-22 | `restartTrigger.txt` funciona em `/site/wwwroot/`, NÃO em `/site/config/` no KuduLite Linux |
| 2026-02-22 | Codex não consegue testar `/upload` localmente por falta de `python-multipart` — validar em produção |
| 2026-02-22 | Claude deve SEMPRE re-ler ficheiros antes de aprovar — não confiar apenas nos relatórios do Codex |
| 2026-02-23 | SSE streaming path e sync path têm fluxos de dados diferentes — validar AMBOS em cada tarefa que toque em tool_details/tool_results |
| 2026-02-23 | "Quantas USs ativas?" devolve contagem textual (sem tabela) — comportamento correto do system prompt. Para tabela, usar "Lista as USs ativas" |
| 2026-02-23 | Na última tarefa de cada fase: incluir bump APP_VERSION + lista de validações em produção no handoff do Codex |
| 2026-02-23 | `table_merge` em `storage.py` NÃO é upsert — só funciona para entidades existentes. Para upsert: query existência → insert se novo, merge se existe |
| 2026-02-23 | `_ensure_conversation()` agora é `async` — qualquer novo call site DEVE usar `await` |
| 2026-02-23 | fpdf2 com fontes core (Helvetica) só suporta Latin-1 — usar `_latin1_safe()` para qualquer texto passado a `pdf.cell()` |
| 2026-02-23 | Content-Disposition headers precisam de filename sanitizado — `safe_filename` em `app.py` export endpoint |
| 2026-02-23 | `generate_chart` é LOCAL (sem API calls) — o LLM fornece dados e a tool constrói o Plotly spec. Sequência correcta: compute_kpi → generate_chart (não paralelo) |
| 2026-02-23 | `_chart` key no tool result é o contrato frontend↔backend para charts. Frontend `getChartSpecs()` procura `parsed._chart` em cada tool_result |
| 2026-02-23 | `col_analysis` no upload: tipo detectado por heurística (>60% valores parseable como número = numeric). Não é perfeito mas suficiente para guiar o LLM |
| 2026-02-23 | Pedro pediu "duas tarefas em simultâneo" pela primeira vez na Fase 3 — workflow pode ser paralelo quando as tarefas são independentes |
| 2026-02-23 | ChartBlock da 3.1 já inclui botões SVG/PNG — 3.4 pode ser apenas validação em produção em vez de implementação |
| 2026-02-23 | Hotfix de segurança: `create_workitem` só avança com confirmação explícita robusta (negações bloqueiam; aprovações comuns aceites) |
| 2026-02-23 | Hotfix de dados: `col_analysis` no upload passou a usar delimitador dinâmico (`sep`) em vez de split hardcoded por vírgula |
| 2026-02-23 | Deploy técnico confirmado por Codex: PUT de `agent.py`, `app.py`, `tools.py`, `learning.py`, `static/index.html` + `restartTrigger.txt` + validação `/api/info` e `/health` |
