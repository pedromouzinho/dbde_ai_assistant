# Fase 6 — Análise Profunda (v7.2.1) — Mensagens para o Codex

> Gerado por: Claude (Arquiteto) | Data: 2026-02-24
> Enviar ao Codex uma tarefa de cada vez. Aguardar resultado e aprovação do Arquiteto antes de avançar para a próxima.

---

## MENSAGEM 1 de 2

> **MENSAGEM PARA O CODEX — Tarefa 6.1: PDF Chunking + search_uploaded_document**
>
> **Objectivo:** Permitir que utilizadores façam perguntas sobre secções específicas de PDFs grandes (>50K caracteres) usando pesquisa semântica sobre chunks do documento, em vez de depender do texto truncado no contexto do LLM.
>
> **Porquê:** Actualmente, quando um PDF excede 50K caracteres o texto é truncado e o utilizador perde acesso a grande parte do conteúdo. Com chunking + pesquisa semântica, o agente consegue encontrar e devolver os trechos relevantes do documento completo.
>
> **Ficheiros a ler para contexto:**
> - `DBDE_AI_ASSISTANT_V7_HANDOFF.md` — arquitectura geral
> - `tool_registry.py` — como registar novas tools (padrão `register_tool()`)
> - `tools_figma.py` ou `tools_miro.py` — exemplos de tools registadas dinamicamente
> - `tools.py` — onde estão as tools existentes, `get_embedding()`, e `get_agent_system_prompt()`
> - `agent.py` — onde está `uploaded_files_store` (dict in-memory por conv_id)
> - `app.py` — endpoint `/upload` onde o PDF é processado
>
> **Ficheiros a alterar:**
> - `app.py` — endpoint `/upload` (adicionar lógica de chunking para PDFs grandes)
> - `tools.py` — nova tool `search_uploaded_document` + actualizar `get_agent_system_prompt()`
>
> **O que fazer:**
>
> 1. **No endpoint `/upload` de `app.py`**, depois de extrair o texto do PDF e antes de guardar em `uploaded_files_store`: se o texto extraído exceder 50000 caracteres, dividi-lo em chunks de aproximadamente 4000 caracteres cada, com overlap de 200 caracteres entre chunks consecutivos. Para cada chunk, calcular o embedding usando `get_embedding()` de `tools.py`. Guardar a lista de chunks (texto + embedding) dentro de `uploaded_files_store[conv_id]` numa chave nova chamada `"chunks"`. O texto completo continua a ser truncado a 100000 caracteres como está hoje para o contexto do LLM — os chunks são um complemento, não substituição.
>
> 2. **Em `tools.py`**, criar uma nova tool chamada `search_uploaded_document`. Deve ser registada via `register_tool()` do `tool_registry.py`, seguindo o mesmo padrão que `tools_figma.py` e `tools_miro.py`. Parâmetros da tool: `query` (string obrigatória — o que o utilizador quer pesquisar) e `conv_id` (string — identificador da conversa, que o agente infere automaticamente). A execução deve: calcular o embedding da query via `get_embedding()`, comparar com os embeddings dos chunks guardados usando cosine similarity, e retornar os top 5 chunks mais relevantes com o texto e a posição (índice do chunk). Se não houver documento carregado ou se o documento não tiver chunks (porque era pequeno), retornar uma mensagem de erro controlada a explicar isso. A description da tool para o LLM deve ser: "Pesquisa semântica no documento carregado pelo utilizador. Usar quando o utilizador perguntar sobre conteúdos específicos de um documento que fez upload e o documento é grande."
>
> 3. **Em `get_agent_system_prompt()` de `tools.py`**, adicionar uma regra de routing condicional (só aparece se `has_tool("search_uploaded_document")` for true): quando o utilizador faz upload de um documento grande e pergunta sobre secções específicas, o agente deve usar `search_uploaded_document` para encontrar o conteúdo relevante em vez de tentar responder só com o texto truncado.
>
> **Nota sobre cosine similarity:** Não há numpy disponível no runtime. A cosine similarity deve ser calculada com `math.sqrt` e `sum` — é uma fórmula simples de dot product / (norma A × norma B). O Codex sabe como fazer isto.
>
> **Nota sobre `uploaded_files_store`:** É um dict importado de `agent.py` em `app.py` (já está importado na linha 66 de app.py). Em `tools.py`, será necessário importar `uploaded_files_store` de `agent.py` para a nova tool poder aceder aos chunks. Atenção a imports circulares — se houver conflito, a alternativa é passar os chunks como argumento via o agente (o agent.py já tem acesso ao store).
>
> **Regras:**
> - NÃO alterar `agent.py`, `auth.py`, `models.py`, `storage.py`, `config.py`
> - NÃO instalar dependências novas — `get_embedding()` já existe, cosine similarity é inline
> - NÃO alterar o comportamento de PDFs pequenos (<50K) — esses continuam como hoje
> - Os chunks são IN-MEMORY por conversa — desaparecem quando a conversa é evicted do `ConversationStore`
> - O registo da tool deve ser automático no import (mesmo padrão de tools_figma/tools_miro)
> - Manter todos os endpoints existentes funcionais (zero regressão)
>
> **Validação:**
> 1. Upload de PDF grande (>50K chars) → resposta OK, sem erro, `uploaded_files_store` contém chave `"chunks"` com lista não vazia
> 2. Pergunta "O que diz o capítulo 3?" com documento carregado → agente usa `search_uploaded_document` → retorna chunks relevantes
> 3. Upload de PDF pequeno (<50K chars) → comportamento idêntico ao actual (sem chunks, sem erro)
> 4. Sem upload → `search_uploaded_document` retorna mensagem de erro controlada (não crash)
> 5. Chat normal sem upload → todas as 13 tools existentes funcionam sem alteração
> 6. `GET /api/info` → `active_tools` inclui `search_uploaded_document` (14 tools no total)
> 7. `GET /health` → 200
>
> **Deploy:** ZIP deploy após validação. Não há dependências novas no `requirements.txt`.

---

## MENSAGEM 2 de 2

> **MENSAGEM PARA O CODEX — Tarefa 6.3: Daily Digest Endpoint**
>
> **Objectivo:** Criar um endpoint que gera automaticamente um resumo diário do estado do Azure DevOps — USs criadas, bugs antigos, items sem assignee, items fechados esta semana — sem qualquer interacção do utilizador no chat.
>
> **Porquê:** Actualmente, para obter este tipo de visão geral o utilizador tem de fazer 4 perguntas separadas no chat. O daily digest consolida tudo num único pedido, permitindo dashboards externos ou automações futuras.
>
> **Ficheiros a ler para contexto:**
> - `DBDE_AI_ASSISTANT_V7_HANDOFF.md` — arquitectura geral
> - `tools.py` — função `_devops_request_with_retry()` (helper de retry para Azure DevOps) e `_devops_url()` e `_devops_headers()`
> - `app.py` — padrão de endpoints existentes (autenticação, rate limiting)
> - `config.py` — constantes `DEVOPS_PAT`, `DEVOPS_ORG`, `DEVOPS_PROJECT`
>
> **Ficheiros a alterar:**
> - `app.py` — novo endpoint `GET /api/digest`
>
> **O que fazer:**
>
> 1. **Novo endpoint `GET /api/digest` em `app.py`**. Requer autenticação (qualquer user autenticado, não apenas admin). Rate limit partilhado com os outros endpoints (chat_budget).
>
> 2. O endpoint executa **4 queries WIQL** ao Azure DevOps. Todas as queries devem filtrar por `[System.TeamProject] = 'IT.DIT'`. As 4 queries são:
>    - **USs criadas ontem**: WorkItemType = 'User Story' com CreatedDate >= @Today-1 AND CreatedDate < @Today
>    - **Bugs abertos há mais de 7 dias**: WorkItemType = 'Bug', State = 'Active', CreatedDate < @Today-7
>    - **Items sem assignee**: AssignedTo vazio, State diferente de 'Closed' e diferente de 'Removed'
>    - **Items fechados esta semana**: State = 'Closed' com ClosedDate >= @StartOfWeek (campo `[Microsoft.VSTS.Common.ClosedDate]`)
>
> 3. Para cada query, obter os detalhes dos work items (ID, título, estado, tipo, assigned to, created date). Reutilizar o padrão de `_devops_request_with_retry` — pode importá-lo de `tools.py` ou criar um helper local equivalente em `app.py`. Usar `_devops_headers()` e `_devops_url()` de `tools.py` para os headers e URLs do DevOps (importar se necessário).
>
> 4. Retornar **JSON estruturado** (não HTML) com a data, e as 4 secções cada uma com count e lista de items. Cada item deve ter: id, title, state, type, assigned_to, created_date, e url (link directo para o work item no DevOps).
>
> 5. Se o Azure DevOps estiver indisponível ou uma query falhar, retornar JSON com campo `"error"` descritivo em vez de HTTP 500. As queries que funcionaram devem ser retornadas normalmente — uma falha numa query não deve bloquear as outras.
>
> **Regras:**
> - NÃO alterar `tools.py`, `agent.py`, `auth.py`, `models.py`, `storage.py`, `config.py`
> - As queries WIQL são hardcoded (não recebem input do utilizador) — portanto não há risco de WIQL injection. Mesmo assim, usar constantes de `config.py` para nomes de campos.
> - Rate limit: partilhar o scope `chat_budget` existente
> - Se precisar importar helpers de `tools.py` (como `_devops_request_with_retry`, `_devops_url`, `_devops_headers`), garantir que os imports não partem nada
>
> **Validação:**
> 1. `GET /api/digest` com auth → 200 + JSON com 4 secções (`created_yesterday`, `old_bugs`, `unassigned`, `closed_this_week`), cada uma com `count` e `items`
> 2. `GET /api/digest` sem auth → 401 ou 403
> 3. Se DevOps indisponível → JSON com `"error"` na secção afectada (não 500)
> 4. Todos os outros endpoints continuam a funcionar (zero regressão)
> 5. `GET /health` → 200
>
> **Deploy:** ZIP deploy após validação. Não há dependências novas.

---

## Sequência de Execução

1. **Enviar Mensagem 1** (Task 6.1) ao Codex
2. Aguardar resultado → Pedro envia "MENSAGEM PARA O CLAUDE" com output
3. Claude audita, dá veredicto
4. Se APROVADO → ZIP deploy → validar
5. **Enviar Mensagem 2** (Task 6.3) ao Codex
6. Aguardar resultado → mesma sequência de audit
7. Se APROVADO → ZIP deploy → validar
8. Bump version para 7.2.1 em `config.py`
9. ZIP deploy final v7.2.1
10. Actualizar Handoff e Team Protocol

---

— Claude (Arquiteto), 2026-02-24
