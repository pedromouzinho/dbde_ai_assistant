# =============================================================================
# agent.py — Agent loop com streaming SSE v7.0
# =============================================================================
# Suporta 2 modos de execução:
# 1. agent_chat() — request/response clássico (retorna AgentChatResponse)
# 2. agent_chat_stream() — SSE streaming (yield StreamEvents)
# =============================================================================

import json
import uuid
import asyncio
import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Dict, List, Optional, AsyncGenerator, Callable, Iterator
from collections.abc import MutableMapping

from config import (
    AGENT_MAX_ITERATIONS, AGENT_MAX_TOKENS, AGENT_TEMPERATURE,
    AGENT_HISTORY_LIMIT, LLM_DEFAULT_TIER, UPLOAD_MAX_IMAGES_PER_MESSAGE,
    UPLOAD_INDEX_TOP, DEVOPS_AREAS, CHAT_TOOLRESULT_BLOB_CONTAINER,
)
from models import (
    AgentChatRequest, AgentChatResponse, LLMToolCall,
)
from llm_provider import (
    get_provider, llm_with_fallback,
    make_assistant_message_from_response,
)
from learning import get_learned_rules, get_few_shot_examples
from tool_registry import execute_tool, get_all_tool_definitions
from tools import (
    truncate_tool_result,
    get_agent_system_prompt, get_userstory_system_prompt,
)
from storage import (
    table_insert,
    table_merge,
    table_query,
    blob_download_bytes,
    blob_upload_json,
    parse_blob_ref,
)
from utils import odata_escape, safe_blob_component, create_logged_task

# =============================================================================
# IN-MEMORY STORES (migra para persistent storage em fase futura)
# =============================================================================
MAX_CONVERSATIONS = 200
CONVERSATION_TTL_SECONDS = 4 * 3600


class ConversationStore(MutableMapping[str, List[dict]]):
    """Store em memória com TTL + eviction LRU."""

    def __init__(
        self,
        max_conversations: int,
        ttl_seconds: int,
        on_evict: Optional[Callable[[str], None]] = None,
    ):
        self._data: Dict[str, List[dict]] = {}
        self._last_accessed: Dict[str, datetime] = {}
        self.max_conversations = max_conversations
        self.ttl_seconds = ttl_seconds
        self._on_evict = on_evict

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    def _touch(self, key: str) -> None:
        self._last_accessed[key] = self._utcnow()

    def _evict(self, key: str, reason: str) -> None:
        self._data.pop(key, None)
        self._last_accessed.pop(key, None)
        if self._on_evict:
            self._on_evict(key)
        logging.getLogger(__name__).info("[ConversationStore] Evicted %s (%s)", key, reason)

    def _is_expired(self, key: str, now: datetime) -> bool:
        last = self._last_accessed.get(key)
        if not last:
            return False
        return (now - last).total_seconds() > self.ttl_seconds

    def cleanup_expired(self) -> List[str]:
        now = self._utcnow()
        expired = [k for k in list(self._data.keys()) if self._is_expired(k, now)]
        for key in expired:
            self._evict(key, reason="ttl")
        return expired

    def _evict_lru(self, exclude_key: Optional[str] = None) -> Optional[str]:
        candidates = [
            (ts, key)
            for key, ts in self._last_accessed.items()
            if key in self._data and key != exclude_key
        ]
        if not candidates:
            return None
        _, oldest_key = min(candidates, key=lambda item: item[0])
        self._evict(oldest_key, reason="lru")
        return oldest_key

    def ensure_capacity_for_new(self, new_key: str) -> None:
        self.cleanup_expired()
        while len(self._data) >= self.max_conversations:
            if self._evict_lru(exclude_key=new_key) is None:
                break

    def touch(self, key: str) -> None:
        if key in self._data:
            self._touch(key)

    def __getitem__(self, key: str) -> List[dict]:
        value = self._data[key]
        self._touch(key)
        return value

    def __setitem__(self, key: str, value: List[dict]) -> None:
        if key not in self._data:
            self.ensure_capacity_for_new(new_key=key)
        self._data[key] = value
        self._touch(key)

    def __delitem__(self, key: str) -> None:
        if key not in self._data:
            raise KeyError(key)
        self._evict(key, reason="manual")

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def get(self, key: str, default=None):
        if key in self._data:
            self._touch(key)
            return self._data[key]
        return default

    def items(self):
        return self._data.items()

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()


conversation_meta: Dict[str, Dict] = {}
uploaded_files_store: Dict[str, Dict] = {}
_conversation_locks: Dict[str, asyncio.Lock] = {}


def _cleanup_conversation_related_state(conv_id: str) -> None:
    conversation_meta.pop(conv_id, None)
    uploaded_files_store.pop(conv_id, None)
    _conversation_locks.pop(conv_id, None)


conversations = ConversationStore(
    max_conversations=MAX_CONVERSATIONS,
    ttl_seconds=CONVERSATION_TTL_SECONDS,
    on_evict=_cleanup_conversation_related_state,
)
logger = logging.getLogger(__name__)


def _user_partition_key(user: Optional[dict]) -> str:
    raw = (user or {}).get("sub") or (user or {}).get("username") or "anon"
    # Azure Table key hygiene: avoid problematic characters for filters/URLs.
    safe = "".join(c if c.isalnum() or c in "._-@" else "_" for c in str(raw))
    return (safe or "anon")[:100]


def _get_conversation_lock(conv_id: str) -> asyncio.Lock:
    return _conversation_locks.setdefault(conv_id, asyncio.Lock())

# =============================================================================
# HISTORY MANAGEMENT
# =============================================================================

def _trim_history(messages: List[dict]) -> List[dict]:
    """Gere histórico: mantém system msgs + últimas N non-system msgs."""
    if len(messages) <= 20:
        return messages
    
    sys_msgs = [m for m in messages if m.get("role") == "system"]
    other = [m for m in messages if m.get("role") != "system"]
    kept = other[-AGENT_HISTORY_LIMIT:]
    
    # Comprimir tool results antigos
    for i, msg in enumerate(kept):
        if msg.get("role") == "tool" and len(msg.get("content", "")) > 500:
            try:
                data = json.loads(msg["content"])
                summary = {"total_count": data.get("total_count", "?"), "items_returned": len(data.get("items", [])), "_compressed": True}
                kept[i] = {**msg, "content": json.dumps(summary, ensure_ascii=False)}
            except (json.JSONDecodeError, TypeError):
                kept[i] = {**msg, "content": msg["content"][:200] + "...(truncado)"}
    
    return sys_msgs + kept


MAX_IMAGES_PER_INPUT = max(1, int(UPLOAD_MAX_IMAGES_PER_MESSAGE))
MAX_FILES_CONTEXT = 10


def _normalize_uploaded_files_entry(conv_id: str) -> dict:
    current = uploaded_files_store.get(conv_id)
    if isinstance(current, dict) and isinstance(current.get("files"), list):
        return current
    if isinstance(current, dict) and current:
        legacy = dict(current)
        legacy.pop("files", None)
        normalized = {"files": [legacy]}
        uploaded_files_store[conv_id] = normalized
        return normalized
    return {"files": []}


def _get_uploaded_files(conv_id: str) -> List[dict]:
    return _normalize_uploaded_files_entry(conv_id).get("files", [])


async def _ensure_uploaded_files_loaded(conv_id: str, user_sub: str = "") -> None:
    current_files = _get_uploaded_files(conv_id)
    if current_files:
        return
    try:
        safe_conv = odata_escape(conv_id)
        rows = await table_query("UploadIndex", f"PartitionKey eq '{safe_conv}'", top=max(1, min(UPLOAD_INDEX_TOP, 500)))
    except Exception as e:
        logger.warning("[Agent] upload index query failed for %s: %s", conv_id, e)
        return
    if not rows:
        return

    safe_user = str(user_sub or "")
    filtered = []
    for row in rows:
        owner_sub = str(row.get("UserSub", "") or "")
        if owner_sub and safe_user and owner_sub != safe_user:
            continue
        filtered.append(row)
    if not filtered:
        return

    files = []
    for row in sorted(filtered, key=lambda r: str(r.get("UploadedAt", ""))):
        preview_text = str(row.get("PreviewText", "") or "")
        extracted_ref = str(row.get("ExtractedBlobRef", "") or "")
        if not preview_text and extracted_ref:
            container, blob_name = parse_blob_ref(extracted_ref)
            if container and blob_name:
                try:
                    raw = await blob_download_bytes(container, blob_name)
                    if raw:
                        preview_text = raw.decode("utf-8", errors="replace")[:16000]
                except Exception as e:
                    logger.warning("[Agent] extracted blob read failed for %s: %s", extracted_ref, e)
        try:
            col_names = json.loads(row.get("ColNamesJson", "[]") or "[]")
        except Exception:
            col_names = []
        try:
            col_analysis = json.loads(row.get("ColAnalysisJson", "[]") or "[]")
        except Exception:
            col_analysis = []
        files.append(
            {
                "filename": row.get("Filename", ""),
                "data_text": preview_text,
                "row_count": int(row.get("RowCount", 0) or 0),
                "col_names": col_names if isinstance(col_names, list) else [],
                "col_analysis": col_analysis if isinstance(col_analysis, list) else [],
                "truncated": bool(row.get("Truncated", False)),
                "uploaded_at": row.get("UploadedAt", ""),
                "has_chunks": str(row.get("HasChunks", "")).lower() in ("true", "1"),
                "chunks_blob_ref": row.get("ChunksBlobRef", ""),
                "extracted_blob_ref": extracted_ref,
            }
        )
    if files:
        uploaded_files_store[conv_id] = {
            "files": files[-MAX_FILES_CONTEXT:],
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "from_index": True,
        }


def _extract_request_images(request: AgentChatRequest) -> List[dict]:
    """Extrai até MAX_IMAGES_PER_INPUT imagens do request (novo campo images + fallback legacy)."""
    extracted: List[dict] = []
    raw_images = getattr(request, "images", None) or []
    for raw in raw_images[:MAX_IMAGES_PER_INPUT]:
        if isinstance(raw, dict):
            base64_data = raw.get("base64")
            content_type = raw.get("content_type") or raw.get("contentType")
            filename = raw.get("filename")
        else:
            base64_data = getattr(raw, "base64", None)
            content_type = getattr(raw, "content_type", None) or getattr(raw, "contentType", None)
            filename = getattr(raw, "filename", None)
        b64 = str(base64_data or "").strip()
        if not b64:
            continue
        extracted.append(
            {
                "base64": b64,
                "content_type": str(content_type or "image/png"),
                "filename": str(filename or "")[:120],
            }
        )

    if extracted:
        return extracted

    legacy_b64 = str(getattr(request, "image_base64", "") or "").strip()
    if legacy_b64:
        extracted.append(
            {
                "base64": legacy_b64,
                "content_type": str(getattr(request, "image_content_type", "image/png") or "image/png"),
                "filename": "",
            }
        )
    return extracted


def _build_user_message(request: AgentChatRequest, conv_id: Optional[str] = None) -> dict:
    """Constrói a mensagem do user (texto puro ou multimodal com 1..MAX_IMAGES_PER_INPUT imagens)."""
    images = _extract_request_images(request)

    # Fallback: usar imagem previamente carregada via /upload nesta conversa.
    if not images and conv_id:
        uploaded_images = []
        for file_data in _get_uploaded_files(conv_id):
            upload_b64 = str(file_data.get("image_base64", "") or "").strip()
            if not upload_b64:
                continue
            uploaded_images.append(
                {
                    "base64": upload_b64,
                    "content_type": str(file_data.get("image_content_type") or "image/png"),
                    "filename": str(file_data.get("filename") or "")[:120],
                }
            )
        images = uploaded_images[:MAX_IMAGES_PER_INPUT]

    if not images:
        return {"role": "user", "content": request.question}

    content_blocks: List[dict] = [{"type": "text", "text": request.question}]
    if len(images) == 2:
        content_blocks.append(
            {"type": "text", "text": "Contexto visual: Imagem 1 = ANTES; Imagem 2 = DEPOIS."}
        )
    for idx, img in enumerate(images, 1):
        if img.get("filename"):
            content_blocks.append({"type": "text", "text": f"Imagem {idx}: {img['filename']}"})
        content_blocks.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{img['content_type']};base64,{img['base64']}",
                },
            }
        )

    return {"role": "user", "content": content_blocks}


def _inject_file_context(conv_id: str, messages: List[dict]):
    """Injeta contexto de ficheiros uploaded na conversa."""
    files = _get_uploaded_files(conv_id)
    if files and not conversation_meta.get(conv_id, {}).get("file_injected"):
        messages[:] = [
            m for m in messages
            if not (
                m.get("role") == "system"
                and isinstance(m.get("content"), str)
                and (
                    m.get("content", "").startswith("FICHEIROS CARREGADOS:")
                    or m.get("content", "").startswith("FICHEIRO CARREGADO:")
                )
            )
        ]
        mode = conversation_meta.get(conv_id, {}).get("mode", "general")
        use_files = files[-MAX_FILES_CONTEXT:]
        ctx_parts = [
            f"FICHEIROS CARREGADOS: {len(use_files)} (máximo analisado por pedido: {MAX_FILES_CONTEXT})",
            "Analisa TODOS os ficheiros listados antes de responder. Não ignores nenhum.",
        ]

        total_budget = 90000
        per_file_budget = max(3000, min(18000, total_budget // max(1, len(use_files))))

        if mode == "userstory":
            ctx_parts.append(
                "MODO USERSTORY — integra requisitos de todos os anexos numa proposta coerente e sem duplicações."
            )

        for idx, file_data in enumerate(use_files, 1):
            filename = str(file_data.get("filename", ""))
            filename_lower = filename.lower()
            is_tabular = filename_lower.endswith((".xlsx", ".xls", ".csv"))
            is_pdf = filename_lower.endswith(".pdf")
            is_pptx = filename_lower.endswith(".pptx")
            is_image = bool(file_data.get("image_base64"))
            cols = file_data.get("col_names", [])
            cols_txt = ", ".join(cols) if isinstance(cols, list) else str(cols or "")

            block = [
                f"[FICHEIRO {idx}] {filename}",
                f"Linhas: {file_data.get('row_count', 0)} | Colunas: {cols_txt}",
            ]
            if file_data.get("truncated"):
                block.append("NOTA: Conteúdo truncado para contexto.")

            col_analysis = file_data.get("col_analysis")
            if col_analysis:
                block.append("ANÁLISE DE COLUNAS:")
                for ca in col_analysis[:20]:
                    sample = ", ".join((ca.get("sample") or [])[:3])
                    block.append(f"- {ca.get('name','')} ({ca.get('type','text')}): {sample}")

            if mode == "userstory":
                if is_tabular:
                    block.append("Interpretar como lista estruturada de requisitos funcionais.")
                elif is_pdf:
                    block.append("Interpretar secções como hierarquia Épico > Feature > US > AC.")
                elif is_pptx:
                    block.append("Interpretar cada slide como bloco de requisitos.")
                elif is_image:
                    block.append("Imagem de apoio visual: extrair CTAs, inputs, labels, estados e validações.")
                else:
                    block.append("Extrair requisitos acionáveis e testáveis.")

            data_text = str(file_data.get("data_text", "") or "")
            if data_text:
                block.append("CONTEÚDO:")
                block.append(data_text[:per_file_budget])

            if (isinstance(file_data.get("chunks"), list) and file_data.get("chunks")) or file_data.get("has_chunks"):
                block.append(
                    "NOTA: Documento grande com chunks semânticos disponíveis; usa search_uploaded_document para pesquisa profunda."
                )

            ctx_parts.append("\n".join(block))

        ctx = "\n\n".join(ctx_parts)[:120000]
        messages.append({"role": "system", "content": ctx})
        conversation_meta.setdefault(conv_id, {})["file_injected"] = True


async def _load_conversation_from_storage(conv_id: str, partition_key: str) -> bool:
    """Tenta carregar conversa do Table Storage. Retorna True se encontrou."""
    try:
        safe_pk = odata_escape(partition_key)
        safe_conv = odata_escape(conv_id)
        rows = await table_query(
            "ChatHistory",
            f"PartitionKey eq '{safe_pk}' and RowKey eq '{safe_conv}'",
            top=1,
        )
        if not rows:
            return False

        row = rows[0]
        messages_json = row.get("Messages", "[]")
        messages = json.loads(messages_json)

        if not messages:
            return False

        # Verificar que o system prompt é actual (pode ter mudado entre deploys)
        stored_mode = row.get("Mode", "general")
        current_sp = get_userstory_system_prompt() if stored_mode == "userstory" else get_agent_system_prompt()

        # Substituir system prompt armazenado pelo actual
        if messages and messages[0].get("role") == "system":
            messages[0] = {"role": "system", "content": current_sp}
        else:
            messages.insert(0, {"role": "system", "content": current_sp})

        conversations[conv_id] = messages
        conversation_meta[conv_id] = {
            "mode": stored_mode,
            "created_at": row.get("CreatedAt", datetime.now(timezone.utc).isoformat()),
            "loaded_from_storage": True,
        }

        logger.info("[Agent] Loaded conversation %s from storage (%d messages)", conv_id, len(messages))
        return True
    except Exception as e:
        logger.warning("[Agent] _load_conversation_from_storage failed for %s: %s", conv_id, e)
        return False


async def _ensure_conversation(conv_id: str, mode: str, partition_key: str) -> str:
    """Garante que a conversa existe — tenta lazy-load do Table Storage se não estiver em memória."""
    conversations.cleanup_expired()
    if conv_id not in conversations:
        # Tentar carregar do Table Storage
        loaded = await _load_conversation_from_storage(conv_id, partition_key)
        if not loaded:
            # Conversa nova
            sp = get_userstory_system_prompt() if mode == "userstory" else get_agent_system_prompt()
            conversations[conv_id] = [{"role": "system", "content": sp}]
            conversation_meta[conv_id] = {"mode": mode, "created_at": datetime.now(timezone.utc).isoformat()}
    else:
        conversations.touch(conv_id)
    return conv_id


async def _build_llm_messages(
    conv_id: str, question: str, request: Optional[AgentChatRequest] = None
) -> List[dict]:
    """Cópia efémera do histórico + regras + few-shot. Não muta conversations[]."""
    base = conversations[conv_id].copy()
    insert_pos = 1 if base else 0

    learned = await get_learned_rules()
    if learned:
        base.insert(insert_pos, {"role": "system", "content": learned})
        insert_pos += 1

    fewshot = await get_few_shot_examples(question)
    if fewshot:
        base.insert(insert_pos, {"role": "system", "content": fewshot})

    # Injeta conteúdo multimodal apenas na cópia efémera para evitar bloat no histórico real.
    if request:
        user_msg = _build_user_message(request, conv_id=conv_id)
        if isinstance(user_msg.get("content"), list):
            replaced = False
            for idx in range(len(base) - 1, -1, -1):
                msg = base[idx]
                if msg.get("role") != "user":
                    continue
                if isinstance(msg.get("content"), str) and msg.get("content") == request.question:
                    base[idx] = user_msg
                    replaced = True
                    break
            if not replaced:
                base.append(user_msg)

    return base


def _compact_message_for_storage(message: dict) -> dict:
    compact = dict(message)
    role = compact.get("role")
    content = compact.get("content", "")

    if role == "tool":
        if len(content) > 500:
            try:
                data = json.loads(content)
                summary = {
                    "total_count": data.get("total_count", "?"),
                    "items_returned": len(data.get("items", [])),
                    "_persisted_summary": True,
                }
                content = json.dumps(summary, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                content = str(content)[:500] + "...(truncado)"
        compact["content"] = content
        return compact

    if isinstance(content, str):
        compact["content"] = content[:8000] + ("...(truncado)" if len(content) > 8000 else "")
        return compact

    if isinstance(content, list):
        reduced = []
        for part in content[:10]:
            if not isinstance(part, dict):
                reduced.append(str(part)[:200])
                continue
            ptype = part.get("type")
            if ptype == "text":
                txt = str(part.get("text", ""))
                if len(txt) > 2000:
                    txt = txt[:2000] + "...(truncado)"
                reduced.append({"type": "text", "text": txt})
            elif ptype == "image_url":
                reduced.append({"type": "image_url", "image_url": {"url": "[base64_omitted]"}})
            else:
                reduced.append({"type": str(ptype or "unknown")})
        compact["content"] = reduced
        return compact

    if isinstance(content, dict):
        compact["content"] = json.dumps(content, ensure_ascii=False, default=str)[:2000]
        return compact

    compact["content"] = str(content)[:2000]
    return compact


def _has_explicit_create_confirmation(conv_id: str) -> bool:
    def _normalize(text: str) -> str:
        lowered = (text or "").lower()
        deaccented = unicodedata.normalize("NFKD", lowered)
        return "".join(ch for ch in deaccented if not unicodedata.combining(ch))

    approval_patterns = (
        r"\bconfirmo\b",
        r"\bconfirmado\b",
        r"\bsim\b",
        r"\bavanca\b",
        r"\bpodes\s+criar\b",
        r"\bpodes\s+avancar\b",
        r"\bclaro\b",
        r"\byep\b",
        r"\bok,\s*cria\b",
    )
    explicit_negative_patterns = (
        r"\bnao\s+confirmo\b",
        r"\bnao\s+avanc(?:a|ar|o|es)\b",
        r"\bnunca\s+confirmo\b",
        r"\bnunca\s+avanc(?:a|ar|o|es)\b",
    )
    negation_tokens = r"\b(nao|nunca|jamais)\b"

    for msg in reversed(conversations.get(conv_id, [])):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            txt = " ".join(
                str(p.get("text", ""))
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        else:
            txt = str(content)

        normalized = _normalize(txt)

        if any(re.search(p, normalized) for p in explicit_negative_patterns):
            return False

        for pattern in approval_patterns:
            for match in re.finditer(pattern, normalized):
                # Bloqueia aprovações com negação imediatamente antes (ex: "não confirmo")
                prefix = normalized[max(0, match.start() - 24):match.start()]
                if re.search(negation_tokens, prefix):
                    return False
                return True
        return False
    return False


def _normalize_request_text(value: str) -> str:
    lowered = str(value or "").lower()
    deaccented = unicodedata.normalize("NFKD", lowered)
    clean = "".join(ch for ch in deaccented if not unicodedata.combining(ch))
    clean = clean.replace("|", " ").replace("—", " ").replace("-", " ").replace("_", " ")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _canonicalize_area_path(area_hint: str) -> str:
    raw = str(area_hint or "").strip()
    if not raw:
        return ""
    if "\\" in raw:
        return raw
    norm_hint = _normalize_request_text(raw)
    for known in DEVOPS_AREAS:
        known_norm = _normalize_request_text(known)
        if norm_hint and (known_norm.endswith(norm_hint) or norm_hint in known_norm):
            return known
    return raw


def _extract_forced_dual_hierarchy_calls(question: str) -> List[LLMToolCall]:
    norm = _normalize_request_text(question)
    has_bug = bool(re.search(r"\bbugs?\b", norm))
    has_us = bool(re.search(r"\buser stor(?:y|ies)\b", norm))
    has_epic = bool(re.search(r"\bepic\b", norm))
    has_feature = bool(re.search(r"\bfeature\b", norm))
    if not (has_bug and has_us and has_epic and has_feature):
        return []

    epic_match = re.search(r"\bepic\b[^\d]{0,90}(\d{4,9})", norm)
    feature_match = re.search(r"\bfeature\b[^\d]{0,90}(\d{4,9})", norm)
    if not epic_match or not feature_match:
        return []

    try:
        epic_id = int(epic_match.group(1))
        feature_id = int(feature_match.group(1))
    except (TypeError, ValueError):
        return []
    if epic_id <= 0 or feature_id <= 0:
        return []

    area_hint = ""
    area_match = re.search(r"\barea\b\s+(.+?)(?:\bnao\b|[.,;]|$)", norm)
    if area_match:
        area_hint = area_match.group(1).strip()
    area_path = _canonicalize_area_path(area_hint)

    title_contains = ""
    title_match = re.search(
        r"\btitulo\s+tem\s+(.+?)(?:\bdentro\s+da\s+area\b|\bna\s+area\b|\bnao\b|[.,;]|$)",
        norm,
    )
    if title_match:
        title_contains = title_match.group(1).strip()

    calls = [
        LLMToolCall(
            id=f"forced_qh_bug_{uuid.uuid4().hex[:8]}",
            name="query_hierarchy",
            arguments={
                "parent_id": epic_id,
                "parent_type": "Epic",
                "child_type": "Bug",
                "area_path": area_path,
            },
        ),
        LLMToolCall(
            id=f"forced_qh_us_{uuid.uuid4().hex[:8]}",
            name="query_hierarchy",
            arguments={
                "parent_id": feature_id,
                "parent_type": "Feature",
                "child_type": "User Story",
                "area_path": area_path,
                "title_contains": title_contains,
            },
        ),
    ]
    return calls


def _make_tool_calls_assistant_message(tool_calls: List[LLMToolCall]) -> dict:
    msg_calls = []
    for tc in tool_calls:
        msg_calls.append(
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False, default=str),
                },
            }
        )
    return {"role": "assistant", "content": "", "tool_calls": msg_calls}


async def _run_forced_dual_hierarchy(
    question: str,
    conv_id: str,
    user_sub: str = "",
) -> tuple[List[str], List[Dict]]:
    forced_calls = _extract_forced_dual_hierarchy_calls(question)
    if not forced_calls:
        return [], []

    logger.info(
        "[Agent] forcing dual query_hierarchy calls for mixed hierarchy request (conv=%s)",
        conv_id,
    )
    conversations[conv_id].append(_make_tool_calls_assistant_message(forced_calls))
    return await _execute_tool_calls(forced_calls, conv_id, user_sub=user_sub)


async def _persist_conversation(conv_id: str, partition_key: str) -> None:
    """Persiste conversa na tabela ChatHistory (fire-and-forget)."""
    try:
        async with _get_conversation_lock(conv_id):
            msgs = conversations.get(conv_id)
            if not msgs:
                return

            compact = [_compact_message_for_storage(m) for m in msgs]
            messages_json = json.dumps(compact, ensure_ascii=False, default=str)

            # Verificar se cabe (Azure Table Storage: 64KB por propriedade)
            if len(messages_json.encode("utf-8")) > 60000:
                compact = [compact[0]] + compact[-10:] if compact else []
                messages_json = json.dumps(compact, ensure_ascii=False, default=str)

            if len(messages_json.encode("utf-8")) > 60000:
                head = compact[:1]
                tail = compact[-4:] if len(compact) > 1 else []
                compact = head + tail
                slimmed = []
                for m in compact:
                    c = m.get("content", "")
                    if isinstance(c, str) and len(c) > 1200:
                        c = c[:1200] + "...(truncado)"
                    elif isinstance(c, list):
                        c = [{"type": "summary", "text": "conteúdo multimodal omitido"}]
                    slim_item = {"role": m.get("role", ""), "content": c}
                    if m.get("role") == "tool":
                        if m.get("tool_call_id"):
                            slim_item["tool_call_id"] = m.get("tool_call_id")
                        if m.get("result_blob_ref"):
                            slim_item["result_blob_ref"] = m.get("result_blob_ref")
                    slimmed.append(slim_item)
                compact = slimmed
                messages_json = json.dumps(compact, ensure_ascii=False, default=str)

            if len(messages_json.encode("utf-8")) > 60000:
                compact = [{"role": "system", "content": "Histórico truncado para persistência."}]
                messages_json = json.dumps(compact, ensure_ascii=False, default=str)

            meta = conversation_meta.get(conv_id, {})
            entity = {
                "PartitionKey": partition_key,
                "RowKey": conv_id,
                "Messages": messages_json,
                "Mode": meta.get("mode", "general"),
                "CreatedAt": meta.get("created_at", datetime.now(timezone.utc).isoformat()),
                "UpdatedAt": datetime.now(timezone.utc).isoformat(),
                "MessageCount": len(compact),
            }

        safe_pk = odata_escape(partition_key)
        safe_conv = odata_escape(conv_id)
        existing = await table_query(
            "ChatHistory",
            f"PartitionKey eq '{safe_pk}' and RowKey eq '{safe_conv}'",
            top=1,
        )
        if existing:
            await table_merge("ChatHistory", entity)
        else:
            inserted = await table_insert("ChatHistory", entity)
            if not inserted:
                await table_merge("ChatHistory", entity)
    except Exception as e:
        logger.warning("[Agent] _persist_conversation failed for %s: %s", conv_id, e)


# =============================================================================
# TOOL EXECUTION HELPER
# =============================================================================

async def _execute_tool_calls(
    tool_calls: List[LLMToolCall], conv_id: str, user_sub: str = "",
) -> tuple[List[str], List[Dict]]:
    """Executa tools em paralelo, retorna (tools_used, tool_details)."""
    tools_used = []
    tool_details = []

    def _latest_user_text() -> str:
        for msg in reversed(conversations.get(conv_id, [])):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        parts.append(str(p.get("text", "")))
                return " ".join(parts).strip()
        return ""
    
    async def _run(tc: LLMToolCall):
        args = tc.arguments
        user_text = _latest_user_text()
        # Auto-inject file context for US generation
        files = _get_uploaded_files(conv_id)
        if tc.name == "generate_user_stories" and files and not args.get("context"):
            context_blocks = []
            per_file_budget = max(2000, min(12000, 80000 // max(1, len(files))))
            for idx, f in enumerate(files[-MAX_FILES_CONTEXT:], 1):
                fname = f.get("filename", f"ficheiro_{idx}")
                content = str(f.get("data_text", "") or "")
                if not content:
                    continue
                context_blocks.append(f"[{idx}] {fname}\n{content[:per_file_budget]}")
            if context_blocks:
                args["context"] = "\n\n".join(context_blocks)[:90000]
        if tc.name == "search_uploaded_document" and not args.get("conv_id"):
            args["conv_id"] = conv_id
        if tc.name == "search_uploaded_document" and user_sub and not args.get("user_sub"):
            args["user_sub"] = user_sub
        if tc.name == "query_hierarchy":
            has_parent_id = bool(args.get("parent_id"))
            parent_type = str(args.get("parent_type", "") or "").strip().lower()
            title_contains = str(args.get("title_contains", "") or "").strip()
            explicit_title_filter = bool(re.search(r"\bt[ií]tulo\b|\btitle\b", user_text.lower()))
            if not has_parent_id and parent_type in ("epic", "feature"):
                m = None
                if parent_type == "epic":
                    m = re.search(
                        r"\bepic\b\s+(.+?)(?:\bcujo\b|\bcom\s+t[ií]tulo\b|\bt[ií]tulo\b|\bna\b|\bno\b|\bdentro\b|[?.!,]|$)",
                        user_text,
                        re.IGNORECASE,
                    )
                elif parent_type == "feature":
                    m = re.search(
                        r"\bfeature\b\s+(.+?)(?:\bcujo\b|\bcom\s+t[ií]tulo\b|\bt[ií]tulo\b|\bna\b|\bno\b|\bdentro\b|[?.!,]|$)",
                        user_text,
                        re.IGNORECASE,
                    )

                hint = ""
                if m:
                    hint = str(m.group(1) or "").strip()
                if hint and not re.search(r"\d{4,9}", hint):
                    args["parent_title_hint"] = hint[:160]
                    if title_contains and not explicit_title_filter:
                        args["title_contains"] = ""
                elif title_contains and not explicit_title_filter:
                    args["parent_title_hint"] = title_contains
                    args["title_contains"] = ""
        if tc.name == "create_workitem":
            if _has_explicit_create_confirmation(conv_id):
                args["confirmed"] = True
            else:
                return tc, {
                    "error": (
                        "Confirmação explícita necessária para criar work item. "
                        "Pede ao utilizador para responder 'confirmo' e tenta novamente."
                    )
                }
        result = await execute_tool(tc.name, args)
        return tc, result
    
    results = await asyncio.gather(
        *[_run(tc) for tc in tool_calls],
        return_exceptions=True,
    )
    
    for res in results:
        if isinstance(res, Exception):
            logger.error("[Agent] Tool execution failed: %s", res)
            continue
        tc, tool_result = res
        tools_used.append(tc.name)

        result_blob_ref = ""
        try:
            safe_user = safe_blob_component(user_sub or "anon", max_len=80)
            safe_conv = safe_blob_component(conv_id, max_len=80)
            safe_tool = safe_blob_component(tc.name, max_len=40)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            blob_name = f"{safe_user}/{safe_conv}/{ts}_{safe_tool}_{safe_blob_component(tc.id, max_len=60)}.json"
            uploaded = await blob_upload_json(CHAT_TOOLRESULT_BLOB_CONTAINER, blob_name, tool_result)
            result_blob_ref = str(uploaded.get("blob_ref", "") or "")
        except Exception as e:
            logger.warning("[Agent] tool result blob persist failed (%s): %s", tc.name, e)
        
        serialized_tool_result = truncate_tool_result(
            json.dumps(tool_result, ensure_ascii=False, default=str)
        )

        td = {
            "tool": tc.name, "arguments": tc.arguments,
            "result_summary": {
                "total_count": tool_result.get("total_count", tool_result.get("total_results", tool_result.get("total_found", "N/A"))),
                "items_returned": len(tool_result.get("items", tool_result.get("analysis_data", []))),
                "has_error": "error" in tool_result,
            },
            "result_json": serialized_tool_result,
            "result_blob_ref": result_blob_ref,
        }
        tool_details.append(td)
        result_str = serialized_tool_result
        
        # Add tool result to conversation
        conversations[conv_id].append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": result_str,
            "result_blob_ref": result_blob_ref,
        })
    
    return tools_used, tool_details


# =============================================================================
# AGENT CHAT (request/response clássico)
# =============================================================================

async def agent_chat(request: AgentChatRequest, user: dict) -> AgentChatResponse:
    start = datetime.now(timezone.utc)
    mode = request.mode or "general"
    tier = request.model_tier or LLM_DEFAULT_TIER
    conv_id = request.conversation_id or str(uuid.uuid4())
    partition_key = _user_partition_key(user)

    tools_used = []
    tool_details = []
    total_usage = {}
    model_used = ""
    has_exportable = False
    export_idx = None
    should_persist = False

    async with _get_conversation_lock(conv_id):
        await _ensure_conversation(conv_id, mode, partition_key)
        await _ensure_uploaded_files_loaded(conv_id, user_sub=str((user or {}).get("sub", "") or ""))
        _inject_file_context(conv_id, conversations[conv_id])

        conversations[conv_id].append({"role": "user", "content": request.question})
        conversations[conv_id] = _trim_history(conversations[conv_id])

        try:
            forced_tu, forced_td = await _run_forced_dual_hierarchy(
                request.question,
                conv_id,
                user_sub=str((user or {}).get("sub", "") or ""),
            )
            if forced_td:
                tools_used.extend(forced_tu)
                tool_details.extend(forced_td)
                batch_start = len(tool_details) - len(forced_td)
                for local_idx, d in enumerate(forced_td):
                    if d["result_summary"].get("items_returned", 0) > 0:
                        has_exportable = True
                        export_idx = batch_start + local_idx

            # Agent loop
            ephemeral = await _build_llm_messages(conv_id, request.question, request=request)
            response = await llm_with_fallback(
                ephemeral, tools=get_all_tool_definitions(), tier=tier,
                temperature=AGENT_TEMPERATURE, max_tokens=AGENT_MAX_TOKENS,
            )
            model_used = response.model
            total_usage = response.usage

            iteration = 0
            while response.tool_calls and iteration < AGENT_MAX_ITERATIONS:
                iteration += 1

                # Add assistant message with tool calls to history
                conversations[conv_id].append(
                    make_assistant_message_from_response(response)
                )

                # Execute tools
                tu, td = await _execute_tool_calls(
                    response.tool_calls,
                    conv_id,
                    user_sub=str((user or {}).get("sub", "") or ""),
                )
                tools_used.extend(tu)
                tool_details.extend(td)

                # Check for exportable data
                batch_start = len(tool_details) - len(td)
                for local_idx, d in enumerate(td):
                    if d["result_summary"].get("items_returned", 0) > 0:
                        has_exportable = True
                        export_idx = batch_start + local_idx

                # Next LLM call
                ephemeral = await _build_llm_messages(conv_id, request.question, request=request)
                response = await llm_with_fallback(
                    ephemeral, tools=get_all_tool_definitions(), tier=tier,
                    temperature=AGENT_TEMPERATURE, max_tokens=AGENT_MAX_TOKENS,
                )
                for k, v in response.usage.items():
                    if isinstance(v, (int, float)):
                        total_usage[k] = total_usage.get(k, 0) + v

            answer = response.content or "Não consegui processar a tua pergunta."
            conversations[conv_id].append({"role": "assistant", "content": answer})
            should_persist = True

        except Exception as e:
            logger.error("[Agent] agent_chat exception: %s", e, exc_info=True)
            answer = f"Erro: {str(e)}"

    if should_persist:
        try:
            await asyncio.wait_for(_persist_conversation(conv_id, partition_key), timeout=8.0)
        except Exception:
            create_logged_task(_persist_conversation(conv_id, partition_key), "persist_conversation_sync_timeout_fallback")
    
    total_time = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    
    return AgentChatResponse(
        answer=answer,
        conversation_id=conv_id,
        tools_used=list(set(tools_used)),
        tool_details=tool_details,
        tokens_used=total_usage,
        total_time_ms=total_time,
        model_used=model_used,
        mode=mode,
        has_exportable_data=has_exportable,
        export_index=export_idx,
    )


# =============================================================================
# AGENT CHAT STREAM (SSE)
# =============================================================================

async def agent_chat_stream(request: AgentChatRequest, user: dict) -> AsyncGenerator[str, None]:
    """SSE streaming — yields 'data: {json}\n\n' strings."""
    start = datetime.now(timezone.utc)
    mode = request.mode or "general"
    tier = request.model_tier or LLM_DEFAULT_TIER
    conv_id = request.conversation_id or str(uuid.uuid4())
    partition_key = _user_partition_key(user)

    tools_used = []
    tool_details = []
    total_usage = {}
    model_used = ""
    should_persist = False
    
    def _sse(event: dict) -> str:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    
    async with _get_conversation_lock(conv_id):
        await _ensure_conversation(conv_id, mode, partition_key)
        await _ensure_uploaded_files_loaded(conv_id, user_sub=str((user or {}).get("sub", "") or ""))
        _inject_file_context(conv_id, conversations[conv_id])
        conversations[conv_id].append({"role": "user", "content": request.question})
        conversations[conv_id] = _trim_history(conversations[conv_id])

        # Emit conversation_id immediately
        yield _sse({"type": "init", "conversation_id": conv_id, "mode": mode})

        try:
            forced_tu, forced_td = await _run_forced_dual_hierarchy(
                request.question,
                conv_id,
                user_sub=str((user or {}).get("sub", "") or ""),
            )
            if forced_td:
                tools_used.extend(forced_tu)
                tool_details.extend(forced_td)
                for d in forced_td:
                    yield _sse({"type": "tool_start", "tool": d["tool"], "text": f"🔍 {d['tool']} (forced)..."})
                    count = d["result_summary"].get("total_count", d["result_summary"].get("items_returned", ""))
                    yield _sse({"type": "tool_result", "tool": d["tool"], "text": f"✅ {d['tool']}: {count} resultados"})

            provider = get_provider(tier)
            model_used = getattr(provider, 'model', getattr(provider, 'deployment', ''))

            iteration = 0
            need_final_response = True

            while iteration <= AGENT_MAX_ITERATIONS and need_final_response:
                iteration += 1

                yield _sse({"type": "thinking", "text": "A analisar..." if iteration == 1 else "A processar resultados..."})

                # Non-streaming call for tool detection
                ephemeral = await _build_llm_messages(conv_id, request.question, request=request)
                response = await llm_with_fallback(
                    ephemeral, tools=get_all_tool_definitions(), tier=tier,
                    temperature=AGENT_TEMPERATURE, max_tokens=AGENT_MAX_TOKENS,
                )
                for k, v in response.usage.items():
                    if isinstance(v, (int, float)):
                        total_usage[k] = total_usage.get(k, 0) + v

                if response.tool_calls:
                    # Add assistant msg with tool calls
                    conversations[conv_id].append(
                        make_assistant_message_from_response(response)
                    )

                    # Signal tool execution
                    for tc in response.tool_calls:
                        yield _sse({"type": "tool_start", "tool": tc.name, "text": f"🔍 {tc.name}..."})

                    # Execute tools
                    tu, td = await _execute_tool_calls(
                        response.tool_calls,
                        conv_id,
                        user_sub=str((user or {}).get("sub", "") or ""),
                    )
                    tools_used.extend(tu)
                    tool_details.extend(td)

                    for d in td:
                        count = d["result_summary"].get("total_count", d["result_summary"].get("items_returned", ""))
                        yield _sse({"type": "tool_result", "tool": d["tool"], "text": f"✅ {d['tool']}: {count} resultados"})

                    # Continue loop for next LLM call
                    need_final_response = True
                else:
                    # Final text response — stream it
                    need_final_response = False

                    if response.content:
                        # Try to re-do as streaming for token-by-token delivery
                        # Remove tools so we get pure text streaming
                        try:
                            ephemeral_stream = await _build_llm_messages(conv_id, request.question, request=request)
                            async for event in provider.chat_stream(
                                ephemeral_stream, tools=None,
                                temperature=AGENT_TEMPERATURE, max_tokens=AGENT_MAX_TOKENS,
                            ):
                                if event.type == "token" and event.text:
                                    yield _sse({"type": "token", "text": event.text})
                                elif event.type == "done":
                                    break
                        except Exception as e:
                            logger.warning("[Agent] streaming failed, falling back to non-streaming: %s", e)
                            # Fallback: send full content at once
                            yield _sse({"type": "token", "text": response.content})

                        conversations[conv_id].append({"role": "assistant", "content": response.content})
                        should_persist = True
                    else:
                        yield _sse({"type": "token", "text": "Não consegui processar a tua pergunta."})

        except Exception as e:
            yield _sse({"type": "error", "text": str(e)})

    if should_persist:
        try:
            await asyncio.wait_for(_persist_conversation(conv_id, partition_key), timeout=8.0)
        except Exception:
            create_logged_task(_persist_conversation(conv_id, partition_key), "persist_conversation_stream_timeout_fallback")
    
    total_time = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    has_exportable = False
    export_idx = None
    for idx, d in enumerate(tool_details):
        if d.get("result_summary", {}).get("items_returned", 0) > 0:
            has_exportable = True
            export_idx = idx
    
    yield _sse({
        "type": "done",
        "tools_used": list(set(tools_used)),
        "tool_details": tool_details,
        "has_exportable_data": has_exportable,
        "export_index": export_idx,
        "tokens_used": total_usage,
        "total_time_ms": total_time,
        "model_used": model_used,
        "conversation_id": conv_id,
    })


# =============================================================================
# MODE SWITCHING
# =============================================================================

def switch_conversation_mode(conv_id: str, new_mode: str) -> bool:
    """Muda o modo de uma conversa existente. Reinjecta system prompt."""
    if conv_id not in conversations:
        return False
    if new_mode not in ("general", "userstory"):
        return False
    
    sp = get_userstory_system_prompt() if new_mode == "userstory" else get_agent_system_prompt()
    
    # Replace first system message
    new_msgs = [{"role": "system", "content": sp}]
    new_msgs.extend(m for m in conversations[conv_id] if m.get("role") != "system")
    conversations[conv_id] = new_msgs
    conversation_meta.setdefault(conv_id, {})["mode"] = new_mode
    
    return True
