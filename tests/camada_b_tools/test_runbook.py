"""Tests para Runbook operacional (SPEC-25)."""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRunbook:
    def test_runbook_exists(self):
        """docs/RUNBOOK.md deve existir."""
        path = os.path.join(PROJECT_ROOT, "docs", "RUNBOOK.md")
        assert os.path.isfile(path)

    def test_runbook_has_required_sections(self):
        """Runbook deve ter seccoes criticas."""
        path = os.path.join(PROJECT_ROOT, "docs", "RUNBOOK.md")
        with open(path, encoding="utf-8") as f:
            content = f.read().lower()
        required = [
            "health check",
            "worker",
            "storage",
            "llm",
            "pat",
            "startup",
            "env var",
        ]
        for section in required:
            assert section in content, f"Runbook missing section about: {section}"
