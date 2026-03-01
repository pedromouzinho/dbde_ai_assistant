"""Tests para Vite Phase 1 — estrutura de projecto (SPEC-23)."""

import json
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestViteProjectStructure:
    def test_package_json_exists(self):
        """package.json deve existir na raiz."""
        path = os.path.join(PROJECT_ROOT, "package.json")
        assert os.path.isfile(path)

    def test_package_json_has_vite(self):
        """package.json deve ter vite e react como dependencias."""
        path = os.path.join(PROJECT_ROOT, "package.json")
        with open(path, encoding="utf-8") as f:
            pkg = json.load(f)
        dev_deps = pkg.get("devDependencies", {})
        deps = pkg.get("dependencies", {})
        assert "vite" in dev_deps
        assert "react" in deps
        assert "react-dom" in deps

    def test_vite_config_exists(self):
        """vite.config.js deve existir na raiz."""
        path = os.path.join(PROJECT_ROOT, "vite.config.js")
        assert os.path.isfile(path)

    def test_frontend_src_directory(self):
        """frontend/src/ deve existir com main.jsx e App.jsx."""
        src = os.path.join(PROJECT_ROOT, "frontend", "src")
        assert os.path.isdir(src)
        assert os.path.isfile(os.path.join(src, "main.jsx"))
        assert os.path.isfile(os.path.join(src, "App.jsx"))

    def test_components_directory(self):
        """frontend/src/components/ deve ter todos os componentes."""
        comp = os.path.join(PROJECT_ROOT, "frontend", "src", "components")
        expected = [
            "ErrorBoundary.jsx",
            "ChartBlock.jsx",
            "MessageBubble.jsx",
            "LoginScreen.jsx",
            "UserMenu.jsx",
            "FeedbackWidget.jsx",
            "TypingIndicator.jsx",
            "ToolBadges.jsx",
        ]
        for name in expected:
            assert os.path.isfile(os.path.join(comp, name)), f"Missing {name}"

    def test_utils_directory(self):
        """frontend/src/utils/ deve ter todos os modulos utilitarios."""
        utils = os.path.join(PROJECT_ROOT, "frontend", "src", "utils")
        expected = [
            "constants.js",
            "sanitize.js",
            "markdown.js",
            "chart.js",
            "export.js",
            "toolResults.js",
            "messageHelpers.js",
            "auth.js",
        ]
        for name in expected:
            assert os.path.isfile(os.path.join(utils, name)), f"Missing {name}"

    def test_styles_directory(self):
        """frontend/src/styles/index.css deve existir."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "styles", "index.css")
        assert os.path.isfile(path)

    def test_index_html_exists(self):
        """static/index.html deve existir (legacy na fase 1 ou bundle na fase 2)."""
        path = os.path.join(PROJECT_ROOT, "static", "index.html")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "<html" in content.lower()
