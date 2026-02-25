"""Camada A — Relevancy: a resposta é relevante para a pergunta?"""

from __future__ import annotations

import re

import pytest

from .. import eval_config


class RelevancyJudge:
    """Judge para relevância pergunta-resposta."""

    async def _call_real_llm(self, question: str, answer: str) -> tuple[float, str]:
        from llm_provider import llm_simple

        prompt = (
            "Avalia de 0.0 a 1.0 quão relevante é a resposta para a pergunta. "
            "Responde em formato: <score>|<justificação_curta>.\n"
            f"Pergunta: {question}\nResposta: {answer}"
        )
        raw = str(
            await llm_simple(
                prompt,
                tier=eval_config.JUDGE_MODEL_TIER,
                max_tokens=eval_config.JUDGE_MAX_TOKENS,
            )
        )
        if "|" in raw:
            left, right = raw.split("|", 1)
            try:
                return max(0.0, min(1.0, float(left.strip()))), right.strip()
            except Exception:
                return 0.5, right.strip() or "parse_fallback"
        return 0.5, "parse_fallback"

    async def score(self, question: str, answer: str) -> dict:
        if eval_config.MOCK_LLM:
            q_tokens = {t for t in re.findall(r"\w+", question.lower()) if len(t) > 2}
            a_tokens = {t for t in re.findall(r"\w+", answer.lower()) if len(t) > 2}
            if not q_tokens or not a_tokens:
                return {"score": 0.0, "reasoning": "empty_tokens"}

            overlap = len(q_tokens & a_tokens) / len(q_tokens)
            generic_penalty = 0.0
            generic_markers = [
                "não tenho dados",
                "depende",
                "informação insuficiente",
                "como modelo",
            ]
            lower = answer.lower()
            if any(m in lower for m in generic_markers):
                generic_penalty = 0.2

            score = max(0.0, min(1.0, overlap - generic_penalty))
            reasoning = f"overlap={overlap:.2f}; penalty={generic_penalty:.2f}"
            return {"score": score, "reasoning": reasoning}

        score, reasoning = await self._call_real_llm(question, answer)
        return {"score": score, "reasoning": reasoning}


@pytest.mark.asyncio
class TestRelevancy:
    async def test_relevancy_all_entries(self, rag_golden_set):
        judge = RelevancyJudge()
        scores = []
        for entry in rag_golden_set["entries"]:
            answer = f"{entry['question']} {entry['expected_answer']}"
            result = await judge.score(entry["question"], answer)
            scores.append(result["score"])

        avg_score = sum(scores) / len(scores)
        assert avg_score >= eval_config.RAG_RELEVANCY_THRESHOLD, (
            f"Relevancy média ({avg_score:.2f}) abaixo do threshold "
            f"({eval_config.RAG_RELEVANCY_THRESHOLD})"
        )

    async def test_relevancy_irrelevant_answer(self):
        judge = RelevancyJudge()
        result = await judge.score(
            "Quantos bugs activos existem no backlog DBDE?",
            "A fotossíntese ocorre nos cloroplastos das plantas.",
        )
        assert result["score"] < 0.3

    async def test_relevancy_partial_answer(self):
        judge = RelevancyJudge()
        result = await judge.score(
            "Preciso de bugs activos e velocidade média por sprint.",
            "Existem bugs activos no backlog, mas sem detalhe de velocidade.",
        )
        assert 0.3 <= result["score"] <= 0.85
