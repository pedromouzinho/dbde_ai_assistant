"""Camada A — Context Precision evaluator."""

from __future__ import annotations

import pytest

from .. import eval_config


class ContextPrecisionEvaluator:
    """Dos items recuperados, quantos são relevantes?"""

    def score(self, retrieved_items: list[dict], expected_keywords: list[str]) -> dict:
        if not retrieved_items:
            return {"score": 0.0, "relevant_count": 0, "total_retrieved": 0}

        lowered = [k.lower() for k in expected_keywords]
        relevant = 0
        for item in retrieved_items:
            text = str(item.get("content", "")).lower()
            if any(k in text for k in lowered):
                relevant += 1

        total = len(retrieved_items)
        return {
            "score": relevant / total,
            "relevant_count": relevant,
            "total_retrieved": total,
        }


@pytest.mark.asyncio
class TestContextPrecision:
    async def test_precision_all_entries(self, rag_golden_set):
        evaluator = ContextPrecisionEvaluator()
        scores = []

        for entry in rag_golden_set["entries"]:
            keywords = entry["expected_context_keywords"]
            retrieved = [
                {"id": "A", "content": f"Documento relevante: {' '.join(keywords[:2])}"},
                {"id": "B", "content": f"Outro item com {keywords[-1]} e contexto técnico"},
                {"id": "N", "content": "texto irrelevante lorem ipsum"},
            ]
            result = evaluator.score(retrieved, keywords)
            scores.append(result["score"])

        avg_score = sum(scores) / len(scores)
        assert avg_score >= eval_config.RAG_CONTEXT_PRECISION_THRESHOLD, (
            f"Context precision média ({avg_score:.2f}) abaixo do threshold "
            f"({eval_config.RAG_CONTEXT_PRECISION_THRESHOLD})"
        )

    async def test_precision_empty_retrieval(self):
        evaluator = ContextPrecisionEvaluator()
        result = evaluator.score([], ["bug", "dbde"])
        assert result["score"] == 0.0
        assert result["total_retrieved"] == 0
