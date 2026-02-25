# =============================================================================
# tools.py — Tool definitions, implementations e system prompts v7.2
# =============================================================================

import json, base64, asyncio, logging, uuid, re, math, unicodedata
from datetime import datetime, timezone
from collections import deque
from urllib.parse import quote
from typing import Optional
import httpx

from config import (
    DEVOPS_PAT, DEVOPS_ORG, DEVOPS_PROJECT,
    SEARCH_SERVICE, SEARCH_KEY, API_VERSION_SEARCH,
    DEVOPS_INDEX, OMNI_INDEX,
    DEVOPS_FIELDS, DEVOPS_AREAS, DEVOPS_WORKITEM_TYPES,
    AGENT_TOOL_RESULT_MAX_SIZE, AGENT_TOOL_RESULT_KEEP_ITEMS, DEBUG_LOG_SIZE,
    EXPORT_ASYNC_THRESHOLD_ROWS,
    RERANK_ENABLED, RERANK_ENDPOINT, RERANK_API_KEY, RERANK_MODEL,
    RERANK_TOP_N, RERANK_TIMEOUT_SECONDS, RERANK_AUTH_MODE,
    UPLOAD_INDEX_TOP, GENERATED_FILES_BLOB_CONTAINER,
)
from llm_provider import get_embedding_provider, llm_simple
from export_engine import to_csv, to_xlsx, to_pdf
from storage import (
    table_query,
    table_insert,
    table_merge,
    blob_upload_bytes,
    blob_upload_json,
    blob_download_bytes,
    blob_download_json,
    parse_blob_ref,
)
from tool_registry import (
    register_tool,
    has_tool,
    execute_tool as registry_execute_tool,
    get_all_tool_definitions as registry_get_all_tool_definitions,
)

_devops_debug_log: deque = deque(maxlen=DEBUG_LOG_SIZE)
def get_devops_debug_log(): return list(_devops_debug_log)
def _log(msg):
    _devops_debug_log.append({"ts": datetime.now().isoformat(), "msg": msg})
    logging.info("[Tools] %s", msg)

_generated_files_store = {}
_generated_files_lock = asyncio.Lock()
_GENERATED_FILE_TTL_SECONDS = 30 * 60
_GENERATED_FILE_MAX = 100
_GENERATED_FILE_MAX_TOTAL_BYTES = 500 * 1024 * 1024  # 500 MB
_AUTO_EXPORT_MIN_ROWS = 25
_WRITER_PROFILE_PARTITION = "writer"
_WIQL_BLOCKLIST_RE = re.compile(
    r"(?i)(;|--|/\*|\*/|\b(select|drop|delete|update|insert|merge|exec|execute|union)\b)"
)
_WORKITEM_TYPE_MAP = {str(t).strip().lower(): str(t).strip() for t in DEVOPS_WORKITEM_TYPES}


def _normalize_author(author_name: str) -> str:
    return " ".join((author_name or "").strip().lower().split())


def _writer_profile_row_key(author_name: str) -> str:
    base = _normalize_author(author_name)
    if not base:
        return ""
    safe = (
        base.replace("/", "_")
        .replace("\\", "_")
        .replace("#", "_")
        .replace("?", "_")
        .replace("'", "_")
        .replace('"', "_")
    )
    return safe[:120]


async def _save_writer_profile(
    author_name: str,
    analysis: str,
    sample_ids=None,
    sample_count: int = 0,
    topic: str = "",
    work_item_type: str = "User Story",
) -> bool:
    row_key = _writer_profile_row_key(author_name)
    if not row_key or not analysis:
        return False

    now_iso = datetime.now(timezone.utc).isoformat()
    entity = {
        "PartitionKey": _WRITER_PROFILE_PARTITION,
        "RowKey": row_key,
        "AuthorName": (author_name or "").strip()[:200],
        "AuthorLower": _normalize_author(author_name)[:200],
        "StyleAnalysis": analysis[:20000],
        "SampleCount": int(sample_count or 0),
        "SampleIdsJson": json.dumps((sample_ids or [])[:100], ensure_ascii=False),
        "Topic": (topic or "")[:200],
        "WorkItemType": (work_item_type or "User Story")[:80],
        "UpdatedAt": now_iso,
    }

    try:
        existing = await table_query(
            "WriterProfiles",
            f"PartitionKey eq '{_WRITER_PROFILE_PARTITION}' and RowKey eq '{row_key}'",
            top=1,
        )
        if existing:
            await table_merge("WriterProfiles", entity)
        else:
            entity["CreatedAt"] = now_iso
            inserted = await table_insert("WriterProfiles", entity)
            if not inserted:
                logging.error("[Tools] _save_writer_profile insert returned False")
                return False
        return True
    except Exception as e:
        logging.error("[Tools] _save_writer_profile failed: %s", e)
        return False


async def _load_writer_profile(author_name: str):
    row_key = _writer_profile_row_key(author_name)
    if not row_key:
        return None

    try:
        rows = await table_query(
            "WriterProfiles",
            f"PartitionKey eq '{_WRITER_PROFILE_PARTITION}' and RowKey eq '{row_key}'",
            top=1,
        )
        if not rows:
            return None
        row = rows[0]
        sample_ids = []
        raw_ids = row.get("SampleIdsJson", "[]")
        try:
            sample_ids = json.loads(raw_ids) if raw_ids else []
        except Exception as e:
            logging.warning("[Tools] _load_writer_profile sample ids parse failed: %s", e)
        return {
            "author_name": row.get("AuthorName", author_name),
            "style_analysis": row.get("StyleAnalysis", ""),
            "sample_count": int(row.get("SampleCount", 0) or 0),
            "sample_ids": sample_ids if isinstance(sample_ids, list) else [],
            "topic": row.get("Topic", ""),
            "work_item_type": row.get("WorkItemType", "User Story"),
            "updated_at": row.get("UpdatedAt", ""),
        }
    except Exception as e:
        logging.error("[Tools] _load_writer_profile failed: %s", e)
        return None


def _as_dt(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    txt = str(value or "").strip()
    if not txt:
        return None
    try:
        dt = datetime.fromisoformat(txt)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _generated_blob_paths(download_id: str, fmt: str = "") -> tuple[str, str]:
    safe_id = "".join(c if c.isalnum() else "_" for c in str(download_id or "").strip())[:80] or "file"
    ext = "".join(c if c.isalnum() else "" for c in str(fmt or "").lower())[:10]
    ext = ext or "bin"
    base = f"generated/{safe_id}"
    return f"{base}/content.{ext}", f"{base}/meta.json"


async def _cleanup_generated_files() -> None:
    async with _generated_files_lock:
        now = datetime.now(timezone.utc)
        expired_ids = [
            fid for fid, meta in _generated_files_store.items()
            if (
                (now - (_as_dt(meta.get("created_at")) or now)).total_seconds()
                > _GENERATED_FILE_TTL_SECONDS
            )
        ]
        for fid in expired_ids:
            _generated_files_store.pop(fid, None)

        def _total_bytes() -> int:
            total = 0
            for meta in _generated_files_store.values():
                content = meta.get("content", b"")
                if isinstance(content, (bytes, bytearray)):
                    total += len(content)
            return total

        while (
            len(_generated_files_store) > _GENERATED_FILE_MAX
            or _total_bytes() > _GENERATED_FILE_MAX_TOTAL_BYTES
        ):
            oldest_id = min(
                _generated_files_store.items(),
                key=lambda item: item[1].get("created_at", now),
            )[0]
            _generated_files_store.pop(oldest_id, None)


async def _store_generated_file(content: bytes, mime_type: str, filename: str, fmt: str) -> str:
    if len(content) > _GENERATED_FILE_MAX_TOTAL_BYTES:
        logging.error(
            "[Tools] generated file too large: %s bytes (max %s)",
            len(content),
            _GENERATED_FILE_MAX_TOTAL_BYTES,
        )
        return ""
    await _cleanup_generated_files()
    fid = uuid.uuid4().hex
    async with _generated_files_lock:
        _generated_files_store[fid] = {
            "content": content,
            "mime_type": mime_type,
            "filename": filename,
            "format": fmt,
            "created_at": datetime.now(timezone.utc),
        }
    try:
        content_blob_name, meta_blob_name = _generated_blob_paths(fid, fmt)
        await blob_upload_bytes(
            GENERATED_FILES_BLOB_CONTAINER,
            content_blob_name,
            content,
            content_type=mime_type or "application/octet-stream",
        )
        await blob_upload_json(
            GENERATED_FILES_BLOB_CONTAINER,
            meta_blob_name,
            {
                "download_id": fid,
                "filename": filename,
                "mime_type": mime_type,
                "format": fmt,
                "size_bytes": len(content),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "ttl_seconds": _GENERATED_FILE_TTL_SECONDS,
                "content_blob_name": content_blob_name,
            },
        )
    except Exception as e:
        logging.warning("[Tools] persistent generated file store failed for %s: %s", fid, e)
    await _cleanup_generated_files()
    return fid


async def _attach_auto_csv_export(result: dict, title_hint: str, min_rows: int = _AUTO_EXPORT_MIN_ROWS) -> None:
    """Para resultados pesados, gera CSV completo automaticamente."""
    if not isinstance(result, dict):
        return
    items = result.get("items")
    if not isinstance(items, list):
        return
    total = int(result.get("total_count", len(items)) or 0)
    if total < min_rows or len(items) < min_rows:
        return
    if total >= max(100, EXPORT_ASYNC_THRESHOLD_ROWS):
        # Evita trabalho pesado inline; export pesado deve ir para worker assíncrono.
        result["_auto_export_deferred"] = True
        result["_auto_export_reason"] = "heavy_result_async_recommended"
        return
    if result.get("_auto_file_downloads"):
        return

    try:
        payload = {"items": items, "total_count": total}
        buf = to_csv(payload)
        content = buf.getvalue()
        if not content:
            return
        base_name = "".join(ch if ch.isalnum() or ch in " _-" else "_" for ch in str(title_hint or "export_completo")).strip()
        base_name = (base_name or "export_completo")[:50]
        filename = f"{base_name}.csv"
        download_id = await _store_generated_file(content, "text/csv", filename, "csv")
        if not download_id:
            return
        result["_auto_file_downloads"] = [
            {
                "download_id": download_id,
                "endpoint": f"/api/download/{download_id}",
                "filename": filename,
                "format": "csv",
                "mime_type": "text/csv",
                "size_bytes": len(content),
                "expires_in_seconds": _GENERATED_FILE_TTL_SECONDS,
                "auto_generated": True,
                "scope": "full_result",
            }
        ]
    except Exception as e:
        logging.warning("[Tools] auto csv export skipped: %s", e)


async def get_generated_file(download_id: str):
    await _cleanup_generated_files()
    entry = _generated_files_store.get(download_id)
    if entry:
        created_at = _as_dt(entry.get("created_at")) or datetime.now(timezone.utc)
        if (datetime.now(timezone.utc) - created_at).total_seconds() <= _GENERATED_FILE_TTL_SECONDS:
            return entry
        async with _generated_files_lock:
            _generated_files_store.pop(download_id, None)

    # Cross-instance fallback: load metadata/content from Blob Storage.
    try:
        _, meta_blob_name = _generated_blob_paths(download_id)
        meta = await blob_download_json(GENERATED_FILES_BLOB_CONTAINER, meta_blob_name)
        if not isinstance(meta, dict) or not meta:
            return None

        created_at = _as_dt(meta.get("created_at"))
        ttl_seconds = int(meta.get("ttl_seconds", _GENERATED_FILE_TTL_SECONDS) or _GENERATED_FILE_TTL_SECONDS)
        if created_at and (datetime.now(timezone.utc) - created_at).total_seconds() > max(60, ttl_seconds):
            return None

        blob_name = str(meta.get("content_blob_name", "") or "")
        if not blob_name:
            fmt = str(meta.get("format", "") or "")
            blob_name, _ = _generated_blob_paths(download_id, fmt)
        content = await blob_download_bytes(GENERATED_FILES_BLOB_CONTAINER, blob_name)
        if not content:
            return None

        hydrated = {
            "content": content,
            "mime_type": str(meta.get("mime_type", "") or "application/octet-stream"),
            "filename": str(meta.get("filename", "") or f"download-{download_id}"),
            "format": str(meta.get("format", "") or ""),
            "created_at": created_at or datetime.now(timezone.utc),
        }
        async with _generated_files_lock:
            _generated_files_store[download_id] = hydrated
        await _cleanup_generated_files()
        return hydrated
    except Exception as e:
        logging.warning("[Tools] get_generated_file persistent fallback failed for %s: %s", download_id, e)
        return None

# --- DevOps helpers ---
async def _devops_request_with_retry(client, method, url, headers, json_body=None, max_retries=5):
    last_status = None
    for attempt in range(max_retries):
        try:
            resp = await (client.post(url, json=json_body, headers=headers) if method == "POST" else client.get(url, headers=headers))
            last_status = resp.status_code
            if resp.status_code == 429:
                wait = min(int(resp.headers.get("Retry-After", 3*(attempt+1))), 30)
                _log(f"429, attempt {attempt+1}/{max_retries}, wait {wait}s")
                await asyncio.sleep(wait); continue
            if resp.status_code >= 500:
                await asyncio.sleep(2*(attempt+1)); continue
            if resp.status_code >= 400:
                _log(f"{resp.status_code}: {resp.text[:200]}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if attempt == max_retries-1: return {"error": f"DevOps {e.response.status_code}: {e.response.text[:200]}"}
            await asyncio.sleep(1)
        except httpx.TimeoutException:
            if attempt == max_retries-1: return {"error": f"DevOps timeout após {max_retries} tentativas"}
            await asyncio.sleep(2*(attempt+1))
        except Exception as e:
            if attempt == max_retries-1: return {"error": f"DevOps erro: {str(e)}"}
    return {"error": f"Max retries (last status: {last_status})"}


async def _search_request_with_retry(url, headers, json_body, max_retries=3):
    """POST ao Azure AI Search com retries para 429/5xx/timeouts."""
    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(1, max_retries + 1):
            try:
                resp = await client.post(url, json=json_body, headers=headers)

                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    try:
                        wait = int(float(retry_after)) if retry_after is not None else 2 ** (attempt - 1)
                    except (TypeError, ValueError):
                        wait = 2 ** (attempt - 1)
                    wait = max(1, min(wait, 30))
                    if attempt == max_retries:
                        logging.warning(
                            "[Search] 429 attempt %s/%s, sem retries restantes",
                            attempt, max_retries,
                        )
                        return {"error": f"Search 429 após {max_retries} tentativas"}
                    logging.warning(
                        "[Search] 429 attempt %s/%s, retry em %ss",
                        attempt, max_retries, wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code >= 500:
                    wait = min(2 ** (attempt - 1), 30)
                    if attempt == max_retries:
                        logging.warning(
                            "[Search] %s attempt %s/%s, sem retries restantes",
                            resp.status_code, attempt, max_retries,
                        )
                        return {"error": f"Search {resp.status_code} após {max_retries} tentativas"}
                    logging.warning(
                        "[Search] %s attempt %s/%s, retry em %ss",
                        resp.status_code, attempt, max_retries, wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code >= 400:
                    return {"error": f"Search {resp.status_code}: {resp.text[:200]}"}

                return resp.json()

            except httpx.TimeoutException:
                wait = min(2 ** (attempt - 1), 30)
                if attempt == max_retries:
                    logging.warning(
                        "[Search] timeout attempt %s/%s, sem retries restantes",
                        attempt, max_retries,
                    )
                    return {"error": f"Search timeout após {max_retries} tentativas"}
                logging.warning(
                    "[Search] timeout attempt %s/%s, retry em %ss",
                    attempt, max_retries, wait,
                )
                await asyncio.sleep(wait)
            except httpx.RequestError as e:
                wait = min(2 ** (attempt - 1), 30)
                if attempt == max_retries:
                    logging.warning(
                        "[Search] request error attempt %s/%s (%s), sem retries restantes",
                        attempt, max_retries, str(e),
                    )
                    return {"error": f"Search request error após {max_retries} tentativas: {str(e)}"}
                logging.warning(
                    "[Search] request error attempt %s/%s (%s), retry em %ss",
                    attempt, max_retries, str(e), wait,
                )
                await asyncio.sleep(wait)
            except Exception as e:
                wait = min(2 ** (attempt - 1), 30)
                if attempt == max_retries:
                    logging.warning(
                        "[Search] erro inesperado attempt %s/%s (%s), sem retries restantes",
                        attempt, max_retries, str(e),
                    )
                    return {"error": f"Search erro: {str(e)}"}
                logging.warning(
                    "[Search] erro inesperado attempt %s/%s (%s), retry em %ss",
                    attempt, max_retries, str(e), wait,
                )
                await asyncio.sleep(wait)

    return {"error": "Search erro desconhecido"}


def _build_rerank_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    token = str(RERANK_API_KEY or "").strip()
    mode = str(RERANK_AUTH_MODE or "").strip().lower()
    if token:
        if mode == "bearer":
            headers["Authorization"] = f"Bearer {token}"
        elif mode == "api-key":
            headers["api-key"] = token
    return headers


def _rerank_document_from_item(item: dict) -> str:
    parts = []
    for key in ("title", "content", "tag", "status", "type", "state", "area"):
        val = str((item or {}).get(key, "") or "").strip()
        if val:
            parts.append(val)
    return "\n".join(parts)[:8000]


def _build_chat_rerank_payload(query: str, documents: list, top_n: int) -> dict:
    safe_query = str(query or "").strip()[:2000]
    docs_lines = []
    for idx, doc in enumerate(documents):
        compact = " ".join(str(doc or "").split())
        docs_lines.append(f"{idx}: {compact[:1400]}")
    docs_block = "\n".join(docs_lines)
    system_prompt = (
        "You are a strict retrieval reranker. "
        "Return ONLY JSON with this exact shape: "
        "{\"results\":[{\"index\":0,\"relevance_score\":1.0}]}. "
        "Use indexes from provided documents and sort descending by relevance."
    )
    user_prompt = (
        f"Query:\n{safe_query}\n\n"
        f"TopN: {top_n}\n"
        "Documents (index: content):\n"
        f"{docs_block}\n\n"
        "Return only JSON."
    )
    return {
        "model": RERANK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "max_completion_tokens": max(256, min(1400, top_n * 48)),
    }


def _extract_rerank_rows_from_chat_response(data: dict) -> list:
    if not isinstance(data, dict):
        return []
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return []
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = message.get("content", "")
    text = ""
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                parts.append(str(part.get("text", "") or ""))
        text = "\n".join(parts).strip()
    if not text:
        return []

    payload = None
    try:
        payload = json.loads(text)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return []
        try:
            payload = json.loads(match.group(0))
        except Exception:
            return []
    if not isinstance(payload, dict):
        return []
    rows = payload.get("results")
    if isinstance(rows, list):
        return rows
    rows = payload.get("data")
    if isinstance(rows, list):
        return rows
    return []


async def _rerank_items_post_retrieval(query: str, items: list) -> tuple[list, dict]:
    if not isinstance(items, list):
        return items, {"applied": False, "reason": "invalid_items"}
    if len(items) < 2:
        return items, {"applied": False, "reason": "too_few_items"}
    if not RERANK_ENABLED:
        return items, {"applied": False, "reason": "disabled"}
    if not RERANK_ENDPOINT:
        return items, {"applied": False, "reason": "missing_endpoint"}
    if str(RERANK_AUTH_MODE or "").strip().lower() in ("api-key", "bearer") and not RERANK_API_KEY:
        return items, {"applied": False, "reason": "missing_api_key"}

    top_n = max(1, min(int(RERANK_TOP_N or len(items)), len(items)))
    documents = [_rerank_document_from_item(item) for item in items]
    endpoint_lower = str(RERANK_ENDPOINT or "").strip().lower()
    use_chat_completions = "/chat/completions" in endpoint_lower
    payload = (
        _build_chat_rerank_payload(query, documents, top_n)
        if use_chat_completions
        else {
            "model": RERANK_MODEL,
            "query": str(query or "")[:2000],
            "documents": documents,
            "top_n": top_n,
        }
    )
    headers = _build_rerank_headers()

    try:
        async with httpx.AsyncClient(timeout=RERANK_TIMEOUT_SECONDS) as client:
            resp = await client.post(RERANK_ENDPOINT, headers=headers, json=payload)
        if resp.status_code >= 400:
            logging.warning("[Tools] rerank HTTP %s: %s", resp.status_code, resp.text[:300])
            return items, {"applied": False, "reason": f"http_{resp.status_code}"}

        data = resp.json()
    except Exception as e:
        logging.warning("[Tools] rerank request failed: %s", e)
        return items, {"applied": False, "reason": "request_failed"}

    ranked_rows = data.get("results")
    if not isinstance(ranked_rows, list):
        ranked_rows = data.get("data")
    if not isinstance(ranked_rows, list) and use_chat_completions:
        ranked_rows = _extract_rerank_rows_from_chat_response(data)
    if not isinstance(ranked_rows, list):
        return items, {"applied": False, "reason": "invalid_response"}

    ranked_items = []
    used_indexes = set()
    for row in ranked_rows:
        if not isinstance(row, dict):
            continue
        idx = row.get("index")
        if not isinstance(idx, int):
            continue
        if idx < 0 or idx >= len(items):
            continue
        if idx in used_indexes:
            continue
        cloned = dict(items[idx])
        score = row.get("relevance_score", row.get("score"))
        try:
            if score is not None:
                cloned["rerank_score"] = round(float(score), 6)
        except Exception:
            pass
        ranked_items.append(cloned)
        used_indexes.add(idx)

    if not ranked_items:
        return items, {"applied": False, "reason": "empty_results"}

    for idx, item in enumerate(items):
        if idx not in used_indexes:
            ranked_items.append(item)

    return ranked_items, {
        "applied": True,
        "model": RERANK_MODEL,
        "input_count": len(items),
        "ranked_count": len(ranked_rows),
        "top_n": top_n,
    }

def _devops_headers():
    return {"Authorization": f"Basic {base64.b64encode(f':{DEVOPS_PAT}'.encode()).decode()}", "Content-Type": "application/json"}

def _devops_url(path):
    return f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_apis/{path}"

def _format_wi(item):
    f = item.get("fields", {})
    a = f.get("System.AssignedTo", {}); c = f.get("System.CreatedBy", {})
    result = {
        "id": item["id"], "type": f.get("System.WorkItemType",""),
        "title": f.get("System.Title","").replace(" | "," — "), "state": f.get("System.State",""),
        "area": f.get("System.AreaPath",""),
        "assigned_to": a.get("displayName","") if isinstance(a,dict) else str(a),
        "created_by": c.get("displayName","") if isinstance(c,dict) else str(c),
        "created_date": f.get("System.CreatedDate",""),
        "url": f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_workitems/edit/{item['id']}",
    }
    # Include extra fields when present (Description, AcceptanceCriteria, Tags)
    desc = f.get("System.Description", "")
    ac = f.get("Microsoft.VSTS.Common.AcceptanceCriteria", "")
    tags = f.get("System.Tags", "")
    if desc: result["description"] = (desc or "")[:3000]
    if ac: result["acceptance_criteria"] = (ac or "")[:3000]
    if tags: result["tags"] = tags
    return result


def _safe_wiql_literal(value: str, max_len: int = 200) -> str:
    text = str(value or "").strip()
    if max_len > 0:
        text = text[:max_len]
    return text.replace("'", "''")


def _normalize_match_text(value: str) -> str:
    lowered = str(value or "").lower()
    deaccented = unicodedata.normalize("NFKD", lowered)
    clean = "".join(ch for ch in deaccented if not unicodedata.combining(ch))
    clean = clean.replace("|", " ").replace("—", " ").replace("-", " ").replace("_", " ")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _canonicalize_area_path(area_path: str) -> str:
    raw = str(area_path or "").strip()
    if not raw:
        return ""
    if "\\" in raw:
        return raw
    norm = _normalize_match_text(raw)
    if not norm:
        return raw
    for known in DEVOPS_AREAS:
        known_norm = _normalize_match_text(known)
        if known_norm.endswith(norm) or norm in known_norm:
            return known
    return raw


def _sanitize_wiql_where(wiql_where: str) -> str:
    where = str(wiql_where or "").strip()
    if where.lower().startswith("where "):
        where = where[6:].strip()
    if not where:
        raise ValueError("wiql_where vazio")
    if len(where) > 2000:
        raise ValueError("wiql_where demasiado longo (max 2000 chars)")
    if _WIQL_BLOCKLIST_RE.search(where):
        raise ValueError("wiql_where contém tokens proibidos")
    if where.count("'") % 2 != 0:
        raise ValueError("wiql_where com aspas simples não balanceadas")
    return where


def _extract_json_object(text: str):
    if not isinstance(text, str):
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    candidate = text[start:end + 1]
    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _validate_workitem_type(value: str, default: str = "User Story") -> str:
    candidate = str(value or default).strip().lower()
    safe = _WORKITEM_TYPE_MAP.get(candidate)
    if not safe:
        raise ValueError(
            f"Tipo de work item inválido: '{value}'. Permitidos: {', '.join(DEVOPS_WORKITEM_TYPES)}"
        )
    return safe


async def _resolve_parent_id_by_title_hint(
    client,
    headers: dict,
    *,
    parent_type: str,
    area_path: str = "",
    title_hint: str = "",
) -> tuple[Optional[int], dict]:
    hint_raw = str(title_hint or "").strip()
    hint_norm = _normalize_match_text(hint_raw)
    score_terms = [t for t in hint_norm.split(" ") if t][:8]
    wiql_terms_src = re.sub(r"[|—\\-_]", " ", hint_raw)
    wiql_terms_src = re.sub(r"\s+", " ", wiql_terms_src).strip()
    wiql_terms = [t for t in wiql_terms_src.split(" ") if t][:8]
    if not wiql_terms:
        wiql_terms = score_terms[:]
    if not score_terms:
        score_terms = [_normalize_match_text(t) for t in wiql_terms]
        score_terms = [t for t in score_terms if t][:8]
    if not score_terms:
        return None, {"attempted": False}

    parent_type_norm = str(parent_type or "").strip().lower()
    apply_area_filter = bool(area_path and parent_type_norm != "epic")
    base_conds = [
        f"[System.TeamProject] = '{_safe_wiql_literal(DEVOPS_PROJECT, 120)}'",
        f"[System.WorkItemType] = '{_safe_wiql_literal(parent_type, 80)}'",
    ]
    if apply_area_filter:
        base_conds.append(f"[System.AreaPath] UNDER '{_safe_wiql_literal(area_path, 300)}'")
    strict_conds = list(base_conds)
    for term in wiql_terms:
        strict_conds.append(f"[System.Title] CONTAINS '{_safe_wiql_literal(term, 80)}'")

    wiql = (
        "SELECT [System.Id] FROM WorkItems "
        f"WHERE {' AND '.join(strict_conds)} "
        "ORDER BY [System.ChangedDate] DESC"
    )
    resp = await _devops_request_with_retry(
        client,
        "POST",
        _devops_url("wit/wiql?api-version=7.1"),
        headers,
        {"query": wiql},
    )
    if "error" in resp:
        return None, {
            "attempted": True,
            "area_filter_applied": apply_area_filter,
            "error": resp.get("error", "resolve_parent_failed"),
            "wiql_terms": wiql_terms,
        }

    ids = [wi.get("id") for wi in resp.get("workItems", []) if wi.get("id")]
    fallback_broad_used = False
    if not ids and wiql_terms:
        fallback_wiql = (
            "SELECT [System.Id] FROM WorkItems "
            f"WHERE {' AND '.join(base_conds)} "
            "ORDER BY [System.ChangedDate] DESC"
        )
        fallback_resp = await _devops_request_with_retry(
            client,
            "POST",
            _devops_url("wit/wiql?api-version=7.1"),
            headers,
            {"query": fallback_wiql},
        )
        if "error" not in fallback_resp:
            ids = [wi.get("id") for wi in fallback_resp.get("workItems", []) if wi.get("id")]
            fallback_broad_used = True

    if not ids:
        return None, {
            "attempted": True,
            "area_filter_applied": apply_area_filter,
            "matched_candidates": 0,
            "wiql_terms": wiql_terms,
            "fallback_broad_used": fallback_broad_used,
        }

    batch_ids = ids[: min(50, len(ids))]
    det = await _devops_request_with_retry(
        client,
        "POST",
        _devops_url("wit/workitemsbatch?api-version=7.1"),
        headers,
        {"ids": batch_ids, "fields": ["System.Id", "System.Title", "System.WorkItemType", "System.AreaPath"]},
    )
    if "error" in det:
        return None, {
            "attempted": True,
            "area_filter_applied": apply_area_filter,
            "matched_candidates": len(ids),
            "error": det.get("error", "resolve_parent_batch_failed"),
            "wiql_terms": wiql_terms,
            "fallback_broad_used": fallback_broad_used,
        }

    best_id = None
    best_score = -1
    exact_hits = 0
    exact_title_hits = 0
    for it in det.get("value", []):
        f = it.get("fields", {})
        title_norm = _normalize_match_text(str(f.get("System.Title", "") or ""))
        score = sum(1 for term in score_terms if term in title_norm)
        if score_terms and score == len(score_terms):
            exact_hits += 1
        if hint_norm and title_norm == hint_norm:
            exact_title_hits += 1
            score += 100
        elif hint_norm and title_norm.startswith(hint_norm):
            score += 20
        if score > best_score:
            best_score = score
            best_id = it.get("id")

    if best_id is None:
        return None, {
            "attempted": True,
            "area_filter_applied": apply_area_filter,
            "matched_candidates": len(ids),
            "scored_candidates": len(det.get("value", [])),
            "wiql_terms": wiql_terms,
            "fallback_broad_used": fallback_broad_used,
        }
    return int(best_id), {
        "attempted": True,
        "area_filter_applied": apply_area_filter,
        "matched_candidates": len(ids),
        "scored_candidates": len(det.get("value", [])),
        "best_score": best_score,
        "max_score": len(score_terms),
        "exact_hits": exact_hits,
        "exact_title_hits": exact_title_hits,
        "wiql_terms": wiql_terms,
        "fallback_broad_used": fallback_broad_used,
    }

async def get_embedding(text):
    try:
        return await get_embedding_provider().embed(text[:8000].strip() or " ")
    except Exception as e:
        logging.error("[Tools] get_embedding failed: %s", e)
        return None

# =============================================================================
# TOOL 1: query_workitems
# =============================================================================
async def tool_query_workitems(wiql_where, fields=None, top=200):
    _log(f"query_workitems: top={top}, wiql={str(wiql_where)[:80]}...")
    try:
        safe_where = _sanitize_wiql_where(wiql_where)
    except ValueError as e:
        return {"error": f"WIQL inválido: {e}"}
    use_fields = fields if fields and len(fields) > 0 else DEVOPS_FIELDS
    wiql = (
        "SELECT [System.Id] FROM WorkItems "
        f"WHERE [System.TeamProject] = '{_safe_wiql_literal(DEVOPS_PROJECT, 120)}' "
        f"AND {safe_where} ORDER BY [System.ChangedDate] DESC"
    )
    headers = _devops_headers()
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await _devops_request_with_retry(client, "POST", _devops_url("wit/wiql?api-version=7.1"), headers, {"query": wiql})
        if "error" in resp: return resp
        work_items = resp.get("workItems", [])
        total_count = len(work_items)
        if top == 0: return {"total_count": total_count, "items": []}
        work_items = work_items[:min(top, 1000) if top > 0 else total_count]
        if not work_items: return {"total_count": 0, "items": []}
        await asyncio.sleep(0.5)
        all_details, failed_ids, ids = [], [], [wi["id"] for wi in work_items]
        for i in range(0, len(ids), 100):
            batch = ids[i:i+100]
            r = await _devops_request_with_retry(client, "POST", _devops_url("wit/workitemsbatch?api-version=7.1"), headers, {"ids": batch, "fields": use_fields})
            if "error" in r: failed_ids.extend(batch); await asyncio.sleep(3); continue
            all_details.extend(r.get("value",[])); await asyncio.sleep(0.5)
        if failed_ids and len(failed_ids) <= 50:
            await asyncio.sleep(2)
            fl = ",".join(use_fields)
            for fid in failed_ids[:]:
                r = await _devops_request_with_retry(client, "GET", _devops_url(f"wit/workitems/{fid}?fields={fl}&api-version=7.1"), headers, max_retries=3)
                if "error" not in r and "id" in r: all_details.append(r); failed_ids.remove(fid)
                await asyncio.sleep(0.3)
        items = [_format_wi(it) for it in all_details]
        if failed_ids and not items:
            items = [{"id":fid,"type":"","title":"(rate limited)","state":"","url":f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_workitems/edit/{fid}"} for fid in failed_ids]
        result = {"total_count": total_count, "items_returned": len(items), "items": items}
        await _attach_auto_csv_export(result, title_hint=f"query_workitems_{datetime.now().strftime('%Y%m%d_%H%M')}")
        if failed_ids: result["_partial"] = True; result["_failed_batch_count"] = len(failed_ids)
        return result

# =============================================================================
# TOOL 2: search_workitems
# =============================================================================
async def tool_search_workitems(query, top=30, filter_expr=None):
    emb = await get_embedding(query)
    if not emb: return {"error": "Falha embedding"}
    body = {"vectorQueries":[{"kind":"vector","vector":emb,"fields":"content_vector","k":top}],"select":"id,content,url,tag,status","top":top}
    if filter_expr: body["filter"] = filter_expr
    url = f"https://{SEARCH_SERVICE}.search.windows.net/indexes/{DEVOPS_INDEX}/docs/search?api-version={API_VERSION_SEARCH}"
    data = await _search_request_with_retry(
        url=url,
        headers={"api-key": SEARCH_KEY, "Content-Type": "application/json"},
        json_body=body,
        max_retries=3,
    )
    if "error" in data:
        return {"error": data["error"]}
    items = []
    for d in data.get("value",[]):
        ct = d.get("content","")
        items.append({"id":d.get("id",""),"title":ct.split("]")[0].replace("[","") if "]" in ct else ct[:100],"content":ct[:500],"status":d.get("status",""),"url":d.get("url",""),"score":round(d.get("@search.score",0),4)})
    items, rerank_meta = await _rerank_items_post_retrieval(query, items)
    result = {"total_results": len(items), "items": items}
    if rerank_meta.get("applied"):
        result["_rerank"] = rerank_meta
    return result

# =============================================================================
# TOOL 3: search_website
# =============================================================================
async def tool_search_website(query, top=10):
    emb = await get_embedding(query)
    if not emb: return {"error": "Falha embedding"}
    body = {"vectorQueries":[{"kind":"vector","vector":emb,"fields":"content_vector","k":top}],"select":"id,content,url,tag","top":top}
    url = f"https://{SEARCH_SERVICE}.search.windows.net/indexes/{OMNI_INDEX}/docs/search?api-version={API_VERSION_SEARCH}"
    data = await _search_request_with_retry(
        url=url,
        headers={"api-key": SEARCH_KEY, "Content-Type": "application/json"},
        json_body=body,
        max_retries=3,
    )
    if "error" in data:
        return {"error": data["error"]}
    items = [
        {
            "id": d.get("id", ""),
            "content": d.get("content", "")[:500],
            "url": d.get("url", ""),
            "tag": d.get("tag", ""),
            "score": round(d.get("@search.score", 0), 4),
        }
        for d in data.get("value", [])
    ]
    items, rerank_meta = await _rerank_items_post_retrieval(query, items)
    result = {"total_results": len(items), "items": items}
    if rerank_meta.get("applied"):
        result["_rerank"] = rerank_meta
    return result


def _cosine_similarity(vec_a, vec_b):
    if not isinstance(vec_a, list) or not isinstance(vec_b, list):
        return -1.0
    if not vec_a or not vec_b:
        return -1.0
    size = min(len(vec_a), len(vec_b))
    if size <= 0:
        return -1.0
    a = vec_a[:size]
    b = vec_b[:size]
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return -1.0
    return dot / (norm_a * norm_b)


async def _load_indexed_chunks(conv_id: str, user_sub: str = ""):
    safe_conv = str(conv_id or "").strip().replace("'", "''")
    if not safe_conv:
        return []
    safe_user = str(user_sub or "").strip()
    try:
        rows = await table_query("UploadIndex", f"PartitionKey eq '{safe_conv}'", top=max(1, min(UPLOAD_INDEX_TOP, 500)))
    except Exception as e:
        logging.error("[Tools] _load_indexed_chunks table query failed: %s", e)
        rows = []
    chunk_pool = []
    for row in rows:
        owner_sub = str(row.get("UserSub", "") or "")
        if safe_user:
            # Segurança: impedir leitura de chunks de outros utilizadores.
            if not owner_sub or owner_sub != safe_user:
                continue
        has_chunks = str(row.get("HasChunks", "")).lower() in ("true", "1")
        if not has_chunks:
            continue
        filename = str(row.get("Filename", "") or "")
        chunk_ref = str(row.get("ChunksBlobRef", "") or "")
        container, blob_name = parse_blob_ref(chunk_ref)
        if not container or not blob_name:
            continue
        try:
            payload = await blob_download_json(container, blob_name)
        except Exception as e:
            logging.warning("[Tools] _load_indexed_chunks blob read failed for %s: %s", chunk_ref, e)
            continue
        chunks = []
        if isinstance(payload, dict):
            chunks = payload.get("chunks", []) if isinstance(payload.get("chunks"), list) else []
        if not chunks:
            continue
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            chunk_pool.append((filename, chunk))
    return chunk_pool


def _resolve_uploaded_files_memory(conv_id: str = "", user_sub: str = ""):
    try:
        from agent import uploaded_files_store  # import lazy para evitar ciclo no import-time
    except Exception as e:
        logging.error("[Tools] search_uploaded_document cannot import uploaded_files_store: %s", e)
        return None, []

    requested = (conv_id or "").strip()
    safe_user = str(user_sub or "").strip()
    if requested:
        raw = uploaded_files_store.get(requested)
        if isinstance(raw, dict) and isinstance(raw.get("files"), list):
            files = raw.get("files", [])
            if safe_user:
                files = [f for f in files if str((f or {}).get("user_sub", "") or "") == safe_user]
            return requested, files
        if isinstance(raw, dict) and raw:
            files = [raw]
            if safe_user:
                files = [f for f in files if str((f or {}).get("user_sub", "") or "") == safe_user]
            return requested, files
        return requested, []
    return None, []


async def tool_search_uploaded_document(query: str = "", conv_id: str = "", user_sub: str = ""):
    q = (query or "").strip()
    if not q:
        return {"error": "query é obrigatório"}

    resolved_conv_id = (conv_id or "").strip()
    if not resolved_conv_id:
        return {"error": "conv_id é obrigatório para pesquisa em documento carregado"}

    safe_user = str(user_sub or "").strip()
    chunk_pool = await _load_indexed_chunks(resolved_conv_id, user_sub=safe_user)

    # Fallback retrocompatível: memória local (deploy antigo / jobs ainda sem indexação persistida).
    source = "upload_index"
    if not chunk_pool:
        source = "memory_fallback"
        _, files = _resolve_uploaded_files_memory(resolved_conv_id, user_sub=safe_user)
        for file_data in files:
            chunks = file_data.get("chunks")
            if not isinstance(chunks, list) or not chunks:
                continue
            fname = file_data.get("filename", "")
            for chunk in chunks:
                chunk_pool.append((fname, chunk))

    if not chunk_pool:
        return {"error": "Nenhum documento com chunks semânticos indexados nesta conversa."}

    query_embedding = await get_embedding(q)
    if not query_embedding:
        return {"error": "Falha ao calcular embedding da query"}

    scored = []
    for filename, chunk in chunk_pool:
        chunk_embedding = chunk.get("embedding")
        try:
            score = _cosine_similarity(query_embedding, chunk_embedding)
        except Exception as e:
            logging.warning("[Tools] search_uploaded_document chunk score failed: %s", e)
            continue
        if score < 0:
            continue
        scored.append(
            {
                "filename": filename,
                "chunk_index": chunk.get("index"),
                "start": chunk.get("start"),
                "end": chunk.get("end"),
                "score": score,
                "text": chunk.get("text", ""),
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)
    top_chunks = scored[:5]
    for item in top_chunks:
        item["score"] = round(item["score"], 4)

    return {
        "source": source,
        "conversation_id": resolved_conv_id,
        "filenames": sorted(list({f for f, _ in chunk_pool if f})),
        "query": q,
        "total_chunks": len(chunk_pool),
        "total_results": len(top_chunks),
        "items": top_chunks,
    }

# =============================================================================
# TOOL 4: analyze_patterns
# =============================================================================
async def tool_analyze_patterns(created_by=None, topic=None, work_item_type="User Story", area_path=None, sample_size=15):
    try:
        safe_type = _validate_workitem_type(work_item_type, "User Story")
    except ValueError as e:
        return {"error": str(e)}

    conds = [f"[System.WorkItemType]='{_safe_wiql_literal(safe_type, 80)}'"]
    if created_by:
        conds.append(f"[System.CreatedBy] CONTAINS '{_safe_wiql_literal(created_by, 200)}'")
    if topic:
        conds.append(f"[System.Title] CONTAINS '{_safe_wiql_literal(topic, 200)}'")
    if area_path:
        conds.append(f"[System.AreaPath] UNDER '{_safe_wiql_literal(area_path, 300)}'")
    else:
        conds.append(
            "(" + " OR ".join(
                f"[System.AreaPath] UNDER '{_safe_wiql_literal(a, 300)}'" for a in DEVOPS_AREAS
            ) + ")"
        )
    result = await tool_query_workitems(" AND ".join(conds), top=sample_size)
    if "error" in result: return result
    ids = [it.get("id") for it in result.get("items",[]) if it.get("id")]
    samples = []
    if ids:
        det_fields = DEVOPS_FIELDS + ["System.Description","Microsoft.VSTS.Common.AcceptanceCriteria","System.Tags"]
        async with httpx.AsyncClient(timeout=30) as c:
            try:
                r = await _devops_request_with_retry(c, "POST", _devops_url("wit/workitemsbatch?api-version=7.1"), _devops_headers(), {"ids":ids[:sample_size],"fields":det_fields})
                if "error" not in r:
                    for it in r.get("value",[]):
                        f=it.get("fields",{}); cb=f.get("System.CreatedBy",{})
                        samples.append({"id":it["id"],"title":f.get("System.Title","").replace(" | "," — "),"created_by":cb.get("displayName","") if isinstance(cb,dict) else str(cb),"description":(f.get("System.Description","") or "")[:1000],"acceptance_criteria":(f.get("Microsoft.VSTS.Common.AcceptanceCriteria","") or "")[:1000],"tags":f.get("System.Tags","")})
            except Exception as e:
                logging.error("[Tools] tool_analyze_patterns LLM block failed: %s", e)
    if not samples: samples = [{"id":it.get("id"),"title":it.get("title","")} for it in result.get("items",[])]
    return {"total_found": result.get("total_count",0), "samples_returned": len(samples), "analysis_data": samples}

async def tool_analyze_patterns_with_llm(created_by=None, topic=None, work_item_type="User Story", area_path=None, sample_size=15, analysis_type="template"):
    raw = await tool_analyze_patterns(created_by, topic, work_item_type, area_path, sample_size)
    if "error" in raw or raw.get("samples_returned",0)==0: return raw
    txt = ""
    for i,s in enumerate(raw.get("analysis_data",[])[:15],1):
        txt += f"\n--- Exemplo {i} (ID {s.get('id','?')}) ---\nTítulo: {s.get('title','')}\nCriado por: {s.get('created_by','')}\n"
        if s.get("description"): txt += f"Descrição: {s['description'][:600]}\n"
        if s.get("acceptance_criteria"): txt += f"Critérios: {s['acceptance_criteria'][:600]}\n"
    prompts = {"template": f"Analisa {raw['samples_returned']} {work_item_type}s e extrai PADRÃO DE ESCRITA.\n\n{txt}\n\nExtrai: 1.Estrutura 2.Linguagem 3.Campos 4.Template 5.Observações\nPT-PT.", "author_style": f"Analisa estilo de '{created_by or 'autor'}' em:\n\n{txt}\n\nDescreve: estilo, estrutura, vocabulário, detalhe, template.\nPT-PT."}
    fallback_prompt = f"Analisa:\n{txt}\nPT-PT."
    try: analysis = await llm_simple(f"És analista de padrões de escrita.\n\n{prompts.get(analysis_type, fallback_prompt)}", tier="standard", max_tokens=2000)
    except Exception as e:
        logging.error("[Tools] tool_analyze_patterns_with_llm failed: %s", e)
        analysis = f"Erro: {e}"
    profile_saved = False
    if analysis_type == "author_style" and created_by and isinstance(analysis, str) and not analysis.startswith("Erro:"):
        profile_saved = await _save_writer_profile(
            author_name=created_by,
            analysis=analysis,
            sample_ids=[s.get("id") for s in raw.get("analysis_data", []) if s.get("id")],
            sample_count=raw.get("samples_returned", 0),
            topic=topic or "",
            work_item_type=work_item_type,
        )

    return {
        "total_found": raw.get("total_found",0),
        "samples_analyzed": raw.get("samples_returned",0),
        "analysis_type": analysis_type,
        "analysis": analysis,
        "sample_ids": [s.get("id") for s in raw.get("analysis_data",[])],
        "writer_profile_saved": profile_saved,
    }

# =============================================================================
# TOOL 5: generate_user_stories
# =============================================================================
async def tool_generate_user_stories(topic, context="", num_stories=3, reference_area=None, reference_author=None, reference_topic=None):
    style_profile = None
    if reference_author:
        style_profile = await _load_writer_profile(reference_author)

    raw = {"samples_returned": 0, "analysis_data": []}
    reference_ids = []
    style_hint = ""
    ex = ""

    if style_profile and style_profile.get("style_analysis"):
        _log(f"generate_user_stories: using cached writer profile for '{reference_author}'")
        reference_ids = style_profile.get("sample_ids", [])
        style_hint = (
            f"\nPERFIL DE ESCRITA CACHEADO ({style_profile.get('author_name', reference_author)}):\n"
            f"{style_profile.get('style_analysis', '')[:3000]}\n"
        )
        ex = "(Perfil de autor carregado de WriterProfiles; não foi necessário reanalisar padrões.)"
    else:
        search_topic = reference_topic or topic
        raw = await tool_analyze_patterns(
            created_by=reference_author,
            topic=(search_topic[:35] if len(search_topic) > 35 else search_topic) or None,
            area_path=reference_area,
            sample_size=20,
        )
        if raw.get("samples_returned", 0) < 5:
            raw2 = await tool_analyze_patterns(
                created_by=reference_author,
                area_path=reference_area,
                sample_size=20,
            )
            if raw2.get("samples_returned", 0) > raw.get("samples_returned", 0):
                raw = raw2
        for i, s in enumerate(raw.get("analysis_data", [])[:12], 1):
            ex += f"\n{'='*50}\nEXEMPLO {i} (ID:{s.get('id','?')})\n{'='*50}\nTÍTULO: {s.get('title','')}\nCRIADOR: {s.get('created_by','')}\n"
            if s.get("description"): ex += f"DESC:\n{s['description'][:800]}\n"
            if s.get("acceptance_criteria"): ex += f"AC:\n{s['acceptance_criteria'][:800]}\n"
        if not ex:
            ex = "(Sem exemplos — usa boas práticas)"
        reference_ids = [s.get("id") for s in raw.get("analysis_data", [])]

    prompt = f'Gerar {num_stories} USs sobre "{topic}".\n\nEXEMPLOS REAIS:\n{ex}\n{style_hint}\nCONTEXTO: {context or "Nenhum."}\n\nINSTRUÇÕES: Mesmo padrão, HTML limpo, vocabulário MSE, Título: MSE|Área|Sub|Func|Detalhe.\nPT-PT.'
    sys_msg = "REGRA: Aprende granularidade dos exemplos, NÃO copies HTML sujo. Tu és PO Sénior MSE."
    try: gen = await llm_simple(f"{sys_msg}\n\n{prompt}", tier="standard", max_tokens=8000)
    except Exception as e:
        logging.error("[Tools] tool_generate_user_stories failed: %s", e)
        gen = f"Erro: {e}"
    return {
        "generated_user_stories": gen,
        "based_on_examples": raw.get("samples_returned", 0) if raw else 0,
        "reference_ids": reference_ids,
        "used_writer_profile": bool(style_profile),
        "topic": topic,
        "num_requested": num_stories,
    }

# =============================================================================
# TOOL 6: query_hierarchy
# =============================================================================
async def tool_query_hierarchy(
    parent_id=None,
    parent_type="Epic",
    child_type="User Story",
    area_path=None,
    title_contains=None,
    parent_title_hint=None,
):
    try:
        safe_parent_type = _validate_workitem_type(parent_type, "Epic")
        safe_child_type = _validate_workitem_type(child_type, "User Story")
    except ValueError as e:
        return {"error": str(e)}

    canonical_area = _canonicalize_area_path(area_path) if area_path else ""
    safe_area = _safe_wiql_literal(canonical_area, 300) if canonical_area else ""
    parent_hint = str(parent_title_hint or "").strip()
    child_title_filter = str(title_contains or "").strip()

    headers = _devops_headers()
    async with httpx.AsyncClient(timeout=60) as client:
        resolved_meta = {"attempted": False}
        safe_parent_id = None
        if parent_id:
            try:
                safe_parent_id = int(parent_id)
            except (TypeError, ValueError):
                return {"error": "parent_id inválido: deve ser inteiro positivo"}
            if safe_parent_id <= 0:
                return {"error": "parent_id inválido: deve ser inteiro positivo"}
        elif parent_hint:
            resolved_parent_id, resolved_meta = await _resolve_parent_id_by_title_hint(
                client,
                headers,
                parent_type=safe_parent_type,
                area_path=safe_area,
                title_hint=parent_hint,
            )
            if not resolved_parent_id and safe_area:
                fallback_id, fallback_meta = await _resolve_parent_id_by_title_hint(
                    client,
                    headers,
                    parent_type=safe_parent_type,
                    area_path="",
                    title_hint=parent_hint,
                )
                resolved_meta["fallback_without_area_attempted"] = True
                resolved_meta["fallback_without_area_meta"] = fallback_meta
                if fallback_id:
                    resolved_parent_id = fallback_id
                    resolved_meta["fallback_without_area_used"] = True
            if resolved_parent_id:
                safe_parent_id = int(resolved_parent_id)
                # Neste caminho, o hint foi usado para resolver o PAI e não para filtrar o TÍTULO dos filhos.
                child_title_filter = ""
            else:
                return {
                    "error": (
                        f"Não foi possível identificar {safe_parent_type} com título '{parent_hint}'. "
                        "Indica o ID do parent para resultado exato."
                    ),
                    "total_count": 0,
                    "items_returned": 0,
                    "items": [],
                    "parent_id": parent_id,
                    "parent_type": safe_parent_type,
                    "child_type": safe_child_type,
                    "title_contains": child_title_filter,
                    "parent_title_hint": parent_hint,
                    "_parent_resolve": resolved_meta,
                }

        if safe_parent_id:
            af = f"AND ([Target].[System.AreaPath] UNDER '{safe_area}')" if safe_area else ""
            wiql = (
                "SELECT [System.Id] FROM WorkItemLinks WHERE "
                f"([Source].[System.Id] = {safe_parent_id}) "
                "AND ([System.Links.LinkType] = 'System.LinkTypes.Hierarchy-Forward') "
                f"AND ([Target].[System.WorkItemType] = '{_safe_wiql_literal(safe_child_type, 80)}') "
                f"AND ([Target].[System.TeamProject] = '{_safe_wiql_literal(DEVOPS_PROJECT, 120)}') "
                f"{af} MODE (Recursive)"
            )
        else:
            source_af = f"AND [Source].[System.AreaPath] UNDER '{safe_area}'" if safe_area else ""
            target_af = f"AND [Target].[System.AreaPath] UNDER '{safe_area}'" if safe_area else ""
            wiql = (
                "SELECT [System.Id] FROM WorkItemLinks WHERE "
                f"([Source].[System.WorkItemType] = '{_safe_wiql_literal(safe_parent_type, 80)}' "
                f"{source_af} AND [Source].[System.TeamProject] = '{_safe_wiql_literal(DEVOPS_PROJECT, 120)}') "
                "AND ([System.Links.LinkType] = 'System.LinkTypes.Hierarchy-Forward') "
                f"AND ([Target].[System.WorkItemType] = '{_safe_wiql_literal(safe_child_type, 80)}') "
                f"AND ([Target].[System.TeamProject] = '{_safe_wiql_literal(DEVOPS_PROJECT, 120)}') "
                f"{target_af} "
                "MODE (Recursive)"
            )

        resp = await _devops_request_with_retry(client, "POST", _devops_url("wit/wiql?api-version=7.1"), headers, {"query": wiql})
        if "error" in resp: return resp
        rels = resp.get("workItemRelations",[])
        tids = list(set(r["target"]["id"] for r in rels if r.get("target") and r.get("rel")))
        if not tids: tids = [wi["id"] for wi in resp.get("workItems",[])]
        total_raw = len(tids)
        if not tids:
            return {
                "total_count": 0,
                "total_raw_count": 0,
                "items_returned": 0,
                "items": [],
                "parent_id": safe_parent_id if safe_parent_id else parent_id,
                "parent_type": safe_parent_type,
                "child_type": safe_child_type,
                "title_contains": child_title_filter,
                "parent_title_hint": parent_hint,
            }
        flds = DEVOPS_FIELDS + ["System.Parent"]
        all_det, failed = [], []
        for i in range(0,len(tids),100):
            batch = tids[i:i+100]
            r = await _devops_request_with_retry(client,"POST",_devops_url("wit/workitemsbatch?api-version=7.1"),headers,{"ids":batch,"fields":flds})
            if "error" not in r: all_det.extend(r.get("value",[])) 
            else: failed.extend(batch)
            await asyncio.sleep(0.5)
        items = []
        for it in all_det:
            fi = _format_wi(it); fi["parent_id"] = it.get("fields",{}).get("System.Parent"); items.append(fi)
        # Filtro defensivo final: garante tipo e área pedidos, mesmo se WIQL trouxer ruído.
        filtered_out = 0
        if safe_child_type or safe_area:
            expected_type = str(safe_child_type or "").strip().lower()
            expected_area = str(safe_area or "").strip().lower()
            filtered = []
            for item in items:
                item_type = str(item.get("type", "") or "").strip().lower()
                item_area = str(item.get("area", "") or "").strip().lower()
                type_ok = not expected_type or item_type == expected_type
                area_ok = not expected_area or item_area.startswith(expected_area)
                if type_ok and area_ok:
                    filtered.append(item)
                else:
                    filtered_out += 1
            items = filtered
        title_filter = _normalize_match_text(child_title_filter)
        if title_filter:
            terms = [t for t in title_filter.split(" ") if t]
            if terms:
                by_title = []
                for item in items:
                    title_norm = _normalize_match_text(str(item.get("title", "") or ""))
                    if all(term in title_norm for term in terms):
                        by_title.append(item)
                    else:
                        filtered_out += 1
                items = by_title

        if failed and not items:
            items = [{"id":fid,"type":child_type,"title":"(rate limited)","state":"","url":f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_workitems/edit/{fid}"} for fid in failed]
        matched_count = len(items)
        result = {
            "total_count": matched_count,
            "total_raw_count": total_raw,
            "items_returned": matched_count,
            "parent_id": safe_parent_id if safe_parent_id else parent_id,
            "parent_type":safe_parent_type,
            "child_type":safe_child_type,
            "title_contains": child_title_filter,
            "parent_title_hint": parent_hint,
            "items":items,
        }
        await _attach_auto_csv_export(
            result,
            title_hint=f"hierarchy_{safe_parent_type}_{safe_child_type}_{(safe_parent_id if safe_parent_id else 'all')}",
        )
        if resolved_meta.get("attempted"):
            result["_parent_resolve"] = resolved_meta
        if filtered_out:
            result["_post_filtered_out"] = filtered_out
        if failed: result["_partial"]=True; result["_failed_batch_count"]=len(failed)
        return result

# =============================================================================
# TOOL 7: compute_kpi
# =============================================================================
async def tool_compute_kpi(wiql_where, group_by=None, kpi_type="count"):
    result = await tool_query_workitems(wiql_where=wiql_where, top=1000)
    if "error" in result: return result
    items = result.get("items",[]); total = result.get("total_count",len(items))
    kpi = {"total_count": total, "items_analyzed": len(items)}
    if group_by and items:
        fm = {"state":"state","estado":"state","type":"type","tipo":"type","assigned_to":"assigned_to","assignee":"assigned_to","created_by":"created_by","criador":"created_by","autor":"created_by","area":"area","area_path":"area"}
        fk = fm.get(group_by.lower(), group_by.lower())
        grps = {}
        for it in items: v=it.get(fk,"N/A") or "N/A"; grps[v]=grps.get(v,0)+1
        kpi["group_by"]=group_by; kpi["groups"]=[{"value":k,"count":v} for k,v in sorted(grps.items(),key=lambda x:x[1],reverse=True)]; kpi["unique_values"]=len(grps)
    if kpi_type=="timeline" and items:
        m={}
        for it in items:
            d=it.get("created_date","")
            if d: mo=d[:7]; m[mo]=m.get(mo,0)+1
        kpi["timeline"]=sorted(m.items())
    if kpi_type=="distribution" and items:
        st,tp = {},{}
        for it in items: s=it.get("state","?"); st[s]=st.get(s,0)+1; t=it.get("type","?"); tp[t]=tp.get(t,0)+1
        kpi["state_distribution"]=st; kpi["type_distribution"]=tp
    return kpi


async def tool_create_workitem(
    work_item_type: str = "User Story",
    title: str = "",
    description: str = "",
    acceptance_criteria: str = "",
    area_path: str = "",
    assigned_to: str = "",
    tags: str = "",
    confirmed: bool = False,
):
    """Cria um Work Item no Azure DevOps via JSON Patch."""
    normalized_type = (work_item_type or "User Story").strip().lower()
    allowed_types = {
        "user story": "User Story",
        "bug": "Bug",
        "task": "Task",
        "feature": "Feature",
    }
    work_item_type = allowed_types.get(normalized_type, "User Story")

    title = (title or "").strip()[:250]
    description = (description or "").strip()[:12000]
    acceptance_criteria = (acceptance_criteria or "").strip()[:12000]
    area_path = (area_path or "").strip()[:300]
    assigned_to = (assigned_to or "").strip()[:200]
    tags = (tags or "").strip()[:500]

    if not confirmed:
        return {"error": "Confirmação explícita necessária (envia confirmed=true após 'confirmo')."}
    if not title:
        return {"error": "Título é obrigatório"}

    _log(f"create_workitem: type={work_item_type}, title={title[:60]}...")

    patch_doc = [
        {"op": "add", "path": "/fields/System.Title", "value": title},
    ]
    if description:
        patch_doc.append({"op": "add", "path": "/fields/System.Description", "value": description})
    if acceptance_criteria:
        patch_doc.append({"op": "add", "path": "/fields/Microsoft.VSTS.Common.AcceptanceCriteria", "value": acceptance_criteria})
    if area_path:
        patch_doc.append({"op": "add", "path": "/fields/System.AreaPath", "value": area_path})
    if assigned_to:
        patch_doc.append({"op": "add", "path": "/fields/System.AssignedTo", "value": assigned_to})
    if tags:
        patch_doc.append({"op": "add", "path": "/fields/System.Tags", "value": tags})

    wi_type_encoded = quote(work_item_type, safe="")
    url = _devops_url(f"wit/workitems/${wi_type_encoded}?api-version=7.1")
    headers = _devops_headers()
    headers["Content-Type"] = "application/json-patch+json"

    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(3):
            try:
                resp = await client.post(
                    url,
                    headers=headers,
                    content=json.dumps(patch_doc),
                )
                if resp.status_code == 429:
                    wait = min(int(resp.headers.get("Retry-After", 3 * (attempt + 1))), 30)
                    _log(f"create_workitem 429, attempt {attempt+1}/3, wait {wait}s")
                    await asyncio.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    _log(f"create_workitem {resp.status_code}: {resp.text[:200]}")
                    return {"error": f"DevOps {resp.status_code}: {resp.text[:200]}"}
                data = resp.json()
                break
            except httpx.TimeoutException:
                if attempt == 2:
                    return {"error": "DevOps timeout ao criar work item"}
                await asyncio.sleep(2 * (attempt + 1))
            except httpx.RequestError as e:
                if attempt == 2:
                    return {"error": f"DevOps request error ao criar work item: {str(e)}"}
                await asyncio.sleep(2 * (attempt + 1))
            except Exception as e:
                return {"error": f"Erro ao criar work item: {str(e)}"}
        else:
            return {"error": "Max retries ao criar work item"}

    wi_id = data.get("id")
    wi_url = data.get("_links", {}).get("html", {}).get("href", "")
    if not wi_url and wi_id:
        wi_url = f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_workitems/edit/{wi_id}"

    return {
        "created": True,
        "id": wi_id,
        "url": wi_url,
        "title": title,
        "work_item_type": work_item_type,
        "area_path": area_path or "(default)",
    }


async def tool_refine_workitem(
    work_item_id: int = 0,
    refinement_request: str = "",
):
    """Refina uma US existente com base numa instrução curta, sem alterar DevOps."""
    try:
        safe_id = int(work_item_id)
    except (TypeError, ValueError):
        return {"error": "work_item_id inválido: deve ser inteiro positivo"}
    if safe_id <= 0:
        return {"error": "work_item_id inválido: deve ser inteiro positivo"}

    req = (refinement_request or "").strip()
    if not req:
        return {"error": "refinement_request é obrigatório"}

    fields = [
        "System.Id",
        "System.Title",
        "System.State",
        "System.WorkItemType",
        "System.AreaPath",
        "System.Description",
        "Microsoft.VSTS.Common.AcceptanceCriteria",
        "System.Tags",
    ]
    fields_param = ",".join(fields)
    headers = _devops_headers()

    async with httpx.AsyncClient(timeout=45) as client:
        wi = await _devops_request_with_retry(
            client,
            "GET",
            _devops_url(f"wit/workitems/{safe_id}?fields={fields_param}&api-version=7.1"),
            headers,
            max_retries=3,
        )
    if "error" in wi:
        return wi
    if not isinstance(wi, dict) or not wi.get("id"):
        return {"error": "Work item não encontrado"}

    f = wi.get("fields", {})
    original = {
        "id": wi.get("id"),
        "title": f.get("System.Title", ""),
        "state": f.get("System.State", ""),
        "type": f.get("System.WorkItemType", ""),
        "area": f.get("System.AreaPath", ""),
        "description_html": f.get("System.Description", "") or "",
        "acceptance_criteria_html": f.get("Microsoft.VSTS.Common.AcceptanceCriteria", "") or "",
        "tags": f.get("System.Tags", "") or "",
        "url": f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_workitems/edit/{safe_id}",
    }

    prompt = f"""És PO Sénior MSE.
Recebeste uma User Story existente e um pedido de refinamento.

US ORIGINAL:
- ID: {original['id']}
- Tipo: {original['type']}
- Título: {original['title']}
- Área: {original['area']}
- Descrição HTML: {original['description_html'][:6000]}
- AC HTML: {original['acceptance_criteria_html'][:6000]}
- Tags: {original['tags']}

PEDIDO DE REFINAMENTO:
{req}

Objetivo:
- Devolver uma versão revista, mantendo estilo MSE e estrutura testável.
- Aplicar apenas as mudanças pedidas.
- PT-PT.
- HTML limpo (div, b, ul, li, br).

Responde APENAS em JSON válido neste formato:
{{
  "title": "Título revisto",
  "description_html": "<div>...</div>",
  "acceptance_criteria_html": "<ul><li>...</li></ul>",
  "change_summary": "Resumo curto das alterações"
}}"""

    try:
        llm_output = await llm_simple(prompt, tier="standard", max_tokens=2600)
    except Exception as e:
        return {"error": f"Falha LLM ao refinar work item: {str(e)}"}

    parsed = _extract_json_object(llm_output or "")
    if not parsed:
        return {
            "work_item_id": safe_id,
            "work_item_url": original["url"],
            "refinement_request": req,
            "original": original,
            "ready_to_apply": False,
            "error": "Não foi possível estruturar JSON da revisão. Repetir pedido com instrução mais objetiva.",
            "refined_raw": (llm_output or "")[:12000],
            "note": "Esta tool não altera o work item no DevOps; gera apenas proposta de revisão.",
        }

    refined = {
        "title": str(parsed.get("title", "")).strip() or original["title"],
        "description_html": str(parsed.get("description_html", "")).strip(),
        "acceptance_criteria_html": str(parsed.get("acceptance_criteria_html", "")).strip(),
        "change_summary": str(parsed.get("change_summary", "")).strip(),
    }

    return {
        "work_item_id": safe_id,
        "work_item_url": original["url"],
        "refinement_request": req,
        "original": original,
        "refined": refined,
        "ready_to_apply": True,
        "note": "Esta tool não altera o work item no DevOps; gera proposta para revisão DRAFT->REVIEW->FINAL.",
    }


async def tool_generate_chart(
    chart_type: str = "bar",
    title: str = "Chart",
    x_values: list = None,
    y_values: list = None,
    labels: list = None,
    values: list = None,
    series: list = None,
    x_label: str = "",
    y_label: str = "",
):
    """Gera um chart spec para Plotly.js. Retorna _chart no resultado."""
    chart_type = (chart_type or "bar").lower().strip()
    supported = ["bar", "pie", "line", "scatter", "histogram", "hbar"]
    if chart_type not in supported:
        chart_type = "bar"

    data = []
    layout = {
        "title": {"text": title, "font": {"size": 16}},
        "font": {"family": "Montserrat, sans-serif"},
    }

    # Multi-series via 'series' param
    if series and isinstance(series, list):
        for s in series:
            trace = {"type": s.get("type", chart_type), "name": s.get("name", "")}
            if s.get("x"): trace["x"] = s["x"]
            if s.get("y"): trace["y"] = s["y"]
            if s.get("labels"): trace["labels"] = s["labels"]
            if s.get("values"): trace["values"] = s["values"]
            if trace["type"] == "pie":
                trace.pop("x", None); trace.pop("y", None)
            data.append(trace)
    elif chart_type == "pie":
        data.append({
            "type": "pie",
            "labels": labels or x_values or [],
            "values": values or y_values or [],
            "textinfo": "label+percent",
            "hole": 0.3,
        })
    elif chart_type == "hbar":
        data.append({
            "type": "bar",
            "y": x_values or [],
            "x": y_values or [],
            "orientation": "h",
            "name": title,
        })
        layout["yaxis"] = {"title": x_label, "automargin": True}
        layout["xaxis"] = {"title": y_label}
    elif chart_type == "histogram":
        data.append({
            "type": "histogram",
            "x": x_values or y_values or [],
            "name": title,
        })
        layout["xaxis"] = {"title": x_label}
        layout["yaxis"] = {"title": y_label or "Frequência"}
    else:
        # bar, line, scatter
        data.append({
            "type": chart_type if chart_type != "bar" else "bar",
            "x": x_values or [],
            "y": y_values or [],
            "name": title,
        })
        if x_label: layout["xaxis"] = {"title": x_label}
        if y_label: layout["yaxis"] = {"title": y_label}

    chart_spec = {"data": data, "layout": layout, "config": {"responsive": True}}

    return {
        "chart_generated": True,
        "chart_type": chart_type,
        "title": title,
        "data_points": len(x_values or labels or []),
        "_chart": chart_spec,
    }


async def tool_generate_file(
    format: str = "csv",
    title: str = "Export",
    data: list = None,
    columns: list = None,
):
    """Gera ficheiro em memória (CSV/XLSX/PDF) e devolve metadados de download."""
    fmt = (format or "csv").strip().lower()
    if fmt not in ("csv", "xlsx", "pdf"):
        return {"error": "Formato inválido. Usa: csv, xlsx ou pdf"}

    if not isinstance(data, list) or len(data) == 0:
        return {"error": "Campo 'data' deve ser array com pelo menos uma linha"}

    if columns is None:
        first = data[0]
        if isinstance(first, dict):
            columns = list(first.keys())
        elif isinstance(first, (list, tuple)):
            columns = [f"col_{i+1}" for i in range(len(first))]
        else:
            return {"error": "Não foi possível inferir colunas. Envia 'columns' explicitamente."}

    if not isinstance(columns, list) or len(columns) == 0:
        return {"error": "Campo 'columns' deve ser array de strings"}

    clean_columns = [str(c).strip() for c in columns if str(c).strip()]
    if not clean_columns:
        return {"error": "Sem colunas válidas para gerar ficheiro"}

    items = []
    for row in data[:5000]:
        if isinstance(row, dict):
            item = {c: row.get(c, "") for c in clean_columns}
        elif isinstance(row, (list, tuple)):
            item = {c: (row[idx] if idx < len(row) else "") for idx, c in enumerate(clean_columns)}
        else:
            continue
        items.append(item)

    if not items:
        return {"error": "Sem linhas válidas para gerar ficheiro"}

    payload = {"items": items, "total_count": len(items)}
    safe_title = "".join(ch if ch.isalnum() or ch in " _-" else "_" for ch in (title or "Export")).strip()[:40] or "Export"

    try:
        if fmt == "csv":
            mime_type = "text/csv"
            buf = to_csv(payload)
        elif fmt == "xlsx":
            mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            buf = to_xlsx(payload, safe_title)
        else:
            mime_type = "application/pdf"
            buf = to_pdf(payload, safe_title)
    except Exception as e:
        logging.error("[Tools] tool_generate_file failed (%s): %s", fmt, e)
        return {"error": f"Erro ao gerar ficheiro {fmt}: {str(e)}"}

    content = buf.getvalue()
    if not content:
        return {"error": "Ficheiro gerado está vazio"}

    filename = f"{safe_title}.{fmt}"
    download_id = await _store_generated_file(content, mime_type, filename, fmt)
    if not download_id:
        return {"error": "Ficheiro demasiado grande para armazenamento temporário no servidor"}

    return {
        "file_generated": True,
        "format": fmt,
        "title": safe_title,
        "rows": len(items),
        "columns": clean_columns,
        "_file_download": {
            "download_id": download_id,
            "endpoint": f"/api/download/{download_id}",
            "filename": filename,
            "format": fmt,
            "mime_type": mime_type,
            "size_bytes": len(content),
            "expires_in_seconds": _GENERATED_FILE_TTL_SECONDS,
        },
    }

# =============================================================================
# TOOL RESULT TRUNCATION
# =============================================================================
def truncate_tool_result(result_str):
    if len(result_str) <= AGENT_TOOL_RESULT_MAX_SIZE: return result_str
    try:
        data = json.loads(result_str)
        if isinstance(data, dict) and "items" in data:
            original_items = len(data.get("items", []) or [])
            data["items"] = (data.get("items") or [])[:AGENT_TOOL_RESULT_KEEP_ITEMS]
            data["_truncated"] = True
            data["_original_items"] = original_items
            data["items_returned"] = len(data.get("items", []))
            return json.dumps(data, ensure_ascii=False)
    except Exception as e:
        logging.warning("[Tools] truncate_tool_result fallback: %s", e)
    return result_str[:AGENT_TOOL_RESULT_MAX_SIZE] + "\n...(truncado)"

# =============================================================================
# TOOL DEFINITIONS (formato OpenAI — traduzido auto para Anthropic pelo llm_provider)
# =============================================================================
_BUILTIN_TOOL_DEFINITIONS = [
    {"type":"function","function":{"name":"query_workitems","description":"Query Azure DevOps via WIQL para contagens, listagens, filtros. Dados em TEMPO REAL.","parameters":{"type":"object","properties":{"wiql_where":{"type":"string","description":"WHERE WIQL. Ex: [System.WorkItemType]='User Story' AND [System.State]='Active'"},"fields":{"type":"array","items":{"type":"string"},"description":"Campos extra a retornar. Default: Id,Title,State,Type,AssignedTo,CreatedBy,AreaPath,CreatedDate. Adicionar 'System.Description' e 'Microsoft.VSTS.Common.AcceptanceCriteria' quando o user pedir detalhes/descrição/AC."},"top":{"type":"integer","description":"Max resultados. 0=só contagem."}},"required":["wiql_where"]}}},
    {"type":"function","function":{"name":"search_workitems","description":"Pesquisa semântica em work items indexados. Retorna AMOSTRA dos mais relevantes.","parameters":{"type":"object","properties":{"query":{"type":"string","description":"Texto. Ex: 'transferências SPIN'"},"top":{"type":"integer","description":"Nº resultados. Default: 30."},"filter":{"type":"string","description":"Filtro OData."}},"required":["query"]}}},
    {"type":"function","function":{"name":"search_website","description":"Pesquisa no site MSE. Usa para navegação, funcionalidades, operações.","parameters":{"type":"object","properties":{"query":{"type":"string","description":"Texto. Ex: 'transferência SEPA'"},"top":{"type":"integer","description":"Default: 10"}},"required":["query"]}}},
    {"type":"function","function":{"name":"search_uploaded_document","description":"Pesquisa semântica no documento carregado pelo utilizador. Usar quando o utilizador perguntar sobre conteúdos específicos de um documento que fez upload e o documento é grande.","parameters":{"type":"object","properties":{"query":{"type":"string","description":"Texto a pesquisar semanticamente no documento carregado."},"conv_id":{"type":"string","description":"ID da conversa. Opcional; se vazio, tenta inferir automaticamente."}},"required":["query"]}}},
    {"type":"function","function":{"name":"analyze_patterns","description":"Analisa padrões de escrita de work items com LLM. Templates, estilo de autor.","parameters":{"type":"object","properties":{"created_by":{"type":"string"},"topic":{"type":"string"},"work_item_type":{"type":"string","description":"Default: 'User Story'"},"area_path":{"type":"string"},"sample_size":{"type":"integer","description":"Default: 50"},"analysis_type":{"type":"string","description":"'template','author_style','general'"}}}}},
    {"type":"function","function":{"name":"generate_user_stories","description":"Gera USs NOVAS baseadas em padrões reais. USA SEMPRE quando pedirem criar/gerar USs.","parameters":{"type":"object","properties":{"topic":{"type":"string","description":"Tema das USs."},"context":{"type":"string","description":"Contexto: Miro, Figma, requisitos."},"num_stories":{"type":"integer","description":"Nº USs. Default: 3."},"reference_area":{"type":"string"},"reference_author":{"type":"string"},"reference_topic":{"type":"string"}},"required":["topic"]}}},
    {"type":"function","function":{"name":"query_hierarchy","description":"Query hierárquica parent/child. OBRIGATÓRIO para 'Epic', 'dentro de', 'filhos de'.","parameters":{"type":"object","properties":{"parent_id":{"type":"integer","description":"ID do pai."},"parent_type":{"type":"string","description":"Default: 'Epic'."},"child_type":{"type":"string","description":"Default: 'User Story'."},"area_path":{"type":"string"},"title_contains":{"type":"string","description":"Filtro opcional por título (contains, case/accent-insensitive). Ex: 'Créditos Consultar Carteira'"},"parent_title_hint":{"type":"string","description":"(Interno) dica de título do parent para resolução quando parent_id não for fornecido."}}}}},
    {"type":"function","function":{"name":"compute_kpi","description":"Calcula KPIs (até 1000 items). OBRIGATÓRIO para rankings, distribuições, tendências.","parameters":{"type":"object","properties":{"wiql_where":{"type":"string"},"group_by":{"type":"string","description":"'state','type','assigned_to','created_by','area'"},"kpi_type":{"type":"string","description":"'count','timeline','distribution'"}},"required":["wiql_where"]}}},
    {"type":"function","function":{"name":"create_workitem","description":"Cria um Work Item no Azure DevOps. USA APENAS quando o utilizador CONFIRMAR explicitamente a criação. PERGUNTA SEMPRE antes de criar.","parameters":{"type":"object","properties":{"work_item_type":{"type":"string","description":"Tipo: 'User Story', 'Bug', 'Task', 'Feature'. Default: 'User Story'."},"title":{"type":"string","description":"Título do Work Item."},"description":{"type":"string","description":"Descrição em HTML. Usa formato MSE."},"acceptance_criteria":{"type":"string","description":"Critérios de aceitação em HTML."},"area_path":{"type":"string","description":"AreaPath. Ex: 'IT.DIT\\\\DIT\\\\ADMChannels\\\\DBKS\\\\AM24\\\\RevampFEE MVP2'"},"assigned_to":{"type":"string","description":"Nome completo da pessoa. Ex: 'Pedro Mousinho'"},"tags":{"type":"string","description":"Tags separadas por ';'. Ex: 'MVP2;FEE;Sprint23'"},"confirmed":{"type":"boolean","description":"true apenas após confirmação explícita do utilizador (ex: 'confirmo')."}},"required":["title"]}}},
    {"type":"function","function":{"name":"refine_workitem","description":"Refina uma User Story existente no DevOps a partir de uma instrução curta (sem alterar automaticamente o item). Usa quando o utilizador pedir ajustes numa US já criada, ex: 'na US 12345 adiciona validação de email'.","parameters":{"type":"object","properties":{"work_item_id":{"type":"integer","description":"ID do work item existente a refinar."},"refinement_request":{"type":"string","description":"Instrução objetiva do que mudar na US existente."}},"required":["work_item_id","refinement_request"]}}},
    {"type":"function","function":{"name":"generate_chart","description":"Gera gráfico interativo (bar, pie, line, scatter, histogram, hbar). USA SEMPRE que o utilizador pedir gráfico, chart, visualização ou distribuição visual. Extrai dados de tool_results anteriores ou de dados fornecidos.","parameters":{"type":"object","properties":{"chart_type":{"type":"string","description":"Tipo: 'bar','pie','line','scatter','histogram','hbar'. Default: 'bar'."},"title":{"type":"string","description":"Título do gráfico."},"x_values":{"type":"array","items":{"type":"string"},"description":"Valores eixo X (categorias ou datas). Ex: ['Active','Closed','New']"},"y_values":{"type":"array","items":{"type":"number"},"description":"Valores eixo Y (numéricos). Ex: [45, 30, 12]"},"labels":{"type":"array","items":{"type":"string"},"description":"Labels para pie chart. Ex: ['Bug','US','Task']"},"values":{"type":"array","items":{"type":"number"},"description":"Valores para pie chart. Ex: [20, 50, 30]"},"series":{"type":"array","items":{"type":"object"},"description":"Multi-series. Cada obj: {type,name,x,y,labels,values}"},"x_label":{"type":"string","description":"Label do eixo X"},"y_label":{"type":"string","description":"Label do eixo Y"}},"required":["title"]}}},
    {"type":"function","function":{"name":"generate_file","description":"Gera ficheiro para download (CSV, XLSX, PDF) quando o utilizador pedir explicitamente para gerar/descarregar ficheiro com dados.","parameters":{"type":"object","properties":{"format":{"type":"string","enum":["csv","xlsx","pdf"],"description":"Formato do ficheiro a gerar."},"title":{"type":"string","description":"Título/nome base do ficheiro."},"data":{"type":"array","items":{"type":"object"},"description":"Linhas de dados (array de objetos)."},"columns":{"type":"array","items":{"type":"string"},"description":"Headers/ordem das colunas no ficheiro."}},"required":["format","title","data","columns"]}}},
]

_TOOL_DEFINITION_BY_NAME = {
    d.get("function", {}).get("name"): d
    for d in _BUILTIN_TOOL_DEFINITIONS
    if d.get("function", {}).get("name")
}


def _tool_dispatch() -> dict:
    return {
        "query_workitems": lambda arguments: tool_query_workitems(arguments.get("wiql_where",""), arguments.get("fields"), arguments.get("top",200)),
        "search_workitems": lambda arguments: tool_search_workitems(arguments.get("query",""), arguments.get("top",30), arguments.get("filter")),
        "search_website": lambda arguments: tool_search_website(arguments.get("query",""), arguments.get("top",10)),
        "search_uploaded_document": lambda arguments: tool_search_uploaded_document(
            arguments.get("query", ""),
            arguments.get("conv_id", ""),
            arguments.get("user_sub", ""),
        ),
        "analyze_patterns": lambda arguments: tool_analyze_patterns_with_llm(arguments.get("created_by"), arguments.get("topic"), arguments.get("work_item_type","User Story"), arguments.get("area_path"), arguments.get("sample_size",50), arguments.get("analysis_type","template")),
        "generate_user_stories": lambda arguments: tool_generate_user_stories(arguments.get("topic",""), arguments.get("context",""), arguments.get("num_stories",3), arguments.get("reference_area"), arguments.get("reference_author"), arguments.get("reference_topic")),
        "generate_workitem": lambda arguments: tool_generate_user_stories(arguments.get("topic",""), arguments.get("requirements",""), reference_area=arguments.get("reference_area"), reference_author=arguments.get("reference_author")),
        "query_hierarchy": lambda arguments: tool_query_hierarchy(
            arguments.get("parent_id"),
            arguments.get("parent_type", "Epic"),
            arguments.get("child_type", "User Story"),
            arguments.get("area_path"),
            arguments.get("title_contains"),
            arguments.get("parent_title_hint"),
        ),
        "compute_kpi": lambda arguments: tool_compute_kpi(arguments.get("wiql_where",""), arguments.get("group_by"), arguments.get("kpi_type","count")),
        "create_workitem": lambda arguments: tool_create_workitem(
            arguments.get("work_item_type", "User Story"),
            arguments.get("title", ""),
            arguments.get("description", ""),
            arguments.get("acceptance_criteria", ""),
            arguments.get("area_path", ""),
            arguments.get("assigned_to", ""),
            arguments.get("tags", ""),
            arguments.get("confirmed", False),
        ),
        "refine_workitem": lambda arguments: tool_refine_workitem(
            arguments.get("work_item_id", 0),
            arguments.get("refinement_request", ""),
        ),
        "generate_chart": lambda arguments: tool_generate_chart(
            arguments.get("chart_type", "bar"),
            arguments.get("title", "Chart"),
            arguments.get("x_values"),
            arguments.get("y_values"),
            arguments.get("labels"),
            arguments.get("values"),
            arguments.get("series"),
            arguments.get("x_label", ""),
            arguments.get("y_label", ""),
        ),
        "generate_file": lambda arguments: tool_generate_file(
            arguments.get("format", "csv"),
            arguments.get("title", "Export"),
            arguments.get("data"),
            arguments.get("columns"),
        ),
    }


def _register_builtin_tools() -> None:
    dispatch = _tool_dispatch()
    for tool_name, handler in dispatch.items():
        definition = _TOOL_DEFINITION_BY_NAME.get(tool_name)
        register_tool(tool_name, handler, definition=definition)


_register_builtin_tools()

# Optional integrations (registo condicional por token em env).
for _optional_module in ("tools_figma", "tools_miro"):
    try:
        __import__(_optional_module)
    except Exception:
        logging.exception("[Tools] optional module %s failed to load", _optional_module)


_SEARCH_FIGMA_PROXY_DEFINITION = {
    "type": "function",
    "function": {
        "name": "search_figma",
        "description": "Pesquisa no Figma (read-only). Usa quando o utilizador mencionar designs, mockups, ecras, UI ou prototipos.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Texto de pesquisa em nomes de ficheiro/frame."},
                "file_key": {"type": "string", "description": "Figma file key para detalhar um ficheiro especifico."},
                "node_id": {"type": "string", "description": "Node/frame id para detalhe especifico dentro do ficheiro."},
            },
        },
    },
}

_SEARCH_MIRO_PROXY_DEFINITION = {
    "type": "function",
    "function": {
        "name": "search_miro",
        "description": "Pesquisa no Miro (read-only). Usa quando o utilizador mencionar workshops, brainstorms, boards, sticky notes ou planning sessions.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Texto de pesquisa para boards/conteudo."},
                "board_id": {"type": "string", "description": "Board id para detalhar conteudo desse board."},
            },
        },
    },
}


async def _search_figma_proxy(arguments):
    try:
        from tools_figma import tool_search_figma

        return await tool_search_figma(
            query=(arguments or {}).get("query", ""),
            file_key=(arguments or {}).get("file_key", ""),
            node_id=(arguments or {}).get("node_id", ""),
        )
    except Exception as e:
        logging.error("[Tools] search_figma proxy failed: %s", e, exc_info=True)
        return {"error": "Integração Figma indisponível neste runtime"}


async def _search_miro_proxy(arguments):
    try:
        from tools_miro import tool_search_miro

        return await tool_search_miro(
            query=(arguments or {}).get("query", ""),
            board_id=(arguments or {}).get("board_id", ""),
        )
    except Exception as e:
        logging.error("[Tools] search_miro proxy failed: %s", e, exc_info=True)
        return {"error": "Integração Miro indisponível neste runtime"}


def _ensure_optional_tool_proxies() -> None:
    """Garante presença de tools opcionais no registry mesmo com falhas de import."""
    if not has_tool("search_figma"):
        register_tool(
            "search_figma",
            lambda args: _search_figma_proxy(args),
            definition=_SEARCH_FIGMA_PROXY_DEFINITION,
        )
        logging.warning("[Tools] search_figma registada via proxy fallback")

    if not has_tool("search_miro"):
        register_tool(
            "search_miro",
            lambda args: _search_miro_proxy(args),
            definition=_SEARCH_MIRO_PROXY_DEFINITION,
        )
        logging.warning("[Tools] search_miro registada via proxy fallback")


_ensure_optional_tool_proxies()


async def execute_tool(tool_name, arguments):
    """Compat wrapper; execução real vive no tool_registry."""
    return await registry_execute_tool(tool_name, arguments)


def get_all_tool_definitions():
    return registry_get_all_tool_definitions()


# Compatibilidade com código antigo que ainda importa TOOLS.
TOOLS = get_all_tool_definitions()

# =============================================================================
# SYSTEM PROMPTS
# =============================================================================
def get_agent_system_prompt():
    figma_enabled = has_tool("search_figma")
    miro_enabled = has_tool("search_miro")
    uploaded_doc_enabled = has_tool("search_uploaded_document")

    def _join_with_ou(parts):
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        return ", ".join(parts[:-1]) + " ou " + parts[-1]

    data_sources = ["DevOps", "AI Search", "site MSE"]
    if uploaded_doc_enabled:
        data_sources.append("documento carregado")
    if figma_enabled:
        data_sources.append("Figma")
    if miro_enabled:
        data_sources.append("Miro")
    data_sources_text = _join_with_ou(data_sources)

    gate_priority_hints = []
    if uploaded_doc_enabled:
        gate_priority_hints.append(
            "- Se o utilizador perguntar sobre secções específicas de documento carregado (especialmente PDF grande), usa search_uploaded_document."
        )
    if figma_enabled:
        gate_priority_hints.append(
            "- Se o utilizador mencionar Figma, design, mockup, ecras UI ou prototipos, usa search_figma (nao responder diretamente)."
        )
    if miro_enabled:
        gate_priority_hints.append(
            "- Se o utilizador mencionar Miro, board, workshop, brainstorm ou sticky notes, usa search_miro (nao responder diretamente)."
        )
    gate_priority_hints_text = "\n".join(gate_priority_hints)
    exception_targets = []
    if uploaded_doc_enabled:
        exception_targets.append("documento carregado")
    if figma_enabled:
        exception_targets.append("Figma")
    if miro_enabled:
        exception_targets.append("Miro")
    exception_priority_line = ""
    if exception_targets:
        exception_priority_line = (
            "EXCEÇÃO PRIORITÁRIA: pedidos sobre "
            f"{_join_with_ou(exception_targets)} DEVEM usar as respetivas tools quando estiverem ativas."
        )

    routing_rules = [
        "1. Para CONTAGENS, LISTAGENS ou FILTROS EXATOS -> usa query_workitems (WIQL direto ao Azure DevOps)\n"
        "   Exemplos: \"quantas USs existem\", \"lista bugs ativos\", \"USs criadas em janeiro\"",
        "2. Para PESQUISA SEMANTICA por topico/similaridade -> usa search_workitems (busca vetorial)\n"
        "   Exemplos: \"USs sobre transferencias SPIN\", \"bugs relacionados com timeout\"\n"
        "   NOTA: Retorna os mais RELEVANTES, nao TODOS. Diz sempre \"resultados mais relevantes\".",
        "3. Para perguntas sobre o SITE/APP MSE -> usa search_website (busca no conteudo web)",
        "4. Para ANALISE DE PADROES de escrita -> usa analyze_patterns (busca exemplos + analise LLM)",
        "5. Para GERAR NOVOS WORK ITEMS -> usa generate_user_stories (busca exemplos + gera no mesmo padrao)",
        "6. Para HIERARQUIAS (Epic->Feature->US->Task) -> usa query_hierarchy (OBRIGATORIO)\n"
        "   Exemplos: \"USs dentro do Epic 12345\", \"filhos do Feature X\"\n"
        "   REGRA: Sempre que o utilizador mencionar \"Epic\", \"dentro de\", \"filhos de\" -> query_hierarchy\n"
        "   REGRA: Se pedir filtro por título (ex: \"cujo título tem ...\"), preencher title_contains.\n"
        "   REGRA: Se o pedido tiver múltiplas hierarquias (ex: bugs do Epic X E US da Feature Y), fazer múltiplas chamadas query_hierarchy e combinar.\n"
        "   REGRA: query_hierarchy devolve lista EXATA (não semântica). Nunca dizer \"mais relevantes\".\n"
        "   REGRA: Se total_count <= 100, listar TODOS os itens devolvidos.",
        "7. Para KPIs, RANKINGS, DISTRIBUICOES, ANALISE -> usa compute_kpi (OBRIGATORIO)\n"
        "   Exemplos: \"quem criou mais USs\", \"distribuicao por estado\", \"top contributors\"\n"
        "   REGRA: Sempre que o utilizador pedir ranking, comparacao, tendencia -> compute_kpi",
        "8. Para CRIAR WORK ITEMS no board -> usa create_workitem (OBRIGATORIO)\n"
        "   Exemplos: \"cria esta US no DevOps\", \"coloca no board\", \"adiciona ao backlog\"\n"
        "   REGRA CRITICA: NUNCA criar sem confirmacao explicita do utilizador.\n"
        "   Fluxo: 1) Gerar/mostrar conteudo -> 2) Perguntar \"Confirmas a criacao?\" -> 3) So criar apos \"sim/confirmo\"",
        "9. Para REFINAR/ATUALIZAR US EXISTENTE por ID -> usa refine_workitem (OBRIGATORIO)\n"
        "   Exemplos: \"na US 912345 adiciona validacao de email\", \"ajusta a US 800123 para incluir toast de sucesso\"\n"
        "   REGRA: Primeiro apresenta DRAFT revisto e pede validacao antes de qualquer criacao derivada.",
        "10. Para GRAFICOS, CHARTS, VISUALIZACOES -> usa generate_chart (OBRIGATORIO)\n"
        "   Exemplos: \"mostra um grafico de bugs por estado\", \"chart de USs por mes\", \"visualiza a distribuicao\"\n"
        "   REGRA: Primeiro obtem os dados (query_workitems/compute_kpi), depois chama generate_chart com os valores extraidos.\n"
        "   REGRA: Podes chamar compute_kpi + generate_chart em sequencia (nao em paralelo - precisas dos dados primeiro).",
        "11. Para GERAR ou DESCARREGAR ficheiros (Excel/CSV/PDF) com dados -> usa generate_file (OBRIGATORIO)\n"
        "   Exemplos: \"gera um Excel com estes dados\", \"descarrega em CSV\", \"quero PDF da tabela\"\n"
        "   REGRA: So usar quando o utilizador pedir EXPLICITAMENTE geracao/download de ficheiro.",
        "12. Para resultados extensos (muitas linhas) -> mostra PREVIEW no chat e indica que o ficheiro completo está disponível para download.\n"
        "   REGRA: Evita listar dezenas de linhas completas na resposta textual.",
    ]
    next_rule = 13
    if uploaded_doc_enabled:
        routing_rules.append(
            f"{next_rule}. Para PERGUNTAS SOBRE DOCUMENTO CARREGADO (sobretudo PDF grande) -> usa search_uploaded_document (OBRIGATORIO)\n"
            "   Exemplos: \"o que diz o capitulo 3?\", \"resume a secção de requisitos\", \"onde fala de autenticação?\"\n"
            "   REGRA: Usa pesquisa semântica nos chunks do documento, em vez de depender só do texto truncado."
        )
        next_rule += 1
    if figma_enabled:
        routing_rules.append(
            f"{next_rule}. Para DESIGN, MOCKUPS, ECRAS UI e PROTOTIPOS FIGMA -> usa search_figma (OBRIGATORIO)\n"
            "   Exemplos: \"mostra os designs recentes\", \"abre o ficheiro figma X\", \"que frames existem no mockup?\"\n"
            "   REGRA: Nao usar search_website para pedidos de Figma. Usa sempre search_figma."
        )
        next_rule += 1
    if miro_enabled:
        routing_rules.append(
            f"{next_rule}. Para WORKSHOPS, BRAINSTORMS, STICKY NOTES e BOARDS MIRO -> usa search_miro (OBRIGATORIO)\n"
            "   Exemplos: \"lista os boards do miro\", \"o que foi discutido no board X?\"\n"
            "   REGRA: Nao usar search_website para pedidos de Miro. Usa sempre search_miro."
        )
    routing_rules_text = "\n".join(routing_rules)

    usage_examples = [
        "- \"Quantas USs existem no RevampFEE?\" -> query_workitems com top=0 (contagem rapida)",
        "- \"Quais USs falam sobre pagamentos?\" -> search_workitems (semantica)",
        "- \"Lista TODAS as USs com 'SPIN' no titulo\" -> query_workitems com CONTAINS e top=1000",
        "- \"Quem criou mais USs em 2025?\" -> compute_kpi com group_by=\"created_by\"",
        "- \"USs do Epic 12345\" -> query_hierarchy com parent_id=12345",
        "- \"Distribuicao de estados no MDSE\" -> compute_kpi com kpi_type=\"distribution\"",
        "- Para CRIAR -> usa create_workitem (pede SEMPRE confirmacao)",
        "- \"Na US 912345 adiciona validacao de email\" -> refine_workitem",
        "- \"Mostra grafico de bugs por estado\" -> compute_kpi DEPOIS generate_chart",
        "- \"Visualiza distribuicao de USs\" -> compute_kpi DEPOIS generate_chart",
        "- \"Gera um Excel/CSV/PDF com esta tabela\" -> generate_file",
    ]
    if uploaded_doc_enabled:
        usage_examples.extend(
            [
                "- \"O que diz o capítulo 3 do PDF?\" -> search_uploaded_document",
                "- \"Procura no documento onde fala de validação\" -> search_uploaded_document",
            ]
        )
    if figma_enabled:
        usage_examples.extend(
            [
                "- \"Mostra os ficheiros recentes do Figma\" -> search_figma",
                "- \"Detalha os frames do ficheiro Figma ABC\" -> search_figma com file_key",
            ]
        )
    if miro_enabled:
        usage_examples.extend(
            [
                "- \"Lista os boards do Miro\" -> search_miro",
                "- \"O que foi discutido no board X?\" -> search_miro com board_id",
            ]
        )
    usage_examples_text = "\n".join(usage_examples)

    return f"""Tu és o Assistente IA do Millennium BCP para a equipa de desenvolvimento DIT/ADMChannels.
Tens acesso a ferramentas para consultar dados reais do Azure DevOps e do site MSE.

DATA ACTUAL: {datetime.now().strftime('%Y-%m-%d')} (usa esta data como referência para queries temporais)

REGRAS DE CLARIFICAÇÃO (IMPORTANTE):
- Se a pergunta do utilizador mencionar um NOME DE PESSOA que pode corresponder a múltiplas pessoas, DEVES perguntar qual pessoa antes de executar. Isto é OBRIGATÓRIO.
- Exemplos de quando PERGUNTAR (OBRIGATÓRIO):
  • Só primeiro nome: "mostra o que o Jorge criou" → PERGUNTA "Queres dizer Jorge Eduardo Rodrigues, ou outro Jorge? Indica o nome completo."
  • Nome parcial ambíguo: "bugs do Pedro" → PERGUNTA "Qual Pedro? Pedro Mousinho, Pedro Silva, ou outro?"
- Exemplos de quando NÃO perguntar (responde diretamente):
  • Nome completo fornecido: "bugs do Jorge Eduardo Rodrigues" → executa imediatamente
  • A intenção é clara sem ambiguidade: "quantas user stories em 2025" → executa imediatamente
- REGRA: Para NOMES DE PESSOAS, pergunta sempre que o nome não seja completo. Para tudo o resto, na dúvida EXECUTA.

NOMES NO AZURE DEVOPS:
- Os nomes no DevOps são nomes completos (ex: "Jorge Eduardo Rodrigues", não "Jorge Rodrigues")
- Quando usares Contains para nomes, usa APENAS o primeiro nome OU o nome completo confirmado

REGRA PRIORITÁRIA — RESPOSTA DIRECTA SEM FERRAMENTAS:
Antes de decidir qual ferramenta usar, avalia se a pergunta PRECISA de dados do {data_sources_text}.
Se NÃO precisa, responde DIRETAMENTE sem chamar nenhuma ferramenta.
{exception_priority_line}
{gate_priority_hints_text}

Categorias que NÃO precisam de ferramentas (responde directamente):
1. CONCEPTUAL/EDUCATIVO: "O que é uma user story?", "Explica WIQL", "Diferença entre Epic e Feature", "Boas práticas de Agile"
2. REDACÇÃO E ESCRITA: "Escreve-me um email para...", "Ajuda-me a redigir...", "Resume este texto", "Traduz isto para inglês"
3. OPINIÃO/CONSELHO: "Qual a melhor forma de organizar sprints?", "Achas que devia dividir esta US?"
4. CONVERSAÇÃO: Saudações, agradecimentos, perguntas sobre ti próprio, clarificações sobre respostas anteriores
5. ANÁLISE DE CONTEÚDO FORNECIDO: Quando o utilizador cola texto/dados directamente no chat e pede análise, resumo ou reformulação — os dados JÁ ESTÃO na mensagem, não precisas de os ir buscar
6. DOCUMENTAÇÃO E TEMPLATES: "Dá-me um template de Definition of Ready", "Como se estrutura um AC?"

REGRA: Na dúvida entre responder directamente ou usar ferramenta, prefere responder directamente.
Só usa ferramentas quando precisas de dados ESPECÍFICOS que não tens no contexto da conversa.

ROUTING SIMULTÂNEO (IMPORTANTE):
- Podes e DEVES chamar MÚLTIPLAS ferramentas EM PARALELO quando a pergunta precisa de dados de fontes diferentes.
- Chama todas as ferramentas necessárias de uma vez — NÃO esperes pela resposta de uma para chamar a outra quando são independentes.

REGRAS DE ROUTING (decide qual ferramenta usar):
{routing_rules_text}

QUANDO USAR query_workitems vs search_workitems vs compute_kpi (IMPORTANTE):
{usage_examples_text}

CAMPOS ESPECIAIS (IMPORTANTE):
- Para obter DESCRIÇÃO ou CRITÉRIOS DE ACEITAÇÃO, inclui fields: ["System.Id","System.Title","System.State","System.WorkItemType","System.Description","Microsoft.VSTS.Common.AcceptanceCriteria"]
- Default sem esses campos é suficiente para listagens/contagens

REGRA ANTI-CRASH (IMPORTANTE):
- Se uma ferramenta retornar erro, NÃO entres em pânico. Explica o erro ao utilizador e sugere alternativa.
- Se retornar muitos dados truncados, diz quantos existem no total e mostra os que tens.
- NUNCA chames a mesma ferramenta com os mesmos argumentos duas vezes seguidas.

RESPOSTA: PT-PT. IDs: [US 912700]. Links DevOps. Contagens EXATAS com total_count. Tabelas markdown quando apropriado. Parágrafos naturais.

ÁREAS: RevampFEE MVP2, MDSE, ACEDigital, MSE (sob IT.DIT\\DIT\\ADMChannels\\DBKS\\AM24)
TIPOS: User Story, Bug, Task, Feature, Epic
ESTADOS: New, Active, Closed, Resolved, Removed
CAMPOS WIQL: System.WorkItemType, State, AreaPath, Title (CONTAINS), AssignedTo, CreatedBy, CreatedDate ('YYYY-MM-DD'), ChangedDate, Tags
- [Microsoft.VSTS.Common.AcceptanceCriteria]

EXEMPLOS DE WIQL:
- USs criadas em 2025: [System.CreatedDate] >= '2025-01-01' AND [System.CreatedDate] < '2026-01-01'
- Para "quem criou mais", query SEM filtro de criador, top=500, conta por created_by"""

def get_userstory_system_prompt():
    return f"""Tu és PO Sénior especialista no MSE (Millennium Site Empresas).
Objetivo: transformar pedidos em User Stories rigorosas, refinadas iterativamente.
DATA: {datetime.now().strftime('%Y-%m-%d')}

MODO OBRIGATÓRIO: DRAFT → REVIEW → FINAL
1) DRAFT: gera primeiro uma versão inicial (clara e completa) com base no pedido.
2) REVIEW: apresenta o draft e pede feedback objetivo (ex: "O que queres ajustar?").
3) FINAL: só após feedback explícito do utilizador, produz a versão final consolidada.

REGRA DE REFINAMENTO (CRÍTICA):
- Se o utilizador der feedback, NÃO ignores.
- Reaplica generate_user_stories com o novo contexto e mostra uma versão revista.
- Mantém rastreabilidade: diz o que foi alterado (breve) antes da versão final.

FERRAMENTA OBRIGATÓRIA:
- Usa SEMPRE generate_user_stories para gerar/refinar USs.
- Quando o utilizador pedir "como o [autor] escreve", passa reference_author para aproveitar WriterProfiles.
- Se o utilizador referir uma US existente por ID e pedir alteração, usa refine_workitem para criar o draft de revisão antes do final.

PARSING DE INPUT (PRIORIDADE):
- Texto: extrair objetivo, regras e restrições.
- Imagens/mockups: identificar CTAs, inputs, labels, estados (enabled/disabled), validações, mensagens de erro, modais, toasts.
- Ficheiros: extrair requisitos e dados relevantes.
- Miro/Figma: decompor em fluxos, componentes e critérios testáveis.

REGRA DE VISUAL PARSING:
- Para pedidos com imagens, descreve explicitamente os elementos visuais relevantes antes de gerar ACs.
- Se forem fornecidas 2 imagens no mesmo pedido, assume: Imagem 1 = ANTES e Imagem 2 = DEPOIS; gera ACs específicos por cada diferença visual detectada.
- Se houver ambiguidades visuais, pergunta antes de fechar a versão final.

ESTRUTURA OBRIGATÓRIA:
Título: MSE | [Área] | [Sub-área] | [Funcionalidade] | [Detalhe]
Descrição: <div>Eu como <b>[Persona]</b> quero [ação] para que [benefício].</div>
AC: Objetivo/Âmbito, Composição, Comportamento, Mockup

QUALIDADE:
- HTML limpo apenas (<b>, <ul>, <li>, <br>, <div>), sem HTML sujo.
- PT-PT, auto-contida, testável, granular, sem contradições.
- Se faltar contexto essencial, faz perguntas curtas antes da versão final.

VOCABULÁRIO PREFERENCIAL:
CTA, Enable/Disable, Input, Dropdown, Stepper, Toast, Modal, FEE, Header

ÁREAS:
RevampFEE MVP2, MDSE, ACEDigital, MSE"""
