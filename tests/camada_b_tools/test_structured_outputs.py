"""Tests para Structured Outputs (response_format + schemas)."""

from __future__ import annotations

import pytest

from models import LLMResponse
from structured_schemas import (
    SPRINT_ANALYSIS_SCHEMA,
    USER_STORY_SCHEMA,
    SCREENSHOT_USER_STORIES_SCHEMA,
)


def test_sprint_schema_valid():
    schema = SPRINT_ANALYSIS_SCHEMA
    assert schema["type"] == "json_schema"
    assert schema["json_schema"]["strict"] is True
    required = schema["json_schema"]["schema"]["required"]
    assert "sprint_name" in required
    assert "health" in required


def test_user_story_schema_valid():
    schema = USER_STORY_SCHEMA
    props = schema["json_schema"]["schema"]["properties"]
    assert "acceptance_criteria" in props
    assert "test_scenarios" in props
    assert props["test_scenarios"]["items"]["required"] == ["given", "when", "then"]


def test_screenshot_schema_shape():
    schema = SCREENSHOT_USER_STORIES_SCHEMA
    js = schema["json_schema"]["schema"]
    assert js["additionalProperties"] is False
    assert "stories" in js["properties"]
    story_item = js["properties"]["stories"]["items"]
    assert set(story_item["required"]) == {"title", "description", "acceptance_criteria"}


@pytest.mark.asyncio
async def test_llm_with_fallback_forwards_response_format(monkeypatch):
    import llm_provider

    captured = {}

    class DummyProvider:
        name = "dummy"

        async def chat(self, messages, tools=None, temperature=None, max_tokens=None, response_format=None, **kwargs):
            _ = messages, temperature, max_tokens, kwargs
            captured["tools"] = tools
            captured["response_format"] = response_format
            return LLMResponse(
                content='{"ok":true}',
                tool_calls=None,
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                model="dummy",
                provider="dummy",
            )

    dummy = DummyProvider()
    monkeypatch.setattr(llm_provider, "get_provider", lambda tier=None: dummy)
    monkeypatch.setattr(llm_provider, "get_fallback_provider", lambda: dummy)

    fmt = {
        "type": "json_schema",
        "json_schema": {
            "name": "simple",
            "strict": True,
            "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False},
        },
    }
    result = await llm_provider.llm_with_fallback(
        messages=[{"role": "user", "content": "test"}],
        tier="standard",
        response_format=fmt,
    )
    assert captured["response_format"] == fmt
    assert result.content == '{"ok":true}'


@pytest.mark.asyncio
async def test_azure_chat_adds_response_format_without_tools(monkeypatch):
    import llm_provider

    class FakeResponse:
        status_code = 200
        headers = {}

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "model": "gpt-4.1",
            }

    payloads = []

    class FakeClient:
        async def post(self, url, json=None, headers=None):
            _ = url, headers
            payloads.append(json or {})
            return FakeResponse()

    provider = llm_provider.AzureOpenAIProvider(deployment="gpt-4.1")
    monkeypatch.setattr(provider, "_get_client", lambda: FakeClient())

    fmt = {
        "type": "json_schema",
        "json_schema": {
            "name": "simple",
            "strict": True,
            "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False},
        },
    }
    await provider.chat(
        messages=[{"role": "user", "content": "test"}],
        tools=None,
        response_format=fmt,
    )
    assert payloads[-1].get("response_format") == fmt

    tool_def = {
        "type": "function",
        "function": {
            "name": "demo_tool",
            "description": "demo",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    await provider.chat(
        messages=[{"role": "user", "content": "test"}],
        tools=[tool_def],
        response_format=fmt,
    )
    assert "response_format" not in payloads[-1]

