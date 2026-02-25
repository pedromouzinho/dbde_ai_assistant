# =============================================================================
# http_helpers.py — HTTP retry helpers (public API)
# =============================================================================

import asyncio
import logging
from typing import Any

import httpx


def _log(prefix: str, msg: str) -> None:
    logging.info("[%s] %s", prefix, msg)


async def _request_with_retry(
    *,
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    content_body: str | bytes | None = None,
    max_retries: int = 3,
    timeout: int = 30,
    log_prefix: str = "HTTP",
) -> dict:
    request_method = (method or "GET").upper()
    if request_method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        return {"error": f"{log_prefix} método não suportado: {request_method}"}

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(1, max_retries + 1):
            try:
                request_kwargs: dict[str, Any] = {"headers": headers}
                if content_body is not None:
                    request_kwargs["content"] = content_body
                elif json_body is not None:
                    request_kwargs["json"] = json_body

                resp = await client.request(request_method, url, **request_kwargs)

                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    try:
                        wait = int(float(retry_after)) if retry_after is not None else 2 ** (attempt - 1)
                    except (TypeError, ValueError):
                        wait = 2 ** (attempt - 1)
                    wait = max(1, min(wait, 30))
                    if attempt == max_retries:
                        return {"error": f"{log_prefix} 429 após {max_retries} tentativas"}
                    _log(log_prefix, f"429 attempt {attempt}/{max_retries}, retry em {wait}s")
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code >= 500:
                    wait = max(1, min(2 ** (attempt - 1), 30))
                    if attempt == max_retries:
                        return {"error": f"{log_prefix} {resp.status_code} após {max_retries} tentativas"}
                    _log(log_prefix, f"{resp.status_code} attempt {attempt}/{max_retries}, retry em {wait}s")
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code >= 400:
                    return {"error": f"{log_prefix} {resp.status_code}: {resp.text[:200]}"}

                if not resp.content:
                    return {}
                try:
                    payload = resp.json()
                except ValueError:
                    return {"error": f"{log_prefix} resposta não-JSON"}
                if isinstance(payload, dict):
                    return payload
                return {"value": payload}

            except httpx.TimeoutException:
                wait = max(1, min(2 ** (attempt - 1), 30))
                if attempt == max_retries:
                    return {"error": f"{log_prefix} timeout após {max_retries} tentativas"}
                _log(log_prefix, f"timeout attempt {attempt}/{max_retries}, retry em {wait}s")
                await asyncio.sleep(wait)
            except httpx.RequestError as e:
                wait = max(1, min(2 ** (attempt - 1), 30))
                if attempt == max_retries:
                    return {"error": f"{log_prefix} request error após {max_retries} tentativas: {str(e)}"}
                _log(log_prefix, f"request error attempt {attempt}/{max_retries}: {str(e)}; retry em {wait}s")
                await asyncio.sleep(wait)
            except Exception as e:
                wait = max(1, min(2 ** (attempt - 1), 30))
                if attempt == max_retries:
                    return {"error": f"{log_prefix} erro: {str(e)}"}
                _log(log_prefix, f"erro attempt {attempt}/{max_retries}: {str(e)}; retry em {wait}s")
                await asyncio.sleep(wait)

    return {"error": f"{log_prefix} erro desconhecido"}


async def devops_request_with_retry(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    *,
    content_body: str | bytes | None = None,
    max_retries: int = 5,
    timeout: int = 30,
) -> dict:
    """Call Azure DevOps REST API with retry for 429/5xx/timeouts."""
    return await _request_with_retry(
        method=method,
        url=url,
        headers=headers,
        json_body=json_body,
        content_body=content_body,
        max_retries=max_retries,
        timeout=timeout,
        log_prefix="DevOps",
    )


async def search_request_with_retry(
    url: str,
    headers: dict[str, str] | None,
    json_body: Any,
    max_retries: int = 3,
    timeout: int = 30,
) -> dict:
    """POST to Azure AI Search endpoint with retry for 429/5xx/timeouts."""
    return await _request_with_retry(
        method="POST",
        url=url,
        headers=headers,
        json_body=json_body,
        max_retries=max_retries,
        timeout=timeout,
        log_prefix="Search",
    )
