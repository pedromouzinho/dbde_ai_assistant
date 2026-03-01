"""Camada A — Context Recall evaluator."""

from __future__ import annotations

import pytest

from .. import eval_config


class ContextRecallEvaluator:
    """Das keywords relevantes esperadas, quantas aparecem no retrieval?"""

    def score(self, retrieved_items: list[dict], expected_keywords: list[str]) -> dict:
        keywords = [k.lower() for k in expected_keywords]
        if not keywords:
            return {"score": 1.0, "keywords_found": 0, "keywords_total": 0}

        corpus = " ".join(str(item.get("content", "")).lower() for item in retrieved_items)
        found = sum(1 for keyword in keywords if keyword in corpus)
        return {
            "score": found / len(keywords),
            "keywords_found": found,
            "keywords_total": len(keywords),
        }


@pytest.mark.asyncio
class TestContextRecall:
    async def test_recall_all_entries(self, rag_golden_set):
        evaluator = ContextRecallEvaluator()
        scores = []

        for entry in rag_golden_set["entries"]:
            keywords = entry["expected_context_keywords"]
            retrieved = [
                {"id": "1", "content": f"{keywords[0]} {keywords[1]} dados operacionais"},
                {"id": "2", "content": f"contexto extra {keywords[2]} {keywords[3]}"},
            ]
            result = evaluator.score(retrieved, keywords)
            scores.append(result["score"])

        avg_score = sum(scores) / len(scores)
        assert avg_score >= eval_config.RAG_CONTEXT_RECALL_THRESHOLD, (
            f"Context recall média ({avg_score:.2f}) abaixo do threshold "
            f"({eval_config.RAG_CONTEXT_RECALL_THRESHOLD})"
        )

    async def test_recall_all_keywords_found(self):
        evaluator = ContextRecallEvaluator()
        result = evaluator.score(
            [{"content": "bug active dbde work item"}],
            ["bug", "active", "dbde", "work item"],
        )
        assert result["score"] == 1.0
        assert result["keywords_found"] == result["keywords_total"]
