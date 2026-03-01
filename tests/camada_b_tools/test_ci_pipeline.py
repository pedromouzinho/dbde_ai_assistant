"""Tests para CI pipeline (SPEC-27)."""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCIPipeline:
    def test_ci_workflow_exists(self):
        """GitHub Actions CI workflow deve existir."""
        path = os.path.join(PROJECT_ROOT, ".github", "workflows", "ci.yml")
        assert os.path.isfile(path)

    def test_ci_workflow_has_test_and_build(self):
        """CI workflow deve ter jobs de test e build."""
        path = os.path.join(PROJECT_ROOT, ".github", "workflows", "ci.yml")
        with open(path, encoding="utf-8") as f:
            content = f.read().lower()
        assert "jobs:" in content, "Missing jobs section"
        assert "test-backend:" in content or "pytest" in content, "Missing test job"
        assert "build-frontend:" in content or "npm run build" in content, "Missing build job"
