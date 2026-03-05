# =============================================================================
# tools.py — Tool definitions, implementations e system prompts v7.2
# =============================================================================

import json, base64, asyncio, logging, uuid, re, math, unicodedata, io, csv, statistics
from datetime import datetime, timezone
from collections import deque, Counter
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
    VISION_ENABLED,
)
from llm_provider import get_embedding_provider, llm_simple, llm_with_fallback
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
from tools_devops import (
    tool_query_workitems,
    tool_analyze_patterns_with_llm,
    tool_generate_user_stories,
    tool_query_hierarchy,
    tool_compute_kpi,
    tool_create_workitem,
    tool_refine_workitem,
)
from tools_knowledge import tool_search_workitems, tool_search_website, tool_search_web
from tools_upload import tool_search_uploaded_document
from tools_export import tool_generate_chart, tool_generate_file
from tools_learning import tool_get_writer_profile, tool_save_writer_profile
from structured_schemas import SCREENSHOT_USER_STORIES_SCHEMA

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
CHART_MAX_POINTS = 10_000  # limite de pontos no payload chart_ready (render)

US_PREFERRED_VOCAB = [
    "CTA",
    "Label",
    "Card",
    "Stepper",
    "Modal",
    "Toast",
    "Dropdown",
    "Input",
    "Toggle",
    "Header",
    "Tab",
    "Breadcrumb",
    "Sidebar",
]

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

def _devops_headers():
    return {"Authorization": f"Basic {base64.b64encode(f':{DEVOPS_PAT}'.encode()).decode()}", "Content-Type": "application/json"}

def _devops_url(path):
    return f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_apis/{path}"

async def get_embedding(text):
    try:
        return await get_embedding_provider().embed(text[:8000].strip() or " ")
    except Exception as e:
        logging.error("[Tools] get_embedding failed: %s", e)
        return None

def _normalize_lookup_key(value: str) -> str:
    txt = unicodedata.normalize("NFKD", str(value or ""))
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    return txt.lower().strip()


def _parse_numeric_value(raw_val):
    txt = str(raw_val or "").strip()
    if not txt:
        return None
    txt = txt.replace("\u00A0", "").replace(" ", "")
    if "," in txt and "." in txt:
        if txt.rfind(",") > txt.rfind("."):
            txt = txt.replace(".", "").replace(",", ".")
        else:
            txt = txt.replace(",", "")
    else:
        txt = txt.replace(",", ".")
    try:
        return float(txt)
    except Exception:
        return None


def _parse_datetime_value(raw_val):
    txt = str(raw_val or "").strip()
    if not txt:
        return None
    # Normalizações comuns (ISO com Z e espaço entre data/hora).
    iso_txt = txt.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_txt)
    except Exception:
        pass
    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
    )
    for fmt in formats:
        try:
            return datetime.strptime(txt, fmt)
        except Exception:
            continue
    return None


def _infer_group_by_mode(query: str, group_by: str) -> str:
    raw = _normalize_lookup_key(group_by or "")
    if raw in ("year", "ano", "anual"):
        return "year"
    if raw in ("month", "mes", "mês", "mensal"):
        return "month"
    if raw in ("quarter", "trimestre", "trimestral", "q"):
        return "quarter"
    if raw in ("week", "semana", "semanal"):
        return "week"
    if raw in ("day", "dia", "diario", "diário"):
        return "day"
    if raw in ("none", "raw", "sem"):
        return "none"
    q = _normalize_lookup_key(query or "")
    if re.search(r"\b(ano|anual|year|yearly)\b", q):
        return "year"
    if re.search(r"\b(mes|mensal|month|monthly)\b", q):
        return "month"
    if re.search(r"\b(quarter|trimestre|trimestral|q[1-4])\b", q):
        return "quarter"
    if re.search(r"\b(week|weekly|semana|semanal)\b", q):
        return "week"
    if re.search(r"\b(day|daily|dia|diario)\b", q):
        return "day"
    return "none"


def _infer_agg_mode(query: str, agg: str) -> str:
    raw = _normalize_lookup_key(agg or "")
    if raw in ("mean", "avg", "average", "media", "média"):
        return "mean"
    if raw in ("sum", "soma", "total"):
        return "sum"
    if raw in ("min", "minimum", "minimo", "mínimo"):
        return "min"
    if raw in ("max", "maximum", "maximo", "máximo"):
        return "max"
    if raw in ("count", "contagem", "numero", "número", "quantidade"):
        return "count"
    q = _normalize_lookup_key(query or "")
    if re.search(r"\b(m[eé]dia|m[eé]dio|average|mean)\b", q):
        return "mean"
    if re.search(r"\b(soma|sum|total)\b", q):
        return "sum"
    if re.search(r"\b(min|minimo|mínimo|minimum|menor)\b", q):
        return "min"
    if re.search(r"\b(max|maximo|máximo|maximum|maior)\b", q):
        return "max"
    if re.search(r"\b(count|quantidade|numero|número|contagem)\b", q):
        return "count"
    return "mean"


def _normalize_metric_name(value: str) -> str:
    raw = _normalize_lookup_key(value or "")
    aliases = {
        "avg": "mean",
        "average": "mean",
        "media": "mean",
        "mediana": "median",
        "stddev": "std",
        "stdev": "std",
        "desvio": "std",
        "q1": "p25",
        "q3": "p75",
    }
    return aliases.get(raw, raw)


def _resolve_requested_metrics(metrics, fallback_agg: str) -> list[str]:
    allowed = {"min", "max", "mean", "sum", "count", "std", "median", "p25", "p75"}
    resolved = []
    for m in (metrics or []):
        name = _normalize_metric_name(str(m or ""))
        if name in allowed and name not in resolved:
            resolved.append(name)
    if resolved:
        return resolved
    fallback = _normalize_metric_name(fallback_agg or "mean")
    if fallback not in allowed:
        fallback = "mean"
    return [fallback]


def _extract_metric_requests_from_query(query: str) -> list[str]:
    q = _normalize_lookup_key(query or "")
    ordered = []
    metric_patterns = [
        ("min", r"\b(min|minimo|mínimo|minimum|menor)\b"),
        ("max", r"\b(max|maximo|máximo|maximum|maior)\b"),
        ("mean", r"\b(media|média|mean|average|medio|médio)\b"),
        ("std", r"\b(std|desvio|desvio padrao|desvio padrão|stdev|stddev)\b"),
        ("median", r"\b(median|mediana)\b"),
        ("sum", r"\b(sum|soma|total)\b"),
        ("count", r"\b(count|contagem|quantidade|numero|número)\b"),
        ("p25", r"\b(p25|q1|percentil 25)\b"),
        ("p75", r"\b(p75|q3|percentil 75)\b"),
    ]
    for metric, pattern in metric_patterns:
        if re.search(pattern, q) and metric not in ordered:
            ordered.append(metric)
    return ordered


def _infer_text_column(query: str, columns, date_column: str = "", records: Optional[list[dict]] = None) -> str:
    q = _normalize_lookup_key(query or "")
    token_candidates = [
        _normalize_lookup_key(t)
        for t in re.findall(r"\b[A-Za-z][A-Za-z0-9_-]{3,}\b", str(query or ""))
        if any(ch.isdigit() for ch in t)
    ]
    if token_candidates and records:
        best_by_value = ("", 0)
        sample_rows = records[:3000]
        for c in columns or []:
            if c == date_column:
                continue
            hits = 0
            for row in sample_rows:
                cell = _normalize_lookup_key((row or {}).get(c, ""))
                if not cell:
                    continue
                if any(tok == cell or tok in cell for tok in token_candidates):
                    hits += 1
            if hits > best_by_value[1]:
                best_by_value = (c, hits)
        if best_by_value[1] > 0:
            return best_by_value[0]

    best = ("", -1)
    for c in columns or []:
        if c == date_column:
            continue
        n = _normalize_lookup_key(c)
        score = 0
        if n and n in q:
            score += 10
        tokens = [t for t in re.split(r"[^a-z0-9]+", n) if len(t) >= 3]
        score += sum(1 for t in tokens if t in q)
        if score > best[1]:
            best = (c, score)
    if best[1] > 0:
        return best[0]
    for c in columns or []:
        if c != date_column:
            return c
    return ""


def _column_numeric_ratio(records: list[dict], column: str, sample_limit: int = 5000) -> float:
    if not column:
        return 0.0
    inspected = 0
    numeric = 0
    for row in records[: max(1, sample_limit)]:
        val = str((row or {}).get(column, "") or "").strip()
        if not val:
            continue
        inspected += 1
        if _parse_numeric_value(val) is not None:
            numeric += 1
    if inspected == 0:
        return 0.0
    return numeric / inspected


def _build_column_profiles(records: list[dict], columns: list[str], max_columns: int = 80) -> list[dict]:
    profiles = []
    limited_columns = list(columns or [])[: max(1, max_columns)]
    for c in limited_columns:
        raw_vals = [str((row or {}).get(c, "") or "").strip() for row in records]
        non_empty_vals = [v for v in raw_vals if v]
        empty_count = len(raw_vals) - len(non_empty_vals)
        numeric_vals = []
        dt_hits = 0
        for v in non_empty_vals[:20000]:
            num = _parse_numeric_value(v)
            if num is not None:
                numeric_vals.append(num)
            if _parse_datetime_value(v) is not None:
                dt_hits += 1
        ratio = len(numeric_vals) / max(1, min(len(non_empty_vals), 20000))
        type_hint = "numeric" if ratio >= 0.8 and numeric_vals else "text"
        if type_hint == "text" and dt_hits >= max(5, int(0.6 * max(1, min(len(non_empty_vals), 20000)))):
            type_hint = "datetime"
        profile = {
            "name": c,
            "non_empty": len(non_empty_vals),
            "empty": empty_count,
            "type": type_hint,
            "sample": non_empty_vals[:5],
        }
        if type_hint == "numeric" and numeric_vals:
            profile.update(
                {
                    "min": round(min(numeric_vals), 6),
                    "max": round(max(numeric_vals), 6),
                    "mean": round(sum(numeric_vals) / len(numeric_vals), 6),
                    "std": round(statistics.stdev(numeric_vals), 6) if len(numeric_vals) > 1 else 0.0,
                }
            )
        else:
            value_counter = Counter(non_empty_vals[:50000])
            profile["distinct_count"] = len(value_counter)
            profile["top_values"] = [
                {"value": value, "count": count}
                for value, count in value_counter.most_common(5)
            ]
        profiles.append(profile)
    return profiles


def _compute_metrics(vals: list[float], requested_metrics: list[str], count_override: Optional[int] = None) -> dict:
    if not vals and not (requested_metrics == ["count"] and count_override is not None):
        return {}
    result = {}
    sorted_vals = sorted(vals) if vals else []
    n_vals = len(sorted_vals)
    for metric in requested_metrics:
        m = _normalize_metric_name(metric)
        if m == "count":
            result["count"] = int(count_override if count_override is not None else n_vals)
        elif m == "sum" and n_vals:
            result["sum"] = round(sum(sorted_vals), 6)
        elif m == "mean" and n_vals:
            result["mean"] = round(sum(sorted_vals) / n_vals, 6)
        elif m == "min" and n_vals:
            result["min"] = round(sorted_vals[0], 6)
        elif m == "max" and n_vals:
            result["max"] = round(sorted_vals[-1], 6)
        elif m == "std":
            result["std"] = round(statistics.stdev(sorted_vals), 6) if n_vals > 1 else 0.0
        elif m == "median" and n_vals:
            result["median"] = round(statistics.median(sorted_vals), 6)
        elif m == "p25" and n_vals:
            result["p25"] = round(sorted_vals[int((n_vals - 1) * 0.25)], 6)
        elif m == "p75" and n_vals:
            result["p75"] = round(sorted_vals[int((n_vals - 1) * 0.75)], 6)
    return result


def _infer_chart_metric(requested_metrics: list[str], groups: list[dict]) -> str:
    for metric in requested_metrics:
        norm = _normalize_metric_name(metric)
        if any(isinstance(g.get("metrics"), dict) and norm in g.get("metrics", {}) for g in groups):
            return norm
    for fallback in ("mean", "sum", "count", "max", "min", "median", "std", "p25", "p75"):
        if any(isinstance(g.get("metrics"), dict) and fallback in g.get("metrics", {}) for g in groups):
            return fallback
    return requested_metrics[0] if requested_metrics else "mean"


def _match_period(dt: datetime, period_expr: str) -> bool:
    expr = str(period_expr or "").strip()
    if not expr:
        return False
    if re.fullmatch(r"\d{4}$", expr):
        return f"{dt.year:04d}" == expr
    if re.fullmatch(r"\d{4}-\d{2}$", expr):
        return f"{dt.year:04d}-{dt.month:02d}" == expr
    if re.fullmatch(r"\d{4}-Q[1-4]$", expr, flags=re.IGNORECASE):
        return f"{dt.year:04d}-Q{((dt.month - 1) // 3) + 1}".upper() == expr.upper()
    if re.fullmatch(r"\d{4}-W\d{2}$", expr, flags=re.IGNORECASE):
        iso_year, iso_week, _ = dt.isocalendar()
        return f"{iso_year:04d}-W{iso_week:02d}".upper() == expr.upper()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}$", expr):
        return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}" == expr
    return str(dt.date()).startswith(expr)


def _match_column_name(requested: str, columns):
    if not requested:
        return ""
    wanted = _normalize_lookup_key(requested)
    if not wanted:
        return ""
    direct = { _normalize_lookup_key(c): c for c in (columns or []) }
    if wanted in direct:
        return direct[wanted]
    for c in columns or []:
        n = _normalize_lookup_key(c)
        if wanted in n or n in wanted:
            return c
    return ""


def _infer_date_column(query: str, columns, records):
    q = _normalize_lookup_key(query or "")
    date_hints = ["time", "date", "data", "datetime", "timestamp", "hora"]
    for c in columns:
        n = _normalize_lookup_key(c)
        if any(h in n for h in date_hints):
            return c
    # Fallback por detectabilidade de datetime nos primeiros registos.
    sample = records[:200]
    best = ("", 0)
    for c in columns:
        ok = 0
        for r in sample:
            if _parse_datetime_value(r.get(c, "")) is not None:
                ok += 1
        if ok > best[1]:
            best = (c, ok)
    if best[0] and best[1] > 0:
        return best[0]
    if re.search(r"\b(ano|mes|m[eê]s|dia|data|tempo|time)\b", q):
        return columns[0] if columns else ""
    return ""


def _infer_value_column(query: str, columns, records, date_column: str = ""):
    q = _normalize_lookup_key(query or "")
    explicit_match = ""
    # Heurística de score por tokens da query.
    best = ("", -1)
    for c in columns:
        if c == date_column:
            continue
        n = _normalize_lookup_key(c)
        score = 0
        if n and n in q:
            score += 10
        tokens = [t for t in re.split(r"[^a-z0-9]+", n) if len(t) >= 3]
        score += sum(1 for t in tokens if t in q)
        if score > best[1]:
            best = (c, score)
    if best[1] > 0:
        explicit_match = best[0]
    if explicit_match:
        return explicit_match

    # Fallback: primeira coluna maioritariamente numérica.
    sample = records[:300]
    best_numeric = ("", -1.0)
    for c in columns:
        if c == date_column:
            continue
        vals = [r.get(c, "") for r in sample]
        non_empty = [v for v in vals if str(v).strip()]
        if not non_empty:
            continue
        numeric = sum(1 for v in non_empty if _parse_numeric_value(v) is not None)
        ratio = numeric / max(1, len(non_empty))
        if ratio > best_numeric[1]:
            best_numeric = (c, ratio)
    if best_numeric[0] and best_numeric[1] >= 0.5:
        return best_numeric[0]
    return ""


async def tool_analyze_uploaded_table(
    query: str = "",
    conv_id: str = "",
    user_sub: str = "",
    filename: str = "",
    value_column: str = "",
    date_column: str = "",
    group_by: str = "",
    agg: str = "mean",
    top: int = 500,
    metrics: list = None,
    top_n: int = 0,
    compare_periods: dict = None,
    full_points: bool = False,
):
    q = str(query or "").strip()
    safe_conv = str(conv_id or "").strip()
    safe_user = str(user_sub or "").strip()
    if not safe_conv:
        return {"error": "conv_id é obrigatório para analisar ficheiros carregados."}

    odata_conv = safe_conv.replace("'", "''")
    try:
        rows = await table_query("UploadIndex", f"PartitionKey eq '{odata_conv}'", top=max(1, min(UPLOAD_INDEX_TOP, 500)))
    except Exception as e:
        return {"error": f"Falha a carregar UploadIndex: {str(e)}"}
    if not rows:
        return {"error": "Não foram encontrados ficheiros carregados nesta conversa."}

    wanted_filename = _normalize_lookup_key(filename)
    selected = None
    tabular_rows = []
    for row in rows:
        owner_sub = str(row.get("UserSub", "") or "")
        if safe_user and owner_sub and owner_sub != safe_user:
            continue
        fname = str(row.get("Filename", "") or "")
        fname_lower = fname.lower()
        if not fname_lower.endswith((".csv", ".xlsx", ".xls")):
            continue
        if not str(row.get("RawBlobRef", "") or ""):
            continue
        tabular_rows.append(row)
    if not tabular_rows:
        return {"error": "Não há ficheiros CSV/Excel com raw blob disponível nesta conversa."}

    tabular_rows.sort(key=lambda r: str(r.get("UploadedAt", "")), reverse=True)
    if wanted_filename:
        for row in tabular_rows:
            fname = str(row.get("Filename", "") or "")
            norm = _normalize_lookup_key(fname)
            if norm == wanted_filename or wanted_filename in norm:
                selected = row
                break
        if selected is None:
            return {"error": f"Ficheiro '{filename}' não encontrado nesta conversa."}
    else:
        selected = tabular_rows[0]

    selected_filename = str(selected.get("Filename", "") or "")
    raw_blob_ref = str(selected.get("RawBlobRef", "") or "")
    container, blob_name = parse_blob_ref(raw_blob_ref)
    if not container or not blob_name:
        return {"error": "RawBlobRef inválido para o ficheiro selecionado."}

    try:
        raw_bytes = await blob_download_bytes(container, blob_name)
    except Exception as e:
        return {"error": f"Falha ao descarregar raw blob: {str(e)}"}
    if not raw_bytes:
        return {"error": "Raw blob vazio para o ficheiro selecionado."}

    records = []
    columns = []
    max_rows = 500000
    fname_lower = selected_filename.lower()
    if fname_lower.endswith(".csv"):
        text = raw_bytes.decode("utf-8", errors="replace")
        if not text.strip():
            return {"error": "CSV vazio."}
        sample = "\n".join(text.splitlines()[:20])
        delimiter = ","
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            delimiter = dialect.delimiter
        except Exception:
            delimiter = ";" if ";" in sample and sample.count(";") >= sample.count(",") else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        if not reader.fieldnames:
            return {"error": "CSV sem header válido."}
        columns = [str(c).strip() for c in reader.fieldnames]
        for i, row in enumerate(reader):
            if i >= max_rows:
                break
            records.append({c: row.get(c, "") for c in columns})
    else:
        try:
            import openpyxl
        except Exception:
            return {"error": "openpyxl indisponível no servidor para analisar Excel."}
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
        ws = wb.active
        row_iter = ws.iter_rows(values_only=True)
        header = next(row_iter, None)
        if not header:
            wb.close()
            return {"error": "Excel vazio."}
        columns = [str(c).strip() if c is not None and str(c).strip() else f"Col{idx+1}" for idx, c in enumerate(header)]
        for i, vals in enumerate(row_iter):
            if i >= max_rows:
                break
            item = {}
            for ci, c in enumerate(columns):
                item[c] = "" if ci >= len(vals) or vals[ci] is None else str(vals[ci])
            records.append(item)
        wb.close()

    if not records:
        return {"error": "Ficheiro sem linhas de dados."}

    matched_date_col = _match_column_name(date_column, columns) if date_column else ""
    matched_value_col = _match_column_name(value_column, columns) if value_column else ""
    group_mode = _infer_group_by_mode(q, group_by)
    agg_mode = _infer_agg_mode(q, agg)
    requested_metrics = _resolve_requested_metrics(metrics, agg_mode)
    query_metrics = _extract_metric_requests_from_query(q)
    if not metrics and query_metrics:
        requested_metrics = query_metrics
        if agg_mode not in requested_metrics:
            agg_mode = requested_metrics[0]
    q_norm = _normalize_lookup_key(q)
    full_list_intent = bool(
        re.search(r"\b(lista completa|completo|completa|todos os valores|sem amostra|integral|analisa tudo)\b", q_norm)
    )
    schema_profile_intent = bool(
        re.search(r"\b(o que contem|o que contém|estrutura|schema|colunas|campos|significado|dicionario|dicionário)\b", q_norm)
    )
    categorical_intent = bool(
        re.search(
            r"\b(distint|unic|únic|valores|moda|mais comum|frequencia|frequência|sempre|cont[eé]m|apenas)\b",
            q_norm,
        )
    )
    warnings_list = []
    rows_total = len(records)
    valid_data_points = 0
    was_sampled = False

    if not matched_date_col and group_mode in ("year", "month", "quarter", "week", "day"):
        matched_date_col = _infer_date_column(q, columns, records)
    if not matched_value_col and requested_metrics != ["count"]:
        matched_value_col = _infer_value_column(q, columns, records, date_column=matched_date_col)
    if not matched_value_col and (categorical_intent or requested_metrics == ["count"]):
        matched_value_col = _infer_text_column(q, columns, date_column=matched_date_col, records=records)

    if group_mode == "none" and (schema_profile_intent or not matched_value_col):
        column_profiles = _build_column_profiles(records, columns)
        return {
            "source": "uploaded_table_raw_blob",
            "conversation_id": safe_conv,
            "filename": selected_filename,
            "row_count": len(records),
            "columns": columns,
            "column_profiles": column_profiles[:40],
            "total_columns_profiled": len(column_profiles),
            "summary": (
                f"Perfil completo de '{selected_filename}' ({len(records)} linhas, {len(columns)} colunas). "
                "Usa estes perfis para responder sem assumir amostras."
            ),
            "analysis_quality": {
                "coverage": 1.0,
                "sampled": False,
                "rows_processed": len(records),
                "rows_total": rows_total,
                "warnings": warnings_list,
            },
        }

    if group_mode in ("year", "month", "quarter", "week", "day") and not matched_date_col:
        return {
            "error": "Não consegui inferir a coluna de data. Indica date_column explicitamente.",
            "columns": columns,
            "filename": selected_filename,
        }
    if not matched_value_col:
        return {
            "error": "Não consegui inferir a coluna para análise. Indica value_column explicitamente.",
            "columns": columns,
            "filename": selected_filename,
        }

    value_numeric_ratio = _column_numeric_ratio(records, matched_value_col)

    chart_top = max(1, min(int(top or 500), 5000))
    top_n_limit = max(0, min(int(top_n or 0), 5000))
    groups = []
    chart_groups = []

    if isinstance(compare_periods, dict) and compare_periods:
        period_col = _match_column_name(compare_periods.get("col", ""), columns) if compare_periods.get("col") else matched_date_col
        if not period_col:
            return {"error": "compare_periods requer coluna de data válida.", "filename": selected_filename}
        period_1 = str(compare_periods.get("period1", "") or "").strip()
        period_2 = str(compare_periods.get("period2", "") or "").strip()
        if not period_1 or not period_2:
            return {"error": "compare_periods requer period1 e period2.", "filename": selected_filename}

        period_1_vals = []
        period_2_vals = []
        period_1_count = 0
        period_2_count = 0
        for row in records:
            dt = _parse_datetime_value(row.get(period_col, ""))
            if dt is None:
                continue
            if _match_period(dt, period_1):
                period_1_count += 1
                if requested_metrics != ["count"]:
                    num = _parse_numeric_value(row.get(matched_value_col, ""))
                    if num is not None:
                        period_1_vals.append(num)
            elif _match_period(dt, period_2):
                period_2_count += 1
                if requested_metrics != ["count"]:
                    num = _parse_numeric_value(row.get(matched_value_col, ""))
                    if num is not None:
                        period_2_vals.append(num)

        valid_data_points = len(period_1_vals) + len(period_2_vals) if requested_metrics != ["count"] else (period_1_count + period_2_count)
        metrics_p1 = _compute_metrics(period_1_vals, requested_metrics, count_override=period_1_count)
        metrics_p2 = _compute_metrics(period_2_vals, requested_metrics, count_override=period_2_count)
        if not metrics_p1 and not metrics_p2:
            return {"error": "Sem dados suficientes para comparar os períodos pedidos.", "filename": selected_filename}

        delta = {}
        for key in sorted(set(metrics_p1.keys()) | set(metrics_p2.keys())):
            v1 = metrics_p1.get(key)
            v2 = metrics_p2.get(key)
            if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
                delta[key] = round(v2 - v1, 6)

        return {
            "source": "uploaded_table_raw_blob",
            "comparison": True,
            "conversation_id": safe_conv,
            "filename": selected_filename,
            "row_count": len(records),
            "columns": columns,
            "date_column": period_col,
            "value_column": matched_value_col,
            "requested_metrics": requested_metrics,
            "period1": {"name": period_1, "metrics": metrics_p1, "count": period_1_count},
            "period2": {"name": period_2, "metrics": metrics_p2, "count": period_2_count},
            "delta": delta,
            "analysis_quality": {
                "coverage": round(valid_data_points / max(1, len(records)), 4),
                "sampled": False,
                "rows_processed": len(records),
                "rows_total": rows_total,
                "warnings": warnings_list,
            },
        }

    if group_mode in ("year", "month", "quarter", "week", "day"):
        buckets = {}
        for row in records:
            dt = _parse_datetime_value(row.get(matched_date_col, ""))
            if dt is None:
                continue
            if group_mode == "year":
                key = f"{dt.year:04d}"
            elif group_mode == "month":
                key = f"{dt.year:04d}-{dt.month:02d}"
            elif group_mode == "quarter":
                key = f"{dt.year:04d}-Q{((dt.month - 1) // 3) + 1}"
            elif group_mode == "week":
                iso_year, iso_week, _ = dt.isocalendar()
                key = f"{iso_year:04d}-W{iso_week:02d}"
            else:
                key = f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"
            bucket = buckets.setdefault(key, {"key": key, "values": [], "count": 0})
            bucket["count"] += 1
            if requested_metrics == ["count"]:
                continue
            num = _parse_numeric_value(row.get(matched_value_col, ""))
            if num is None:
                continue
            bucket["values"].append(num)
            valid_data_points += 1

        for key in sorted(buckets.keys()):
            bucket = buckets[key]
            metrics_map = _compute_metrics(
                bucket["values"],
                requested_metrics,
                count_override=bucket["count"],
            )
            if not metrics_map:
                continue
            if metrics:
                groups.append({"group": key, "metrics": metrics_map, "count": int(bucket["count"])})
            else:
                value = metrics_map.get(agg_mode)
                if value is None:
                    continue
                groups.append({"group": key, "value": round(float(value), 6), "count": int(bucket["count"])})
    else:
        series_intent = bool(re.search(r"\b(grafico|gráfico|chart|linha|line|evolucao|evolução|time series|serie temporal)\b", q_norm))
        if matched_date_col and matched_value_col and series_intent:
            # Modo "none" com pedido de gráfico temporal.
            series = []
            for row in records:
                dt = _parse_datetime_value(row.get(matched_date_col, ""))
                num = _parse_numeric_value(row.get(matched_value_col, ""))
                if dt is None or num is None:
                    continue
                series.append((dt, num))
            series.sort(key=lambda x: x[0])
            if not series:
                return {"error": "Sem dados numéricos/datas válidos para gerar série temporal.", "filename": selected_filename}
            if full_points:
                sampled = series
            else:
                if len(series) > chart_top:
                    step = max(1, len(series) // chart_top)
                    sampled = [series[i] for i in range(0, len(series), step)][:chart_top]
                    was_sampled = True
                    warnings_list.append(f"Série temporal amostrada para {len(sampled)} de {len(series)} pontos.")
                else:
                    sampled = series
            valid_data_points = len(series)
            groups = [
                {"group": dt.isoformat(), "value": round(val, 6), "count": 1}
                for dt, val in sampled
            ]
        elif matched_value_col and (categorical_intent or value_numeric_ratio < 0.35):
            # Modo categórico/textual: contagem exata de valores na coluna.
            vals = [str((row or {}).get(matched_value_col, "") or "").strip() for row in records]
            non_empty_vals = [v for v in vals if v]
            empty_count = len(vals) - len(non_empty_vals)
            valid_data_points = len(non_empty_vals)
            if not non_empty_vals:
                return {"error": "Sem dados válidos na coluna indicada.", "filename": selected_filename}

            counter = Counter(non_empty_vals)
            distinct_count = len(counter)
            sorted_values = counter.most_common()
            group_payload = sorted_values
            if not full_points:
                limit = top_n_limit if top_n_limit > 0 else max(10, min(chart_top, 200))
                group_payload = sorted_values[:limit]
                if len(sorted_values) > len(group_payload):
                    warnings_list.append(
                        f"Mostrados top {len(group_payload)} valores de {len(sorted_values)} distintos."
                    )
            elif len(group_payload) > 10000:
                group_payload = group_payload[:10000]
                was_sampled = True
                warnings_list.append("Lista de valores distintos limitada a 10.000 para resposta.")

            groups = [
                {
                    "group": value,
                    "value": int(count),
                    "count": int(count),
                    "ratio": round(count / max(1, len(non_empty_vals)), 6),
                }
                for value, count in group_payload
            ]

            all_values = None
            all_values_truncated = False
            if full_list_intent:
                all_limit = 2000
                sliced = sorted_values[:all_limit]
                all_values = [
                    {
                        "value": value,
                        "count": int(count),
                        "ratio": round(count / max(1, len(non_empty_vals)), 6),
                    }
                    for value, count in sliced
                ]
                all_values_truncated = len(sorted_values) > all_limit
                if all_values_truncated:
                    warnings_list.append(
                        f"Lista completa truncada a {all_limit} valores distintos; pede export para total."
                    )

            chart_groups = groups
            if len(chart_groups) > CHART_MAX_POINTS:
                step = max(1, len(chart_groups) // CHART_MAX_POINTS)
                chart_groups = [chart_groups[i] for i in range(0, len(chart_groups), step)][:CHART_MAX_POINTS]
                was_sampled = True
                warnings_list.append(f"chart_ready limitado a {len(chart_groups)} de {len(groups)} categorias.")

            return {
                "source": "uploaded_table_raw_blob",
                "conversation_id": safe_conv,
                "filename": selected_filename,
                "row_count": len(records),
                "columns": columns,
                "group_by": "none",
                "agg": "count",
                "requested_metrics": ["count"],
                "date_column": matched_date_col,
                "value_column": matched_value_col,
                "categorical": True,
                "distinct_count": distinct_count,
                "non_empty_count": len(non_empty_vals),
                "empty_count": empty_count,
                "is_constant": distinct_count == 1,
                "constant_value": sorted_values[0][0] if distinct_count == 1 else None,
                "groups": groups,
                "all_values": all_values,
                "all_values_truncated": all_values_truncated,
                "summary": (
                    f"Análise categórica completa de '{selected_filename}' ({len(records)} linhas) "
                    f"na coluna '{matched_value_col}': {distinct_count} valor(es) distinto(s)."
                ),
                "analysis_quality": {
                    "coverage": round(valid_data_points / max(1, len(records)), 4),
                    "sampled": was_sampled,
                    "rows_processed": len(records),
                    "rows_total": rows_total,
                    "warnings": warnings_list,
                },
                "chart_ready": {
                    "chart_type": "bar",
                    "title": f"Frequência de {matched_value_col}",
                    "x_values": [g.get("group", "") for g in chart_groups],
                    "y_values": [int(g.get("value", 0)) for g in chart_groups],
                    "x_label": matched_value_col,
                    "y_label": f"count({matched_value_col})",
                },
            }
        elif matched_value_col:
            nums = []
            for row in records:
                val = _parse_numeric_value(row.get(matched_value_col, ""))
                if val is not None:
                    nums.append(val)
            valid_data_points = len(nums)
            if not nums:
                return {"error": "Sem dados numéricos válidos na coluna indicada.", "filename": selected_filename}
            metrics_map = _compute_metrics(nums, requested_metrics, count_override=len(nums))
            if not metrics_map:
                return {"error": "Sem métricas válidas para o conjunto de dados.", "filename": selected_filename}
            if metrics:
                groups = [{"group": "overall", "metrics": metrics_map, "count": len(nums)}]
            else:
                overall = metrics_map.get(agg_mode)
                if overall is None:
                    return {"error": f"Métrica '{agg_mode}' indisponível para os dados.", "filename": selected_filename}
                groups = [{"group": "overall", "value": round(float(overall), 6), "count": len(nums)}]
        else:
            return {"error": "Indica value_column para análise sem agrupamento.", "columns": columns, "filename": selected_filename}

    if not groups:
        return {"error": "Não foi possível produzir agregações com os filtros atuais.", "filename": selected_filename}

    if top_n_limit > 0 and len(groups) > top_n_limit:
        if metrics:
            sort_metric = _infer_chart_metric(requested_metrics, groups)
            groups = sorted(
                groups,
                key=lambda g: float((g.get("metrics") or {}).get(sort_metric, float("-inf"))),
                reverse=True,
            )[:top_n_limit]
            warnings_list.append(f"Resultado limitado aos top {top_n_limit} grupos por {sort_metric}.")
        else:
            groups = sorted(groups, key=lambda g: float(g.get("value", float("-inf"))), reverse=True)[:top_n_limit]
            warnings_list.append(f"Resultado limitado aos top {top_n_limit} grupos.")

    chart_groups = groups
    if len(chart_groups) > CHART_MAX_POINTS:
        step = max(1, len(chart_groups) // CHART_MAX_POINTS)
        chart_groups = [chart_groups[i] for i in range(0, len(chart_groups), step)][:CHART_MAX_POINTS]
        was_sampled = True
        if full_points:
            warnings_list.append(
                f"chart_ready amostrado para {len(chart_groups)} de {len(groups)} pontos "
                "(full_points=true: groups contém todos)."
            )
        else:
            warnings_list.append(f"chart_ready limitado a {len(chart_groups)} de {len(groups)} grupos.")

    x_values = [g.get("group", "") for g in chart_groups]
    if metrics:
        chart_metric = _infer_chart_metric(requested_metrics, chart_groups)
        y_values = [float((g.get("metrics") or {}).get(chart_metric, 0)) for g in chart_groups]
        metric_label = "count" if chart_metric == "count" else f"{chart_metric}({matched_value_col})"
    else:
        chart_metric = agg_mode
        y_values = [float(g.get("value", 0)) for g in chart_groups]
        metric_label = "count" if agg_mode == "count" else f"{agg_mode}({matched_value_col})"
    chart_type = "bar" if group_mode in ("year", "month", "quarter", "week", "day") else "line"
    if group_mode == "year":
        x_label = "Ano"
    elif group_mode == "month":
        x_label = "Ano-Mês"
    elif group_mode == "quarter":
        x_label = "Ano-Trimestre"
    elif group_mode == "week":
        x_label = "Ano-Semana"
    elif group_mode == "day":
        x_label = "Dia"
    else:
        x_label = matched_date_col or "Grupo"
    chart_title = (
        f"{metric_label} por {x_label.lower()}"
        if group_mode in ("year", "month", "quarter", "week", "day")
        else f"{metric_label} - {selected_filename}"
    )

    return {
        "source": "uploaded_table_raw_blob",
        "conversation_id": safe_conv,
        "filename": selected_filename,
        "row_count": len(records),
        "columns": columns,
        "group_by": group_mode,
        "agg": agg_mode,
        "requested_metrics": requested_metrics,
        "date_column": matched_date_col,
        "value_column": matched_value_col,
        "groups": groups,
        "summary": (
            f"Análise completa de '{selected_filename}' ({len(records)} linhas). "
            f"Agrupamento={group_mode}, agregação={agg_mode}, "
            f"date_column={matched_date_col or '-'}, value_column={matched_value_col or '-'}."
        ),
        "analysis_quality": {
            "coverage": round(valid_data_points / max(1, len(records)), 4),
            "sampled": was_sampled,
            "rows_processed": len(records),
            "rows_total": rows_total,
            "warnings": warnings_list,
        },
        "chart_ready": {
            "chart_type": chart_type,
            "title": chart_title,
            "x_values": x_values,
            "y_values": y_values,
            "x_label": x_label,
            "y_label": metric_label,
        },
    }


async def _load_uploaded_files_for_code(
    conv_id: str,
    user_sub: str = "",
    filename: str = "",
    max_files: int = 3,
    max_total_bytes: int = 25_000_000,
) -> dict:
    safe_conv = str(conv_id or "").strip()
    safe_user = str(user_sub or "").strip()
    if not safe_conv:
        return {}

    odata_conv = safe_conv.replace("'", "''")
    try:
        rows = await table_query("UploadIndex", f"PartitionKey eq '{odata_conv}'", top=max(1, min(UPLOAD_INDEX_TOP, 500)))
    except Exception as e:
        logging.warning("[Tools] run_code UploadIndex query failed: %s", e)
        return {}

    if not rows:
        return {}

    wanted_filename = _normalize_lookup_key(filename)
    candidates = []
    for row in rows:
        owner_sub = str(row.get("UserSub", "") or "")
        if safe_user and owner_sub and owner_sub != safe_user:
            continue
        fname = str(row.get("Filename", "") or "")
        raw_ref = str(row.get("RawBlobRef", "") or "")
        if not fname or not raw_ref:
            continue
        norm = _normalize_lookup_key(fname)
        if wanted_filename and wanted_filename not in norm and norm != wanted_filename:
            continue
        candidates.append(row)

    if not candidates:
        return {}

    candidates.sort(key=lambda r: str(r.get("UploadedAt", "")), reverse=True)
    selected = candidates[: max(1, min(max_files, 10))]

    uploaded_files: dict = {}
    total = 0
    for row in selected:
        fname = str(row.get("Filename", "") or "").strip()
        safe_name = fname.replace("\\", "_").replace("/", "_")
        raw_blob_ref = str(row.get("RawBlobRef", "") or "")
        container, blob_name = parse_blob_ref(raw_blob_ref)
        if not container or not blob_name:
            continue
        try:
            raw_bytes = await blob_download_bytes(container, blob_name)
        except Exception as e:
            logging.warning("[Tools] run_code failed to download upload %s: %s", safe_name, e)
            continue
        if not raw_bytes:
            continue
        if total + len(raw_bytes) > max_total_bytes:
            break
        uploaded_files[safe_name] = raw_bytes
        total += len(raw_bytes)
    return uploaded_files


async def tool_run_code(
    code: str = "",
    description: str = "",
    conv_id: str = "",
    user_sub: str = "",
    filename: str = "",
):
    from code_interpreter import execute_code

    safe_code = str(code or "")
    safe_desc = str(description or "").strip()
    safe_conv = str(conv_id or "").strip()
    safe_user = str(user_sub or "").strip()
    safe_filename = str(filename or "").strip()

    mounted_files = {}
    if safe_conv:
        mounted_files = await _load_uploaded_files_for_code(
            safe_conv,
            user_sub=safe_user,
            filename=safe_filename,
        )

    result = await execute_code(
        code=safe_code,
        uploaded_files=mounted_files or None,
    )

    artifacts = []
    for img in (result.get("images") or []):
        fname = str(img.get("filename", "") or "").strip()
        b64 = str(img.get("data", "") or "")
        if not fname or not b64:
            continue
        try:
            content = base64.b64decode(b64)
        except Exception:
            continue
        fmt = fname.rsplit(".", 1)[-1].lower() if "." in fname else "bin"
        download_id = await _store_generated_file(content, str(img.get("mime_type", "") or "application/octet-stream"), fname, fmt)
        artifacts.append(
            {
                "type": "image",
                "filename": fname,
                "size": int(img.get("size", len(content)) or len(content)),
                "download_id": download_id,
                "url": f"/api/download/{download_id}" if download_id else "",
            }
        )

    for file_obj in (result.get("files") or []):
        fname = str(file_obj.get("filename", "") or "").strip()
        b64 = str(file_obj.get("data", "") or "")
        if not fname or not b64:
            continue
        try:
            content = base64.b64decode(b64)
        except Exception:
            continue
        fmt = fname.rsplit(".", 1)[-1].lower() if "." in fname else "bin"
        download_id = await _store_generated_file(content, str(file_obj.get("mime_type", "") or "application/octet-stream"), fname, fmt)
        artifacts.append(
            {
                "type": "file",
                "filename": fname,
                "size": int(file_obj.get("size", len(content)) or len(content)),
                "download_id": download_id,
                "url": f"/api/download/{download_id}" if download_id else "",
            }
        )

    stdout = str(result.get("stdout", "") or "")
    stderr = str(result.get("stderr", "") or "")
    error = str(result.get("error", "") or "")

    output_parts = []
    if safe_desc:
        output_parts.append(f"Descrição: {safe_desc}")
    if stdout:
        output_parts.append(f"STDOUT:\n{stdout}")
    if stderr:
        output_parts.append(f"STDERR:\n{stderr}")
    if error:
        output_parts.append(f"ERROR: {error}")
    if mounted_files:
        output_parts.append(f"Ficheiros montados no sandbox: {', '.join(sorted(mounted_files.keys()))}")
    if artifacts:
        names = [a.get("filename", "") for a in artifacts if a.get("filename")]
        output_parts.append(f"Ficheiros gerados: {', '.join(names)}")
    if not output_parts:
        output_parts.append("Código executado sem output.")

    payload = {
        "source": "code_interpreter",
        "success": bool(result.get("success", False)),
        "description": safe_desc,
        "stdout": stdout,
        "stderr": stderr or None,
        "error": error or None,
        "return_code": result.get("return_code"),
        "mounted_files": sorted(mounted_files.keys()),
        "generated_artifacts": artifacts,
        "items": artifacts,
        "total_count": len(artifacts),
        "output_text": "\n\n".join(output_parts)[:12000],
    }
    if not payload["success"] and not payload.get("error"):
        payload["error"] = "Falha na execução do código."
    return payload

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


async def tool_screenshot_to_us(
    image_base64: str = "",
    context: str = "",
    author_style: str = "",
) -> dict:
    """Analisa screenshot de UI e gera User Stories estruturadas com modelo vision-capable."""
    if not VISION_ENABLED:
        return {"error": "Vision feature is disabled. Set VISION_ENABLED=true to enable."}

    raw_b64 = str(image_base64 or "").strip()
    if not raw_b64:
        return {"error": "image_base64 e obrigatorio. Enviar screenshot em base64."}

    if len(raw_b64) > 14_000_000:
        return {"error": "Imagem demasiado grande para analise (max ~10MB)."}

    content_type = "image/png"
    b64_payload = raw_b64
    if raw_b64.startswith("data:") and "," in raw_b64:
        header, payload = raw_b64.split(",", 1)
        b64_payload = payload.strip()
        m = re.match(r"data:([^;]+);base64", header, flags=re.I)
        if m:
            content_type = m.group(1).strip().lower() or content_type

    try:
        base64.b64decode(b64_payload, validate=True)
    except Exception:
        return {"error": "image_base64 invalido (nao e base64 valido)."}

    prompt_parts = [
        "Analisa este screenshot de interface de utilizador.",
        "Identifica elementos visiveis de UI (inputs, labels, CTAs, tabelas, modais, toasts, menus, validacoes).",
        "Gera User Stories estruturadas no formato MSE com titulo, descricao e criterios de aceitacao testaveis.",
        (
            "Retorna JSON no formato: "
            '{"stories":[{"title":"...","description":"...","acceptance_criteria":["..."]}]}.'
        ),
        "Nao inventes APIs/endpoints de backend sem evidencia visual ou contexto explicito.",
    ]
    ctx = str(context or "").strip()
    if ctx:
        prompt_parts.append(f"Contexto adicional: {ctx}")
    style = str(author_style or "").strip()
    if style:
        prompt_parts.append(f"Estilo de escrita preferido: {style}")
    vision_prompt = "\n".join(prompt_parts)

    content_blocks = [
        {"type": "text", "text": vision_prompt},
        {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{b64_payload}"}},
    ]

    try:
        llm_resp = await llm_with_fallback(
            messages=[{"role": "user", "content": content_blocks}],
            tier="vision",
            max_tokens=4096,
            response_format=SCREENSHOT_USER_STORIES_SCHEMA,
        )
        answer = str(getattr(llm_resp, "content", "") or "")
        if not answer and isinstance(llm_resp, dict):
            answer = str(llm_resp.get("content", "") or "")

        parsed = None
        try:
            parsed = json.loads(answer)
        except Exception:
            match = re.search(r"\{[\s\S]*\"stories\"[\s\S]*\}", answer)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except Exception:
                    parsed = None

        if isinstance(parsed, dict):
            stories = parsed.get("stories")
            if isinstance(stories, list):
                return {
                    "stories": stories,
                    "raw_analysis": answer[:2000],
                    "source": "vision_llm",
                }

        return {
            "stories": [],
            "raw_analysis": answer[:4000],
            "source": "vision_llm",
            "note": "Resposta nao estruturada como JSON. Ver raw_analysis.",
        }
    except Exception as e:
        logging.warning("[Tools] screenshot_to_us failed: %s", e)
        return {"error": f"Analise de screenshot falhou: {str(e)[:200]}"}

# =============================================================================
# TOOL DEFINITIONS (formato OpenAI — traduzido auto para Anthropic pelo llm_provider)
# =============================================================================
_BUILTIN_TOOL_DEFINITIONS = [
    {"type":"function","function":{"name":"query_workitems","description":"Query Azure DevOps via WIQL para contagens, listagens, filtros. Dados em TEMPO REAL.","parameters":{"type":"object","properties":{"wiql_where":{"type":"string","description":"WHERE WIQL. Ex: [System.WorkItemType]='User Story' AND [System.State]='Active'"},"fields":{"type":"array","items":{"type":"string"},"description":"Campos extra a retornar. Default: Id,Title,State,Type,AssignedTo,CreatedBy,AreaPath,CreatedDate. Adicionar 'System.Description' e 'Microsoft.VSTS.Common.AcceptanceCriteria' quando o user pedir detalhes/descrição/AC."},"top":{"type":"integer","description":"Max resultados. 0=só contagem."}},"required":["wiql_where"]}}},
    {"type":"function","function":{"name":"search_workitems","description":"Pesquisa semântica em work items indexados. Retorna AMOSTRA dos mais relevantes.","parameters":{"type":"object","properties":{"query":{"type":"string","description":"Texto. Ex: 'transferências SPIN'"},"top":{"type":"integer","description":"Nº resultados. Default: 30."},"filter":{"type":"string","description":"Filtro OData."}},"required":["query"]}}},
    {"type":"function","function":{"name":"search_website","description":"Pesquisa no site MSE. Usa para navegação, funcionalidades, operações.","parameters":{"type":"object","properties":{"query":{"type":"string","description":"Texto. Ex: 'transferência SEPA'"},"top":{"type":"integer","description":"Default: 10"}},"required":["query"]}}},
    {"type":"function","function":{"name":"search_web","description":"Pesquisa na web via Brave Search. Usar para informação atual, dados externos, ou contexto que não está nos documentos internos. Só usar quando o utilizador pedir pesquisa web ou quando a informação não existir nas fontes internas.","parameters":{"type":"object","properties":{"query":{"type":"string","description":"Termos de pesquisa (max 200 chars)."},"top":{"type":"integer","description":"Número de resultados (max 5, default 5)."}},"required":["query"]}}},
    {"type":"function","function":{"name":"search_uploaded_document","description":"Pesquisa semântica no documento carregado pelo utilizador. Usar quando o utilizador perguntar sobre conteúdos específicos de um documento que fez upload e o documento é grande.","parameters":{"type":"object","properties":{"query":{"type":"string","description":"Texto a pesquisar semanticamente no documento carregado."},"conv_id":{"type":"string","description":"ID da conversa. Opcional; se vazio, tenta inferir automaticamente."}},"required":["query"]}}},
    {
        "type": "function",
        "function": {
            "name": "analyze_uploaded_table",
            "description": "Analisa ficheiro CSV/Excel carregado (ficheiro completo via RawBlobRef), com agregações determinísticas e output pronto para generate_chart.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Pedido do utilizador (ex: 'volume médio por ano')."},
                    "conv_id": {"type": "string", "description": "ID da conversa (autopreenchido pelo agente)."},
                    "filename": {"type": "string", "description": "Nome do ficheiro (opcional; por omissão usa o mais recente tabular)."},
                    "value_column": {"type": "string", "description": "Coluna numérica para agregação (opcional se inferível)."},
                    "date_column": {"type": "string", "description": "Coluna de data/hora para agrupamento (opcional se inferível)."},
                    "group_by": {"type": "string", "description": "Agrupamento: 'year','month','quarter','week','day','none'."},
                    "agg": {"type": "string", "description": "Agregação principal para retrocompatibilidade: 'mean','sum','min','max','count'."},
                    "top": {"type": "integer", "description": "Máximo de pontos para saída/chart (default 500)."},
                    "metrics": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["min", "max", "mean", "sum", "count", "std", "median", "p25", "p75"]},
                        "description": "Lista de métricas a calcular. Se ausente, usa 'agg'.",
                    },
                    "top_n": {"type": "integer", "description": "Retorna apenas os top-N grupos ordenados por valor/métrica."},
                    "compare_periods": {
                        "type": "object",
                        "properties": {"col": {"type": "string"}, "period1": {"type": "string"}, "period2": {"type": "string"}},
                        "description": "Comparar métricas entre dois períodos (ex: {'col':'Date','period1':'2020','period2':'2024'}).",
                    },
                    "full_points": {
                        "type": "boolean",
                        "description": "Se true, retorna TODOS os data points nos groups (sem downsample). chart_ready aplica downsample controlado para render. Usar para exports completos.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {"type":"function","function":{"name":"analyze_patterns","description":"Analisa padrões de escrita de work items com LLM. Templates, estilo de autor.","parameters":{"type":"object","properties":{"created_by":{"type":"string"},"topic":{"type":"string"},"work_item_type":{"type":"string","description":"Default: 'User Story'"},"area_path":{"type":"string"},"sample_size":{"type":"integer","description":"Default: 50"},"analysis_type":{"type":"string","description":"'template','author_style','general'"}}}}},
    {"type":"function","function":{"name":"generate_user_stories","description":"Gera USs NOVAS baseadas em padrões reais. USA SEMPRE quando pedirem criar/gerar USs.","parameters":{"type":"object","properties":{"topic":{"type":"string","description":"Tema das USs."},"context":{"type":"string","description":"Contexto: Miro, Figma, requisitos."},"num_stories":{"type":"integer","description":"Nº USs. Default: 3."},"reference_area":{"type":"string"},"reference_author":{"type":"string"},"reference_topic":{"type":"string"}},"required":["topic"]}}},
    {"type":"function","function":{"name":"get_writer_profile","description":"Carrega perfil de escrita de um autor para personalizar user stories. Usar quando o utilizador mencionar um autor específico.","parameters":{"type":"object","properties":{"author_name":{"type":"string","description":"Nome do autor (ex: 'Pedro Mousinho')."}},"required":["author_name"]}}},
    {"type":"function","function":{"name":"save_writer_profile","description":"Guarda perfil de escrita após analisar padrões de um autor.","parameters":{"type":"object","properties":{"author_name":{"type":"string","description":"Nome do autor."},"analysis":{"type":"string","description":"Análise do estilo de escrita."},"preferred_vocabulary":{"type":"string","description":"Vocabulário preferido do autor."},"title_pattern":{"type":"string","description":"Padrão de títulos."},"ac_structure":{"type":"string","description":"Estrutura de critérios de aceitação."}},"required":["author_name","analysis"]}}},
    {"type":"function","function":{"name":"screenshot_to_us","description":"Analisa screenshot de UI e gera User Stories estruturadas (titulo, descricao, criterios de aceitacao). Usar quando o utilizador enviar imagem/screenshot e pedir criação de user stories.","parameters":{"type":"object","properties":{"image_base64":{"type":"string","description":"Screenshot em base64."},"context":{"type":"string","description":"Contexto adicional (projeto, área funcional, requisitos)."},"author_style":{"type":"string","description":"Estilo de escrita a seguir (opcional)."}},"required":["image_base64"]}}},
    {"type":"function","function":{"name":"query_hierarchy","description":"Query hierárquica parent/child. OBRIGATÓRIO para 'Epic', 'dentro de', 'filhos de'.","parameters":{"type":"object","properties":{"parent_id":{"type":"integer","description":"ID do pai."},"parent_type":{"type":"string","description":"Default: 'Epic'."},"child_type":{"type":"string","description":"Default: 'User Story'."},"area_path":{"type":"string"},"title_contains":{"type":"string","description":"Filtro opcional por título (contains, case/accent-insensitive). Ex: 'Créditos Consultar Carteira'"},"parent_title_hint":{"type":"string","description":"(Interno) dica de título do parent para resolução quando parent_id não for fornecido."}}}}},
    {"type":"function","function":{"name":"compute_kpi","description":"Calcula KPIs (até 1000 items). OBRIGATÓRIO para rankings, distribuições, tendências.","parameters":{"type":"object","properties":{"wiql_where":{"type":"string"},"group_by":{"type":"string","description":"'state','type','assigned_to','created_by','area'"},"kpi_type":{"type":"string","description":"'count','timeline','distribution'"}},"required":["wiql_where"]}}},
    {"type":"function","function":{"name":"create_workitem","description":"Cria um Work Item no Azure DevOps. USA APENAS quando o utilizador CONFIRMAR explicitamente a criação. PERGUNTA SEMPRE antes de criar.","parameters":{"type":"object","properties":{"work_item_type":{"type":"string","description":"Tipo: 'User Story', 'Bug', 'Task', 'Feature'. Default: 'User Story'."},"title":{"type":"string","description":"Título do Work Item."},"description":{"type":"string","description":"Descrição em HTML. Usa formato MSE."},"acceptance_criteria":{"type":"string","description":"Critérios de aceitação em HTML."},"area_path":{"type":"string","description":"AreaPath. Ex: 'IT.DIT\\\\DIT\\\\ADMChannels\\\\DBKS\\\\AM24\\\\RevampFEE MVP2'"},"assigned_to":{"type":"string","description":"Nome completo da pessoa. Ex: 'Pedro Mousinho'"},"tags":{"type":"string","description":"Tags separadas por ';'. Ex: 'MVP2;FEE;Sprint23'"},"confirmed":{"type":"boolean","description":"true apenas após confirmação explícita do utilizador (ex: 'confirmo')."}},"required":["title"]}}},
    {"type":"function","function":{"name":"refine_workitem","description":"Refina uma User Story existente no DevOps a partir de uma instrução curta (sem alterar automaticamente o item). Usa quando o utilizador pedir ajustes numa US já criada, ex: 'na US 12345 adiciona validação de email'.","parameters":{"type":"object","properties":{"work_item_id":{"type":"integer","description":"ID do work item existente a refinar."},"refinement_request":{"type":"string","description":"Instrução objetiva do que mudar na US existente."}},"required":["work_item_id","refinement_request"]}}},
    {"type":"function","function":{"name":"generate_chart","description":"Gera gráfico interativo (bar, pie, line, scatter, histogram, hbar). USA SEMPRE que o utilizador pedir gráfico, chart, visualização ou distribuição visual. Extrai dados de tool_results anteriores ou de dados fornecidos.","parameters":{"type":"object","properties":{"chart_type":{"type":"string","description":"Tipo: 'bar','pie','line','scatter','histogram','hbar'. Default: 'bar'."},"title":{"type":"string","description":"Título do gráfico."},"x_values":{"type":"array","items":{"type":"string"},"description":"Valores eixo X (categorias ou datas). Ex: ['Active','Closed','New']"},"y_values":{"type":"array","items":{"type":"number"},"description":"Valores eixo Y (numéricos). Ex: [45, 30, 12]"},"labels":{"type":"array","items":{"type":"string"},"description":"Labels para pie chart. Ex: ['Bug','US','Task']"},"values":{"type":"array","items":{"type":"number"},"description":"Valores para pie chart. Ex: [20, 50, 30]"},"series":{"type":"array","items":{"type":"object"},"description":"Multi-series. Cada obj: {type,name,x,y,labels,values}"},"x_label":{"type":"string","description":"Label do eixo X"},"y_label":{"type":"string","description":"Label do eixo Y"}},"required":["title"]}}},
    {"type":"function","function":{"name":"run_code","description":"Executa código Python em sandbox seguro para cálculos, análise de dados, manipulação de CSV/Excel e geração de gráficos/ficheiros. Usa quando o pedido exigir computação programática que outras tools não cobrem.","parameters":{"type":"object","properties":{"code":{"type":"string","description":"Código Python a executar. Usa print() para output textual. Para gráficos matplotlib, usa plt.show(). Ficheiros guardados no diretório atual serão devolvidos para download."},"description":{"type":"string","description":"Descrição breve do objetivo do código (auditoria/log)."},"filename":{"type":"string","description":"Nome do ficheiro carregado a montar no sandbox (opcional; por omissão usa os mais recentes da conversa)."},"conv_id":{"type":"string","description":"ID da conversa (preenchido automaticamente pelo agente)."},"user_sub":{"type":"string","description":"Sub do utilizador para filtrar uploads da conversa (interno)."}},"required":["code"]}}},
    {"type":"function","function":{"name":"generate_file","description":"Gera ficheiro para download (CSV, XLSX, PDF, DOCX, HTML) quando o utilizador pedir explicitamente para gerar/descarregar ficheiro com dados.","parameters":{"type":"object","properties":{"format":{"type":"string","enum":["csv","xlsx","pdf","docx","html"],"description":"Formato do ficheiro a gerar."},"title":{"type":"string","description":"Título/nome base do ficheiro."},"data":{"type":"array","items":{"type":"object"},"description":"Linhas de dados (array de objetos)."},"columns":{"type":"array","items":{"type":"string"},"description":"Headers/ordem das colunas no ficheiro."}},"required":["format","title","data","columns"]}}},
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
        "search_web": lambda arguments: tool_search_web(arguments.get("query", ""), arguments.get("top", 5)),
        "search_uploaded_document": lambda arguments: tool_search_uploaded_document(
            arguments.get("query", ""),
            arguments.get("conv_id", ""),
            arguments.get("user_sub", ""),
        ),
        "analyze_uploaded_table": lambda arguments: tool_analyze_uploaded_table(
            arguments.get("query", ""),
            arguments.get("conv_id", ""),
            arguments.get("user_sub", ""),
            arguments.get("filename", ""),
            arguments.get("value_column", ""),
            arguments.get("date_column", ""),
            arguments.get("group_by", ""),
            arguments.get("agg", "mean"),
            arguments.get("top", 500),
            arguments.get("metrics"),
            arguments.get("top_n", 0),
            arguments.get("compare_periods"),
            arguments.get("full_points", False),
        ),
        "analyze_patterns": lambda arguments: tool_analyze_patterns_with_llm(arguments.get("created_by"), arguments.get("topic"), arguments.get("work_item_type","User Story"), arguments.get("area_path"), arguments.get("sample_size",50), arguments.get("analysis_type","template")),
        "generate_user_stories": lambda arguments: tool_generate_user_stories(arguments.get("topic",""), arguments.get("context",""), arguments.get("num_stories",3), arguments.get("reference_area"), arguments.get("reference_author"), arguments.get("reference_topic")),
        "get_writer_profile": lambda arguments: tool_get_writer_profile(arguments.get("author_name", "")),
        "save_writer_profile": lambda arguments: tool_save_writer_profile(
            arguments.get("author_name", ""),
            arguments.get("analysis", ""),
            arguments.get("preferred_vocabulary", ""),
            arguments.get("title_pattern", ""),
            arguments.get("ac_structure", ""),
        ),
        "screenshot_to_us": lambda arguments: tool_screenshot_to_us(
            arguments.get("image_base64", ""),
            arguments.get("context", ""),
            arguments.get("author_style", ""),
        ),
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
        "run_code": lambda arguments: tool_run_code(
            arguments.get("code", ""),
            arguments.get("description", ""),
            arguments.get("conv_id", ""),
            arguments.get("user_sub", ""),
            arguments.get("filename", ""),
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

_ANALYZE_FIGMA_FLOW_PROXY_DEFINITION = {
    "type": "function",
    "function": {
        "name": "analyze_figma_flow",
        "description": "Analisa um fluxo Figma e decompõe em steps ordenados para geração de User Stories.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_key": {"type": "string", "description": "Figma file key."},
                "node_ids": {"type": "string", "description": "IDs de frames em CSV ou lista JSON serializada."},
                "start_node_id": {"type": "string", "description": "Node inicial opcional para seguir fluxo de protótipo."},
                "include_branches": {"type": "boolean", "description": "Incluir branches de erro/fallback/cancel."},
                "max_steps": {"type": "integer", "description": "Máximo de steps a processar."},
            },
            "required": ["file_key"],
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


async def _analyze_figma_flow_proxy(arguments):
    try:
        from tools_figma import tool_analyze_figma_flow

        return await tool_analyze_figma_flow(
            file_key=(arguments or {}).get("file_key", ""),
            node_ids=(arguments or {}).get("node_ids", ""),
            start_node_id=(arguments or {}).get("start_node_id", ""),
            include_branches=(arguments or {}).get("include_branches", True),
            max_steps=(arguments or {}).get("max_steps", 15),
        )
    except Exception as e:
        logging.error("[Tools] analyze_figma_flow proxy failed: %s", e, exc_info=True)
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
    if not has_tool("analyze_figma_flow"):
        register_tool(
            "analyze_figma_flow",
            lambda args: _analyze_figma_flow_proxy(args),
            definition=_ANALYZE_FIGMA_FLOW_PROXY_DEFINITION,
        )
        logging.warning("[Tools] analyze_figma_flow registada via proxy fallback")

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
    uploaded_table_enabled = has_tool("analyze_uploaded_table")

    def _join_with_ou(parts):
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        return ", ".join(parts[:-1]) + " ou " + parts[-1]

    data_sources = ["DevOps", "AI Search", "site MSE"]
    if uploaded_doc_enabled:
        data_sources.append("documento carregado")
    if uploaded_table_enabled:
        data_sources.append("ficheiro tabular carregado (CSV/Excel)")
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
    if uploaded_table_enabled:
        gate_priority_hints.append(
            "- Se o utilizador pedir análise de CSV/Excel carregado, escolhe a tool certa: analyze_uploaded_table para agregações simples e run_code para análise completa/linha-a-linha/lista completa/lógica custom."
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
    if uploaded_table_enabled:
        exception_targets.append("ficheiro tabular carregado")
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
        "11. Para GERAR ou DESCARREGAR ficheiros (Excel/CSV/PDF/DOCX/HTML) com dados -> usa generate_file (OBRIGATORIO)\n"
        "   FORMATOS SUPORTADOS: csv, xlsx, pdf, docx, html.\n"
        "   Exemplos: \"gera um Excel com estes dados\", \"descarrega em CSV\", \"quero PDF da tabela\", \"gera em DOCX\", \"exporta HTML\"\n"
        "   REGRA: So usar quando o utilizador pedir EXPLICITAMENTE geracao/download de ficheiro.",
        "12. Para resultados extensos (muitas linhas) -> mostra PREVIEW no chat e indica que o ficheiro completo está disponível para download.\n"
        "   REGRA: Evita listar dezenas de linhas completas na resposta textual.",
        "13. Para CÁLCULOS AVANÇADOS, SCRIPT PYTHON, transformação customizada de dados, ou geração programática de ficheiros/gráficos -> usa run_code.\n"
        "   Exemplos: \"calcula correlação de colunas\", \"gera ficheiro Excel com duas folhas\", \"faz análise estatística custom\".\n"
        "   REGRA: Se o pedido exigir análise EXAUSTIVA (ficheiro todo, sem amostra, lista completa, todos os valores, top N por linha, correlação/scatter, validação exata), usa run_code.\n"
        "   REGRA: Não pedir confirmação extra para pedidos read-only de análise; executa diretamente.\n"
        "   REGRA: Prefere analyze_uploaded_table apenas para agregações simples quando cobre totalmente o pedido.",
    ]
    next_rule = 14
    if uploaded_doc_enabled:
        routing_rules.append(
            f"{next_rule}. Para PERGUNTAS SOBRE DOCUMENTO CARREGADO (sobretudo PDF grande) -> usa search_uploaded_document (OBRIGATORIO)\n"
            "   Exemplos: \"o que diz o capitulo 3?\", \"resume a secção de requisitos\", \"onde fala de autenticação?\"\n"
            "   REGRA: Usa pesquisa semântica nos chunks do documento, em vez de depender só do texto truncado."
        )
        next_rule += 1
    if uploaded_table_enabled:
        routing_rules.append(
            f"{next_rule}. Para ANALISE DE CSV/EXCEL CARREGADO -> decide entre analyze_uploaded_table e run_code\n"
            "   Exemplos simples: \"volume medio por ano\", \"min/max do Close\", \"agrega por mês\" -> analyze_uploaded_table\n"
            "   Exemplos exaustivos/custom: \"analisa tudo\", \"lista completa\", \"correlação\", \"scatter\", \"top 10 por amplitude\" -> run_code\n"
            "   REGRA: NUNCA usar query_workitems para dados de ficheiro carregado.\n"
            "   REGRA: Em pedidos read-only (analisar, resumir, listar, validar), executa diretamente sem pedir confirmação adicional.\n"
            "   REGRA: Assume análise completa por defeito; só usa amostragem quando o utilizador pedir explicitamente.\n"
            "   REGRA: Se começares com analyze_uploaded_table e faltar detalhe para cumprir o pedido, chama run_code na mesma resposta (sem perguntar ao utilizador).\n"
            "   REGRA: Se analyze_uploaded_table devolver chart_ready, chama generate_chart com os campos de chart_ready."
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
        "- \"Gera um Excel/CSV/PDF/DOCX/HTML com esta tabela\" -> generate_file",
        "- \"Calcula correlação entre colunas do CSV\" -> run_code",
        "- \"Transforma estes dados e gera XLSX com múltiplas folhas\" -> run_code",
    ]
    if uploaded_doc_enabled:
        usage_examples.extend(
            [
                "- \"O que diz o capítulo 3 do PDF?\" -> search_uploaded_document",
                "- \"Procura no documento onde fala de validação\" -> search_uploaded_document",
            ]
        )
    if uploaded_table_enabled:
        usage_examples.extend(
            [
                "- \"Faz bar chart com volume médio por ano do CSV\" -> analyze_uploaded_table DEPOIS generate_chart",
                "- \"Qual o min/max do Close no ficheiro?\" -> analyze_uploaded_table",
                "- \"Analisa o ficheiro todo sem amostra\" -> run_code",
                "- \"Lista completa de valores distintos da coluna X\" -> run_code",
                "- \"Mostra top 10 candles com maior amplitude\" -> run_code",
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
    figma_flow_instruction = ""
    if has_tool("analyze_figma_flow"):
        figma_flow_instruction = (
            "- Se o utilizador fornecer um fluxo Figma com múltiplos ecrãs/frames, usa analyze_figma_flow "
            "para decompor em steps antes de gerar US.\n"
        )
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
{figma_flow_instruction}

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
Título: MSE | [Domínio] | [Jornada/Subárea] | [Fluxo/Step] | [Detalhe da Alteração]
- 4 a 6 segmentos separados por " | "
- Se o domínio não for inferível, usar "Transversal"
Descrição: <div>Eu como <b>[Persona]</b>, quero <b>[ação]</b>, para <b>[benefício de negócio/utilizador]</b>.</div>
AC (ordem obrigatória):
- <b>Proveniência</b> + <ul><li>...</li></ul>
- <b>Condições</b> + <ul><li>...</li></ul>
- <b>Composição</b> + <ul><li>...</li></ul>
- <b>Comportamento</b> + <ul><li>...</li></ul>
- <b>Mockup</b> + <ul><li>Mockup a confirmar com UX.</li></ul>

QUALIDADE:
- HTML limpo apenas (<b>, <ul>, <li>, <br>, <div>), sem HTML sujo nem HTML escapado.
- PT-PT, auto-contida, testável, granular, sem contradições.
- Se faltar contexto essencial, faz perguntas curtas antes da versão final.
- Não usar Given/When/Then (não é padrão MSE).
- Não inventar endpoints, APIs, serviços de backoffice ou arquitetura técnica sem evidência explícita no pedido.
- Quando faltar contexto de negócio, acrescentar secção <b>Assunções</b> no fim dos AC.
- Prioridade template > WriterProfile: usar perfil histórico apenas para vocabulário/nível de detalhe, nunca para estrutura de secções.
- Política de detalhe: por defeito seguir template canónico; se o utilizador pedir formato explícito, seguir o formato pedido.

VOCABULÁRIO PREFERENCIAL:
{", ".join(US_PREFERRED_VOCAB)}

ÁREAS:
RevampFEE MVP2, MDSE, ACEDigital, MSE"""
