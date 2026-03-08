from __future__ import annotations

import pytest

import agent
from models import LLMToolCall


@pytest.mark.asyncio
async def test_chart_uploaded_table_receives_conv_context(monkeypatch):
    captured = {}

    async def fake_execute_tool(name, args):
        captured["name"] = name
        captured["args"] = dict(args)
        return {"status": "ok"}

    async def fake_uploaded_files(_conv_id):
        return []

    monkeypatch.setattr(agent, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(agent, "_get_uploaded_files_async", fake_uploaded_files)

    conv_id = "conv-chart-injection"
    agent.conversations[conv_id] = [{"role": "user", "content": "faz um gráfico deste ficheiro"}]
    try:
        tool_call = LLMToolCall(id="tool-1", name="chart_uploaded_table", arguments={})
        await agent._execute_tool_calls([tool_call], conv_id=conv_id, user_sub="user-123")
    finally:
        agent.conversations.pop(conv_id, None)

    assert captured["name"] == "chart_uploaded_table"
    assert captured["args"]["conv_id"] == conv_id
    assert captured["args"]["user_sub"] == "user-123"
