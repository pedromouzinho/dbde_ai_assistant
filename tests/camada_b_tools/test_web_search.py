"""Camada B — testes da tool search_web (Brave Search)."""

from __future__ import annotations

import pytest


class _MockResponse:
    def __init__(self, status_code: int, data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._data = data or {}
        self.text = text or ""

    def json(self):
        return self._data


class _MockClient:
    def __init__(self, response: _MockResponse | None = None, exc: Exception | None = None):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        _ = (exc_type, exc, tb)
        return False

    async def get(self, url, headers=None, params=None):
        _ = (url, headers, params)
        if self._exc:
            raise self._exc
        return self._response or _MockResponse(200, {})


@pytest.mark.asyncio
class TestWebSearchTool:
    async def test_search_web_parses_results(self, monkeypatch):
        import tools_knowledge

        monkeypatch.setattr(tools_knowledge, "WEB_SEARCH_ENABLED", True)
        monkeypatch.setattr(tools_knowledge, "WEB_SEARCH_API_KEY", "k-test")
        monkeypatch.setattr(tools_knowledge, "WEB_SEARCH_ENDPOINT", "https://brave.test/search")
        monkeypatch.setattr(tools_knowledge, "WEB_SEARCH_MAX_RESULTS", 5)
        monkeypatch.setattr(tools_knowledge, "WEB_SEARCH_MARKET", "pt-PT")

        payload = {
            "web": {
                "results": [
                    {"title": "DORA regulation", "url": "https://ex.com/dora", "description": "Resumo DORA"},
                    {"title": "Azure DevOps", "url": "https://ex.com/ado", "description": "Novidades"},
                ],
            }
        }
        monkeypatch.setattr(
            tools_knowledge.httpx,
            "AsyncClient",
            lambda timeout=15: _MockClient(response=_MockResponse(200, payload)),
        )

        result = await tools_knowledge.tool_search_web("dora", top=2)
        assert result["query"] == "dora"
        assert result["total_estimated"] == 2
        assert result["results_count"] == 2
        assert result["results"][0]["title"] == "DORA regulation"
        assert result["results"][0]["url"] == "https://ex.com/dora"

    async def test_search_web_requires_configuration(self, monkeypatch):
        import tools_knowledge

        monkeypatch.setattr(tools_knowledge, "WEB_SEARCH_ENABLED", True)
        monkeypatch.setattr(tools_knowledge, "WEB_SEARCH_API_KEY", "")
        result = await tools_knowledge.tool_search_web("dora", top=3)
        assert "error" in result

    async def test_search_web_rejects_empty_query(self, monkeypatch):
        import tools_knowledge

        monkeypatch.setattr(tools_knowledge, "WEB_SEARCH_ENABLED", True)
        monkeypatch.setattr(tools_knowledge, "WEB_SEARCH_API_KEY", "k-test")
        result = await tools_knowledge.tool_search_web("   ", top=3)
        assert "error" in result

    async def test_search_web_timeout_is_graceful(self, monkeypatch):
        import tools_knowledge

        monkeypatch.setattr(tools_knowledge, "WEB_SEARCH_ENABLED", True)
        monkeypatch.setattr(tools_knowledge, "WEB_SEARCH_API_KEY", "k-test")
        monkeypatch.setattr(tools_knowledge, "WEB_SEARCH_ENDPOINT", "https://brave.test/search")
        monkeypatch.setattr(
            tools_knowledge.httpx,
            "AsyncClient",
            lambda timeout=15: _MockClient(exc=TimeoutError("timeout")),
        )

        result = await tools_knowledge.tool_search_web("azure devops", top=3)
        assert "error" in result
        assert "falhou" in result["error"].lower()

    async def test_search_web_http_error_is_graceful(self, monkeypatch):
        import tools_knowledge

        monkeypatch.setattr(tools_knowledge, "WEB_SEARCH_ENABLED", True)
        monkeypatch.setattr(tools_knowledge, "WEB_SEARCH_API_KEY", "k-test")
        monkeypatch.setattr(tools_knowledge, "WEB_SEARCH_ENDPOINT", "https://brave.test/search")
        monkeypatch.setattr(
            tools_knowledge.httpx,
            "AsyncClient",
            lambda timeout=15: _MockClient(response=_MockResponse(503, text="service unavailable")),
        )

        result = await tools_knowledge.tool_search_web("azure", top=3)
        assert "error" in result
        assert "503" in result["error"]
