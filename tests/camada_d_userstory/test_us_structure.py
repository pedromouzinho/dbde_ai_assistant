"""
Camada D — US Structure: Cada US gerada segue o formato MSE obrigatório?
"""

from __future__ import annotations

import json
import re

import pytest

from .. import eval_config


class USStructureValidator:
    """Valida estrutura de uma User Story contra padrão MSE."""

    REQUIRED_AC_SECTIONS = ["Objetivo", "Composição", "Comportamento"]

    def validate_title(self, title: str) -> dict:
        score = 1.0
        issues = []
        title = str(title or "")
        if not title.strip().startswith("MSE"):
            issues.append("Título não começa com 'MSE'")
            score -= 0.3
        pipes = title.count("|")
        if pipes < 3:
            issues.append(f"Título com {pipes} separadores (mínimo 3)")
            score -= 0.2
        if pipes > 5:
            issues.append(f"Título com demasiados separadores ({pipes})")
            score -= 0.1
        return {"score": max(0.0, score), "issues": issues}

    def validate_description(self, desc_html: str) -> dict:
        score = 1.0
        issues = []
        desc_html = str(desc_html or "")
        if "Eu como" not in desc_html:
            issues.append("Descrição não contém 'Eu como'")
            score -= 0.4
        if "<b>" not in desc_html:
            issues.append("Descrição sem bold tags")
            score -= 0.2
        if "para que" not in desc_html.lower():
            issues.append("Descrição sem 'para que' (benefício)")
            score -= 0.2
        return {"score": max(0.0, score), "issues": issues}

    def validate_ac(self, ac_html: str) -> dict:
        score = 1.0
        issues = []
        ac_html = str(ac_html or "")
        ac_lower = ac_html.lower()
        found = sum(1 for section in self.REQUIRED_AC_SECTIONS if section.lower() in ac_lower)
        if found < len(self.REQUIRED_AC_SECTIONS):
            issues.append(f"AC com {found}/{len(self.REQUIRED_AC_SECTIONS)} secções obrigatórias")
            score -= 0.3 * (1 - found / len(self.REQUIRED_AC_SECTIONS))
        if "<li>" not in ac_html:
            issues.append("AC sem list items (<li>)")
            score -= 0.2
        return {"score": max(0.0, score), "issues": issues}

    def validate_html_cleanliness(self, html: str) -> dict:
        score = 1.0
        issues = []
        html = str(html or "")
        dirty = re.findall(r'<(?:font|span\s+style|table|td|tr|th|p\s+style|h[1-6]\s+style)[^>]*>', html)
        if dirty:
            issues.append(f"HTML sujo: {dirty[:3]}")
            score -= 0.4
        if "&nbsp;" in html:
            issues.append("Contém &nbsp;")
            score -= 0.1
        if 'style="' in html:
            issues.append("Contém inline styles")
            score -= 0.3
        return {"score": max(0.0, score), "issues": issues}


@pytest.mark.asyncio
class TestUSStructure:
    """Testes de estrutura de User Stories."""

    async def test_golden_set_structure(self):
        path = eval_config.DATASETS_DIR / "us_golden_set.json"
        with open(path, "r", encoding="utf-8") as f:
            golden = json.load(f)

        validator = USStructureValidator()
        results = []

        for entry in golden["entries"]:
            go = entry["golden_output"]
            title_result = validator.validate_title(go["title"])
            desc_result = validator.validate_description(go["description_html"])
            ac_result = validator.validate_ac(go["acceptance_criteria_html"])
            html_result = validator.validate_html_cleanliness(
                go["description_html"] + go["acceptance_criteria_html"]
            )

            avg_score = (
                title_result["score"]
                + desc_result["score"]
                + ac_result["score"]
                + html_result["score"]
            ) / 4

            results.append(
                {
                    "entry_id": entry["id"],
                    "scores": {
                        "title": title_result["score"],
                        "description": desc_result["score"],
                        "ac": ac_result["score"],
                        "html": html_result["score"],
                        "average": avg_score,
                    },
                    "issues": (
                        title_result["issues"]
                        + desc_result["issues"]
                        + ac_result["issues"]
                        + html_result["issues"]
                    ),
                }
            )

        avg_all = sum(r["scores"]["average"] for r in results) / len(results)
        threshold = getattr(eval_config, "US_STRUCTURE_THRESHOLD", 0.8)
        assert avg_all >= threshold, f"Qualidade estrutural média ({avg_all:.2f}) abaixo do threshold ({threshold})"
