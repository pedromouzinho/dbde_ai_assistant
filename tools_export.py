# =============================================================================
# tools_export.py — File/chart generation and temporary file store
# =============================================================================

import asyncio
import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from config import (
    AGENT_TOOL_RESULT_MAX_SIZE,
    AGENT_TOOL_RESULT_KEEP_ITEMS,
    EXPORT_ASYNC_THRESHOLD_ROWS,
    GENERATED_FILES_BLOB_CONTAINER,
)
from export_engine import to_csv, to_xlsx, to_pdf
from storage import (
    blob_upload_bytes,
    blob_upload_json,
    blob_download_bytes,
    blob_download_json,
    parse_blob_ref,
)

_generated_files_store = {}
_generated_files_lock = asyncio.Lock()
_GENERATED_FILE_TTL_SECONDS = 30 * 60
_GENERATED_FILE_MAX = 100
_GENERATED_FILE_MAX_TOTAL_BYTES = 500 * 1024 * 1024  # 500 MB
_AUTO_EXPORT_MIN_ROWS = 25

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

