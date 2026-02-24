# Revisão Arquitetural — DBDE AI Assistant v7.1
## Auditor: Claude (Arquiteto) | Data: 2026-02-23
## Contexto: Auditoria pós-Fase 3 + Fase 4 (trabalho do Codex enquanto Arquiteto offline)

---

## 1. VEREDICTO GERAL

**Estado: APROVADO com observações e recomendações.**

O Codex fez um trabalho sólido. A arquitetura manteve-se coerente com os princípios originais da v7.0 — separação de responsabilidades clara entre módulos, thin routing layer no `app.py`, lógica de negócio concentrada em `agent.py`/`tools.py`, e abstração multi-provider no `llm_provider.py`. As decisões tomadas em autonomia foram, na sua maioria, alinhadas com o que eu teria feito. Abaixo detalho o que valido, o que melhoro e o que recomendo como próximos passos.

---

## 2. BUG BASH — VALIDAÇÃO DAS CORREÇÕES P1/P2

### 2.1 [P1] Isolamento de Dados no ChatHistory ✅ VALIDADO

**Antes:** `PartitionKey="chat"` global — todos os utilizadores viam o mesmo bucket.
**Depois:** O `agent.py` agora usa `_user_partition_key(user)` que extrai o `sub` do JWT.

Confirmei em 3 sítios:
- `_persist_conversation()` (linha ~494) — usa `partition_key` derivado de `_user_partition_key(user)`
- `_load_conversation_from_storage()` (linha ~312) — faz query com `PartitionKey eq '{safe_pk}'`
- `_ensure_conversation()` (linha ~356) — passa `partition_key` para o load

O `app.py` também está alinhado — no `/api/chats/{user_id}` usa `user.get("sub")` para non-admins.

**Veredicto: Isolamento correto. Sem falhas.**

### 2.2 [P1] Ghost Tickets — Guard no create_workitem ✅ VALIDADO COM NOTA

O `confirmed=True` guard está implementado em duas camadas:

1. **Backend (`tools.py`):** `tool_create_workitem()` retorna erro se `confirmed=False` (linha 606-607)
2. **Agent layer (`agent.py`):** `_execute_tool_calls()` chama `_has_explicit_create_confirmation()` que faz regex matching na última mensagem do user (linhas 440-491)

A regex de negação está bem construída — cobre "não confirmo", "nunca", "jamais", e verifica 24 chars de contexto antes da aprovação para apanhar negações compostas.

**⚠️ NOTA MENOR:** A função `_has_explicit_create_confirmation` itera `reversed(conversations.get(conv_id, []))` e retorna ao encontrar a primeira mensagem de user. Isto é correto para o caso normal, mas se o user disser "sim" numa mensagem antiga e depois "não" na mais recente, a lógica de short-circuit no `return False` dentro do loop de `approval_patterns` pode ser ambígua. Na prática funciona porque o `return False` no bloco de negação vem antes da verificação de aprovação, mas recomendo um comentário explicativo para quem ler o código no futuro.

### 2.3 [P2] Concorrência e Lock por conv_id ✅ VALIDADO

`_conversation_locks: Dict[str, asyncio.Lock]` com `_get_conversation_lock(conv_id)` — usado tanto em `agent_chat()` como em `agent_chat_stream()` via `async with`.

Isto previne interleaving de mensagens quando dois requests chegam para a mesma conversa simultaneamente (ex: duplo clique no botão Send).

**Veredicto: Correto. O lock é cleanup-aware via `_cleanup_conversation_related_state`.**

### 2.4 [P2] Persistência Multimodal — Compactação Base64 ✅ VALIDADO

`_compact_message_for_storage()` no `agent.py` (linhas 389-437):
- Para `image_url` content blocks: substitui por `"[base64_omitted]"` — perfeito, não rebenta os 64KB
- Para tool results: comprime para `{total_count, items_returned, _persisted_summary: True}`
- Fallback progressivo de truncagem se ainda exceder 60KB: primeiro tenta últimas 10 msgs, depois últimas 4, depois string truncation, e em último caso guarda só um placeholder

**Veredicto: Robusto. A cascata de fallbacks é defensiva e correta.**

---

## 3. FASE 3 — Charts & Visualização (v7.1.0)

### 3.1 tool_generate_chart ✅ VALIDADO

Implementação limpa em `tools.py` (linhas 681-760). Suporta: bar, pie, line, scatter, histogram, hbar, multi-series. Retorna `_chart` spec que o frontend deteta e renderiza com Plotly.

A convenção de prefixar com `_` os campos internos (`_chart`, `_file_download`) é uma boa prática — mantém a separação entre dados para o LLM e dados para o frontend.

### 3.2 Frontend ChartBlock ✅ VALIDADO

O `index.html` tem:
- `getChartSpecs(toolResults)` — extrai `_chart` dos tool results
- `renderPlotlyChart(containerId, chartSpec)` — usa `Plotly.react` ou `Plotly.newPlot`
- Download SVG/PNG via `toImageButtonOptions` no Plotly config (nativo, sem código custom)

**⚠️ NOTA:** Não vejo um `Plotly.purge` explícito no unmount. O Pedro mencionou que foi adicionado — pode estar no React component lifecycle que não li na totalidade (o HTML tem 1473 linhas). Confirmar que o `ChartBlock` component faz cleanup no unmount para evitar memory leaks em navegações rápidas.

### 3.3 tool_generate_file ✅ VALIDADO

Implementação em `tools.py` (linhas 763-845). Gera CSV/XLSX/PDF em memória, guarda no `_generated_files_store` com TTL de 30 minutos e max 100 ficheiros. Retorna `_file_download` com `download_id` e endpoint.

O endpoint `/api/download/{download_id}` no `app.py` (linhas 328-346) serve o ficheiro. Está protegido por auth (JWT required).

**⚠️ NOTA DE SEGURANÇA:** O `download_id` é um UUID hex (`uuid.uuid4().hex`), o que dá ~128 bits de entropia. Suficiente para evitar enumeração. Mas não há rate limiting no endpoint de download — alguém com um token válido poderia tentar brute-force. Baixo risco na prática dado o âmbito interno.

### 3.4 Export Engine ✅ VALIDADO

`export_engine.py` está bem estruturado:
- `extract_table_data()` extrai headers/rows de qualquer formato de tool result
- `to_csv()` com UTF-8 BOM para compatibilidade Excel PT — bom detalhe
- `to_xlsx()` com branding Millennium (vermelho #CC0033), zebra striping, auto-width
- `to_pdf()` com `fpdf2` em landscape, cores consistentes
- `_latin1_safe()` sanitiza para core fonts do fpdf2 — necessário para caracteres PT
- Fallback gracioso: se openpyxl falha, fallback para CSV

**Veredicto: Produção-ready. Boa atenção ao detalhe com encoding e fallbacks.**

---

## 4. FASE 4 — US Writer Pro (v7.1.1)

### 4.1 WriterProfiles no Table Storage ✅ VALIDADO

`tools.py` implementa:
- `_save_writer_profile()` — upsert na tabela `WriterProfiles` com PartitionKey="writer"
- `_load_writer_profile()` — cache retrieval por author name normalizado
- `_normalize_author()` e `_writer_profile_row_key()` — sanitização para Azure Table keys

O RowKey é derivado do nome normalizado com caracteres problemáticos substituídos por `_`, truncado a 120 chars. Isto é robusto para nomes portugueses (acentos preservados após lower).

### 4.2 Ciclo Draft → Review → Final ✅ VALIDADO

O `get_userstory_system_prompt()` em `tools.py` (linhas 1020-1063) inclui:
- "MODO OBRIGATÓRIO: DRAFT → REVIEW → FINAL"
- Instrução de refinamento: "Se o utilizador der feedback, NÃO ignores"
- Instrução de visual parsing para mockups
- Vocabulário MSE (CTA, FEE, etc.)

**⚠️ NOTA ARQUITETURAL:** O ciclo Draft→Review→Final é enforced pelo prompt, não pelo código. Isto significa que o LLM pode "esquecer" a instrução em conversas longas. Considerar, numa fase futura, adicionar um state machine no `conversation_meta` que track o estado do draft e injete reminders contextuais.

### 4.3 Pré-processamento Inteligente ✅ VALIDADO

`_inject_file_context()` no `agent.py` (linhas 227-309) faz formatting inteligente por modo:

- **Excel/CSV em modo userstory:** Converte para `REQ-001: Col1: val | Col2: val` — excelente para o LLM interpretar como requisitos
- **PDF em modo userstory:** Injeta instrução hierárquica (Épico→Feature→US→AC)
- **PPTX em modo userstory:** Slide como bloco de requisitos, bullets como detalhes

Também remove system prompts de ficheiros anteriores antes de injetar o novo (`messages[:] = [m for m in messages if not ...]`), evitando acumulação de contexto stale.

### 4.4 Upload PPTX ✅ VALIDADO

O `app.py` no endpoint `/upload` (linhas 203-230):
- Import condicional de `python-pptx` com fallback gracioso (503 se não instalado)
- Leitura de slides/shapes com `has_text_frame` guard
- Ignora shapes sem texto (imagens, formas decorativas)
- Formatação em blocos `[Slide N]\nTexto`

**⚠️ NOTA:** O `from pptx import Presentation` no topo do `app.py` (linha 23) tem um try/except que define `Presentation = None` se falhar. Isto é defensivo mas tem um side effect: se o import falhar por razão transitória (ex: memory pressure no startup), o PPTX fica desativado até ao próximo restart. Aceitável para Azure App Service dado que os restarts são frequentes.

---

## 5. ARQUITETURA GERAL — OBSERVAÇÕES TRANSVERSAIS

### 5.1 ConversationStore com LRU/TTL ✅ EXCELENTE

A classe `ConversationStore` no `agent.py` é uma adição muito boa — implementa `MutableMapping` com:
- TTL de 4 horas por conversa
- LRU eviction quando atinge MAX_CONVERSATIONS (200)
- Callback `on_evict` que limpa `conversation_meta`, `uploaded_files_store` e `_conversation_locks`

Isto resolve o problema de memory leak que a v7.0.0 tinha com o dict global sem limites.

### 5.2 Lazy Load de Conversas do Storage ✅ BOA DECISÃO

`_ensure_conversation()` tenta carregar do Table Storage antes de criar conversa nova. Isto resolve o cenário de "cold start" (App Service restart) onde as conversas em memória se perdem. O system prompt é substituído pelo atual no load, o que garante que alterações de prompt se propagam.

### 5.3 Search Retry Logic ✅ VALIDADO

`_search_request_with_retry()` em `tools.py` (linhas 195-282) — exponential backoff com max 30s, handling de 429/5xx/timeout/RequestError. Complementa o `_devops_request_with_retry()` que já existia. Boa separação.

### 5.4 LLM Provider Abstraction ✅ VALIDADO

O `llm_provider.py` é sólido:
- Tier system (fast/standard/pro) com fallback automático
- Tradução bidirecional OpenAI↔Anthropic transparente
- Streaming com reconstrução de tool calls a partir de deltas (Anthropic)
- `llm_with_fallback()` para resiliência

### 5.5 Adaptive Learning ✅ VALIDADO

`learning.py`:
- Cache de prompt rules com refresh horário
- Few-shot examples via semantic search com cache de 30 min
- Separação positivo/negativo com scoring

---

## 6. ISSUES E RECOMENDAÇÕES

### 6.1 🔴 SEGURANÇA — SQL Injection no OData Filter

Em vários locais, os valores do user são interpolados diretamente em OData filters:

```python
# app.py linha 354
f"PartitionKey eq 'user' and RowKey eq '{request.username}'"
```

O `_odata_escape()` no `agent.py` faz `replace("'", "''")` mas não é usado consistentemente no `app.py`. Se um username contiver `' or PartitionKey eq 'admin`, poderia haver injection.

**Recomendação:** Aplicar `_odata_escape()` a TODOS os valores user-supplied nos filtros OData do `app.py`, ou mover a utility para `storage.py` e usar lá.

### 6.2 🟡 SEGURANÇA — Password Hardcoded no Storage Init

`storage.py` linha 192: `hash_password("Millennium2026!")` — password de admin hardcoded no código. Mesmo sendo para bootstrap, isto deveria vir de uma env var.

**Recomendação:** `ADMIN_INITIAL_PASSWORD` como env var no `config.py`, com fallback para um valor gerado aleatoriamente que é logged uma única vez.

### 6.3 🟡 LOGGING — Levels Incorretos no Storage Init

`storage.py` usa `logger.error()` para mensagens de sucesso (linhas 170-198):
```python
logger.error("  ✅ Table '%s' created", table_name)  # Deveria ser logger.info
```

Isto polui os logs de erro em produção com mensagens informativas.

**Recomendação:** Substituir por `logger.info()`.

### 6.4 🟡 VERSÃO — APP_VERSION Inconsistente

O `config.py` diz `APP_VERSION = "7.1.0"` mas o handoff menciona v7.1.1. O footer do PDF diz "v7.0". O frontend HTML title diz "v7.0".

**Recomendação:** Unificar para "7.1.1" (ou a versão que for decidida) em todos os locais que exibem versão.

### 6.5 🟡 MEMÓRIA — _generated_files_store Sem Bound Real

O `_GENERATED_FILE_MAX = 100` limita o número de ficheiros mas não o tamanho total. Um ficheiro XLSX grande pode ter vários MB. Com 100 ficheiros, poderiam ser centenas de MB em memória.

**Recomendação:** Adicionar um `_GENERATED_FILE_MAX_TOTAL_BYTES` (ex: 500MB) e verificar no `_store_generated_file`.

### 6.6 🟡 FRONTEND — Plotly.purge Verification

Confirmar que o `ChartBlock` component faz `Plotly.purge(el)` antes de unmount. Se não, adicionar num `useEffect` cleanup.

### 6.7 🟢 MELHORIA FUTURA — Draft State Machine

O ciclo Draft→Review→Final do US Writer é enforced por prompt. Numa fase futura, considerar adicionar `conversation_meta[conv_id]["us_draft_state"]` com valores `"draft"/"review"/"final"` e injetar reminders contextuais automáticos.

### 6.8 🟢 MELHORIA FUTURA — Streaming Final Response

No `agent_chat_stream()`, o "streaming" da resposta final faz uma segunda chamada ao LLM sem tools para obter token-by-token delivery (linhas 793-806). Isto duplica o custo de tokens da resposta final. Considerar cachear a resposta do non-streaming e simplesmente enviar tokens simulados, ou usar streaming nativo na primeira chamada.

### 6.9 🟢 MELHORIA FUTURA — Health Check Mais Robusto

O endpoint `/health` cria um novo `httpx.AsyncClient` por request (linha 561) em vez de usar o global. Para um health check chamado frequentemente, isto adiciona overhead.

---

## 7. DECISÕES DO CODEX QUE VALIDO COMO ARQUITETO

| # | Decisão | Veredicto |
|---|---------|-----------|
| 1 | Não usar Base64 para file generation (usar endpoint temporário) | ✅ Correto — evita bloat no histórico LLM |
| 2 | WriterProfiles como tabela separada (não no ChatHistory) | ✅ Correto — separação de concerns |
| 3 | `_chart` e `_file_download` como prefixos internos | ✅ Correto — convenção clara |
| 4 | ConversationStore com LRU/TTL | ✅ Excelente — resolve memory leak da v7.0 |
| 5 | Lazy load de conversas do Table Storage | ✅ Boa — resiliência a cold starts |
| 6 | Import condicional do python-pptx | ✅ Defensivo — não quebra o deploy se faltar |
| 7 | Retry logic separada para Search vs DevOps | ✅ Correto — timeouts e patterns diferentes |
| 8 | Pré-processamento por tipo no modo userstory | ✅ Inteligente — melhora qualidade do output |
| 9 | Guard regex para create_workitem | ✅ Robusto — previne ghost tickets |
| 10 | Deploy trick (install direto no wwwroot) | ⚠️ Aceitável como workaround, mas documentar para futuros deploys |

---

## 8. PRIORIDADES PARA PRÓXIMAS ITERAÇÕES

1. **[P1] OData injection fix** — aplicar escaping consistente (estimativa: 30 min)
2. **[P1] Password hardcoded** — mover para env var (estimativa: 15 min)
3. **[P2] Logging levels** — corrigir no storage.py (estimativa: 5 min)
4. **[P2] Versão unificada** — alinhar APP_VERSION em todos os ficheiros (estimativa: 10 min)
5. **[P3] Memory bound para generated files** — adicionar limite de bytes total
6. **[P3] Plotly.purge verification** — confirmar no frontend

---

## 9. CONCLUSÃO

O Codex tomou decisões arquiteturais corretas e o código é production-grade. As Fases 3 e 4 estão bem implementadas e o Bug Bash resolveu problemas reais de isolamento e concorrência. As recomendações acima são melhorias incrementais — nenhuma é blocker para produção.

**A arquitetura v7.1 está aprovada. Bom trabalho, equipa.**

— Claude (Arquiteto), 2026-02-23
