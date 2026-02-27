# =============================================================================
# tools_upload.py — Uploaded document semantic search
# =============================================================================

import asyncio
import math
import json
import logging
import csv
import io
import unicodedata
from datetime import datetime

from config import UPLOAD_INDEX_TOP
from storage import table_query, blob_download_json, blob_download_bytes, parse_blob_ref
from tools_knowledge import get_embedding, _cosine_similarity

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


def _odata_escape(value: str) -> str:
    return (value or "").replace("'", "''")


def _normalize_key(value: str) -> str:
    txt = unicodedata.normalize("NFKD", str(value or ""))
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    txt = txt.lower()
    return "".join(ch if ch.isalnum() else " " for ch in txt).strip()


def _parse_number(raw_val) -> float | None:
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


def _parse_datetime(raw_val) -> datetime | None:
    txt = str(raw_val or "").strip()
    if not txt:
        return None
    candidates = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    )
    for fmt in candidates:
        try:
            return datetime.strptime(txt, fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(txt.replace("Z", "+00:00"))
    except Exception:
        return None


def _pick_column(explicit_name: str, columns: list[str]) -> str:
    if not columns:
        return ""
    requested = str(explicit_name or "").strip()
    if not requested:
        return ""
    norm_req = _normalize_key(requested)
    by_norm = {_normalize_key(col): col for col in columns}
    if norm_req in by_norm:
        return by_norm[norm_req]
    for key, col in by_norm.items():
        if norm_req and (norm_req in key or key in norm_req):
            return col
    return ""


def _infer_group_by(query: str, explicit_group: str) -> str:
    group = str(explicit_group or "").strip().lower()
    if group in ("year", "month"):
        return group
    if group == "none":
        group = ""
    qn = _normalize_key(query)
    if any(token in qn for token in ("por ano", "anual", "year", "yearly", "ano")):
        return "year"
    if any(token in qn for token in ("por mes", "mensal", "month", "monthly", "mes")):
        return "month"
    return "none"


def _infer_agg(query: str, explicit_agg: str) -> str:
    agg = str(explicit_agg or "").strip().lower()
    if agg in ("mean", "sum", "min", "max", "count"):
        return agg
    qn = _normalize_key(query)
    if any(token in qn for token in ("media", "médio", "medio", "average", "mean")):
        return "mean"
    if any(token in qn for token in ("total", "soma", "sum")):
        return "sum"
    if any(token in qn for token in ("minimo", "mínimo", "min")):
        return "min"
    if any(token in qn for token in ("maximo", "máximo", "max")):
        return "max"
    if any(token in qn for token in ("quantos", "contagem", "count")):
        return "count"
    return "mean"


def _infer_date_column(query: str, explicit_date_column: str, columns: list[str]) -> str:
    explicit = _pick_column(explicit_date_column, columns)
    if explicit:
        return explicit
    if not columns:
        return ""
    qn = _normalize_key(query)
    preferred = []
    for col in columns:
        cn = _normalize_key(col)
        if any(token in cn for token in ("time", "date", "data", "timestamp", "datetime")):
            preferred.append(col)
    if preferred:
        if "hora" in qn or "time" in qn:
            for col in preferred:
                if "time" in _normalize_key(col):
                    return col
        return preferred[0]
    return ""


def _infer_value_column(
    query: str,
    explicit_value_column: str,
    columns: list[str],
    numeric_columns: list[str],
) -> str:
    explicit = _pick_column(explicit_value_column, columns)
    if explicit:
        return explicit
    if not columns:
        return ""
    qn = _normalize_key(query)
    best = ""
    best_len = -1
    for col in columns:
        cn = _normalize_key(col)
        if cn and cn in qn and len(cn) > best_len:
            best = col
            best_len = len(cn)
    if best:
        return best
    q_tokens = {tok for tok in qn.split() if len(tok) >= 3}
    overlap_best = ("", 0)
    for col in columns:
        c_tokens = {tok for tok in _normalize_key(col).split() if len(tok) >= 3}
        overlap = len(q_tokens.intersection(c_tokens))
        if overlap > overlap_best[1]:
            overlap_best = (col, overlap)
    if overlap_best[1] > 0:
        return overlap_best[0]
    if numeric_columns:
        if "volume" in qn:
            for col in numeric_columns:
                if "volume" in _normalize_key(col):
                    return col
        if "close" in qn:
            for col in numeric_columns:
                if "close" in _normalize_key(col):
                    return col
        return numeric_columns[0]
    return ""


async def _load_upload_index_rows(conv_id: str, user_sub: str) -> list[dict]:
    safe_conv = str(conv_id or "").strip()
    if not safe_conv:
        return []
    try:
        rows = await table_query(
            "UploadIndex",
            f"PartitionKey eq '{_odata_escape(safe_conv)}'",
            top=max(1, min(UPLOAD_INDEX_TOP, 500)),
        )
    except Exception as e:
        logging.error("[Tools] _load_upload_index_rows failed: %s", e)
        return []
    if not isinstance(rows, list):
        return []
    safe_user = str(user_sub or "").strip()
    filtered = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        owner = str(row.get("UserSub", "") or "")
        if safe_user and owner and owner != safe_user:
            continue
        filtered.append(row)
    filtered.sort(key=lambda r: str(r.get("UploadedAt", "")))
    return filtered


def _select_tabular_row(rows: list[dict], filename: str = "") -> dict | None:
    candidates = []
    wanted = str(filename or "").strip().lower()
    for row in rows:
        fname = str(row.get("Filename", "") or "")
        lower = fname.lower()
        if not lower.endswith((".csv", ".xlsx", ".xls")):
            continue
        if wanted and wanted not in lower:
            continue
        candidates.append(row)
    if candidates:
        return candidates[-1]
    if wanted:
        return None
    fallback = [r for r in rows if str(r.get("Filename", "") or "").lower().endswith((".csv", ".xlsx", ".xls"))]
    return fallback[-1] if fallback else None


def _detect_csv_delimiter(sample_text: str) -> str:
    first_line = ""
    for line in str(sample_text or "").splitlines():
        if line.strip():
            first_line = line
            break
    if not first_line:
        return ","
    candidates = [",", ";", "\t", "|"]
    return max(candidates, key=lambda sep: first_line.count(sep))


def _clean_header(raw_header: list) -> list[str]:
    cols = []
    for idx, col in enumerate(raw_header or []):
        txt = str(col or "").strip().strip('"')
        cols.append(txt or f"col_{idx + 1}")
    return cols


def _load_csv_table(raw_bytes: bytes, max_rows: int = 500000) -> tuple[list[str], list[list[str]]]:
    text = raw_bytes.decode("utf-8", errors="replace")
    delimiter = _detect_csv_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = next(reader, None)
    if not header:
        return [], []
    columns = _clean_header(header)
    rows = []
    for idx, row in enumerate(reader):
        if idx >= max_rows:
            break
        rows.append([str(v or "").strip().strip('"') for v in row])
    return columns, rows


def _load_xlsx_table(raw_bytes: bytes, max_rows: int = 500000) -> tuple[list[str], list[list[str]], str]:
    try:
        import openpyxl
    except Exception:
        return [], [], "openpyxl indisponível para analisar ficheiros Excel neste ambiente"
    wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
    try:
        ws = wb.active
        iterator = ws.iter_rows(values_only=True)
        header = next(iterator, None)
        if not header:
            return [], [], "Excel vazio"
        columns = _clean_header(list(header))
        rows = []
        for idx, row in enumerate(iterator):
            if idx >= max_rows:
                break
            rows.append(["" if cell is None else str(cell) for cell in row])
        return columns, rows, ""
    finally:
        wb.close()

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


async def tool_analyze_uploaded_table(
    query: str = "",
    conv_id: str = "",
    user_sub: str = "",
    filename: str = "",
    value_column: str = "",
    date_column: str = "",
    group_by: str = "",
    agg: str = "",
    top: int = 200,
):
    """Analisa CSV/Excel carregado (ficheiro completo via RawBlobRef) com agregações determinísticas."""
    resolved_conv_id = str(conv_id or "").strip()
    if not resolved_conv_id:
        return {"error": "conv_id é obrigatório para analisar ficheiro tabular carregado"}

    rows = await _load_upload_index_rows(resolved_conv_id, user_sub=str(user_sub or "").strip())
    selected = _select_tabular_row(rows, filename=filename)
    if not selected:
        return {
            "error": "Nenhum CSV/Excel encontrado nesta conversa.",
            "conversation_id": resolved_conv_id,
        }

    raw_blob_ref = str(selected.get("RawBlobRef", "") or "")
    container, blob_name = parse_blob_ref(raw_blob_ref)
    if not container or not blob_name:
        return {
            "error": "RawBlobRef inválido para o ficheiro selecionado.",
            "conversation_id": resolved_conv_id,
            "filename": selected.get("Filename", ""),
        }
    raw_bytes = await blob_download_bytes(container, blob_name)
    if not raw_bytes:
        return {
            "error": "Não foi possível ler o ficheiro completo no Blob Storage.",
            "conversation_id": resolved_conv_id,
            "filename": selected.get("Filename", ""),
        }

    fname = str(selected.get("Filename", "") or "")
    lower = fname.lower()
    load_warning = ""
    if lower.endswith(".csv"):
        columns, table_rows = _load_csv_table(raw_bytes)
    elif lower.endswith((".xlsx", ".xls")):
        columns, table_rows, load_warning = _load_xlsx_table(raw_bytes)
    else:
        return {"error": "Formato não suportado para análise tabular", "filename": fname}

    if load_warning:
        return {"error": load_warning, "filename": fname, "conversation_id": resolved_conv_id}
    if not columns:
        return {"error": "Ficheiro tabular sem cabeçalho ou vazio.", "filename": fname}

    full_stats = []
    try:
        parsed_stats = json.loads(selected.get("FullColStatsJson", "[]") or "[]")
        if isinstance(parsed_stats, list):
            full_stats = parsed_stats
    except Exception:
        full_stats = []
    numeric_columns = [str(s.get("name", "")) for s in full_stats if str(s.get("type", "")) == "numeric"]

    effective_group = _infer_group_by(query, group_by)
    effective_agg = _infer_agg(query, agg)
    effective_value_col = _infer_value_column(query, value_column, columns, numeric_columns)
    effective_date_col = _infer_date_column(query, date_column, columns)

    if not effective_value_col and effective_group != "none":
        return {
            "error": "Não consegui inferir a coluna numérica para agregação. Indica value_column explicitamente.",
            "filename": fname,
            "available_columns": columns,
        }
    if effective_group in ("year", "month") and not effective_date_col:
        return {
            "error": "Não consegui inferir a coluna temporal para group_by. Indica date_column explicitamente.",
            "filename": fname,
            "available_columns": columns,
        }

    col_to_idx = {col: idx for idx, col in enumerate(columns)}
    value_idx = col_to_idx.get(effective_value_col, -1) if effective_value_col else -1
    date_idx = col_to_idx.get(effective_date_col, -1) if effective_date_col else -1

    analyzed_rows = 0
    value_count = 0
    value_sum = 0.0
    value_min = None
    value_max = None
    running_mean = 0.0
    value_m2 = 0.0
    value_zeros = 0
    grouped = {}

    for row in table_rows:
        analyzed_rows += 1
        if value_idx < 0 or value_idx >= len(row):
            continue
        value_num = _parse_number(row[value_idx])
        if value_num is None:
            continue

        value_count += 1
        value_sum += value_num
        if value_min is None or value_num < value_min:
            value_min = value_num
        if value_max is None or value_num > value_max:
            value_max = value_num
        if value_num == 0:
            value_zeros += 1
        if value_count == 1:
            value_m2 = 0.0
            running_mean = value_num
        else:
            delta = value_num - running_mean
            running_mean += delta / value_count
            value_m2 += delta * (value_num - running_mean)

        if effective_group in ("year", "month"):
            if date_idx < 0 or date_idx >= len(row):
                continue
            dt = _parse_datetime(row[date_idx])
            if dt is None:
                continue
            group_key = str(dt.year) if effective_group == "year" else f"{dt.year:04d}-{dt.month:02d}"
            bucket = grouped.setdefault(group_key, {"sum": 0.0, "count": 0, "min": None, "max": None})
            bucket["sum"] += value_num
            bucket["count"] += 1
            bucket["min"] = value_num if bucket["min"] is None else min(bucket["min"], value_num)
            bucket["max"] = value_num if bucket["max"] is None else max(bucket["max"], value_num)

    if value_count == 0 and effective_value_col:
        return {
            "error": f"A coluna '{effective_value_col}' não tem valores numéricos válidos.",
            "filename": fname,
            "rows_analyzed": analyzed_rows,
            "available_columns": columns,
        }

    summary = {}
    if value_count > 0:
        variance = (value_m2 / value_count) if value_count > 1 else 0.0
        summary = {
            "non_null": value_count,
            "min": round(value_min, 6) if value_min is not None else None,
            "max": round(value_max, 6) if value_max is not None else None,
            "mean": round(value_sum / value_count, 6),
            "std": round(math.sqrt(max(0.0, variance)), 6),
            "zeros": value_zeros,
        }

    result_groups = []
    x_values = []
    y_values = []
    if effective_group in ("year", "month"):
        ordered_keys = sorted(grouped.keys())
        top_n = max(1, min(int(top or 200), 2000))
        for key in ordered_keys[:top_n]:
            bucket = grouped[key]
            count = int(bucket.get("count", 0) or 0)
            if count <= 0:
                continue
            if effective_agg == "sum":
                value = bucket["sum"]
            elif effective_agg == "min":
                value = bucket["min"]
            elif effective_agg == "max":
                value = bucket["max"]
            elif effective_agg == "count":
                value = count
            else:
                value = bucket["sum"] / count
            value_round = round(float(value), 6)
            result_groups.append({"group": key, "value": value_round, "count": count})
            x_values.append(key)
            y_values.append(value_round)

    chart_ready = None
    if x_values and y_values:
        chart_ready = {
            "chart_type": "bar" if effective_group in ("year", "month") else "line",
            "title": f"{effective_agg} de {effective_value_col}" + (f" por {effective_group}" if effective_group != "none" else ""),
            "x_values": x_values,
            "y_values": y_values,
            "x_label": "Ano" if effective_group == "year" else ("Ano-Mês" if effective_group == "month" else ""),
            "y_label": effective_value_col or "Valor",
        }

    return {
        "source": "upload_table",
        "conversation_id": resolved_conv_id,
        "filename": fname,
        "query": str(query or ""),
        "rows_analyzed": analyzed_rows,
        "columns": columns,
        "value_column": effective_value_col,
        "date_column": effective_date_col,
        "group_by": effective_group,
        "agg": effective_agg,
        "summary": summary,
        "groups": result_groups,
        "chart_ready": chart_ready,
        "notes": [
            "Dados analisados sobre o ficheiro completo via RawBlobRef.",
            "Para gráfico, reutilizar chart_ready diretamente em generate_chart.",
        ],
    }
