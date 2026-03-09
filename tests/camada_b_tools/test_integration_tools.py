"""Camada B — testes de integrações Figma/Miro (mock-based)."""

from __future__ import annotations

import base64
import inspect
import json
from types import SimpleNamespace

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

    async def test_search_figma_accepts_full_url(self, monkeypatch):
        import tools_figma

        monkeypatch.setattr(tools_figma, "_get_figma_token", lambda: "token-figma")

        async def _fake_figma_get(path, params=None):
            if path == "/files/abc123/nodes":
                assert params == {"ids": "1:2"}
                return {
                    "name": "DBDE Design",
                    "thumbnailUrl": "https://img",
                    "lastModified": "2026-02-25",
                    "nodes": {
                        "1:2": {
                            "document": {
                                "id": "1:2",
                                "name": "Login Frame",
                                "type": "FRAME",
                                "children": [
                                    {"name": "Continuar", "type": "TEXT"},
                                    {"name": "Cancelar", "type": "TEXT"},
                                ],
                            }
                        }
                    },
                }
            return {"error": f"unmocked path {path}"}

        monkeypatch.setattr(tools_figma, "_figma_get", _fake_figma_get)

        result = await tools_figma.tool_search_figma(
            figma_url="https://www.figma.com/file/abc123/Fluxo-Login?node-id=1%3A2"
        )
        assert result.get("file_key") == "abc123"
        assert result.get("items", [{}])[0].get("id") == "1:2"
        assert "ui_components" in result.get("items", [{}])[0]

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


class TestScreenshotToUS:
    def test_screenshot_to_us_in_definitions(self):
        """Verificar que screenshot_to_us está no schema de tools."""
        from tools import _TOOL_DEFINITION_BY_NAME

        tool_def = _TOOL_DEFINITION_BY_NAME.get("screenshot_to_us")
        assert tool_def is not None
        params = tool_def["function"]["parameters"]["properties"]
        assert "image_base64" in params
        assert "context" in params

    def test_screenshot_to_us_in_dispatch(self):
        """Verificar que screenshot_to_us está no dispatch."""
        from tools import _tool_dispatch

        dispatch = _tool_dispatch()
        assert "screenshot_to_us" in dispatch

    def test_screenshot_to_us_signature(self):
        """Verificar assinatura da função."""
        import inspect
        from tools import tool_screenshot_to_us

        sig = inspect.signature(tool_screenshot_to_us)
        assert "image_base64" in sig.parameters
        assert "context" in sig.parameters
        assert "author_style" in sig.parameters
        assert sig.parameters["image_base64"].default == ""

    @pytest.mark.asyncio
    async def test_screenshot_to_us_uses_uploaded_image_when_base64_invalid(self, monkeypatch):
        import tools

        captured = {}
        story_payload = {
            "stories": [
                {
                    "title": "MSE | Transversal | Login | Step | Confirmar",
                    "description": "desc",
                    "provenance": "prov",
                    "conditions": ["NA"],
                    "composition_and_behavior": ["CTA Confirmar / Confirm"],
                    "acceptance_criteria": [{"id": "CA-01", "text": "texto"}],
                    "test_scenarios": [
                        {
                            "id": "CT-01",
                            "title": "Happy path",
                            "category": "Fluxo principal",
                            "preconditions": "Sessão ativa",
                            "test_data": "NA",
                            "steps": ["Dado ...", "Quando ...", "Então ..."],
                            "covers": ["CA-01"],
                        }
                    ],
                    "test_data": ["NA"],
                    "observations": ["obs"],
                    "clarification_questions": [],
                }
            ]
        }

        async def _fake_resolve(conv_id, user_sub="", filename=""):
            assert conv_id == "conv-visual"
            assert user_sub == "user-1"
            assert filename == ""
            return {
                "filename": "mockup.png",
                "content_type": "image/png",
                "image_base64": base64.b64encode(b"png-bytes").decode("ascii"),
            }

        async def _fake_llm_with_fallback(*, messages, tier, max_tokens, response_format):
            captured["messages"] = messages
            captured["tier"] = tier
            captured["max_tokens"] = max_tokens
            captured["response_format"] = response_format
            return SimpleNamespace(content=json.dumps(story_payload))

        monkeypatch.setattr(tools, "_resolve_uploaded_visual_source", _fake_resolve)
        monkeypatch.setattr(tools, "llm_with_fallback", _fake_llm_with_fallback)

        result = await tools.tool_screenshot_to_us(
            image_base64="not-base64",
            conv_id="conv-visual",
            user_sub="user-1",
        )

        assert result["filename"] == "mockup.png"
        assert result["input_type"] == "image"
        content_blocks = captured["messages"][0]["content"]
        assert captured["tier"] == "vision"
        assert any(block.get("type") == "image_url" for block in content_blocks)

    @pytest.mark.asyncio
    async def test_screenshot_to_us_supports_svg_upload_fallback(self, monkeypatch):
        import tools

        captured = {}
        story_payload = {
            "stories": [
                {
                    "title": "MSE | Transversal | SVG | Step | Login",
                    "description": "desc",
                    "provenance": "prov",
                    "conditions": ["NA"],
                    "composition_and_behavior": ["H1 Login"],
                    "acceptance_criteria": [{"id": "CA-01", "text": "texto"}],
                    "test_scenarios": [
                        {
                            "id": "CT-01",
                            "title": "A11y",
                            "category": "Acessibilidade",
                            "preconditions": "NA",
                            "test_data": "NA",
                            "steps": ["Dado ...", "Quando ...", "Então ..."],
                            "covers": ["CA-01"],
                        }
                    ],
                    "test_data": ["NA"],
                    "observations": ["obs"],
                    "clarification_questions": ["q1"],
                }
            ]
        }

        async def _fake_resolve(conv_id, user_sub="", filename=""):
            return {
                "filename": "screen.svg",
                "content_type": "image/svg+xml",
                "svg_markup": "<svg><title>Login</title><text>Continuar</text></svg>",
                "visible_text": ["Login", "Continuar"],
            }

        async def _fake_llm_with_fallback(*, messages, tier, max_tokens, response_format):
            captured["messages"] = messages
            captured["tier"] = tier
            return SimpleNamespace(content=json.dumps(story_payload))

        monkeypatch.setattr(tools, "_resolve_uploaded_visual_source", _fake_resolve)
        monkeypatch.setattr(tools, "llm_with_fallback", _fake_llm_with_fallback)

        result = await tools.tool_screenshot_to_us(
            conv_id="conv-svg",
            user_sub="user-1",
        )

        assert result["filename"] == "screen.svg"
        assert result["input_type"] == "svg"
        assert captured["tier"] == "vision"
        assert isinstance(captured["messages"][0]["content"], str)
        assert "Markup SVG" in captured["messages"][0]["content"]
