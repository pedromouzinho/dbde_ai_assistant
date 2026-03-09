# DBDE AI Assistant — Runbook Operacional
## Versao: 7.3.0 | Data: 2026-03-01

## 1. Arquitetura de Runtime
- Azure App Service (Linux, Sweden Central).
- FastAPI/Uvicorn (porta 8000).
- Upload worker dedicado com supervisor (`startup.sh`).
- Export worker dedicado com supervisor (`startup.sh`).
- Azure Table Storage (estado, histórico, rate limits, jobs).
- Azure Blob Storage (ficheiros/upload/export).
- Azure OpenAI (tiers fast/standard/pro/vision).
- Azure AI Search (RAG/knowledge index).
- Brave Search (opcional, feature flag).

## 2. Startup e Shutdown
### Sequência de startup
1. `startup.sh` define `PYTHONPATH`, cria diretórios `run/` e `/home/LogFiles`.
2. Sobe supervisores de worker (upload/export) se sidecars estiverem ativos.
3. Supervisores gravam PID em:
   - `/home/site/wwwroot/run/upload-worker.pid`
   - `/home/site/wwwroot/run/upload-worker-supervisor.pid`
   - `/home/site/wwwroot/run/export-worker.pid`
   - `/home/site/wwwroot/run/export-worker-supervisor.pid`
4. Arranca `uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1`.

### Shutdown
- SIGTERM termina Uvicorn.
- Sidecar workers são terminados com o processo pai/App Service recycle.
- Se necessário, validar limpeza de PID no próximo startup.

## 3. Localização de logs e ficheiros
- Upload worker log: `/home/LogFiles/upload-worker.log`
- Export worker log: `/home/LogFiles/export-worker.log`
- App logs: Azure Log Stream (stdout/stderr)
- PID files: `/home/site/wwwroot/run/*.pid`

## 4. Health Check
### Básico
- `GET /health`
- Verifica se app está viva.

### Deep
- `GET /health?deep=true`
- Checks esperados:
  - `table_storage`
  - `blob_storage`
  - `llm_fast`
  - `llm_vision` (se vision habilitado)
  - `ai_search`
  - `rerank`
  - `upload_worker`
  - `export_worker`

### Semântica
- `200` com `status=healthy`: tudo operacional.
- `503` com `status=degraded`: pelo menos um check em erro.

## 5. Procedimentos de recuperação
### 5.1 Worker down
1. Verificar `GET /health?deep=true` (`upload_worker`/`export_worker`).
2. Consultar logs em `/home/LogFiles/*-worker.log`.
3. Confirmar supervisor PID vivo.
4. Se necessário, restart ao App Service.

### 5.2 Storage indisponível
1. Confirmar erro no health deep (`table_storage` ou `blob_storage`).
2. Validar credenciais/env vars de storage.
3. Validar estado do Azure Storage account.
4. Restart app após corrigir credenciais.

### 5.3 LLM indisponível
1. Confirmar `llm_fast`/`llm_vision` no health deep.
2. Confirmar env vars (`AZURE_OPENAI_*`, deployments).
3. Verificar quota/429 no Azure OpenAI.
4. Aplicar fallback de tier se necessário.

### 5.4 PAT DevOps expirado
1. Sintoma típico: tools DevOps com 401/403.
2. Renovar PAT no Azure DevOps.
3. Atualizar `DEVOPS_PAT` no App Service.
4. Restart app e validar com `query_workitems`.

## 6. Env vars críticas
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_KEY`
- `CHAT_DEPLOYMENT` / `EMBEDDING_DEPLOYMENT`
- `LLM_TIER_FAST`, `LLM_TIER_STANDARD`, `LLM_TIER_PRO`, `LLM_TIER_VISION`
- `RERANK_ENDPOINT`, `RERANK_MODEL`, `RERANK_API_KEY`
- `ANTHROPIC_FOUNDRY_RESOURCE`, `ANTHROPIC_API_KEY`
  - `ANTHROPIC_API_KEY`: usar a key do recurso Azure AI Foundry quando `ANTHROPIC_FOUNDRY_RESOURCE` estiver definido
- `VISION_ENABLED`
- `STORAGE_ACCOUNT`, `STORAGE_KEY`
- `DEVOPS_PAT`
- `SEARCH_SERVICE`, `SEARCH_KEY`
- `JWT_SECRET`

## 7. Escalação
- Product Owner: Pedro
- Infra/Cloud: equipa Azure do domínio
- Segurança/Compliance: equipa de governance do banco

## 8. Checklist operacional diária
1. `GET /health` e `GET /health?deep=true`.
2. Verificar erros críticos no log stream.
3. Verificar 429 em LLM tiers e latência de resposta.
4. Validar workers ativos (PID + health).
5. Confirmar expiração próxima de segredos (PAT/API keys).
