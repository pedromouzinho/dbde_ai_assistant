# =============================================================================
# models.py — Modelos Pydantic do Assistente AI DBDE v7.2
# =============================================================================
# Todos os request/response models centralizados.
# =============================================================================

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# =============================================================================
# AGENT (principal)
# =============================================================================

class ChatImageInput(BaseModel):
    base64: str
    content_type: Optional[str] = "image/png"
    filename: Optional[str] = None


class AgentChatRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    question: str
    conversation_id: Optional[str] = None
    image_base64: Optional[str] = None
    image_content_type: Optional[str] = "image/png"
    images: Optional[List[ChatImageInput]] = None
    mode: Optional[str] = "general"         # "general" | "userstory"
    model_tier: Optional[str] = None        # v7.0: "fast" | "standard" | "pro" | None (usa default)


class AgentChatResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    answer: str
    conversation_id: str
    tools_used: List[str] = Field(default_factory=list)
    tool_details: List[Dict[str, Any]] = Field(default_factory=list)
    tokens_used: Dict = Field(default_factory=dict)
    total_time_ms: int = 0
    model_used: str = ""
    mode: str = "general"
    has_exportable_data: bool = False        # v7.0: sinaliza ao frontend que há dados exportáveis
    export_index: Optional[int] = None      # v7.0: índice da tool call com dados exportáveis


# =============================================================================
# STREAMING EVENTS (SSE)
# =============================================================================

class StreamEvent(BaseModel):
    """Evento SSE enviado ao frontend durante streaming."""
    type: str           # "thinking" | "tool_start" | "tool_result" | "token" | "done" | "error"
    text: Optional[str] = None
    tool: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


# =============================================================================
# AUTH
# =============================================================================

class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    display_name: str
    role: str = "user"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# =============================================================================
# MODE SWITCHING
# =============================================================================

class ModeSwitchRequest(BaseModel):
    conversation_id: str
    mode: str  # "general" | "userstory"


class ModeSwitchResponse(BaseModel):
    success: bool
    mode: str
    conversation_id: str
    message: str = ""


# =============================================================================
# CHAT PERSISTENCE
# =============================================================================

class SaveChatRequest(BaseModel):
    user_id: str
    conversation_id: str
    title: str = ""
    messages: list = Field(default_factory=list)


# =============================================================================
# FEEDBACK & LEARNING
# =============================================================================

class FeedbackRequest(BaseModel):
    conversation_id: str
    message_index: int
    rating: int = Field(ge=1, le=10)
    note: Optional[str] = None


class RuleRequest(BaseModel):
    category: str
    rule_text: str
    source: str = "manual"


# =============================================================================
# LEGACY (retrocompatibilidade com /chat endpoint antigo)
# =============================================================================

class ChatRequest(BaseModel):
    question: str
    index: str = "devops"


class Source(BaseModel):
    id: str
    title: str
    status: str = ""
    url: str = ""
    score: float = 0.0


class ChatResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    answer: str
    sources: List[Source] = Field(default_factory=list)
    tokens_used: Dict = Field(default_factory=dict)
    search_time_ms: int = 0
    total_time_ms: int = 0
    index_used: str = ""
    model_used: str = ""


# =============================================================================
# LLM PROVIDER (interno)
# =============================================================================

class LLMToolCall(BaseModel):
    """Formato normalizado de tool call — independente do provider."""
    id: str
    name: str
    arguments: Dict[str, Any]


class LLMResponse(BaseModel):
    """Formato normalizado de resposta LLM — independente do provider."""
    content: Optional[str] = None
    tool_calls: Optional[List[LLMToolCall]] = None
    usage: Dict[str, int] = Field(default_factory=dict)
    model: str = ""
    provider: str = ""


# =============================================================================
# EXPORT
# =============================================================================

class ExportRequest(BaseModel):
    conversation_id: Optional[str] = None
    tool_call_index: int = -1       # -1 = última tool call com dados
    format: str = "xlsx"            # "csv" | "xlsx" | "pdf" | "svg" | "html" | "zip"
    chart_type: Optional[str] = None  # Para SVG: "bar" | "pie" | "sankey"
    title: Optional[str] = None
    summary: Optional[str] = None
    data: Optional[dict] = None      # v7.0.1: allow direct data from frontend
    result_blob_ref: Optional[str] = None  # Preferir payload persistido completo quando disponível
    prefer_async: bool = True
