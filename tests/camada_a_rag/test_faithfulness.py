"""
Camada A — Faithfulness: A resposta é fiel ao contexto recuperado?

Método: LLM-as-judge
  1. Dado (question, context, answer)
  2. Extrair claims da answer
  3. Para cada claim, verificar se é suportado pelo context
  4. Score = claims_suportados / total_claims
"""

from __future__ import annotations

import json

import pytest

from .. import eval_config


class FaithfulnessJudge:
    """LLM-as-judge para faithfulness."""

    EXTRACT_CLAIMS_PROMPT = """Analisa a seguinte resposta e extrai todas as afirmações factuais (claims) como uma lista JSON.

Resposta: {answer}

Responde APENAS com um JSON array de strings. Exemplo: ["claim 1", "claim 2"]"""

    VERIFY_CLAIM_PROMPT = """Dado o seguinte contexto, verifica se a claim é suportada.

Contexto: {context}

Claim: {claim}

Responde APENAS com: "supported" ou "unsupported"."""

    async def _call_real_llm(self, prompt: str) -> str:
        """Modo real opcional usando llm_provider da app."""
        from llm_provider import llm_simple

        response = await llm_simple(
            [
                {"role": "system", "content": "Responde estritamente ao pedido."},
                {"role": "user", "content": prompt},
            ],
            tier=eval_config.JUDGE_MODEL_TIER,
            temperature=eval_config.JUDGE_TEMPERATURE,
            max_tokens=eval_config.JUDGE_MAX_TOKENS,
        )
        return str(response or "")

    async def extract_claims(self, answer: str) -> list[str]:
        """Extrai claims factuais da resposta. Em modo mock, faz parsing simples."""
        if eval_config.MOCK_LLM:
            normalized = answer.replace("!", ".").replace("?", ".")
            claims = [s.strip(" .") for s in normalized.split(".") if len(s.strip()) > 10]
            return claims[:10]

        prompt = self.EXTRACT_CLAIMS_PROMPT.format(answer=answer)
        raw = await self._call_real_llm(prompt)
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()][:10]
        except Exception:
            pass
        return [s.strip() for s in raw.split("\n") if s.strip()][:10]

    async def verify_claim(self, claim: str, context: str) -> bool:
        """Verifica se claim é suportada pelo contexto."""
        if eval_config.MOCK_LLM:
            claim_words = {w.strip(".,:;()[]{}\"'!?\n\t").lower() for w in claim.split()}
            context_words = {w.strip(".,:;()[]{}\"'!?\n\t").lower() for w in context.split()}
            claim_words.discard("")
            context_words.discard("")
            if not claim_words:
                return True
            overlap = len(claim_words & context_words) / len(claim_words)
            return overlap >= 0.30

        prompt = self.VERIFY_CLAIM_PROMPT.format(context=context, claim=claim)
        raw = (await self._call_real_llm(prompt)).strip().lower()
        return raw.startswith("supported")

    async def score(self, question: str, context: str, answer: str) -> dict:
        """Calcula faithfulness score."""
        _ = question
        claims = await self.extract_claims(answer)
        if not claims:
            return {"score": 1.0, "claims_total": 0, "claims_supported": 0, "details": []}

        supported = 0
        details = []
        for claim in claims:
            is_supported = await self.verify_claim(claim, context)
            details.append({"claim": claim, "supported": is_supported})
            if is_supported:
                supported += 1

        return {
            "score": supported / len(claims),
            "claims_total": len(claims),
            "claims_supported": supported,
            "details": details,
        }


@pytest.mark.asyncio
class TestFaithfulness:
    """Testes de faithfulness sobre o golden set."""

    async def test_faithfulness_all_entries(self, rag_golden_set):
        """Avalia faithfulness para todas as entries do golden set."""
        judge = FaithfulnessJudge()
        results = []

        for entry in rag_golden_set["entries"]:
            mock_answer = f"Com base nos dados, {entry['expected_answer']}"
            result = await judge.score(
                question=entry["question"],
                context=entry["golden_context"],
                answer=mock_answer,
            )
            result["entry_id"] = entry["id"]
            result["category"] = entry["category"]
            results.append(result)

        scores = [r["score"] for r in results]
        avg_score = sum(scores) / len(scores) if scores else 0

        assert avg_score >= eval_config.RAG_FAITHFULNESS_THRESHOLD, (
            f"Faithfulness média ({avg_score:.2f}) abaixo do threshold "
            f"({eval_config.RAG_FAITHFULNESS_THRESHOLD})"
        )

    async def test_faithfulness_no_hallucination_on_empty_context(self):
        """Com contexto vazio, qualquer claim é não-suportada."""
        judge = FaithfulnessJudge()
        result = await judge.score(
            question="Quantos bugs existem?",
            context="",
            answer="Existem 42 bugs activos no sistema.",
        )
        assert result["score"] <= 0.5, "Faithfulness deveria ser baixa sem contexto"
