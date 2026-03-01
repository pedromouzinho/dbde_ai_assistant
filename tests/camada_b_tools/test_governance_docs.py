"""Tests para governance docs (SPEC-30)."""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGovernanceDocs:
    def test_deploy_checklist_exists(self):
        """docs/DEPLOY_CHECKLIST.md deve existir."""
        assert os.path.isfile(os.path.join(PROJECT_ROOT, "docs", "DEPLOY_CHECKLIST.md"))

    def test_continuity_doc_exists(self):
        """docs/CONTINUITY.md deve existir."""
        assert os.path.isfile(os.path.join(PROJECT_ROOT, "docs", "CONTINUITY.md"))

    def test_data_policy_exists(self):
        """docs/DATA_POLICY.md deve existir."""
        assert os.path.isfile(os.path.join(PROJECT_ROOT, "docs", "DATA_POLICY.md"))

    def test_data_policy_has_prohibited_section(self):
        """Data policy deve ter seccao de dados proibidos."""
        path = os.path.join(PROJECT_ROOT, "docs", "DATA_POLICY.md")
        with open(path, encoding="utf-8") as f:
            content = f.read().lower()
        assert "proibid" in content or "prohibited" in content
