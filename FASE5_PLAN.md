# FASE 5 — Modularização + Integrações + Polish (v7.2.0)
## Arquitecto: Claude | Data: 2026-02-23
## Estado de partida: v7.1.1, Fase 4.7 (Security Sprint) concluída

---

## VISÃO ESTRATÉGICA

A Fase 4 consolidou o core funcional e a segurança. A Fase 5 tem três objectivos distintos:

1. **Modularização** — O `tools.py` tem ~1100 linhas e um dispatch monolítico. Antes de adicionar integrações externas, precisa de ser refactored para um registry dinâmico. Isto é pré-requisito arquitectural para escalar.

2. **Integrações Externas** — Figma e Miro read-only. O assistente ganha contexto de design e workshops sem o utilizador ter de copiar/colar. Diferenciador competitivo real dentro do Millennium.

3. **Polish & Dívida Técnica** — Streaming performance, memory cap, e migração JWT para httpOnly cookies (último finding de segurança aberto).

---

## PRÉ-REQUISITOS DO PEDRO

Antes de o Codex começar as tarefas 5.2 e 5.3:

- [ ] **FIGMA_ACCESS_TOKEN** — Personal Access Token do Figma com scope `file:read`. Configurar nas App Settings do Azure.
- [ ] **MIRO_ACCESS_TOKEN** — Access Token do Miro com scope `boards:read`. Configurar nas App Settings do Azure.
- [ ] Confirmar quais os ficheiros/boards Figma e Miro que devem ser acessíveis para teste.

As tarefas 5.1, 5.5, 5.6 e 5.7 podem avançar sem estes tokens.

---

## TAREFAS — ORDEM DE EXECUÇÃO

### Bloco A — Fundações (sem dependências externas)

#### Tarefa 5.1 — Tool Registry Dinâmico

**Objectivo:** Eliminar o dispatch monolítico em `tools.py` e permitir que novas integrações sejam adicionadas como módulos independentes sem alterar código existente.

**Porquê:** O `execute_tool()` actual (linha ~972 de tools.py) é um dict com ~15 lambdas. Cada nova tool obriga a alterar `execute_tool()`, a lista `TOOLS` de definições, e potencialmente o `agent.py`. Isto não escala e viola o Open/Closed Principle.

**O que deve existir no final:**
- Um `TOOL_REGISTRY` (dict ou classe) onde cada tool se auto-regista com: nome, definição OpenAI-format, e handler async
- `execute_tool(name, args)` faz lookup no registry em vez de dispatch hardcoded
- `get_all_tool_definitions()` retorna a lista de tools activas (substituindo a constante `TOOLS` actual)
- Cada grupo funcional pode viver no seu próprio ficheiro (ex: `tools_devops.py`, `tools_charts.py`, `tools_figma.py`) e registar-se no import
- As tools existentes do DevOps, charts e file generation permanecem funcionalmente idênticas — zero regressão

**Critério de sucesso:** A app arranca, todas as tools existentes funcionam, e adicionar uma nova tool requer apenas criar um ficheiro com decorador/registo, sem alterar `tools.py` nem `agent.py`.

**Ficheiros:** `tools.py` (refactor), potencialmente `tool_registry.py` (novo), `agent.py` (adaptar imports)

**Risco:** Médio — refactor de ficheiro core com 1100 linhas. Testar todos os paths.

---

#### Tarefa 5.5 — Streaming Performance (Frontend)

**Objectivo:** Eliminar o lag visível quando o streaming de respostas longas (>2000 chars) causa re-render completo do markdown a cada token.

**Porquê:** O `renderMarkdown()` é chamado no `dangerouslySetInnerHTML` a cada update do streaming text. Com respostas longas, isto faz parse + sanitização DOMPurify da string inteira a cada token recebido. O utilizador vê stuttering.

**O que deve existir no final:**
- Durante o streaming activo, apenas o último bloco de texto (após o último `\n\n`) é re-renderizado. Os blocos anteriores são rendered uma vez e cacheados no DOM
- Quando o streaming termina, faz um render final completo para garantir consistência
- Sem impacto visual — o resultado final é idêntico

**Critério de sucesso:** Streaming de resposta com 5000+ chars sem stuttering visível. O DOMPurify sanitize continua a ser aplicado.

**Ficheiros:** `static/index.html`

---

#### Tarefa 5.6 — Feedback Memory Cap

**Objectivo:** Limitar o `feedback_memory` in-memory para evitar crescimento ilimitado.

**Porquê:** O `feedback_memory` em `app.py` (linha 184) é um `list` sem cap. Em produção com uso continuado, pode crescer indefinidamente. É uma lista de fallback para quando o Table Storage write falha, portanto tipicamente pequena, mas não há garantia.

**O que deve existir no final:**
- Cap de 100 entries (FIFO — quando chega à 101ª, remove a mais antiga)
- Se possível, usar `collections.deque(maxlen=100)` que é O(1) para append e pop automático

**Critério de sucesso:** `len(feedback_memory)` nunca excede 100.

**Ficheiros:** `app.py`

---

#### Tarefa 5.7 — Migração JWT para httpOnly Cookies

**Objectivo:** Eliminar o último finding de segurança aberto (IMPORTANT-2 do Security Audit). O token JWT deixa de estar acessível a JavaScript.

**Porquê:** Mesmo com DOMPurify, defense-in-depth exige que o token não esteja em `localStorage`. Um bypass futuro de XSS (zero-day no DOMPurify, extensão maliciosa no browser) poderia exfiltrar o token. Com httpOnly cookies, o token é invisível a JS.

**O que deve existir no final:**
- O endpoint `/api/login` retorna o token JWT num cookie `Set-Cookie: dbde_token=<jwt>; HttpOnly; Secure; SameSite=Strict; Path=/api`
- O frontend deixa de guardar o token em `localStorage` e deixa de enviar `Authorization: Bearer`
- Todos os endpoints que actualmente lêem `Authorization` passam a ler o cookie (com fallback para header durante período de transição)
- O logout faz `Set-Cookie: dbde_token=; Max-Age=0; ...` para invalidar
- CORS `allow_credentials=True` já está configurado (necessário para cookies cross-origin)

**Atenção:** O `SameSite=Strict` pode interferir se o frontend for servido de domínio diferente do API. Verificar se ambos estão no mesmo domínio (devem estar — o `index.html` é servido pelo FastAPI). Se sim, `SameSite=Strict` funciona. Se não, usar `SameSite=Lax`.

**Critério de sucesso:** Após login, `localStorage` não contém token. DevTools → Application → Cookies mostra `dbde_token` com flags HttpOnly e Secure. Toda a API continua a funcionar.

**Ficheiros:** `auth.py`, `app.py`, `static/index.html`

**Risco:** Médio-alto — afecta toda a cadeia de autenticação. Testar exaustivamente login, chat, upload, export, logout, refresh de página.

---

### Bloco B — Integrações Externas (dependem de 5.1 + tokens)

#### Tarefa 5.2 — Integração Figma (Read-Only)

**Objectivo:** O assistente consegue responder a perguntas sobre designs no Figma com contexto real.

**Porquê:** Quando um utilizador pede "gera USs para o ecrã de login", o assistente actualmente não tem contexto visual. Com Figma, pode buscar os frames do ficheiro, saber que componentes existem, e gerar USs mais precisas.

**O que deve existir no final:**
- Tool `search_figma` registada no TOOL_REGISTRY
- Aceita: query de pesquisa (texto livre), file key (opcional), node_id (opcional)
- Retorna: lista de ficheiros/frames com nome, thumbnail URL, link directo, data de última modificação
- Usa Figma REST API v1 (`GET /v1/files`, `GET /v1/files/:key/nodes`)
- Credencial via `FIGMA_ACCESS_TOKEN` em config.py (env var)
- Se token não configurado, a tool não aparece no TOOL_REGISTRY (graceful disable)

**Critério de sucesso:** "Mostra-me os ecrãs do ficheiro RevampFEE no Figma" → lista de frames com links.

**Ficheiros:** `tools_figma.py` (novo), `config.py`

---

#### Tarefa 5.3 — Integração Miro (Read-Only)

**Objectivo:** O assistente consegue ler conteúdo de boards Miro para contexto de workshops e planning.

**Porquê:** Os POs fazem brainstorms no Miro. O assistente actualmente ignora este contexto. Com Miro, pode ler sticky notes, shapes e texto, e usar isso para gerar USs, KPIs ou resumos.

**O que deve existir no final:**
- Tool `search_miro` registada no TOOL_REGISTRY
- Aceita: query de pesquisa (texto livre), board_id (opcional)
- Retorna: lista de boards ou conteúdo de um board (items: texto, tipo, posição, cor, autor)
- Usa Miro REST API v2 (`GET /v2/boards`, `GET /v2/boards/:id/items`)
- Credencial via `MIRO_ACCESS_TOKEN` em config.py (env var)
- Se token não configurado, a tool não aparece no TOOL_REGISTRY (graceful disable)

**Critério de sucesso:** "O que foi discutido no board de Planning Q2?" → lista de sticky notes com conteúdo.

**Ficheiros:** `tools_miro.py` (novo), `config.py`

---

#### Tarefa 5.4 — System Prompt Awareness

**Objectivo:** O LLM sabe quando usar Figma vs Miro vs DevOps.

**O que deve existir no final:**
- O system prompt (em tools.py ou no ficheiro de prompts) inclui regras de routing: "quando mencionar designs, mockups, ecrãs, UI → usa search_figma", "quando mencionar workshops, brainstorms, boards, sticky notes → usa search_miro"
- As tool definitions de Figma e Miro têm descriptions claras para o LLM saber quando as chamar
- O system prompt só inclui estas regras se os tokens respectivos estiverem configurados

**Critério de sucesso:** Perguntas sobre design trigam Figma. Perguntas sobre brainstorms trigam Miro. Perguntas sobre work items continuam a trigar DevOps.

**Ficheiros:** `tools.py` (ou ficheiro de prompts), `tools_figma.py`, `tools_miro.py`

---

## ORDEM DE EXECUÇÃO E DEPENDÊNCIAS

```
Bloco A (paralelo entre si, sem deps externas):
  5.1 Tool Registry ──────┐
  5.5 Streaming Perf       │  (independentes)
  5.6 Feedback Cap         │
  5.7 httpOnly Cookies     │
                           │
Bloco B (sequencial, depende de 5.1 + tokens):
                           ├── 5.2 Figma ──┐
                           ├── 5.3 Miro  ──┼── 5.4 System Prompt
                           │               │
                           └───────────────┘
```

**Recomendação de ordem para o Codex:**
1. **5.6** primeiro (30 min, quick win, zero risco)
2. **5.5** segundo (2h, impacto UX imediato, independente)
3. **5.1** terceiro (4-6h, fundação para tudo o resto)
4. **5.7** quarto (4h, fecha a dívida de segurança, pode ser feito em paralelo com 5.1)
5. **5.2 + 5.3** quando tokens estiverem configurados (8-12h cada)
6. **5.4** por último (1-2h, depende de 5.2 e 5.3 estarem funcionais)

---

## DEPLOY

**Estratégia:** ZIP deploy (novos ficheiros Python: `tool_registry.py`, `tools_figma.py`, `tools_miro.py`). Sem dependências pip novas — httpx já existe para as chamadas HTTP.

**Excepção:** Se `slowapi` da 4.7.4 ainda não está em produção, incluir no mesmo ZIP deploy.

**Versão final:** Bump `APP_VERSION` para `"7.2.0"` em `config.py` após conclusão de todas as tarefas.

---

## VALIDAÇÃO FINAL (v7.2.0)

1. ✅ Login funciona (cookies httpOnly, sem localStorage)
2. ✅ Chat funciona (sync + stream, sem regressão)
3. ✅ Tools DevOps existentes funcionam (query, create, KPI, charts, export)
4. ✅ Streaming de respostas longas é fluido (sem stuttering)
5. ✅ `len(feedback_memory)` capped a 100
6. ✅ "Mostra ecrãs do Figma" → lista de frames (se token configurado)
7. ✅ "O que foi discutido no Miro?" → conteúdo do board (se token configurado)
8. ✅ Rate limiting da 4.7.4 activo em produção
9. ✅ `/api/info` retorna v7.2.0

---

## RISCOS E MITIGAÇÕES

| Risco | Probabilidade | Impacto | Mitigação |
|-------|--------------|---------|-----------|
| Refactor 5.1 quebra tools existentes | Média | Alto | Testar CADA tool individualmente antes de deploy |
| httpOnly cookies não funciona com CORS | Baixa | Alto | Frontend e API estão no mesmo domínio — SameSite=Strict funciona |
| Figma/Miro API rate limits | Média | Baixo | Cache de resultados 5-10 min, retry com backoff |
| Figma/Miro tokens expiram | Baixa | Médio | Graceful disable — tool desaparece sem crash |
| Streaming perf fix quebra rendering | Baixa | Médio | Render final completo como safety net |

---

— Claude (Arquiteto), 2026-02-23
