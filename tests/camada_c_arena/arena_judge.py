"""LLM-as-Judge para Arena comparison (DBDE vs LLM genérico)."""

from __future__ import annotations

from dataclasses import dataclass

from .. import eval_config


@dataclass
class ArenaResult:
    winner: str
    score_a: float
    score_b: float
    reasoning: str


class ArenaJudge:
    """Avalia qual de duas respostas é melhor para um dado prompt."""

    JUDGE_PROMPT = """És um avaliador imparcial. Compara duas respostas à mesma pergunta.

Pergunta: {question}

Critérios de avaliação:
{criteria}

--- Resposta A (DBDE Assistant) ---
{response_a}

--- Resposta B (LLM Genérico) ---
{response_b}

Avalia cada critério de 1-5 para cada resposta. Responde em JSON:
{{
  "winner": "A" | "B" | "tie",
  "score_a": float (1-5),
  "score_b": float (1-5),
  "reasoning": "explicação breve"
}}"""

    async def judge(self, question, response_a, response_b, criteria) -> dict:
        if eval_config.MOCK_LLM:
            score_a = self._heuristic_score(response_a, criteria)
            score_b = self._heuristic_score(response_b, criteria)
            winner = "A" if score_a > score_b else ("B" if score_b > score_a else "tie")
            return {
                "winner": winner,
                "score_a": score_a,
                "score_b": score_b,
                "reasoning": "mock heuristic",
            }

        raise NotImplementedError("Real LLM mode not yet integrated")

    def _heuristic_score(self, response, criteria):
        text = str(response or "")
        score = 3.0
        if len(text) > 200:
            score += 0.5
        if any(char.isdigit() for char in text):
            score += 0.5
        if any(k in text.lower() for k in ["kpi", "dados", "métrica", "work item", "query"]):
            score += 0.5
        if "error" in text.lower():
            score -= 1.0
        if criteria and len(criteria) >= 3:
            score += 0.2
        return min(5.0, max(1.0, score))
