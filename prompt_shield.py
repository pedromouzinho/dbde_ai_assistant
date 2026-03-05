"""Prompt Shield using Azure AI Content Safety."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from config import CONTENT_SAFETY_ENDPOINT, CONTENT_SAFETY_KEY, PROMPT_SHIELD_ENABLED

logger = logging.getLogger(__name__)

PROMPT_SHIELD_API_VERSION = "2024-09-01"


@dataclass
class PromptShieldResult:
    is_blocked: bool
    attack_type: Optional[str] = None
    details: Optional[str] = None


async def check_prompt_shield(
    user_prompt: str,
    documents: Optional[List[str]] = None,
) -> PromptShieldResult:
    """Check prompt injection/jailbreak using Azure Content Safety Prompt Shields."""
    if not PROMPT_SHIELD_ENABLED or not CONTENT_SAFETY_ENDPOINT or not CONTENT_SAFETY_KEY:
        return PromptShieldResult(is_blocked=False)

    text = (user_prompt or "").strip()
    if len(text) < 5:
        return PromptShieldResult(is_blocked=False)

    url = (
        f"{CONTENT_SAFETY_ENDPOINT.rstrip('/')}/contentsafety/text:shieldPrompt"
        f"?api-version={PROMPT_SHIELD_API_VERSION}"
    )
    payload: Dict[str, Any] = {"userPrompt": text}
    if documents:
        payload["documents"] = [{"content": (doc or "")[:5000]} for doc in documents[:5]]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={
                    "Ocp-Apim-Subscription-Key": CONTENT_SAFETY_KEY,
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            result = resp.json()

        user_analysis = result.get("userPromptAnalysis") or {}
        if bool(user_analysis.get("attackDetected")):
            logger.warning("Prompt Shield: ataque detectado no prompt do utilizador")
            return PromptShieldResult(
                is_blocked=True,
                attack_type="user_attack",
                details="Tentativa de manipulacao detectada no teu pedido.",
            )

        for idx, doc_result in enumerate(result.get("documentsAnalysis") or []):
            if bool(doc_result.get("attackDetected")):
                logger.warning("Prompt Shield: ataque detectado no documento %s", idx)
                return PromptShieldResult(
                    is_blocked=True,
                    attack_type="document_attack",
                    details=f"Conteudo suspeito detectado no documento {idx + 1}.",
                )

        return PromptShieldResult(is_blocked=False)
    except Exception as e:
        # Fail-open por desenho: em erro do serviço não bloqueamos o utilizador.
        logger.warning("Prompt Shield falhou (passthrough): %s", e)
        return PromptShieldResult(is_blocked=False)


def _extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                txt = str(part.get("text", "")).strip()
                if txt:
                    parts.append(txt)
        return " ".join(parts)
    return ""


async def check_messages(messages: List[dict]) -> PromptShieldResult:
    """Check the latest user message from an OpenAI-style messages list."""
    last_user_text = ""
    for msg in reversed(messages or []):
        if (msg or {}).get("role") != "user":
            continue
        last_user_text = _extract_message_text((msg or {}).get("content"))
        if last_user_text:
            break
    if not last_user_text:
        return PromptShieldResult(is_blocked=False)
    return await check_prompt_shield(last_user_text)

