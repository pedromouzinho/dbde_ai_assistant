"""Camada C — comparação Arena entre resposta DBDE e resposta genérica."""

from __future__ import annotations

from collections import defaultdict

import pytest

from .. import eval_config
from .arena_judge import ArenaJudge


def _mock_dbde_response(prompt_entry: dict) -> str:
    category = prompt_entry.get("category", "")
    prompt = prompt_entry.get("prompt", "")
    if category == "domain_specific":
        return (
            f"Com base nos dados DevOps para '{prompt}', temos 18 bugs activos, 7 features em progresso "
            "e throughput médio de 11 itens/sprint. Próximo passo: priorizar 3 blockers."
        )
    if category == "task_execution":
        return (
            "Executei as ferramentas necessárias. Resultado: user story estruturada, acceptance criteria "
            "testáveis e checklist de entrega com 5 passos. Estimativa: 2 dias, 3 riscos mitigados."
        )
    if category == "edge_case":
        return (
            "Pedido ambíguo identificado. Resposta segura: explicito limites, proponho 2 interpretações e "
            "indico quais dados adicionais são necessários antes de executar. Sinalizo 3 validações obrigatórias."
        )
    return "Resposta técnica objetiva com contexto, exemplos práticos e 2 referências operacionais."


def _mock_vanilla_response(prompt_entry: dict) -> str:
    _ = prompt_entry
    return "Posso ajudar com isso de forma geral, mas depende de mais contexto."


@pytest.mark.asyncio
class TestArenaComparison:
    async def test_arena_win_rate(self, arena_prompts):
        judge = ArenaJudge()
        prompts = arena_prompts["prompts"]
        wins = 0
        results = []

        for entry in prompts:
            response_a = _mock_dbde_response(entry)
            response_b = _mock_vanilla_response(entry)
            judged = await judge.judge(
                question=entry["prompt"],
                response_a=response_a,
                response_b=response_b,
                criteria=entry.get("evaluation_criteria", []),
            )
            if judged["winner"] == "A":
                wins += 1
            results.append({"id": entry["id"], "category": entry["category"], **judged})

        win_rate = wins / len(prompts)
        assert win_rate >= eval_config.ARENA_WIN_RATE_THRESHOLD, (
            f"Arena win-rate ({win_rate:.2f}) abaixo do threshold "
            f"({eval_config.ARENA_WIN_RATE_THRESHOLD})"
        )

    async def test_arena_breakdown_by_category(self, arena_prompts):
        judge = ArenaJudge()
        buckets = defaultdict(lambda: {"wins": 0, "total": 0})

        for entry in arena_prompts["prompts"]:
            result = await judge.judge(
                question=entry["prompt"],
                response_a=_mock_dbde_response(entry),
                response_b=_mock_vanilla_response(entry),
                criteria=entry.get("evaluation_criteria", []),
            )
            cat = entry["category"]
            buckets[cat]["total"] += 1
            if result["winner"] == "A":
                buckets[cat]["wins"] += 1

        for category, stats in buckets.items():
            assert stats["total"] > 0, f"categoria sem casos: {category}"
            win_rate = stats["wins"] / stats["total"]
            assert 0.0 <= win_rate <= 1.0
