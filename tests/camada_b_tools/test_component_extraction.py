"""Tests para component extraction (SPEC-31)."""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestComponentExtraction:
    def test_app_jsx_reduced(self):
        """App.jsx deve ter menos de 2000 linhas apos extracao."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "App.jsx")
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) < 2000, f"App.jsx has {len(lines)} lines, expected < 2000"

    def test_no_stub_components(self):
        """Componentes nao devem ser stubs (return null)."""
        comp_dir = os.path.join(PROJECT_ROOT, "frontend", "src", "components")
        stubs_found = []
        for name in os.listdir(comp_dir):
            if not name.endswith(".jsx"):
                continue
            path = os.path.join(comp_dir, name)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            if len(content) < 150 and "return null" in content:
                stubs_found.append(name)
        assert not stubs_found, f"Stub components found: {stubs_found}"

    def test_components_have_imports(self):
        """Cada componente deve importar React."""
        comp_dir = os.path.join(PROJECT_ROOT, "frontend", "src", "components")
        for name in os.listdir(comp_dir):
            if not name.endswith(".jsx"):
                continue
            path = os.path.join(comp_dir, name)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            assert "import React" in content or "from 'react'" in content, f"{name} missing React import"

    def test_app_imports_components(self):
        """App.jsx deve importar componentes de ./components/."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "App.jsx")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        expected_imports = [
            "FeedbackWidget",
            "MessageBubble",
            "LoginScreen",
            "UserMenu",
            "ChartBlock",
        ]
        for comp in expected_imports:
            assert comp in content, f"App.jsx missing import for {comp}"
