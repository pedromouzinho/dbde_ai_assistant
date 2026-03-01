# DBDE AI Assistant — Documentacao de Continuidade
## Versao: 7.3.0

## 1. Visao Geral
Serviço interno para suporte operacional e produto. Este documento permite continuidade operacional por equipa backup.

## 2. Arquitetura
- Azure App Service (Linux, Sweden Central)
- Backend FastAPI (Python)
- Frontend React (Vite build)
- Azure Table + Blob Storage
- Azure OpenAI (+ fallback provider)
- Azure AI Search
- GitHub Actions CI

## 3. Acessos Necessarios
- Azure subscription (Contributor)
- GitHub repo (maintainer/admin)
- Azure DevOps PAT (scopes corretos)
- DNS/domain management

Acao: manter pelo menos 1 backup owner com acesso valido.

## 4. Operacao Diaria
Ver [RUNBOOK.md](./RUNBOOK.md):
- health checks
- troubleshooting workers/storage/llm
- validacao de segredos

## 5. Rotacao de Segredos
- DEVOPS_PAT: rotacao periodica (data atual de expiracao conhecida)
- API keys (OpenAI/Search/Brave/Figma/Miro): rotacao por politica
- JWT_SECRET: mudanca controlada (invalida sessoes)

## 6. Custos (ordem de grandeza)
- App Service, OpenAI, Search, Storage, APIs externas
- Manter tracking mensal e alertas de custo

## 7. Contactos
- Product Owner: Pedro
- Backup operacional: definir
- Infra/Security: definir
