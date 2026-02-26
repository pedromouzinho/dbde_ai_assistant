# =============================================================================
# config.py — Configuração centralizada do Assistente AI DBDE v7.2
# =============================================================================
# Todas as variáveis de ambiente, constantes e configurações num único local.
# Nenhum outro ficheiro faz os.getenv() — tudo passa por aqui.
# =============================================================================

import os
import secrets
import logging
import hashlib
import re


def _get_env(name: str, default: str = "") -> str:
    """Lê env var com fallback para prefixo APPSETTING_ (Azure App Service)."""
    val = os.getenv(name)
    if val is None or val == "":
        val = os.getenv(f"APPSETTING_{name}", default)
    if isinstance(val, str):
        return val.strip()
    return default


logger = logging.getLogger(__name__)

# =============================================================================
# AZURE OPENAI
# =============================================================================
AZURE_OPENAI_ENDPOINT = _get_env(
    "AZURE_OPENAI_ENDPOINT",
    "https://dbdeaccess.openai.azure.com"
)
AZURE_OPENAI_KEY = _get_env("AZURE_OPENAI_KEY", "")
CHAT_DEPLOYMENT = _get_env("CHAT_DEPLOYMENT", "dbde_access_chatbot")
EMBEDDING_DEPLOYMENT = _get_env("EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
API_VERSION_CHAT = _get_env("API_VERSION_CHAT", "2024-02-15-preview")
API_VERSION_OPENAI = _get_env("API_VERSION_OPENAI", "2023-05-15")

# =============================================================================
# ANTHROPIC (Claude) — via API directa OU via Azure AI Foundry
# =============================================================================
# Se ANTHROPIC_FOUNDRY_RESOURCE estiver definido, usa Azure Foundry.
# Caso contrário, usa api.anthropic.com directamente.
# =============================================================================
ANTHROPIC_API_KEY = _get_env("ANTHROPIC_API_KEY", "")
ANTHROPIC_FOUNDRY_RESOURCE = _get_env("ANTHROPIC_FOUNDRY_RESOURCE", "")  # ex: "my-foundry-resource"

# URL base: Foundry se configurado, senão API directa
if ANTHROPIC_FOUNDRY_RESOURCE:
    ANTHROPIC_API_BASE = f"https://{ANTHROPIC_FOUNDRY_RESOURCE}.services.ai.azure.com/anthropic/v1/messages"
else:
    ANTHROPIC_API_BASE = _get_env("ANTHROPIC_API_BASE", "https://api.anthropic.com/v1/messages")

ANTHROPIC_MODEL_OPUS = _get_env("ANTHROPIC_MODEL_OPUS", "claude-opus-4-5-20251101")
ANTHROPIC_MODEL_SONNET = _get_env("ANTHROPIC_MODEL_SONNET", "claude-sonnet-4-5-20250929")
ANTHROPIC_MODEL_HAIKU = _get_env("ANTHROPIC_MODEL_HAIKU", "claude-haiku-4-5-20251001")

# =============================================================================
# LLM PROVIDER CONFIG
# =============================================================================
# Tiers: "fast" (barato/rápido), "standard" (default), "pro" (melhor qualidade)
LLM_DEFAULT_TIER = _get_env("LLM_DEFAULT_TIER", "fast")

# Mapping de tiers para providers+modelos
# Formato: "provider:model" — o provider resolve internamente
LLM_TIER_FAST = _get_env("LLM_TIER_FAST", "azure_openai:gpt-5-mini")
LLM_TIER_STANDARD = _get_env("LLM_TIER_STANDARD", "azure_openai:gpt-5-chat")
LLM_TIER_PRO = _get_env("LLM_TIER_PRO", "azure_openai:gpt-5.2-chat")

# Fallback provider (se o primário falhar)
LLM_FALLBACK = _get_env("LLM_FALLBACK", "azure_openai:dbde_access_chatbot_41")

# Model Router — feature flag para routing inteligente entre modelos.
# Desactivado em produção por omissão. Para activar:
#   1. Definir MODEL_ROUTER_ENABLED=true
#   2. Definir MODEL_ROUTER_SPEC=azure_openai:<deployment-name>
#   3. Opcional: MODEL_ROUTER_NON_PROD_ONLY=false para permitir em produção
_app_env_hint = _get_env("APP_ENV", "").lower()
MODEL_ROUTER_ENABLED = _get_env(
    "MODEL_ROUTER_ENABLED",
    "true" if _app_env_hint in ("test", "staging", "qa") else "false",
).lower() == "true"
MODEL_ROUTER_SPEC = _get_env("MODEL_ROUTER_SPEC", "azure_openai:model-router")
MODEL_ROUTER_TARGET_TIERS = tuple(
    t.strip().lower()
    for t in _get_env("MODEL_ROUTER_TARGET_TIERS", "standard,pro").split(",")
    if t.strip()
)
MODEL_ROUTER_NON_PROD_ONLY = _get_env("MODEL_ROUTER_NON_PROD_ONLY", "true").lower() == "true"

# =============================================================================
# AZURE AI SEARCH
# =============================================================================
SEARCH_SERVICE = _get_env("SEARCH_SERVICE", "dbdeacessrag")
SEARCH_KEY = _get_env("SEARCH_KEY", "")
API_VERSION_SEARCH = _get_env("API_VERSION_SEARCH", "2023-11-01")

DEVOPS_INDEX = _get_env("DEVOPS_INDEX", "millennium-devops-index")
OMNI_INDEX = _get_env("OMNI_INDEX", "millennium-omni-index")
EXAMPLES_INDEX = _get_env("EXAMPLES_INDEX", "millennium-examples-index")

TOP_K = int(_get_env("TOP_K", "10"))

# =============================================================================
# POST-RETRIEVAL RERANK
# =============================================================================
RERANK_ENABLED = _get_env("RERANK_ENABLED", "true").lower() == "true"
RERANK_ENDPOINT = _get_env("RERANK_ENDPOINT", "")
RERANK_API_KEY = _get_env("RERANK_API_KEY", "")
RERANK_MODEL = _get_env("RERANK_MODEL", "cohere-rerank-v4.0-fast")
RERANK_TOP_N = int(_get_env("RERANK_TOP_N", "15"))
RERANK_TIMEOUT_SECONDS = float(_get_env("RERANK_TIMEOUT_SECONDS", "15"))
RERANK_AUTH_MODE = _get_env("RERANK_AUTH_MODE", "api-key").lower()

# =============================================================================
# AZURE DEVOPS
# =============================================================================
DEVOPS_PAT = _get_env("DEVOPS_PAT", "")
DEVOPS_ORG = _get_env("DEVOPS_ORG", "ptbcp")
DEVOPS_PROJECT = _get_env("DEVOPS_PROJECT", "IT.DIT")

# =============================================================================
# FIGMA / MIRO (Read-Only Integrations)
# =============================================================================
FIGMA_ACCESS_TOKEN = _get_env("FIGMA_ACCESS_TOKEN", "")
MIRO_ACCESS_TOKEN = _get_env("MIRO_ACCESS_TOKEN", "")

# =============================================================================
# AZURE TABLE STORAGE
# =============================================================================
STORAGE_CONNECTION_STRING = _get_env("STORAGE_CONNECTION_STRING", "")
STORAGE_ACCOUNT = _get_env("STORAGE_ACCOUNT", "dbdeaccessstorage")
STORAGE_KEY = _get_env("STORAGE_KEY", "")
UPLOAD_BLOB_CONTAINER_RAW = _get_env("UPLOAD_BLOB_CONTAINER_RAW", "upload-raw")
UPLOAD_BLOB_CONTAINER_TEXT = _get_env("UPLOAD_BLOB_CONTAINER_TEXT", "upload-text")
UPLOAD_BLOB_CONTAINER_CHUNKS = _get_env("UPLOAD_BLOB_CONTAINER_CHUNKS", "upload-chunks")
CHAT_TOOLRESULT_BLOB_CONTAINER = _get_env("CHAT_TOOLRESULT_BLOB_CONTAINER", "chat-tool-results")
GENERATED_FILES_BLOB_CONTAINER = _get_env("GENERATED_FILES_BLOB_CONTAINER", "generated-files")

# =============================================================================
# AUTH
# =============================================================================
APP_ENV = _get_env("APP_ENV", "").lower()
RUNNING_IN_AZURE_APP_SERVICE = bool(_get_env("WEBSITE_SITE_NAME", ""))
IS_PRODUCTION = APP_ENV in ("prod", "production") or RUNNING_IN_AZURE_APP_SERVICE
JWT_REQUIRE_EXPLICIT = _get_env(
    "JWT_REQUIRE_EXPLICIT",
    "true" if IS_PRODUCTION else "false",
).lower() == "true"

_jwt_secret_env = _get_env("JWT_SECRET", "")
if _jwt_secret_env:
    JWT_SECRET = _jwt_secret_env
else:
    if JWT_REQUIRE_EXPLICIT:
        raise RuntimeError(
            "[Config] JWT_SECRET obrigatório em produção. "
            "Define JWT_SECRET (ou APPSETTING_JWT_SECRET) nas App Settings."
        )
    _fallback_seed = (
        (STORAGE_KEY or "")
        or (SEARCH_KEY or "")
        or (AZURE_OPENAI_KEY or "")
        or (ANTHROPIC_API_KEY or "")
    )
    if _fallback_seed:
        JWT_SECRET = hashlib.sha256(f"dbde-jwt::{_fallback_seed}".encode("utf-8")).hexdigest()
        logger.critical(
            "[Config] JWT_SECRET não definido. Foi derivado de outro segredo de runtime. "
            "Configura JWT_SECRET em App Settings para rotação controlada."
        )
    else:
        JWT_SECRET = secrets.token_urlsafe(48)
        logger.critical(
            "[Config] JWT_SECRET não definido e sem seed de fallback. "
            "Secret efémero gerado para este processo. Configura JWT_SECRET em App Settings."
        )
JWT_EXPIRATION_HOURS = int(_get_env("JWT_EXPIRATION_HOURS", "10"))
ADMIN_INITIAL_PASSWORD = _get_env("ADMIN_INITIAL_PASSWORD", "")
AUTH_COOKIE_NAME = _get_env("AUTH_COOKIE_NAME", "dbde_token")
_jwt_secret_previous_env = _get_env("JWT_SECRET_PREVIOUS", "")
JWT_SECRET_PREVIOUS = _jwt_secret_previous_env if _jwt_secret_previous_env else None
AUTH_COOKIE_SECURE = _get_env("AUTH_COOKIE_SECURE", "true").lower() == "true"
AUTH_COOKIE_MAX_AGE_SECONDS = int(_get_env("AUTH_COOKIE_MAX_AGE_SECONDS", "86400"))
ALLOWED_ORIGINS = _get_env(
    "ALLOWED_ORIGINS",
    ",".join(
        [
            "https://dbdeai.pt",
            "https://millennium-ai-assistant-epa7d7b4defabwbn.swedencentral-01.azurewebsites.net",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    ),
)

# =============================================================================
# AGENT CONFIG
# =============================================================================
AGENT_MAX_ITERATIONS = int(_get_env("AGENT_MAX_ITERATIONS", "5"))
AGENT_MAX_TOKENS = int(_get_env("AGENT_MAX_TOKENS", "8000"))
AGENT_TEMPERATURE = float(_get_env("AGENT_TEMPERATURE", "0.3"))
AGENT_HISTORY_LIMIT = int(_get_env("AGENT_HISTORY_LIMIT", "14"))
AGENT_TOOL_RESULT_MAX_SIZE = int(_get_env("AGENT_TOOL_RESULT_MAX_SIZE", "30000"))
AGENT_TOOL_RESULT_KEEP_ITEMS = int(_get_env("AGENT_TOOL_RESULT_KEEP_ITEMS", "100"))

# =============================================================================
# UPLOAD CONFIG
# =============================================================================
UPLOAD_MAX_FILES_PER_CONVERSATION = int(_get_env("UPLOAD_MAX_FILES_PER_CONVERSATION", "10"))
UPLOAD_MAX_IMAGES_PER_MESSAGE = int(_get_env("UPLOAD_MAX_IMAGES_PER_MESSAGE", "10"))
UPLOAD_MAX_FILE_MB = int(_get_env("UPLOAD_MAX_FILE_MB", "10"))
UPLOAD_MAX_FILE_BYTES = UPLOAD_MAX_FILE_MB * 1024 * 1024
UPLOAD_MAX_CONCURRENT_JOBS = int(_get_env("UPLOAD_MAX_CONCURRENT_JOBS", "2"))
UPLOAD_MAX_PENDING_JOBS_PER_USER = int(_get_env("UPLOAD_MAX_PENDING_JOBS_PER_USER", "20"))
UPLOAD_EMBEDDING_CONCURRENCY = int(_get_env("UPLOAD_EMBEDDING_CONCURRENCY", "3"))
UPLOAD_MAX_CHUNKS_PER_FILE = int(_get_env("UPLOAD_MAX_CHUNKS_PER_FILE", "300"))
UPLOAD_JOB_STALE_SECONDS = int(_get_env("UPLOAD_JOB_STALE_SECONDS", "900"))
UPLOAD_MAX_BATCH_TOTAL_MB = int(_get_env("UPLOAD_MAX_BATCH_TOTAL_MB", "60"))
UPLOAD_MAX_BATCH_TOTAL_BYTES = UPLOAD_MAX_BATCH_TOTAL_MB * 1024 * 1024
UPLOAD_INDEX_TOP = int(_get_env("UPLOAD_INDEX_TOP", "200"))
UPLOAD_INLINE_WORKER_ENABLED = _get_env("UPLOAD_INLINE_WORKER_ENABLED", "true").lower() == "true"
UPLOAD_WORKER_POLL_SECONDS = float(_get_env("UPLOAD_WORKER_POLL_SECONDS", "2.5"))
UPLOAD_WORKER_BATCH_SIZE = int(_get_env("UPLOAD_WORKER_BATCH_SIZE", "4"))

# =============================================================================
# EXPORT CONFIG
# =============================================================================
_EXPORT_BRAND_COLOR_RAW = _get_env("EXPORT_BRAND_COLOR", "#DE3163")
EXPORT_BRAND_COLOR = _EXPORT_BRAND_COLOR_RAW if re.fullmatch(r"#[0-9A-Fa-f]{6}", _EXPORT_BRAND_COLOR_RAW) else "#DE3163"
EXPORT_BRAND_NAME = "Millennium BCP"
EXPORT_AGENT_NAME = "Assistente AI DBDE"
EXPORT_AUTO_ASYNC_ENABLED = _get_env("EXPORT_AUTO_ASYNC_ENABLED", "true").lower() == "true"
EXPORT_ASYNC_THRESHOLD_ROWS = int(_get_env("EXPORT_ASYNC_THRESHOLD_ROWS", "250"))
EXPORT_MAX_CONCURRENT_JOBS = int(_get_env("EXPORT_MAX_CONCURRENT_JOBS", "2"))
EXPORT_JOB_STALE_SECONDS = int(_get_env("EXPORT_JOB_STALE_SECONDS", "1800"))
EXPORT_INLINE_WORKER_ENABLED = _get_env("EXPORT_INLINE_WORKER_ENABLED", "false").lower() == "true"
EXPORT_WORKER_POLL_SECONDS = float(_get_env("EXPORT_WORKER_POLL_SECONDS", "2.0"))
EXPORT_WORKER_BATCH_SIZE = int(_get_env("EXPORT_WORKER_BATCH_SIZE", "3"))

# =============================================================================
# DEVOPS FIELD CONSTANTS (NUNCA confiar no LLM — campos hardcoded)
# =============================================================================
DEVOPS_FIELDS = [
    "System.Id",
    "System.Title",
    "System.State",
    "System.WorkItemType",
    "System.AssignedTo",
    "System.CreatedBy",
    "System.AreaPath",
    "System.CreatedDate",
]

DEVOPS_AREAS = [
    r"IT.DIT\DIT\ADMChannels\DBKS\AM24\RevampFEE MVP2",
    r"IT.DIT\DIT\ADMChannels\DBKS\AM24\MDSE",
    r"IT.DIT\DIT\ADMChannels\DBKS\AM24\ACEDigital",
    r"IT.DIT\DIT\ADMChannels\DBKS\AM24\MSE",
]

DEVOPS_WORKITEM_TYPES = ["User Story", "Bug", "Task", "Feature", "Epic"]
DEVOPS_STATES = ["New", "Active", "Closed", "Resolved", "Removed"]

# =============================================================================
# DEBUG
# =============================================================================
DEBUG_LOG_SIZE = int(_get_env("DEBUG_LOG_SIZE", "50"))
DEBUG_MODE = _get_env("DEBUG_MODE", "false").lower() == "true"
LOG_FORMAT = _get_env("LOG_FORMAT", "text").lower()  # "json" para produção, "text" para dev

# =============================================================================
# APP METADATA
# =============================================================================
APP_VERSION = "7.2.7"
APP_TITLE = "Millennium BCP AI Agent"
APP_DESCRIPTION = "Agente IA multi-modelo com streaming, exports e integração DevOps"
