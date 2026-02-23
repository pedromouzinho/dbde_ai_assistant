# =============================================================================
# config.py — Configuração centralizada do Assistente AI DBDE v7.0
# =============================================================================
# Todas as variáveis de ambiente, constantes e configurações num único local.
# Nenhum outro ficheiro faz os.getenv() — tudo passa por aqui.
# =============================================================================

import os

# =============================================================================
# AZURE OPENAI
# =============================================================================
AZURE_OPENAI_ENDPOINT = os.getenv(
    "AZURE_OPENAI_ENDPOINT",
    "https://dbdeaccess.openai.azure.com"
)
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY", "")
CHAT_DEPLOYMENT = os.getenv("CHAT_DEPLOYMENT", "dbde_access_chatbot")
EMBEDDING_DEPLOYMENT = os.getenv("EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
API_VERSION_CHAT = os.getenv("API_VERSION_CHAT", "2024-02-15-preview")
API_VERSION_OPENAI = os.getenv("API_VERSION_OPENAI", "2023-05-15")

# =============================================================================
# ANTHROPIC (Claude) — via API directa OU via Azure AI Foundry
# =============================================================================
# Se ANTHROPIC_FOUNDRY_RESOURCE estiver definido, usa Azure Foundry.
# Caso contrário, usa api.anthropic.com directamente.
# =============================================================================
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_FOUNDRY_RESOURCE = os.getenv("ANTHROPIC_FOUNDRY_RESOURCE", "")  # ex: "my-foundry-resource"

# URL base: Foundry se configurado, senão API directa
if ANTHROPIC_FOUNDRY_RESOURCE:
    ANTHROPIC_API_BASE = f"https://{ANTHROPIC_FOUNDRY_RESOURCE}.services.ai.azure.com/anthropic/v1/messages"
else:
    ANTHROPIC_API_BASE = os.getenv("ANTHROPIC_API_BASE", "https://api.anthropic.com/v1/messages")

ANTHROPIC_MODEL_OPUS = os.getenv("ANTHROPIC_MODEL_OPUS", "claude-opus-4-6")
ANTHROPIC_MODEL_SONNET = os.getenv("ANTHROPIC_MODEL_SONNET", "claude-sonnet-4-6")
ANTHROPIC_MODEL_HAIKU = os.getenv("ANTHROPIC_MODEL_HAIKU", "claude-haiku-4-5")

# =============================================================================
# LLM PROVIDER CONFIG
# =============================================================================
# Tiers: "fast" (barato/rápido), "standard" (default), "pro" (melhor qualidade)
LLM_DEFAULT_TIER = os.getenv("LLM_DEFAULT_TIER", "standard")

# Mapping de tiers para providers+modelos
# Formato: "provider:model" — o provider resolve internamente
LLM_TIER_FAST = os.getenv("LLM_TIER_FAST", "azure_openai:gpt-4.1-mini")
LLM_TIER_STANDARD = os.getenv("LLM_TIER_STANDARD", "anthropic:claude-sonnet-4-6")
LLM_TIER_PRO = os.getenv("LLM_TIER_PRO", "anthropic:claude-opus-4-6")

# Fallback provider (se o primário falhar)
LLM_FALLBACK = os.getenv("LLM_FALLBACK", "azure_openai:dbde_access_chatbot")

# =============================================================================
# AZURE AI SEARCH
# =============================================================================
SEARCH_SERVICE = os.getenv("SEARCH_SERVICE", "dbdeacessrag")
SEARCH_KEY = os.getenv("SEARCH_KEY", "")
API_VERSION_SEARCH = os.getenv("API_VERSION_SEARCH", "2023-11-01")

DEVOPS_INDEX = os.getenv("DEVOPS_INDEX", "millennium-devops-index")
OMNI_INDEX = os.getenv("OMNI_INDEX", "millennium-omni-index")
EXAMPLES_INDEX = os.getenv("EXAMPLES_INDEX", "millennium-examples-index")

TOP_K = int(os.getenv("TOP_K", "10"))

# =============================================================================
# AZURE DEVOPS
# =============================================================================
DEVOPS_PAT = os.getenv("DEVOPS_PAT", "")
DEVOPS_ORG = os.getenv("DEVOPS_ORG", "ptbcp")
DEVOPS_PROJECT = os.getenv("DEVOPS_PROJECT", "IT.DIT")

# =============================================================================
# AZURE TABLE STORAGE
# =============================================================================
STORAGE_CONNECTION_STRING = os.getenv("STORAGE_CONNECTION_STRING", "")
STORAGE_ACCOUNT = os.getenv("STORAGE_ACCOUNT", "dbdeaccessstorage")
STORAGE_KEY = os.getenv("STORAGE_KEY", "")

# =============================================================================
# AUTH
# =============================================================================
JWT_SECRET = os.getenv("JWT_SECRET", "mbcp-ai-assistant-jwt-secret-2026-change-in-production")
JWT_EXPIRATION_HOURS = int(os.getenv("JWT_EXPIRATION_HOURS", "10"))

# =============================================================================
# AGENT CONFIG
# =============================================================================
AGENT_MAX_ITERATIONS = int(os.getenv("AGENT_MAX_ITERATIONS", "5"))
AGENT_MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "8000"))
AGENT_TEMPERATURE = float(os.getenv("AGENT_TEMPERATURE", "0.3"))
AGENT_HISTORY_LIMIT = int(os.getenv("AGENT_HISTORY_LIMIT", "14"))
AGENT_TOOL_RESULT_MAX_SIZE = int(os.getenv("AGENT_TOOL_RESULT_MAX_SIZE", "30000"))
AGENT_TOOL_RESULT_KEEP_ITEMS = int(os.getenv("AGENT_TOOL_RESULT_KEEP_ITEMS", "100"))

# =============================================================================
# EXPORT CONFIG
# =============================================================================
EXPORT_BRAND_COLOR = "#CC0033"  # Millennium BCP vermelho
EXPORT_BRAND_NAME = "Millennium BCP"
EXPORT_AGENT_NAME = "Assistente AI DBDE"

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
DEBUG_LOG_SIZE = int(os.getenv("DEBUG_LOG_SIZE", "50"))
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"

# =============================================================================
# APP METADATA
# =============================================================================
APP_VERSION = "7.0.5"
APP_TITLE = "Millennium BCP AI Agent"
APP_DESCRIPTION = "Agente IA multi-modelo com streaming, exports e integração DevOps"
