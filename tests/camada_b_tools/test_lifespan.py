"""Tests para FastAPI lifespan migration (SPEC-32)."""

import inspect


class TestLifespan:
    def test_no_on_event_decorators(self):
        """app.py nao deve usar @app.on_event (deprecated)."""
        import app as app_module

        src = inspect.getsource(app_module)
        assert 'on_event("startup")' not in src, "Still using deprecated on_event('startup')"
        assert 'on_event("shutdown")' not in src, "Still using deprecated on_event('shutdown')"
        assert "on_event('startup')" not in src
        assert "on_event('shutdown')" not in src

    def test_lifespan_exists(self):
        """app.py deve ter funcao lifespan."""
        import app as app_module

        assert hasattr(app_module, "lifespan") or "async def lifespan" in inspect.getsource(app_module)
