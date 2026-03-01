"""Camada D — qualidade subjectiva e critérios testáveis de User Stories."""

from __future__ import annotations

import json
import re

import pytest

from .. import eval_config
from .us_judge import USQualityJudge


TESTABILITY_VERBS = {
    "validar", "mostrar", "apresentar", "bloquear", "permitir", "registar",
    "confirmar", "enviar", "calcular", "exibir", "activar", "desactivar",
}


def _has_testable_action(ac_html: str) -> bool:
    lower = str(ac_html or "").lower()
    return any(verb in lower for verb in TESTABILITY_VERBS)


def _has_contradiction(ac_html: str) -> bool:
    lower = str(ac_html or "").lower()
    contradictory_pairs = [
        ("sempre obrigatório", "opcional"),
        ("apenas leitura", "editável"),
        ("nunca mostrar", "mostrar sempre"),
    ]
    return any(a in lower and b in lower for a, b in contradictory_pairs)


@pytest.mark.asyncio
class TestUSQuality:
    async def test_golden_set_quality(self):
        path = eval_config.DATASETS_DIR / "us_golden_set.json"
        with open(path, "r", encoding="utf-8") as f:
            golden = json.load(f)

        judge = USQualityJudge()
        scores = []

        for entry in golden["entries"]:
            go = entry["golden_output"]
            combined = (
                f"**Título**: {go.get('title','')}\n"
                f"**Descrição**:\n{go.get('description_html','')}\n"
                f"**Critérios de Aceitação**:\n{go.get('acceptance_criteria_html','')}"
            )
            ref = json.dumps(go, ensure_ascii=False)
            judged = await judge.judge(combined, ref)

            vocab_hits = sum(
                1 for term in entry.get("expected_vocabulary", [])
                if term.lower() in combined.lower()
            )
            vocab_ratio = vocab_hits / max(1, len(entry.get("expected_vocabulary", [])))

            quality = float(judged.get("overall", 0.0))
            if not _has_testable_action(go.get("acceptance_criteria_html", "")):
                quality -= 0.2
            if _has_contradiction(go.get("acceptance_criteria_html", "")):
                quality -= 0.3
            quality = max(0.0, min(1.0, (quality + vocab_ratio) / 2))
            scores.append(quality)

        avg_score = sum(scores) / len(scores)
        threshold = getattr(eval_config, "US_QUALITY_THRESHOLD", 0.7)
        assert avg_score >= threshold, f"US quality média ({avg_score:.2f}) abaixo do threshold ({threshold})"

    async def test_golden_titles_follow_mse_pattern(self):
        path = eval_config.DATASETS_DIR / "us_golden_set.json"
        with open(path, "r", encoding="utf-8") as f:
            golden = json.load(f)

        pattern = re.compile(r"^MSE\s*\|\s*[^|]+\|\s*[^|]+\|\s*[^|]+\|\s*[^|]+$")
        invalid = []
        for entry in golden["entries"]:
            title = entry["golden_output"].get("title", "")
            if not pattern.match(title):
                invalid.append((entry["id"], title))

        assert not invalid, f"Títulos fora do padrão MSE: {invalid[:3]}"
