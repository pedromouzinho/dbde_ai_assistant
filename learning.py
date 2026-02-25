# =============================================================================
# learning.py — Adaptive learning helpers (regras + few-shot) v8.0
# =============================================================================

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict

from config import (
    EXAMPLES_INDEX,
    SEARCH_SERVICE,
    SEARCH_KEY,
    API_VERSION_SEARCH,
)
from storage import table_query
from tools import get_embedding, _search_request_with_retry

_prompt_rules_cache: Dict = {"rules": [], "last_refresh": None}
_few_shot_cache: Dict = {}  # key: question_hash -> {"result": str, "ts": datetime}
_FEW_SHOT_CACHE_TTL = 1800  # 30 minutos
_FEW_SHOT_CACHE_MAX = 50    # máximo 50 entradas
_prompt_rules_lock = asyncio.Lock()


def invalidate_prompt_rules_cache():
    """Força refresh das regras aprendidas na próxima leitura."""
    _prompt_rules_cache["last_refresh"] = None


def invalidate_few_shot_cache():
    """Limpa cache de few-shot examples."""
    _few_shot_cache.clear()


async def _search_examples_semantic(embedding, filter_expr="", top=3):
    try:
        url = (
            f"https://{SEARCH_SERVICE}.search.windows.net/indexes/"
            f"{EXAMPLES_INDEX}/docs/search?api-version={API_VERSION_SEARCH}"
        )
        body = {
            "vectorQueries": [{
                "kind": "vector",
                "vector": embedding,
                "fields": "question_vector",
                "k": top,
            }],
            "select": "id,question,answer,tools_used,rating,feedback_note,example_type",
            "top": top,
        }
        if filter_expr:
            body["filter"] = filter_expr

        data = await _search_request_with_retry(
            url=url,
            headers={"api-key": SEARCH_KEY, "Content-Type": "application/json"},
            json_body=body,
            max_retries=3,
        )
        if "error" in data:
            logging.error("[Learning] _search_examples_semantic failed: %s", data["error"])
            return []

        return data.get("value", [])
    except Exception as e:
        logging.error("[Learning] _search_examples_semantic exception: %s", str(e))
        return []


async def get_learned_rules():
    now = datetime.now(timezone.utc)
    needs_refresh = (
        _prompt_rules_cache["last_refresh"] is None
        or (now - _prompt_rules_cache["last_refresh"]).total_seconds() > 3600
    )
    if needs_refresh:
        async with _prompt_rules_lock:
            now = datetime.now(timezone.utc)
            needs_refresh = (
                _prompt_rules_cache["last_refresh"] is None
                or (now - _prompt_rules_cache["last_refresh"]).total_seconds() > 3600
            )
            if needs_refresh:
                try:
                    _prompt_rules_cache["rules"] = await table_query(
                        "PromptRules",
                        "PartitionKey eq 'active'",
                        top=30,
                    )
                    _prompt_rules_cache["last_refresh"] = now
                except Exception as e:
                    logging.warning("[Learning] get_learned_rules refresh failed: %s", e)

    if not _prompt_rules_cache["rules"]:
        return ""

    return "\n\nREGRAS APRENDIDAS:\n" + "\n".join(
        f"- [{r.get('Category', '')}] {r.get('RuleText', '')}"
        for r in _prompt_rules_cache["rules"]
    )


async def get_few_shot_examples(question):
    try:
        cache_key = hashlib.md5(question.strip().lower().encode()).hexdigest()
        now = datetime.now(timezone.utc)

        cached = _few_shot_cache.get(cache_key)
        if cached and (now - cached["ts"]).total_seconds() < _FEW_SHOT_CACHE_TTL:
            return cached["result"]

        emb = await get_embedding(question)
        if not emb:
            return ""

        pos, neg = await asyncio.gather(
            _search_examples_semantic(emb, "rating ge 7", 3),
            _search_examples_semantic(emb, "rating le 3", 2),
        )
        if not pos and not neg:
            return ""

        txt = "\n---\nEXEMPLOS DE REFERÊNCIA:\n"
        if pos:
            txt += "✅ BOAS RESPOSTAS:\n"
            for i, e in enumerate(pos, 1):
                txt += (
                    f"\nEx{i} (rating {e.get('rating', '?')}/10):\n"
                    f"P: {e.get('question', '')}\n"
                    f"R: {e.get('answer', '')[:500]}\n"
                )

        if neg:
            txt += "❌ EVITAR:\n"
            for i, e in enumerate(neg, 1):
                note = e.get("feedback_note", "")
                txt += (
                    f"\nErro{i} (rating {e.get('rating', '?')}/10):\n"
                    f"P: {e.get('question', '')}\n"
                    f"R: {e.get('answer', '')[:300]}\n"
                    f"{'Problema: ' + note if note else ''}\n"
                )

        txt = txt + "\n---\n"
        _few_shot_cache[cache_key] = {"result": txt, "ts": datetime.now(timezone.utc)}

        if len(_few_shot_cache) > _FEW_SHOT_CACHE_MAX:
            oldest_key = min(_few_shot_cache.items(), key=lambda item: item[1]["ts"])[0]
            _few_shot_cache.pop(oldest_key, None)

        return txt
    except Exception as e:
        logging.warning("[Learning] get_few_shot_examples failed: %s", e)
        return ""
