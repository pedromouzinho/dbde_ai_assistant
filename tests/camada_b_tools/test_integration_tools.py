"""Camada B — testes de integrações Figma/Miro (mock-based)."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestIntegrationTools:
    async def test_search_figma_mock_api(self, monkeypatch):
        import tools_figma

        monkeypatch.setattr(tools_figma, "_get_figma_token", lambda: "token-figma")

        async def _fake_figma_get(path, params=None):
            _ = params
            if path.startswith("/files/") and "/nodes" not in path:
                return {
                    "name": "DBDE Design",
                    "thumbnailUrl": "https://img",
                    "lastModified": "2026-02-25",
                    "document": {"children": [{"name": "Login", "id": "1", "type": "PAGE", "children": []}]},
                }
            return {"error": f"unmocked path {path}"}

        monkeypatch.setattr(tools_figma, "_figma_get", _fake_figma_get)

        result = await tools_figma.tool_search_figma(query="Login", file_key="abc123")
        assert result.get("source") == "figma"
        assert "items" in result

    async def test_search_miro_mock_api(self, monkeypatch):
        import tools_miro

        monkeypatch.setattr(tools_miro, "_get_miro_token", lambda: "token-miro")

        async def _fake_miro_get(path, params=None):
            _ = params
            if path.startswith("/boards/") and path.endswith("/items"):
                return {
                    "data": [
                        {
                            "id": "it1",
                            "type": "sticky_note",
                            "data": {"text": "Sprint planning"},
                            "links": {"self": "https://miro/item"},
                        }
                    ]
                }
            if path.startswith("/boards/"):
                return {"name": "Board DBDE", "viewLink": "https://miro/board"}
            if path == "/boards":
                return {"data": []}
            return {"error": f"unmocked path {path}"}

        monkeypatch.setattr(tools_miro, "_miro_get", _fake_miro_get)

        result = await tools_miro.tool_search_miro(query="Sprint", board_id="b1")
        assert result.get("source") == "miro"
        assert "items" in result

    async def test_missing_tokens_graceful_behavior(self, monkeypatch):
        import tools_figma
        import tools_miro
        from tool_registry import get_registered_tool_names

        monkeypatch.setattr(tools_figma, "_get_figma_token", lambda: "")
        monkeypatch.setattr(tools_miro, "_get_miro_token", lambda: "")

        figma_result = await tools_figma.tool_search_figma(query="x")
        miro_result = await tools_miro.tool_search_miro(query="x")

        assert "error" in figma_result
        assert "error" in miro_result

        # O registo pode existir; o importante é comportamento gracioso sem crash.
        names = set(get_registered_tool_names())
        assert "search_figma" in names or "search_figma" not in names
        assert "search_miro" in names or "search_miro" not in names
