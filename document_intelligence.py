"""Azure Document Intelligence helpers for OCR/layout extraction."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List

import httpx

from config import DOC_INTEL_ENDPOINT, DOC_INTEL_KEY, DOC_INTEL_ENABLED

logger = logging.getLogger(__name__)

DOC_INTEL_API_VERSION = "2024-11-30"

SUPPORTED_FORMATS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif",
    ".docx", ".xlsx", ".pptx", ".html",
}


def _content_type_for_extension(ext: str) -> str:
    content_types = {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".html": "text/html",
    }
    return content_types.get(ext.lower(), "application/octet-stream")


async def analyze_document(
    file_bytes: bytes,
    filename: str,
    model_id: str = "prebuilt-layout",
) -> Dict[str, Any]:
    """Analyze a document with Azure Document Intelligence."""
    if not DOC_INTEL_ENABLED or not DOC_INTEL_ENDPOINT or not DOC_INTEL_KEY:
        return {"error": "Document Intelligence nao configurado.", "text": ""}

    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in SUPPORTED_FORMATS:
        return {"error": f"Formato {ext or 'desconhecido'} nao suportado.", "text": ""}

    analyze_url = (
        f"{DOC_INTEL_ENDPOINT.rstrip('/')}/documentintelligence/documentModels/{model_id}:analyze"
        f"?api-version={DOC_INTEL_API_VERSION}"
    )
    content_type = _content_type_for_extension(ext)

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                analyze_url,
                content=file_bytes,
                headers={
                    "Ocp-Apim-Subscription-Key": DOC_INTEL_KEY,
                    "Content-Type": content_type,
                },
            )
            resp.raise_for_status()

            operation_url = resp.headers.get("Operation-Location", "").strip()
            if not operation_url:
                return {"error": "Sem Operation-Location no header.", "text": ""}

            for _ in range(30):
                await asyncio.sleep(2)
                poll_resp = await client.get(
                    operation_url,
                    headers={"Ocp-Apim-Subscription-Key": DOC_INTEL_KEY},
                )
                poll_resp.raise_for_status()
                poll_data = poll_resp.json()
                status = (poll_data.get("status") or "").lower()

                if status == "succeeded":
                    return _parse_result(poll_data.get("analyzeResult") or {})
                if status == "failed":
                    err = (poll_data.get("error") or {}).get("message", "unknown")
                    return {"error": f"Analise falhou: {err}", "text": ""}

        return {"error": "Timeout: analise do documento demorou mais de 60 segundos.", "text": ""}
    except Exception as e:
        logger.warning("Document Intelligence falhou: %s", e)
        return {"error": f"Erro: {str(e)}", "text": ""}


def _parse_result(result: Dict[str, Any]) -> Dict[str, Any]:
    full_text = result.get("content", "") or ""

    tables: List[Dict[str, Any]] = []
    for table in result.get("tables") or []:
        parsed_table = {
            "row_count": int(table.get("rowCount") or 0),
            "column_count": int(table.get("columnCount") or 0),
            "cells": [],
        }
        for cell in table.get("cells") or []:
            parsed_table["cells"].append(
                {
                    "row": int(cell.get("rowIndex") or 0),
                    "col": int(cell.get("columnIndex") or 0),
                    "text": cell.get("content", "") or "",
                    "is_header": (cell.get("kind", "") or "") == "columnHeader",
                }
            )
        tables.append(parsed_table)

    paragraphs: List[Dict[str, str]] = []
    for para in result.get("paragraphs") or []:
        paragraphs.append(
            {
                "role": para.get("role", "") or "",
                "content": para.get("content", "") or "",
            }
        )

    key_values: List[Dict[str, str]] = []
    for kv in result.get("keyValuePairs") or []:
        key = ((kv.get("key") or {}).get("content") or "").strip()
        value = (((kv.get("value") or {}).get("content") or "") if kv.get("value") else "").strip()
        if key:
            key_values.append({"key": key, "value": value})

    pages: List[Dict[str, Any]] = []
    for page in result.get("pages") or []:
        pages.append(
            {
                "page_number": int(page.get("pageNumber") or 0),
                "width": page.get("width", 0),
                "height": page.get("height", 0),
                "unit": page.get("unit", "") or "",
                "words_count": len(page.get("words") or []),
            }
        )

    logger.info(
        "Document Intelligence: %s paginas, %s tabelas, %s key-values, %s chars",
        len(pages),
        len(tables),
        len(key_values),
        len(full_text),
    )

    return {
        "text": full_text,
        "tables": tables,
        "paragraphs": paragraphs,
        "key_values": key_values,
        "pages": pages,
        "table_count": len(tables),
        "page_count": len(pages),
    }


def tables_to_markdown(tables: List[Dict[str, Any]]) -> str:
    """Convert parsed tables to markdown to enrich the extracted text context."""
    md_parts: List[str] = []
    for idx, table in enumerate(tables or []):
        rows = int(table.get("row_count") or 0)
        cols = int(table.get("column_count") or 0)
        if rows <= 0 or cols <= 0:
            continue

        grid = [["" for _ in range(cols)] for _ in range(rows)]
        for cell in table.get("cells") or []:
            r = int(cell.get("row") or 0)
            c = int(cell.get("col") or 0)
            if 0 <= r < rows and 0 <= c < cols:
                grid[r][c] = str(cell.get("text") or "").strip()

        header = grid[0] if grid else []
        if not header:
            continue

        lines = [f"**Tabela {idx + 1}:**", "| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * cols) + " |"]
        for row in grid[1:]:
            lines.append("| " + " | ".join(row) + " |")
        md_parts.append("\n".join(lines))

    return "\n\n".join(md_parts)

