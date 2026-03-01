# DBDE AI Assistant — Inventário de Terceiros
## Versao: 7.3.0 | Data: 2026-03-01

## 1. Resumo
Inventário de integrações externas, com finalidade, dados trocados, risco de PII, jurisdição e dependência operacional.

## 2. Serviços externos

### 2.1 Azure OpenAI
- Finalidade: chat LLM, tool calling, vision.
- Dados enviados: mensagens do utilizador, contexto, payload de tools, imagem base64 (quando vision).
- Dados recebidos: respostas, tool calls.
- PII potencial: média/alta (conforme conteúdo do chat).
- Jurisdição: região Azure configurada (preferencialmente EU).
- Env vars: `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `CHAT_DEPLOYMENT`, `LLM_TIER_*`.

### 2.2 Anthropic (opcional)
- Finalidade: fallback provider LLM.
- Dados enviados/recebidos: mensagens e respostas.
- PII potencial: média/alta.
- Jurisdição: depende do endpoint (API direta vs Foundry).
- Env vars: `ANTHROPIC_API_KEY`, `ANTHROPIC_API_BASE`, `ANTHROPIC_FOUNDRY_RESOURCE`.

### 2.3 Azure AI Search
- Finalidade: RAG (pesquisa semântica/vector).
- Dados enviados: query + embedding.
- Dados recebidos: documentos/snippets/scores.
- PII potencial: média (depende do índice).
- Jurisdição: Azure region do Search service.
- Env vars: `SEARCH_SERVICE`, `SEARCH_KEY`, `DEVOPS_INDEX`, `OMNI_INDEX`, `EXAMPLES_INDEX`.

### 2.4 Brave Search API
- Finalidade: pesquisa web atual.
- Dados enviados: queries de pesquisa.
- Dados recebidos: snippets/links.
- PII potencial: baixo (evitar enviar dados pessoais nas queries).
- Jurisdição: serviço externo Brave (fora tenant Azure).
- Env vars: `WEB_SEARCH_ENABLED`, `WEB_SEARCH_API_KEY`, `WEB_SEARCH_ENDPOINT`, `WEB_SEARCH_DAILY_QUOTA_PER_USER`.

### 2.5 Azure DevOps REST API
- Finalidade: leitura/criação/refino de work items.
- Dados enviados: WIQL, payload de work items.
- Dados recebidos: metadados de backlog.
- PII potencial: média (nomes/emails/work items).
- Jurisdição: organização Azure DevOps configurada.
- Env vars: `DEVOPS_PAT`, `DEVOPS_ORG`, `DEVOPS_PROJECT`.

### 2.6 Azure Table Storage
- Finalidade: estado da app, histórico, feedback, rate limits, jobs.
- PII potencial: média.
- Jurisdição: storage account region.
- Env vars: `STORAGE_ACCOUNT`, `STORAGE_KEY`.

### 2.7 Azure Blob Storage
- Finalidade: ficheiros carregados/exportados e metadados.
- PII potencial: média/alta (depende dos uploads).
- Jurisdição: storage account region.
- Env vars: containers de upload/export e credenciais storage.

### 2.8 Figma API
- Finalidade: pesquisa/análise de assets e fluxos Figma.
- Dados enviados: queries e IDs de ficheiros/nodes.
- Dados recebidos: metadados e estrutura de frames.
- PII potencial: baixo/médio.
- Jurisdição: SaaS Figma.
- Env vars: `FIGMA_API_TOKEN`.

### 2.9 Miro API
- Finalidade: pesquisa de boards e artefactos Miro.
- Dados enviados: query/IDs.
- Dados recebidos: metadados de boards.
- PII potencial: baixo/médio.
- Jurisdição: SaaS Miro.
- Env vars: `MIRO_API_KEY`.

### 2.10 Frontend CDNs (quando fallback)
- Serviços: cdnjs, cdn.plot.ly, fonts.googleapis.com, fonts.gstatic.com.
- Finalidade: libs/fontes estáticas.
- Risco: supply chain e disponibilidade de CDN.
- Mitigação: fallback local em `static/vendor/`.

## 3. Matriz rápida de criticidade
- Alta: Azure OpenAI, Azure Storage, Azure DevOps.
- Média: Azure AI Search, Anthropic fallback.
- Baixa: Brave Search, Figma/Miro, CDNs.

## 4. Classificação de dados
- Dados operacionais internos: work items, métricas, conversas.
- Dados potencialmente sensíveis: anexos, textos com nomes/e-mails.
- Recomendação: mascarar PII em prompts externos sempre que possível.

## 5. Controles recomendados
1. Rotação de segredos em calendário fixo (PAT, API keys).
2. Logs estruturados por tool + user + latência + erro.
3. Quotas por utilizador para web search e LLM tiers.
4. Revisão de jurisdição por integração externa (compliance bancário).
5. Runbook e evidência de health checks para auditoria.
