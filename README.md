# DBDE AI Assistant

Internal AI Assistant for the DBDE team at Millennium BCP.  
Built with **FastAPI** (backend) + **Vite** (frontend), integrating Azure OpenAI, Azure AI Search, Azure DevOps, Figma, Miro, Brave Search, and Azure AI Safety services.

## Architecture

```
frontend/          Vite + JS/TS SPA
app.py             FastAPI application, routes, middleware
agent.py           Agent loop, streaming SSE, conversation management
tools.py           Tool definitions and system prompts
tools_devops.py    Azure DevOps integration (WIQL, work items)
tools_export.py    File/chart generation and temporary store
tools_email.py     Email classification and Outlook draft preparation
tools_figma.py     Figma API integration
tools_miro.py      Miro API integration
tools_knowledge.py Azure AI Search and Brave web search
tools_upload.py    Document upload and vector search
tools_learning.py  Writer profile learning
llm_provider.py    LLM provider abstraction (Azure OpenAI + Anthropic)
config.py          All configuration via environment variables
auth.py            JWT auth and password hashing
storage.py         Azure Table/Blob Storage operations
pii_shield.py      PII detection and masking (Azure AI Language)
prompt_shield.py   Prompt injection detection (Azure AI Content Safety)
export_engine.py   CSV/XLSX/PDF/SVG/HTML export engine
code_interpreter.py Python sandbox execution
```

## Quick Start (Local Development)

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 3. Copy and fill in environment variables
cp .env.example .env
# Edit .env with your Azure credentials

# 4. Run the backend
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload

# 5. (Optional) Build the frontend
npm ci
npm run build
```

## Environment Variables

See `.env.example` for a full list of required and optional configuration variables.  
Critical required variables for production:

| Variable | Description |
|---|---|
| `JWT_SECRET` | Secret key for JWT signing (required in production) |
| `AZURE_OPENAI_KEY` | Azure OpenAI API key |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL |
| `SEARCH_KEY` | Azure AI Search admin key |
| `STORAGE_KEY` | Azure Table/Blob Storage account key |
| `DEVOPS_PAT` | Azure DevOps Personal Access Token |
| `ADMIN_INITIAL_PASSWORD` | Initial password for the admin user |

## Running Tests

```bash
python -m pytest tests/ -x --tb=short -q
```

## CI/CD

GitHub Actions workflow at `.github/workflows/ci.yml` runs on every push to `main`.  
Steps: install deps → lint (ruff) → SAST (bandit) → dependency audit (pip-audit, npm audit) → tests → frontend build.

## Deployment

See `docs/RUNBOOK.md` for full deployment instructions and `docs/DEPLOY_CHECKLIST.md` for pre-release checks.

The application is deployed to **Azure App Service**.  
`startup.sh` starts the FastAPI server + background workers.  
`startup_worker.sh` starts a standalone upload worker (used as a sidecar).

## Security

- JWT tokens are signed with `JWT_SECRET` (must be set explicitly in production)
- PII is masked before sending to external LLMs via `pii_shield.py`
- Prompt injection is detected via Azure AI Content Safety (`prompt_shield.py`)
- The Python code interpreter runs in a sandbox with import restrictions and CPU/memory limits
- See `docs/DATA_POLICY.md` for data handling policies
