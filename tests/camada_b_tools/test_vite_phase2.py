"""Tests para Vite Phase 2 — build integration + CSP (SPEC-24)."""

import os
import inspect


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestViteBuildIntegration:
    def test_index_html_no_babel(self):
        """static/index.html nao deve conter type='text/babel'."""
        path = os.path.join(PROJECT_ROOT, "static", "index.html")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert 'type="text/babel"' not in content
        assert "text/babel" not in content

    def test_babel_vendor_removed(self):
        """static/vendor/babel.min.js nao deve existir."""
        path = os.path.join(PROJECT_ROOT, "static", "vendor", "babel.min.js")
        assert not os.path.exists(path)

    def test_csp_no_unsafe_eval(self):
        """CSP header nao deve conter unsafe-eval."""
        import app as app_module

        src = inspect.getsource(app_module)
        assert "unsafe-eval" not in src

    def test_plotly_vendor_still_exists(self):
        """static/vendor/plotly.min.js deve continuar a existir (fallback CDN)."""
        path = os.path.join(PROJECT_ROOT, "static", "vendor", "plotly.min.js")
        assert os.path.isfile(path)
