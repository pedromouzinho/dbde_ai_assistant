# =============================================================================
# tools_knowledge.py — Search, embeddings and rerank utilities
# =============================================================================

import math
import logging
from typing import Optional

import httpx

from config import (
    SEARCH_SERVICE,
    SEARCH_KEY,
    API_VERSION_SEARCH,
    DEVOPS_INDEX,
    OMNI_INDEX,
    RERANK_ENABLED,
    RERANK_ENDPOINT,
    RERANK_API_KEY,
    RERANK_MODEL,
    RERANK_TOP_N,
    RERANK_TIMEOUT_SECONDS,
    RERANK_AUTH_MODE,
    WEB_SEARCH_ENABLED,
    WEB_SEARCH_API_KEY,
    WEB_SEARCH_ENDPOINT,
    WEB_SEARCH_MAX_RESULTS,
    WEB_SEARCH_MARKET,
    WEB_ANSWERS_ENABLED,
    WEB_ANSWERS_API_KEY,
    WEB_ANSWERS_ENDPOINT,
    WEB_ANSWERS_MODEL,
    WEB_ANSWERS_TIMEOUT_SECONDS,
)
from llm_provider import get_embedding_provider
from http_helpers import search_request_with_retry

_http_client: Optional[httpx.AsyncClient] = None

if RERANK_ENABLED:
    logging.info("[RAG] Reranking enabled: model=%s, top_n=%s", RERANK_MODEL, RERANK_TOP_N)


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=max(25, int(RERANK_TIMEOUT_SECONDS or 25)))
    return _http_client


async def _close_http_client() -> None:
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None


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
    payload = {
        "model": RERANK_MODEL,
        "query": str(query or "")[:2000],
        "documents": documents,
        "top_n": top_n,
    }
    headers = _build_rerank_headers()

    try:
        client = _get_http_client()
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

async def get_embedding(text):
    try:
        return await get_embedding_provider().embed(text[:8000].strip() or " ")
    except Exception as e:
        logging.error("[Tools] get_embedding failed: %s", e)
        return None

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

async def tool_search_workitems(query, top=30, filter_expr=None):
    emb = await get_embedding(query)
    if not emb: return {"error": "Falha embedding"}
    body = {"vectorQueries":[{"kind":"vector","vector":emb,"fields":"content_vector","k":top}],"select":"id,content,url,tag,status","top":top}
    if filter_expr: body["filter"] = filter_expr
    url = f"https://{SEARCH_SERVICE}.search.windows.net/indexes/{DEVOPS_INDEX}/docs/search?api-version={API_VERSION_SEARCH}"
    data = await search_request_with_retry(
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

async def tool_search_website(query, top=10):
    emb = await get_embedding(query)
    if not emb: return {"error": "Falha embedding"}
    body = {"vectorQueries":[{"kind":"vector","vector":emb,"fields":"content_vector","k":top}],"select":"id,content,url,tag","top":top}
    url = f"https://{SEARCH_SERVICE}.search.windows.net/indexes/{OMNI_INDEX}/docs/search?api-version={API_VERSION_SEARCH}"
    data = await search_request_with_retry(
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


async def tool_search_web(query: str, top: int = 5) -> dict:
    """Pesquisa web via Brave Search API. Retorna snippets relevantes."""
    if not WEB_SEARCH_ENABLED or not WEB_SEARCH_API_KEY:
        return {"error": "Pesquisa web não está configurada. Contactar administrador."}

    query = str(query or "").strip()[:200]
    if not query:
        return {"error": "Query de pesquisa vazia."}

    safe_max = max(1, int(WEB_SEARCH_MAX_RESULTS or 5))
    top = min(max(1, int(top or 5)), safe_max)

    headers = {
        "X-Subscription-Token": WEB_SEARCH_API_KEY,
        "Accept": "application/json",
    }

    market_parts = str(WEB_SEARCH_MARKET or "pt-PT").split("-")
    search_lang = market_parts[0] if market_parts else "pt"
    country = market_parts[1] if len(market_parts) > 1 else "PT"

    params = {
        "q": query,
        "count": top,
        "country": country,
        "search_lang": search_lang,
        "text_decorations": "false",
        "result_filter": "web",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(WEB_SEARCH_ENDPOINT, headers=headers, params=params)
    except Exception as e:
        return {"error": f"Pesquisa web falhou: {str(e)}"}

    if resp.status_code != 200:
        return {"error": f"Brave Search API {resp.status_code}: {resp.text[:200]}"}

    try:
        data = resp.json()
    except Exception:
        return {"error": "Resposta inválida da Brave Search API."}

    web_results = (data.get("web") or {}).get("results") or []
    results = []
    for item in web_results[:top]:
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "title": str(item.get("title", "") or ""),
                "url": str(item.get("url", "") or ""),
                "snippet": str(item.get("description", "") or "")[:500],
            }
        )

    result = {
        "query": query,
        "total_estimated": len(web_results),
        "results": results,
        "results_count": len(results),
    }

    # Optional: enrich with Brave Answers when explicitly configured.
    if WEB_ANSWERS_ENABLED and WEB_ANSWERS_API_KEY:
        answer_headers = {
            "X-Subscription-Token": WEB_ANSWERS_API_KEY,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        answer_payload = {
            "messages": [{"role": "user", "content": query}],
            "model": WEB_ANSWERS_MODEL,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=max(5.0, float(WEB_ANSWERS_TIMEOUT_SECONDS or 20))) as client:
                answer_resp = await client.post(
                    WEB_ANSWERS_ENDPOINT,
                    headers=answer_headers,
                    json=answer_payload,
                )
            if answer_resp.status_code == 200:
                answer_data = answer_resp.json()
                answer_text = (
                    (answer_data.get("choices") or [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                if isinstance(answer_text, str) and answer_text.strip():
                    result["answer"] = answer_text.strip()[:4000]
            else:
                logging.warning(
                    "[WebSearch] Brave Answers HTTP %s: %s",
                    answer_resp.status_code,
                    answer_resp.text[:200],
                )
        except Exception as e:
            logging.warning("[WebSearch] Brave Answers failed: %s", e)

    return result
