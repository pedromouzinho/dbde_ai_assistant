# =============================================================================
# config.py — Configuração centralizada do Assistente AI DBDE v7.2
# =============================================================================
# Todas as variáveis de ambiente, constantes e configurações num único local.
# Nenhum outro ficheiro faz os.getenv() — tudo passa por aqui.
# CHANGELOG:
# v7.3.0 (Fase G): Reranking activado, US generation fine-tuned,
#         web search, chat export, file generation improvements
# v7.2.7 (Fase E): Refactoring completo, eval suite
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
CHAT_DEPLOYMENT = _get_env("CHAT_DEPLOYMENT", "gpt-4-1-dz")
EMBEDDING_DEPLOYMENT = _get_env("EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
API_VERSION_CHAT = _get_env("API_VERSION_CHAT", "2024-10-21")
API_VERSION_OPENAI = _get_env("API_VERSION_OPENAI", "2023-05-15")

# =============================================================================
# ANTHROPIC (Claude) — via API directa OU via Azure AI Foundry
# =============================================================================
# Se ANTHROPIC_FOUNDRY_RESOURCE estiver definido, usa Azure Foundry.
# Nesse caso, ANTHROPIC_API_KEY deve apontar para a key do recurso Azure AI Foundry,
# não para uma API key directa da Anthropic.
# Caso contrário, usa api.anthropic.com directamente.
# =============================================================================
ANTHROPIC_API_KEY = _get_env("ANTHROPIC_API_KEY", "")
ANTHROPIC_FOUNDRY_RESOURCE = _get_env("ANTHROPIC_FOUNDRY_RESOURCE", "")  # ex: "my-foundry-resource"

# URL base: Foundry se configurado, senão API directa
if ANTHROPIC_FOUNDRY_RESOURCE:
    ANTHROPIC_API_BASE = f"https://{ANTHROPIC_FOUNDRY_RESOURCE}.services.ai.azure.com/anthropic/v1/messages"
else:
    ANTHROPIC_API_BASE = _get_env("ANTHROPIC_API_BASE", "https://api.anthropic.com/v1/messages")

ANTHROPIC_MODEL_OPUS = _get_env("ANTHROPIC_MODEL_OPUS", "claude-opus-4-6")
ANTHROPIC_MODEL_SONNET = _get_env("ANTHROPIC_MODEL_SONNET", "claude-sonnet-4-6")
ANTHROPIC_MODEL_HAIKU = _get_env("ANTHROPIC_MODEL_HAIKU", "claude-haiku-4-5-20251001")


def _default_standard_tier() -> str:
    """Prefer Claude Sonnet when Anthropic/Foundry is configured."""
    if ANTHROPIC_FOUNDRY_RESOURCE or ANTHROPIC_API_KEY:
        return "anthropic:sonnet"
    return "azure_openai:gpt-5-mini-dz"


def _default_allowed_origins() -> str:
    return "https://millennium-ai-assistant.azurewebsites.net"

# =============================================================================
# LLM PROVIDER CONFIG
# =============================================================================
# Tiers: "fast" (barato/rápido), "standard" (default), "pro" (melhor qualidade)
LLM_DEFAULT_TIER = _get_env("LLM_DEFAULT_TIER", "standard")

# Mapping de tiers para providers+modelos
# Formato: "provider:deployment_name" — o provider resolve internamente
# DataZone deployments (*-dz): dados ficam na EU (Sweden Central) + Abuse Monitoring Opt-Out activo
# Standard prefere Claude Sonnet 4.6 quando Anthropic/Foundry estiver configurado.
# Pro usa Claude Opus 4.6 via Anthropic — PII Shield mascara antes de enviar.
LLM_TIER_FAST = _get_env("LLM_TIER_FAST", "azure_openai:gpt-4-1-dz")          # DataZone, gpt-4.1, 87 TPM
LLM_TIER_STANDARD = _get_env("LLM_TIER_STANDARD", _default_standard_tier())
LLM_TIER_PRO = _get_env("LLM_TIER_PRO", "anthropic:opus")                      # Claude Opus 4.6
LLM_TIER_VISION = _get_env("LLM_TIER_VISION", "azure_openai:gpt-4-1-dz")      # DataZone, multimodal
VISION_ENABLED = _get_env("VISION_ENABLED", "true").lower() == "true"

LEGACY_FAST_DEPLOYMENT = _get_env("FAST_DEPLOYMENT", "")
if LEGACY_FAST_DEPLOYMENT:
    logger.warning(
        "[Config] FAST_DEPLOYMENT=%s está obsoleto e é ignorado. Usa LLM_TIER_FAST.",
        LEGACY_FAST_DEPLOYMENT,
    )

# PII Shield (Azure AI Language)
PII_ENDPOINT = _get_env("PII_ENDPOINT", "")
PII_API_KEY = _get_env("PII_API_KEY", "")
PII_ENABLED = _get_env("PII_ENABLED", "true").lower() == "true"

# Prompt Shield (Azure AI Content Safety)
CONTENT_SAFETY_ENDPOINT = _get_env("CONTENT_SAFETY_ENDPOINT", "")
CONTENT_SAFETY_KEY = _get_env("CONTENT_SAFETY_KEY", "")
PROMPT_SHIELD_ENABLED = _get_env("PROMPT_SHIELD_ENABLED", "true").lower() == "true"

# Document Intelligence (Azure AI Document Intelligence / Form Recognizer)
DOC_INTEL_ENDPOINT = _get_env("DOC_INTEL_ENDPOINT", "")
DOC_INTEL_KEY = _get_env("DOC_INTEL_KEY", "")
DOC_INTEL_ENABLED = _get_env("DOC_INTEL_ENABLED", "true").lower() == "true"
DOC_INTEL_MODEL = _get_env("DOC_INTEL_MODEL", "prebuilt-layout")

# Fallback provider (se o primário falhar) — DataZone para máxima segurança
LLM_FALLBACK = _get_env("LLM_FALLBACK", "azure_openai:gpt-4-1-mini-dz")

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
RERANK_MODEL = _get_env("RERANK_MODEL", "cohere-rerank-v4-fast")
RERANK_TOP_N = int(_get_env("RERANK_TOP_N", "15"))
RERANK_TIMEOUT_SECONDS = float(_get_env("RERANK_TIMEOUT_SECONDS", "15"))
RERANK_AUTH_MODE = _get_env("RERANK_AUTH_MODE", "api-key").lower()

# =============================================================================
# WEB SEARCH (Brave Search API)
# =============================================================================
WEB_SEARCH_ENABLED = _get_env("WEB_SEARCH_ENABLED", "false").lower() == "true"
WEB_SEARCH_API_KEY = _get_env("WEB_SEARCH_API_KEY", "")
WEB_SEARCH_ENDPOINT = _get_env("WEB_SEARCH_ENDPOINT", "https://api.search.brave.com/res/v1/web/search")
WEB_SEARCH_MAX_RESULTS = int(_get_env("WEB_SEARCH_MAX_RESULTS", "5"))
WEB_SEARCH_MARKET = _get_env("WEB_SEARCH_MARKET", "pt-PT")
WEB_ANSWERS_ENABLED = _get_env("WEB_ANSWERS_ENABLED", "false").lower() == "true"
WEB_ANSWERS_API_KEY = _get_env("WEB_ANSWERS_API_KEY", "")
WEB_ANSWERS_ENDPOINT = _get_env("WEB_ANSWERS_ENDPOINT", "https://api.search.brave.com/res/v1/chat/completions")
WEB_ANSWERS_MODEL = _get_env("WEB_ANSWERS_MODEL", "brave")
WEB_ANSWERS_TIMEOUT_SECONDS = float(_get_env("WEB_ANSWERS_TIMEOUT_SECONDS", "20"))
WEB_SEARCH_DAILY_QUOTA_PER_USER = int(_get_env("WEB_SEARCH_DAILY_QUOTA_PER_USER", "50"))

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
ADMIN_USERNAME = _get_env("ADMIN_USERNAME", "pedro.mousinho")
ADMIN_DISPLAY_NAME = _get_env("ADMIN_DISPLAY_NAME", "Pedro Mousinho")
AUTH_COOKIE_NAME = _get_env("AUTH_COOKIE_NAME", "dbde_token")
_jwt_secret_previous_env = _get_env("JWT_SECRET_PREVIOUS", "")
JWT_SECRET_PREVIOUS = _jwt_secret_previous_env if _jwt_secret_previous_env else None
AUTH_COOKIE_SECURE = _get_env("AUTH_COOKIE_SECURE", "true").lower() == "true"
AUTH_COOKIE_MAX_AGE_SECONDS = int(_get_env("AUTH_COOKIE_MAX_AGE_SECONDS", "86400"))
ALLOWED_ORIGINS = _get_env(
    "ALLOWED_ORIGINS",
    _default_allowed_origins(),
)

# =============================================================================
# AGENT CONFIG
# =============================================================================
AGENT_MAX_ITERATIONS = int(_get_env("AGENT_MAX_ITERATIONS", "7"))
AGENT_MAX_TOKENS = int(_get_env("AGENT_MAX_TOKENS", "8000"))
AGENT_TEMPERATURE = float(_get_env("AGENT_TEMPERATURE", "0.3"))
AGENT_HISTORY_LIMIT = int(_get_env("AGENT_HISTORY_LIMIT", "14"))
AGENT_TOOL_RESULT_MAX_SIZE = int(_get_env("AGENT_TOOL_RESULT_MAX_SIZE", "30000"))
AGENT_TOOL_RESULT_KEEP_ITEMS = int(_get_env("AGENT_TOOL_RESULT_KEEP_ITEMS", "100"))
CHAT_BUDGET_PER_MINUTE = int(_get_env("CHAT_BUDGET_PER_MINUTE", "10"))

# =============================================================================
# CODE INTERPRETER CONFIG
# =============================================================================
CODE_INTERPRETER_TIMEOUT = int(_get_env("CODE_INTERPRETER_TIMEOUT", "240"))
CODE_INTERPRETER_MAX_OUTPUT = int(_get_env("CODE_INTERPRETER_MAX_OUTPUT", "50000"))
CODE_INTERPRETER_ENABLED = _get_env("CODE_INTERPRETER_ENABLED", "true").lower() == "true"
CODE_INTERPRETER_MAX_INPUT_FILE_BYTES = int(_get_env("CODE_INTERPRETER_MAX_INPUT_FILE_BYTES", "75000000"))
CODE_INTERPRETER_MAX_MOUNT_BYTES = int(_get_env("CODE_INTERPRETER_MAX_MOUNT_BYTES", "150000000"))

# =============================================================================
# UPLOAD CONFIG
# =============================================================================
UPLOAD_MAX_FILES_PER_CONVERSATION = int(_get_env("UPLOAD_MAX_FILES_PER_CONVERSATION", "10"))
UPLOAD_MAX_IMAGES_PER_MESSAGE = int(_get_env("UPLOAD_MAX_IMAGES_PER_MESSAGE", "10"))
UPLOAD_MAX_FILE_MB = int(_get_env("UPLOAD_MAX_FILE_MB", "10"))
UPLOAD_MAX_FILE_BYTES = UPLOAD_MAX_FILE_MB * 1024 * 1024
UPLOAD_MAX_FILE_MB_CSV = int(_get_env("UPLOAD_MAX_FILE_MB_CSV", "40"))
UPLOAD_MAX_FILE_MB_TSV = int(_get_env("UPLOAD_MAX_FILE_MB_TSV", str(UPLOAD_MAX_FILE_MB_CSV)))
UPLOAD_MAX_FILE_MB_XLSX = int(_get_env("UPLOAD_MAX_FILE_MB_XLSX", "25"))
UPLOAD_MAX_FILE_MB_XLSB = int(_get_env("UPLOAD_MAX_FILE_MB_XLSB", "25"))
UPLOAD_MAX_FILE_MB_XLS = int(_get_env("UPLOAD_MAX_FILE_MB_XLS", "15"))
UPLOAD_MAX_FILE_BYTES_CSV = UPLOAD_MAX_FILE_MB_CSV * 1024 * 1024
UPLOAD_MAX_FILE_BYTES_TSV = UPLOAD_MAX_FILE_MB_TSV * 1024 * 1024
UPLOAD_MAX_FILE_BYTES_XLSX = UPLOAD_MAX_FILE_MB_XLSX * 1024 * 1024
UPLOAD_MAX_FILE_BYTES_XLSB = UPLOAD_MAX_FILE_MB_XLSB * 1024 * 1024
UPLOAD_MAX_FILE_BYTES_XLS = UPLOAD_MAX_FILE_MB_XLS * 1024 * 1024
UPLOAD_MAX_CONCURRENT_JOBS = int(_get_env("UPLOAD_MAX_CONCURRENT_JOBS", "2"))
UPLOAD_MAX_PENDING_JOBS_PER_USER = int(_get_env("UPLOAD_MAX_PENDING_JOBS_PER_USER", "20"))
UPLOAD_EMBEDDING_CONCURRENCY = int(_get_env("UPLOAD_EMBEDDING_CONCURRENCY", "3"))
UPLOAD_MAX_CHUNKS_PER_FILE = int(_get_env("UPLOAD_MAX_CHUNKS_PER_FILE", "300"))
UPLOAD_JOB_STALE_SECONDS = int(_get_env("UPLOAD_JOB_STALE_SECONDS", "900"))
UPLOAD_MAX_BATCH_TOTAL_MB = int(_get_env("UPLOAD_MAX_BATCH_TOTAL_MB", "100"))
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
EXPORT_FILE_ROW_CAP = int(_get_env("EXPORT_FILE_ROW_CAP", "5000"))
EXPORT_FILE_ROW_CAP_MAX = int(_get_env("EXPORT_FILE_ROW_CAP_MAX", "100000"))
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
STARTUP_FAIL_FAST = _get_env("STARTUP_FAIL_FAST", "true").lower() == "true"

# =============================================================================
# TOKEN QUOTAS (per tier, hourly/daily)
# =============================================================================
# Format: "hourly,daily" — 0 means unlimited
_QUOTA_FAST = _get_env("TOKEN_QUOTA_FAST", "500000,5000000")
_QUOTA_STANDARD = _get_env("TOKEN_QUOTA_STANDARD", "200000,2000000")
_QUOTA_PRO = _get_env("TOKEN_QUOTA_PRO", "100000,1000000")


def _parse_quota(raw: str) -> dict:
    parts = str(raw or "").split(",")

    def _to_int(idx: int) -> int:
        if idx >= len(parts):
            return 0
        txt = str(parts[idx] or "").strip()
        if not txt:
            return 0
        try:
            return max(0, int(txt))
        except Exception:
            return 0

    return {"hourly": _to_int(0), "daily": _to_int(1)}


TOKEN_QUOTA_CONFIG = {
    "fast": _parse_quota(_QUOTA_FAST),
    "standard": _parse_quota(_QUOTA_STANDARD),
    "pro": _parse_quota(_QUOTA_PRO),
}

DEBUG_LOG_SIZE = int(_get_env("DEBUG_LOG_SIZE", "50"))
DEBUG_MODE = _get_env("DEBUG_MODE", "false").lower() == "true"
LOG_FORMAT = _get_env("LOG_FORMAT", "json").lower()  # "json" para produção, "text" para dev

# =============================================================================
# APP METADATA
# =============================================================================
APP_VERSION = "7.3.0"
APP_TITLE = "Millennium BCP AI Agent"
APP_DESCRIPTION = "Agente IA multi-modelo com streaming, exports e integração DevOps"
