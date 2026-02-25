# =============================================================================
# http_helpers.py — HTTP retry helpers (public API)
# =============================================================================

import asyncio
import logging
import json

import httpx


def _log(msg: str) -> None:
    logging.info("[HTTP] %s", msg)


async def devops_request_with_retry(client, method, url, headers, json_body=None, max_retries=5):
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

async def search_request_with_retry(url, headers, json_body, max_retries=3):
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

