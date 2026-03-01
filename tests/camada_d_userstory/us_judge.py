"""LLM-as-judge para qualidade de User Stories."""

from __future__ import annotations

import json

from .. import eval_config


class USQualityJudge:
    """LLM-as-judge para qualidade subjectiva de User Stories."""

    JUDGE_PROMPT = """Avalia a seguinte User Story contra o padrão MSE.

User Story:
{us_output}

Golden Reference:
{golden_output}

Critérios (pontua cada um de 0 a 1):
1. ESTRUTURA: Título, Descrição, AC seguem formato MSE?
2. GRANULARIDADE: Nível de detalhe similar ao golden?
3. VOCABULÁRIO: Usa termos MSE (CTA, Toast, Modal, etc.)?
4. TESTABILIDADE: Cada AC é verificável por QA?
5. COMPLETUDE: Cobre todos os aspectos do pedido?

Responde em JSON:
{{
  "structure": float,
  "granularity": float,
  "vocabulary": float,
  "testability": float,
  "completeness": float,
  "overall": float,
  "feedback": "texto curto"
}}"""

    async def judge(self, us_output: str, golden_output: str) -> dict:
        if eval_config.MOCK_LLM:
            return self._mock_judge(us_output, golden_output)

        from llm_provider import llm_simple

        prompt = self.JUDGE_PROMPT.format(
            us_output=str(us_output or "")[:3000],
            golden_output=str(golden_output or "")[:3000],
        )
        try:
            raw = await llm_simple(prompt, tier=eval_config.JUDGE_MODEL_TIER, max_tokens=500)
            return json.loads(raw)
        except Exception:
            return self._mock_judge(us_output, golden_output)

    def _mock_judge(self, us_output: str, golden_output: str) -> dict:
        _ = golden_output
        text = str(us_output or "")
        score = 0.5
        if "MSE |" in text:
            score += 0.1
        if "Eu como <b>" in text:
            score += 0.1
        if "Objetivo" in text or "Âmbito" in text:
            score += 0.1
        if "Comportamento" in text:
            score += 0.1
        if any(v in text for v in ["CTA", "Toast", "Modal"]):
            score += 0.1
        score = min(1.0, max(0.0, score))
        return {
            "structure": score,
            "granularity": score,
            "vocabulary": score,
            "testability": score,
            "completeness": score,
            "overall": score,
            "feedback": "mock evaluation",
        }
