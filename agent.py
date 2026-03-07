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
import time
import unicodedata
from datetime import datetime, timezone
from typing import Dict, List, Optional, AsyncGenerator, Callable, Iterator
from collections.abc import MutableMapping

from config import (
    AGENT_MAX_ITERATIONS, AGENT_MAX_TOKENS, AGENT_TEMPERATURE,
    AGENT_HISTORY_LIMIT, LLM_DEFAULT_TIER, UPLOAD_MAX_IMAGES_PER_MESSAGE,
    UPLOAD_INDEX_TOP, DEVOPS_AREAS, CHAT_TOOLRESULT_BLOB_CONTAINER, PII_ENABLED,
)
from models import (
    AgentChatRequest, AgentChatResponse, LLMToolCall,
)
from llm_provider import (
    get_provider, llm_with_fallback, llm_stream_with_fallback,
    make_assistant_message_from_response,
)
from token_counter import (
    count_messages_tokens,
    count_tools_tokens,
    resolve_context_window,
    RESPONSE_RESERVE_TOKENS,
)
from tool_metrics import tool_metrics
import token_quota as _tq_module
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
from utils import odata_escape
from pii_shield import PIIMaskingContext, mask_pii

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
        self._lock = asyncio.Lock()

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

    async def async_get(self, key: str, default=None):
        """Thread-safe get com touch."""
        async with self._lock:
            if key in self._data:
                self._touch(key)
                return self._data[key]
            return default

    async def async_set(self, key: str, value: List[dict]) -> None:
        """Thread-safe set com capacity check."""
        async with self._lock:
            if key not in self._data:
                self.cleanup_expired()
                while len(self._data) >= self.max_conversations:
                    if self._evict_lru(exclude_key=key) is None:
                        break
            self._data[key] = value
            self._touch(key)

    async def async_delete(self, key: str) -> None:
        """Thread-safe delete."""
        async with self._lock:
            if key in self._data:
                self._evict(key, reason="manual")

    async def async_contains(self, key: str) -> bool:
        """Thread-safe contains check."""
        async with self._lock:
            return key in self._data

    async def async_cleanup_expired(self) -> List[str]:
        """Thread-safe cleanup."""
        async with self._lock:
            return self.cleanup_expired()

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
_conversation_meta_lock = asyncio.Lock()
uploaded_files_store: Dict[str, Dict] = {}
_uploaded_files_lock = asyncio.Lock()
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
_AGENT_LLM_STEP_TIMEOUT_SECONDS = 90.0


def _fallback_answer_from_tool_details(tool_details: List[dict]) -> str:
    for detail in reversed(tool_details or []):
        if not isinstance(detail, dict):
            continue
        summary = detail.get("result_summary", {}) if isinstance(detail.get("result_summary"), dict) else {}
        if summary.get("has_error"):
            continue
        total = summary.get("total_count")
        if total in (None, "", "N/A"):
            total = summary.get("items_returned", 0)
        tool_name = str(detail.get("tool", "consulta") or "consulta")
        try:
            total_int = int(total)
        except Exception:
            total_int = 0
        if total_int > 0:
            return (
                f"Encontrei {total_int} resultados via {tool_name}, "
                "mas não consegui gerar o resumo final. "
                "Pede \"lista completa\" para mostrar os itens diretamente."
            )
    return (
        "Consegui executar a pesquisa, mas não consegui gerar o texto final. "
        "Tenta novamente (preferencialmente em Fast) para resposta mais imediata."
    )


def _log_fallback_chain_if_needed(response) -> None:
    chain = getattr(response, "fallback_chain", None)
    if not isinstance(chain, list) or not chain:
        return
    failed = [p for p in chain if isinstance(p, dict) and p.get("status") == "failed"]
    if failed:
        logger.warning("[Agent] LLM fallback used: %s", json.dumps(chain, ensure_ascii=False))


def _quota_tier_name(tier: Optional[str]) -> str:
    txt = str(tier or "").strip().lower()
    return txt if txt in ("fast", "standard", "pro") else "fast"


def _check_token_quota(tier: Optional[str], conv_id: str) -> str:
    mgr = _tq_module.token_quota_manager
    if not mgr:
        return ""
    quota_tier = _quota_tier_name(tier)
    allowed, reason = mgr.check(quota_tier)
    if allowed:
        return ""
    logger.warning(
        "[Agent] Token quota exceeded: %s tier=%s conv=%s",
        reason,
        quota_tier,
        conv_id,
    )
    return reason or "Token quota exceeded"


def _record_token_quota(tier: Optional[str], usage: Dict) -> None:
    mgr = _tq_module.token_quota_manager
    if not mgr or not isinstance(usage, dict):
        return
    try:
        total_tokens = int(usage.get("total_tokens", 0) or 0)
    except Exception:
        total_tokens = 0
    if total_tokens <= 0:
        return
    mgr.record(_quota_tier_name(tier), total_tokens)


def _user_partition_key(user: Optional[dict]) -> str:
    raw = (user or {}).get("sub") or (user or {}).get("username") or "anon"
    # Azure Table key hygiene: avoid problematic characters for filters/URLs.
    safe = "".join(c if c.isalnum() or c in "._-@" else "_" for c in str(raw))
    return (safe or "anon")[:100]


def _safe_blob_component(raw: str, max_len: int = 120) -> str:
    txt = str(raw or "").strip()
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in txt)
    if not safe:
        safe = "x"
    return safe[:max_len]


def _get_conversation_lock(conv_id: str) -> asyncio.Lock:
    return _conversation_locks.setdefault(conv_id, asyncio.Lock())


def _create_logged_task(coro, label: str) -> None:
    task = asyncio.create_task(coro)

    def _on_done(done_task: asyncio.Task) -> None:
        try:
            done_task.result()
        except Exception as e:
            logger.warning("[Agent] background task '%s' failed: %s", label, e, exc_info=True)

    task.add_done_callback(_on_done)

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


async def _get_conversation_meta(conv_id: str) -> Dict:
    async with _conversation_meta_lock:
        meta = conversation_meta.get(conv_id)
        return dict(meta) if isinstance(meta, dict) else {}


async def _set_conversation_meta(conv_id: str, value: Dict) -> None:
    async with _conversation_meta_lock:
        conversation_meta[conv_id] = dict(value)


async def _update_conversation_meta(conv_id: str, **updates) -> None:
    async with _conversation_meta_lock:
        meta = dict(conversation_meta.get(conv_id, {}))
        meta.update(updates)
        conversation_meta[conv_id] = meta


def _normalize_uploaded_files_entry_unlocked(conv_id: str) -> dict:
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


def _get_uploaded_files_unlocked(conv_id: str) -> List[dict]:
    return list(_normalize_uploaded_files_entry_unlocked(conv_id).get("files", []))


async def _normalize_uploaded_files_entry(conv_id: str) -> dict:
    async with _uploaded_files_lock:
        return _normalize_uploaded_files_entry_unlocked(conv_id)


async def _get_uploaded_files(conv_id: str) -> List[dict]:
    async with _uploaded_files_lock:
        return _get_uploaded_files_unlocked(conv_id)


async def _ensure_uploaded_files_loaded(conv_id: str, user_sub: str = "") -> None:
    async with _uploaded_files_lock:
        current_files = _get_uploaded_files_unlocked(conv_id)
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
        try:
            full_col_stats = json.loads(row.get("FullColStatsJson", "[]") or "[]")
        except Exception:
            full_col_stats = []
        files.append(
            {
                "filename": row.get("Filename", ""),
                "data_text": preview_text,
                "row_count": int(row.get("RowCount", 0) or 0),
                "col_names": col_names if isinstance(col_names, list) else [],
                "col_analysis": col_analysis if isinstance(col_analysis, list) else [],
                "full_col_stats": full_col_stats if isinstance(full_col_stats, list) else [],
                "truncated": bool(row.get("Truncated", False)),
                "uploaded_at": row.get("UploadedAt", ""),
                "has_chunks": str(row.get("HasChunks", "")).lower() in ("true", "1"),
                "chunks_blob_ref": row.get("ChunksBlobRef", ""),
                "extracted_blob_ref": extracted_ref,
            }
        )
    if files:
        async with _uploaded_files_lock:
            if _get_uploaded_files_unlocked(conv_id):
                return
            uploaded_files_store[conv_id] = {
                "files": files[-MAX_FILES_CONTEXT:],
                "uploaded_at": datetime.now().isoformat(),
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


async def _build_user_message(request: AgentChatRequest, conv_id: Optional[str] = None) -> dict:
    """Constrói a mensagem do user (texto puro ou multimodal com 1..MAX_IMAGES_PER_INPUT imagens)."""
    images = _extract_request_images(request)

    # Fallback: usar imagem previamente carregada via /upload nesta conversa.
    if not images and conv_id:
        uploaded_images = []
        for file_data in await _get_uploaded_files(conv_id):
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


async def _inject_file_context(conv_id: str, messages: List[dict]):
    """Injeta contexto de ficheiros uploaded na conversa."""
    files = await _get_uploaded_files(conv_id)
    meta = await _get_conversation_meta(conv_id)
    if files and not meta.get("file_injected"):
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
        mode = meta.get("mode", "general")
        use_files = files[-MAX_FILES_CONTEXT:]
        ctx_parts = [
            f"FICHEIROS CARREGADOS: {len(use_files)} (máximo analisado por pedido: {MAX_FILES_CONTEXT})",
            "Analisa TODOS os ficheiros listados antes de responder. Não ignores nenhum.",
            "Quando existir secção 'ESTATÍSTICAS COMPLETAS', usa SEMPRE esses valores para min/max/mean/std; "
            "não recalcules a partir da amostra truncada.",
            "Para pedidos de gráfico sobre ficheiros carregados, usa generate_chart com os dados fornecidos; "
            "se faltar coluna/agregação, pede clarificação objetiva ao utilizador.",
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

            full_stats = file_data.get("full_col_stats")
            col_analysis = file_data.get("col_analysis")
            if isinstance(full_stats, list) and full_stats:
                block.append("ESTATÍSTICAS COMPLETAS (calculadas sobre TODAS as linhas):")
                for fs in full_stats[:20]:
                    name = str(fs.get("name", "") or "")
                    if fs.get("type") == "numeric":
                        block.append(
                            f"- {name} (numeric): min={fs.get('min')}, max={fs.get('max')}, "
                            f"mean={fs.get('mean')}, std={fs.get('std')}, "
                            f"P25={fs.get('p25')}, P50={fs.get('p50')}, P75={fs.get('p75')}, "
                            f"non_null={fs.get('non_null')}, zeros={fs.get('zeros')}"
                        )
                    else:
                        first_last = ""
                        if fs.get("first") and fs.get("last"):
                            first_last = f", range=[{fs.get('first')} ... {fs.get('last')}]"
                        block.append(
                            f"- {name} (text): unique≈{fs.get('unique_approx', '?')}, "
                            f"sample={fs.get('sample', [])}{first_last}"
                        )
                block.append(
                    "NOTA: Os dados acima representam o ficheiro COMPLETO. "
                    "O conteúdo abaixo é uma AMOSTRA truncada — usa as estatísticas acima para valores exatos."
                )
            elif col_analysis:
                block.append("ANÁLISE DE COLUNAS (amostra):")
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
        await _update_conversation_meta(conv_id, file_injected=True)


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
        await _set_conversation_meta(
            conv_id,
            {
                "mode": stored_mode,
                "created_at": row.get("CreatedAt", datetime.now().isoformat()),
                "loaded_from_storage": True,
            },
        )

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
            await _set_conversation_meta(
                conv_id,
                {"mode": mode, "created_at": datetime.now().isoformat()},
            )
    else:
        conversations.touch(conv_id)
    return conv_id


async def _build_llm_messages(
    conv_id: str,
    question: str,
    request: Optional[AgentChatRequest] = None,
    tier: Optional[str] = None,
    tools_list: Optional[List[dict]] = None,
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
        user_msg = await _build_user_message(request, conv_id=conv_id)
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

    tools_tokens = count_tools_tokens(tools_list or [])
    messages_tokens = count_messages_tokens(base)
    provider = get_provider(tier)
    model_name = str(getattr(provider, "deployment", "") or getattr(provider, "model", "") or "")
    context_window = resolve_context_window(model_name)
    available = context_window - tools_tokens - messages_tokens - RESPONSE_RESERVE_TOKENS
    if available < 500:
        logger.warning(
            "[Agent] Context budget tight (available=%d, model=%s), trimming history",
            available,
            model_name or "unknown",
        )

    while available < 2000:
        non_system_indices = [idx for idx, msg in enumerate(base) if msg.get("role") != "system"]
        if len(non_system_indices) <= 2:
            break
        base.pop(non_system_indices[0])
        messages_tokens = count_messages_tokens(base)
        available = context_window - tools_tokens - messages_tokens - RESPONSE_RESERVE_TOKENS

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


async def _has_tabular_uploads(conv_id: str) -> bool:
    for file_data in await _get_uploaded_files(conv_id):
        name = str(file_data.get("filename", "") or "").lower()
        if name.endswith((".csv", ".xlsx", ".xls")):
            return True
    return False


async def _extract_forced_uploaded_table_calls(
    question: str,
    conv_id: str,
    already_used: Optional[List[str]] = None,
) -> List[LLMToolCall]:
    if not await _has_tabular_uploads(conv_id):
        return []
    # Modo agressivo: não forçar analyze_uploaded_table.
    # A primeira tentativa deve ser run_code via decisão do LLM/prompt.
    # O fallback para analyze_uploaded_table é tratado no handler de run_code
    # quando houver erro/timeout.
    return []


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

            meta = await _get_conversation_meta(conv_id)
            entity = {
                "PartitionKey": partition_key,
                "RowKey": conv_id,
                "Messages": messages_json,
                "Mode": meta.get("mode", "general"),
                "CreatedAt": meta.get("created_at", datetime.now().isoformat()),
                "UpdatedAt": datetime.now().isoformat(),
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

    def _latest_chart_ready() -> Dict:
        for msg in reversed(conversations.get(conv_id, [])):
            if msg.get("role") != "tool":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            try:
                parsed = json.loads(content)
            except Exception:
                continue
            if not isinstance(parsed, dict):
                continue
            chart_ready = parsed.get("chart_ready")
            if isinstance(chart_ready, dict) and chart_ready:
                return chart_ready
        return {}

    def _looks_like_run_code_refusal(result: dict) -> bool:
        if not isinstance(result, dict):
            return False
        has_artifacts = bool(
            result.get("files")
            or result.get("images")
            or result.get("generated_artifacts")
            or result.get("items")
        )
        if has_artifacts:
            return False
        raw = " ".join(
            str(result.get(k, "") or "")
            for k in ("stdout", "stderr", "error", "output_text")
        )
        norm = _normalize_request_text(raw)
        refusal_markers = (
            "nao consigo",
            "nao foi possivel",
            "nao e possivel",
            "limitac",
            "restric",
            "nao posso",
            "nao permite",
            "falta",
        )
        return any(marker in norm for marker in refusal_markers)
    
    async def _run(tc: LLMToolCall):
        started = time.perf_counter()
        status = "ok"
        error_msg = ""
        args = tc.arguments
        user_text = _latest_user_text()
        norm_user = _normalize_request_text(user_text)
        file_analysis_intent = bool(
            re.search(
                r"\b(ficheiro|arquivo|csv|excel|xlsx|tabela|dados|coluna|linhas|registos?|analisa|analisar|resumo|"
                r"estatistic|minimo|maximo|media|desvio|grafico|chart|correlacao|scatter|lista completa|valores distintos)\b",
                norm_user,
            )
        )
        # --- Routing guardrail: CSV/Excel uploaded -> never query DevOps ---
        files = await _get_uploaded_files(conv_id)
        if files and tc.name in ("query_workitems", "search_workitems", "compute_kpi", "query_hierarchy"):
            _file_keywords = ("csv", "excel", "xlsx", "tabela", "ficheiro", "upload", "dados", "coluna",
                              "linha", "media", "soma", "total", "grafico", "chart", "analise", "analisa")
            _user_lower = user_text.lower()
            if any(kw in _user_lower for kw in _file_keywords):
                logger.info("[Agent] Guardrail: blocked %s — uploaded files present and user references data", tc.name)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                logger.info(
                    "[ToolCallEvent] %s",
                    json.dumps(
                        {
                            "event": "tool_call_execution",
                            "conv_id": conv_id,
                            "user_sub": user_sub or "anon",
                            "tool": tc.name,
                            "tool_call_id": tc.id,
                            "status": "blocked",
                            "duration_ms": elapsed_ms,
                        },
                        ensure_ascii=False,
                    ),
                )
                tool_metrics.record(tc.name, elapsed_ms, "blocked")
                return tc, {
                    "error": (
                        "Existem ficheiros carregados nesta conversa. "
                        "Para análise de ficheiro carregado, usa run_code como primeira tentativa. "
                        "Se run_code falhar, usa analyze_uploaded_table como fallback. "
                        "Para gerar gráficos, usa generate_chart com os dados da análise. "
                        "query_workitems/search_workitems sao para Azure DevOps, nao para ficheiros carregados."
                    )
                }
        # Modo agressivo para tabular uploads: run_code primeiro, analyze_uploaded_table só fallback.
        if (
            files
            and tc.name == "analyze_uploaded_table"
            and file_analysis_intent
            and not args.get("_fallback_from_run_code")
        ):
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "[ToolCallEvent] %s",
                json.dumps(
                    {
                        "event": "tool_call_execution",
                        "conv_id": conv_id,
                        "user_sub": user_sub or "anon",
                        "tool": tc.name,
                        "tool_call_id": tc.id,
                        "status": "blocked",
                        "duration_ms": elapsed_ms,
                        "reason": "run_code_first_policy",
                    },
                    ensure_ascii=False,
                ),
            )
            tool_metrics.record(tc.name, elapsed_ms, "blocked")
            return tc, {
                "error": (
                    "Política ativa: para análise de CSV/Excel, usa run_code como primeira tentativa. "
                    "analyze_uploaded_table é fallback automático quando run_code falhar."
                )
            }
        # Auto-inject file context for US generation
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
        if tc.name == "analyze_uploaded_table" and not args.get("conv_id"):
            args["conv_id"] = conv_id
        if tc.name == "analyze_uploaded_table" and user_sub and not args.get("user_sub"):
            args["user_sub"] = user_sub
        if tc.name == "analyze_uploaded_table" and "full_points" not in args:
            args["full_points"] = True
        if tc.name == "analyze_uploaded_table" and "top" not in args:
            args["top"] = 5000
        if tc.name == "run_code" and not args.get("conv_id"):
            args["conv_id"] = conv_id
        if tc.name == "run_code" and user_sub and not args.get("user_sub"):
            args["user_sub"] = user_sub
        if tc.name == "generate_chart":
            has_explicit_data = bool(
                args.get("series")
                or args.get("x_values")
                or args.get("y_values")
                or args.get("labels")
                or args.get("values")
            )
            if not has_explicit_data:
                chart_ready = _latest_chart_ready()
                if chart_ready:
                    for key in ("chart_type", "title", "x_values", "y_values", "labels", "values", "series", "x_label", "y_label"):
                        if key not in args and key in chart_ready:
                            args[key] = chart_ready.get(key)
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
                status = "blocked"
                result = {
                    "error": (
                        "Confirmação explícita necessária para criar work item. "
                        "Pede ao utilizador para responder 'confirmo' e tenta novamente."
                    )
                }
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                logger.info(
                    "[ToolCallEvent] %s",
                    json.dumps(
                        {
                            "event": "tool_call_execution",
                            "conv_id": conv_id,
                            "user_sub": user_sub or "anon",
                            "tool": tc.name,
                            "tool_call_id": tc.id,
                            "status": status,
                            "duration_ms": elapsed_ms,
                        },
                        ensure_ascii=False,
                    ),
                )
                tool_metrics.record(tc.name, elapsed_ms, "blocked")
                return tc, result
        try:
            result = await execute_tool(tc.name, args)
            if tc.name == "run_code":
                failed = (
                    bool(result.get("error"))
                    or not bool(result.get("success", False))
                    or _looks_like_run_code_refusal(result)
                )
                if failed:
                    logger.info(
                        "[Agent] run_code falhou; a executar fallback analyze_uploaded_table (conv=%s)",
                        conv_id,
                    )
                    fallback_args: Dict[str, object] = {
                        "query": user_text,
                        "conv_id": conv_id,
                        "user_sub": user_sub,
                        "full_points": True,
                        "top": 5000,
                        "_fallback_from_run_code": True,
                    }
                    fallback = await execute_tool("analyze_uploaded_table", fallback_args)
                    if isinstance(fallback, dict):
                        fallback["_fallback_from"] = "run_code"
                        fallback["_run_code_error"] = str(
                            result.get("error")
                            or result.get("stderr")
                            or result.get("stdout")
                            or result.get("output_text")
                            or ""
                        )[:500]
                        result = fallback
            if isinstance(result, dict) and "error" in result:
                status = "tool_error"
            return tc, result
        except Exception as e:
            status = "exception"
            error_msg = str(e)
            raise
        finally:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            event = {
                "event": "tool_call_execution",
                "conv_id": conv_id,
                "user_sub": user_sub or "anon",
                "tool": tc.name,
                "tool_call_id": tc.id,
                "status": status,
                "duration_ms": elapsed_ms,
            }
            if error_msg:
                event["error"] = error_msg[:300]
            logger.info("[ToolCallEvent] %s", json.dumps(event, ensure_ascii=False))
            tool_metrics.record(tc.name, elapsed_ms, status)
    
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
            safe_user = _safe_blob_component(user_sub or "anon", 80)
            safe_conv = _safe_blob_component(conv_id, 80)
            safe_tool = _safe_blob_component(tc.name, 40)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            blob_name = f"{safe_user}/{safe_conv}/{ts}_{safe_tool}_{_safe_blob_component(tc.id, 60)}.json"

            blob_payload = tool_result
            if PII_ENABLED:
                try:
                    blob_ctx = PIIMaskingContext()
                    serialized = json.dumps(tool_result, ensure_ascii=False, default=str)
                    masked_serialized = await mask_pii(serialized, blob_ctx)
                    try:
                        blob_payload = json.loads(masked_serialized)
                    except json.JSONDecodeError:
                        # Placeholders can turn numeric JSON scalars into strings;
                        # keep a masked serialized fallback rather than leaking PII.
                        blob_payload = {"masked_content": masked_serialized}
                except Exception as mask_err:
                    logger.warning("[Agent] PII masking for blob failed (%s): %s", tc.name, mask_err)
                    # Never fall back to unmasked PII in blob storage.
                    blob_payload = {"error": "pii_masking_failed", "tool": tc.name}

            uploaded = await blob_upload_json(CHAT_TOOLRESULT_BLOB_CONTAINER, blob_name, blob_payload)
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
    start = datetime.now()
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
    tool_definitions = get_all_tool_definitions()

    async with _get_conversation_lock(conv_id):
        await _ensure_conversation(conv_id, mode, partition_key)
        await _ensure_uploaded_files_loaded(conv_id, user_sub=str((user or {}).get("sub", "") or ""))
        await _inject_file_context(conv_id, conversations[conv_id])

        conversations[conv_id].append({"role": "user", "content": request.question})
        conversations[conv_id] = _trim_history(conversations[conv_id])

        quota_reason = _check_token_quota(tier, conv_id)
        if quota_reason:
            answer = (
                f"⚠️ Limite de utilização do tier atingido: {quota_reason}. "
                "Tenta novamente mais tarde ou muda de tier."
            )
            conversations[conv_id].append({"role": "assistant", "content": answer})
            should_persist = True
        else:
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
                ephemeral = await _build_llm_messages(
                    conv_id,
                    request.question,
                    request=request,
                    tier=tier,
                    tools_list=tool_definitions,
                )
                response = await asyncio.wait_for(
                    llm_with_fallback(
                        ephemeral, tools=tool_definitions, tier=tier,
                        temperature=AGENT_TEMPERATURE, max_tokens=AGENT_MAX_TOKENS,
                    ),
                    timeout=_AGENT_LLM_STEP_TIMEOUT_SECONDS,
                )
                _log_fallback_chain_if_needed(response)
                model_used = response.model
                total_usage = response.usage

                iteration = 0
                while iteration < AGENT_MAX_ITERATIONS:
                    current_calls = list(response.tool_calls or [])
                    forced_uploaded_table = False
                    if not current_calls:
                        forced_calls = await _extract_forced_uploaded_table_calls(
                            request.question,
                            conv_id,
                            already_used=tools_used,
                        )
                        if forced_calls:
                            forced_uploaded_table = True
                            current_calls = forced_calls
                            logger.info(
                                "[Agent] forcing analyze_uploaded_table for uploaded tabular intent (conv=%s)",
                                conv_id,
                            )
                            conversations[conv_id].append(_make_tool_calls_assistant_message(current_calls))
                        else:
                            break

                    iteration += 1

                    # Add assistant message with tool calls to history
                    if not forced_uploaded_table:
                        conversations[conv_id].append(
                            make_assistant_message_from_response(response)
                        )

                    # Execute tools
                    tu, td = await _execute_tool_calls(
                        current_calls,
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
                    ephemeral = await _build_llm_messages(
                        conv_id,
                        request.question,
                        request=request,
                        tier=tier,
                        tools_list=tool_definitions,
                    )
                    response = await asyncio.wait_for(
                        llm_with_fallback(
                            ephemeral, tools=tool_definitions, tier=tier,
                            temperature=AGENT_TEMPERATURE, max_tokens=AGENT_MAX_TOKENS,
                        ),
                        timeout=_AGENT_LLM_STEP_TIMEOUT_SECONDS,
                    )
                    _log_fallback_chain_if_needed(response)
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
            _create_logged_task(_persist_conversation(conv_id, partition_key), "persist_conversation_sync_timeout_fallback")
    _record_token_quota(tier, total_usage)
    
    total_time = int((datetime.now() - start).total_seconds() * 1000)
    
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
    start = datetime.now()
    mode = request.mode or "general"
    tier = request.model_tier or LLM_DEFAULT_TIER
    conv_id = request.conversation_id or str(uuid.uuid4())
    partition_key = _user_partition_key(user)

    tools_used = []
    tool_details = []
    total_usage = {}
    model_used = ""
    should_persist = False
    tool_definitions = get_all_tool_definitions()
    
    def _sse(event: dict) -> str:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    
    async with _get_conversation_lock(conv_id):
        await _ensure_conversation(conv_id, mode, partition_key)
        await _ensure_uploaded_files_loaded(conv_id, user_sub=str((user or {}).get("sub", "") or ""))
        await _inject_file_context(conv_id, conversations[conv_id])
        conversations[conv_id].append({"role": "user", "content": request.question})
        conversations[conv_id] = _trim_history(conversations[conv_id])

        # Emit conversation_id immediately
        yield _sse({"type": "init", "conversation_id": conv_id, "mode": mode})

        quota_reason = _check_token_quota(tier, conv_id)
        if quota_reason:
            yield _sse({
                "type": "error",
                "text": f"Limite de utilização do tier atingido: {quota_reason}.",
            })
            yield _sse({
                "type": "done",
                "tools_used": [],
                "tool_details": [],
                "has_exportable_data": False,
                "export_index": None,
                "tokens_used": {},
                "total_time_ms": int((datetime.now() - start).total_seconds() * 1000),
                "model_used": "",
                "conversation_id": conv_id,
            })
            return

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
                ephemeral = await _build_llm_messages(
                    conv_id,
                    request.question,
                    request=request,
                    tier=tier,
                    tools_list=tool_definitions,
                )
                response = await asyncio.wait_for(
                    llm_with_fallback(
                        ephemeral, tools=tool_definitions, tier=tier,
                        temperature=AGENT_TEMPERATURE, max_tokens=AGENT_MAX_TOKENS,
                    ),
                    timeout=_AGENT_LLM_STEP_TIMEOUT_SECONDS,
                )
                _log_fallback_chain_if_needed(response)
                for k, v in response.usage.items():
                    if isinstance(v, (int, float)):
                        total_usage[k] = total_usage.get(k, 0) + v

                current_calls = list(response.tool_calls or [])
                forced_uploaded_table = False
                if not current_calls:
                    forced_calls = await _extract_forced_uploaded_table_calls(
                        request.question,
                        conv_id,
                        already_used=tools_used,
                    )
                    if forced_calls:
                        forced_uploaded_table = True
                        current_calls = forced_calls
                        logger.info(
                            "[Agent] forcing analyze_uploaded_table for uploaded tabular intent (stream conv=%s)",
                            conv_id,
                        )
                        conversations[conv_id].append(_make_tool_calls_assistant_message(current_calls))

                if current_calls:
                    # Add assistant msg with tool calls
                    if not forced_uploaded_table:
                        conversations[conv_id].append(
                            make_assistant_message_from_response(response)
                        )

                    # Signal tool execution
                    for tc in current_calls:
                        suffix = " (forced)" if forced_uploaded_table else ""
                        yield _sse({"type": "tool_start", "tool": tc.name, "text": f"🔍 {tc.name}{suffix}..."})

                    # Execute tools
                    tu, td = await _execute_tool_calls(
                        current_calls,
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
                    # Final text response — stream token by token
                    need_final_response = False

                    if response.content:
                        # Response já obtida via non-streaming (necessário para tool detection).
                        yield _sse({"type": "token", "text": response.content})
                        conversations[conv_id].append({"role": "assistant", "content": response.content})
                        should_persist = True
                    elif not response.tool_calls:
                        ttft_start = time.perf_counter()
                        ttft_logged = False
                        stream_content = ""
                        try:
                            stream_ephemeral = await _build_llm_messages(
                                conv_id,
                                request.question,
                                request=request,
                                tier=tier,
                                tools_list=None,
                            )
                            async for event in llm_stream_with_fallback(
                                stream_ephemeral,
                                tools=None,
                                tier=tier,
                                temperature=AGENT_TEMPERATURE,
                                max_tokens=AGENT_MAX_TOKENS,
                            ):
                                if event.type == "token" and event.text:
                                    if not ttft_logged:
                                        ttft_ms = int((time.perf_counter() - ttft_start) * 1000)
                                        logger.info("[Agent] TTFT=%dms conv=%s", ttft_ms, conv_id)
                                        ttft_logged = True
                                    stream_content += event.text
                                    yield _sse({"type": "token", "text": event.text})
                                elif event.type == "done":
                                    done_data = event.data or {}
                                    if isinstance(done_data, dict):
                                        for k, v in (done_data.get("usage", {}) or {}).items():
                                            if isinstance(v, (int, float)):
                                                total_usage[k] = total_usage.get(k, 0) + v
                        except Exception as stream_err:
                            logger.warning("[Agent] streaming fallback to static: %s", stream_err)
                            if not stream_content:
                                stream_content = "Não consegui processar a tua pergunta."
                                yield _sse({"type": "token", "text": stream_content})

                        if stream_content:
                            conversations[conv_id].append({"role": "assistant", "content": stream_content})
                            should_persist = True
                        else:
                            yield _sse({"type": "token", "text": "Não consegui processar a tua pergunta."})

            if need_final_response:
                # Garantir sempre resposta textual quando o loop atinge limite de iterações.
                fallback_text = _fallback_answer_from_tool_details(tool_details)
                yield _sse({"type": "token", "text": fallback_text})
                conversations[conv_id].append({"role": "assistant", "content": fallback_text})
                should_persist = True

        except Exception as e:
            yield _sse({"type": "error", "text": str(e)})

    if should_persist:
        try:
            await asyncio.wait_for(_persist_conversation(conv_id, partition_key), timeout=8.0)
        except Exception:
            _create_logged_task(_persist_conversation(conv_id, partition_key), "persist_conversation_stream_timeout_fallback")
    _record_token_quota(tier, total_usage)
    
    total_time = int((datetime.now() - start).total_seconds() * 1000)
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
