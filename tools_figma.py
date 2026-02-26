# =============================================================================
# tools_figma.py - Figma read-only tool (optional)
# =============================================================================

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

from config import FIGMA_ACCESS_TOKEN
from tool_registry import register_tool

_FIGMA_API_BASE = "https://api.figma.com/v1"
_FIGMA_CACHE_TTL_SECONDS = 300
_MAX_CACHE_ENTRIES = 200
_figma_cache = {}
_http_client: httpx.AsyncClient | None = None


def _get_figma_token() -> str:
    return (
        (FIGMA_ACCESS_TOKEN or "").strip()
        or (os.getenv("FIGMA_ACCESS_TOKEN", "") or "").strip()
        or (os.getenv("APPSETTING_FIGMA_ACCESS_TOKEN", "") or "").strip()
    )


def _cache_key(query: str, file_key: str, node_id: str) -> str:
    return f"{(query or '').strip().lower()}|{(file_key or '').strip()}|{(node_id or '').strip()}"


def _cache_get(key: str):
    hit = _figma_cache.get(key)
    if not hit:
        return None
    if datetime.now(timezone.utc) - hit["ts"] > timedelta(seconds=_FIGMA_CACHE_TTL_SECONDS):
        _figma_cache.pop(key, None)
        return None
    return hit["data"]


def _cache_set(key: str, data):
    if key in _figma_cache:
        _figma_cache.pop(key, None)
    if len(_figma_cache) >= _MAX_CACHE_ENTRIES:
        oldest_key = next(iter(_figma_cache))
        _figma_cache.pop(oldest_key, None)
    _figma_cache[key] = {"ts": datetime.now(timezone.utc), "data": data}


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=25)
    return _http_client


async def _close_http_client() -> None:
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None


async def _figma_get(path: str, params=None):
    token = _get_figma_token()
    if not token:
        return {"error": "Integração Figma não configurada (token em falta)"}
    headers = {"X-Figma-Token": token}
    url = f"{_FIGMA_API_BASE}{path}"
    client = _get_http_client()
    for attempt in range(1, 4):
        try:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 429:
                wait = min(int(resp.headers.get("Retry-After", "2")), 20)
                if attempt == 3:
                    return {"error": "Figma 429: limite de requests"}
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 500:
                if attempt == 3:
                    return {"error": f"Figma {resp.status_code}: erro servidor"}
                await asyncio.sleep(attempt)
                continue
            if resp.status_code >= 400:
                return {"error": f"Figma {resp.status_code}: {resp.text[:200]}"}
            return resp.json()
        except httpx.TimeoutException:
            if attempt == 3:
                return {"error": "Figma timeout"}
            await asyncio.sleep(attempt)
        except Exception as e:
            if attempt == 3:
                return {"error": f"Figma erro: {str(e)}"}
            await asyncio.sleep(attempt)
    return {"error": "Figma erro desconhecido"}


def _match_query(text: str, query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    return q in (text or "").strip().lower()


def _figma_file_url(file_key: str, node_id: str = "") -> str:
    safe_key = quote(str(file_key or "").strip(), safe="")
    if not node_id:
        return f"https://www.figma.com/file/{safe_key}"
    return f"https://www.figma.com/file/{safe_key}?node-id={quote(str(node_id).strip(), safe='')}"


async def tool_search_figma(query: str = "", file_key: str = "", node_id: str = ""):
    if not _get_figma_token():
        return {"error": "Integração Figma não configurada (token em falta)"}

    q = (query or "").strip()
    fk = (file_key or "").strip()
    nid = (node_id or "").strip()

    cache_key = _cache_key(q, fk, nid)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    if fk:
        file_name = ""
        thumbnail_url = ""
        last_modified = ""
        items = []

        if nid:
            # Fast path: fetch only the requested node and avoid loading full file payload.
            nodes = await _figma_get(
                f"/files/{quote(fk, safe='')}/nodes",
                params={"ids": nid},
            )
            if "error" in nodes:
                return nodes
            file_name = nodes.get("name", "")
            thumbnail_url = nodes.get("thumbnailUrl", "")
            last_modified = nodes.get("lastModified", "")
            raw_nodes = nodes.get("nodes", {})
            for node_key, node_val in raw_nodes.items():
                document = (node_val or {}).get("document", {})
                name = document.get("name", "")
                if _match_query(name, q):
                    items.append(
                        {
                            "id": node_key,
                            "name": name,
                            "type": document.get("type", "NODE"),
                            "file_key": fk,
                            "file_name": file_name,
                            "thumbnail_url": thumbnail_url,
                            "last_modified": last_modified,
                            "url": _figma_file_url(fk, node_key),
                        }
                    )
        else:
            # Use bounded depth to prevent very large responses on large design files.
            file_meta = await _figma_get(
                f"/files/{quote(fk, safe='')}",
                params={"depth": 2},
            )
            if "error" in file_meta:
                return file_meta

            file_name = file_meta.get("name", "")
            thumbnail_url = file_meta.get("thumbnailUrl", "")
            last_modified = file_meta.get("lastModified", "")
            doc = file_meta.get("document", {})
            for page in doc.get("children", [])[:50]:
                page_name = page.get("name", "")
                page_id = page.get("id", "")
                if _match_query(page_name, q):
                    items.append(
                        {
                            "id": page_id,
                            "name": page_name,
                            "type": page.get("type", "PAGE"),
                            "file_key": fk,
                            "file_name": file_name,
                            "thumbnail_url": thumbnail_url,
                            "last_modified": last_modified,
                            "url": _figma_file_url(fk, page_id),
                        }
                    )
                for frame in (page.get("children") or [])[:50]:
                    frame_name = frame.get("name", "")
                    frame_id = frame.get("id", "")
                    if _match_query(frame_name, q):
                        items.append(
                            {
                                "id": frame_id,
                                "name": frame_name,
                                "type": frame.get("type", "FRAME"),
                                "file_key": fk,
                                "file_name": file_name,
                                "page_name": page_name,
                                "thumbnail_url": thumbnail_url,
                                "last_modified": last_modified,
                                "url": _figma_file_url(fk, frame_id),
                            }
                        )
            items = items[:100]

        result = {
            "source": "figma",
            "query": q,
            "file_key": fk,
            "total_results": len(items),
            "items": items,
        }
        _cache_set(cache_key, result)
        return result

    # Nota: a API pública do Figma não expõe endpoint para "recent files".
    # Validamos o token com /me e devolvemos instrução clara para usar file_key.
    me = await _figma_get("/me")
    if "error" in me:
        return me

    result = {
        "source": "figma",
        "query": q,
        "total_results": 0,
        "items": [],
        "notice": (
            "A API pública do Figma não disponibiliza listagem de ficheiros recentes por token. "
            "Fornece o file_key para obter detalhes de um ficheiro/frames."
        ),
        "user": {
            "id": me.get("id", ""),
            "email": me.get("email", ""),
            "handle": me.get("handle", ""),
        },
    }
    _cache_set(cache_key, result)
    return result


_SEARCH_FIGMA_DEFINITION = {
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


def _register_figma_tool() -> None:
    register_tool(
        "search_figma",
        lambda args: tool_search_figma(
            query=args.get("query", ""),
            file_key=args.get("file_key", ""),
            node_id=args.get("node_id", ""),
        ),
        definition=_SEARCH_FIGMA_DEFINITION,
    )
    if _get_figma_token():
        logging.info("[Figma] search_figma registada")
    else:
        logging.warning("[Figma] search_figma registada sem token (vai devolver erro controlado)")
