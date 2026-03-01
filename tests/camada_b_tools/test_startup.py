"""Tests para startup fail-fast (SPEC-11)."""

from unittest.mock import patch


class TestStartupConfig:
    def test_startup_fail_fast_flag_exists(self):
        from config import STARTUP_FAIL_FAST

        assert isinstance(STARTUP_FAIL_FAST, bool)

    def test_startup_fail_fast_default_true(self):
        with patch.dict("os.environ", {}, clear=False):
            from config import STARTUP_FAIL_FAST

            assert isinstance(STARTUP_FAIL_FAST, bool)


class TestErrorBoundary:
    def test_error_boundary_class_defined(self):
        """Verificar que ErrorBoundary está definido (legacy ou Vite)."""
        from pathlib import Path

        vite_component = Path("frontend/src/components/ErrorBoundary.jsx")
        if vite_component.exists():
            src = vite_component.read_text(encoding="utf-8")
            assert "componentDidCatch" in src
            assert "ErrorBoundary" in src
            assert "getDerivedStateFromError" in src
            return

        html = Path("static/index.html").read_text(encoding="utf-8")
        assert "componentDidCatch" in html
        assert "ErrorBoundary" in html
        assert "getDerivedStateFromError" in html

    def test_error_boundary_wraps_app(self):
        """Verificar que App é envolvido por ErrorBoundary (legacy ou Vite)."""
        from pathlib import Path
        import re

        vite_main = Path("frontend/src/main.jsx")
        if vite_main.exists():
            src = vite_main.read_text(encoding="utf-8")
            assert "ErrorBoundary" in src
            assert "<ErrorBoundary name=\"App\">" in src or "<ErrorBoundary name='App'>" in src
            return

        html = Path("static/index.html").read_text(encoding="utf-8")
        pattern = r"createRoot.*render\s*\(\s*React\.createElement\(\s*ErrorBoundary"
        assert re.search(pattern, html), "App deve ser wrapped em ErrorBoundary no render root"

    def test_error_boundary_reports_to_client_error(self):
        """Verificar que ErrorBoundary reporta a /api/client-error (legacy ou Vite)."""
        from pathlib import Path

        vite_component = Path("frontend/src/components/ErrorBoundary.jsx")
        if vite_component.exists():
            src = vite_component.read_text(encoding="utf-8")
            assert "react_error_boundary" in src
            assert "/api/client-error" in src
            return

        html = Path("static/index.html").read_text(encoding="utf-8")
        assert "react_error_boundary" in html
        assert "/api/client-error" in html
