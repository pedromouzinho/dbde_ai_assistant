"""Camada D — validação de formatação HTML limpa para User Stories."""

from __future__ import annotations

import json
import re

import pytest

from .. import eval_config


DIRTY_TAG_PATTERN = re.compile(r"<(?:font|span|table|tr|td|th|p|h[1-6])(?:\s[^>]*)?>", re.IGNORECASE)
ALLOWED_TAG_PATTERN = re.compile(r"</?(?:b|ul|li|br|div)>", re.IGNORECASE)


@pytest.mark.asyncio
class TestUSFormatting:
    async def test_golden_html_is_clean(self):
        path = eval_config.DATASETS_DIR / "us_golden_set.json"
        with open(path, "r", encoding="utf-8") as f:
            golden = json.load(f)

        scores = []
        for entry in golden["entries"]:
            go = entry["golden_output"]
            combined = f"{go.get('description_html','')}\n{go.get('acceptance_criteria_html','')}"
            score = 1.0

            if DIRTY_TAG_PATTERN.search(combined):
                score -= 0.5
            if "&nbsp;" in combined:
                score -= 0.2
            if 'style="' in combined:
                score -= 0.3

            tags = re.findall(r"</?[a-zA-Z0-9]+[^>]*>", combined)
            unknown = [tag for tag in tags if not ALLOWED_TAG_PATTERN.fullmatch(tag)]
            if unknown:
                score -= 0.2

            scores.append(max(0.0, score))

        avg_score = sum(scores) / len(scores)
        threshold = getattr(eval_config, "US_FORMATTING_THRESHOLD", 0.9)
        assert avg_score >= threshold, f"Formatting médio ({avg_score:.2f}) abaixo do threshold ({threshold})"
