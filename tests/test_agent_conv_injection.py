from __future__ import annotations

import asyncio

import pytest

import agent
from models import AgentChatRequest, LLMToolCall


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


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["update_data_dictionary", "get_data_dictionary"])
async def test_dictionary_tools_receive_conv_context(monkeypatch, tool_name):
    captured = {}

    async def fake_execute_tool(name, args):
        captured["name"] = name
        captured["args"] = dict(args)
        return {"status": "ok"}

    async def fake_uploaded_files(_conv_id):
        return []

    monkeypatch.setattr(agent, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(agent, "_get_uploaded_files_async", fake_uploaded_files)

    conv_id = f"conv-{tool_name}"
    agent.conversations[conv_id] = [{"role": "user", "content": "usa o dicionário"}]
    try:
        tool_call = LLMToolCall(id="tool-dict", name=tool_name, arguments={"table_name": "sample.csv"})
        await agent._execute_tool_calls([tool_call], conv_id=conv_id, user_sub="user-123")
    finally:
        agent.conversations.pop(conv_id, None)

    assert captured["name"] == tool_name
    assert captured["args"]["conv_id"] == conv_id
    assert captured["args"]["user_sub"] == "user-123"


@pytest.mark.asyncio
async def test_execute_tool_calls_handles_non_dict_results(monkeypatch):
    async def fake_execute_tool(_name, _args):
        return "plain-text-result"

    async def fake_blob_upload_json(_container, _blob_name, payload):
        assert payload == "plain-text-result"
        return {"blob_ref": "blob://tool-result"}

    monkeypatch.setattr(agent, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(agent, "blob_upload_json", fake_blob_upload_json)
    monkeypatch.setattr(agent, "PII_ENABLED", False)

    conv_id = "conv-nondict-result"
    agent.conversations[conv_id] = [{"role": "user", "content": "test"}]
    try:
        tool_call = LLMToolCall(id="tool-2", name="chart_uploaded_table", arguments={})
        _, tool_details = await agent._execute_tool_calls([tool_call], conv_id=conv_id, user_sub="user-123")
    finally:
        agent.conversations.pop(conv_id, None)

    assert tool_details[0]["result_summary"] == {
        "total_count": "N/A",
        "items_returned": 0,
        "has_error": False,
    }


@pytest.mark.asyncio
async def test_mask_pii_structured_preserves_json_shape(monkeypatch):
    async def fake_mask_pii(text, _ctx):
        return text.replace("João Silva", "[MASKED_PERSON]")

    monkeypatch.setattr(agent, "mask_pii", fake_mask_pii)

    masked = await agent._mask_pii_structured(
        {"items": [{"name": "João Silva", "count": 42}], "ok": True},
        agent.PIIMaskingContext(),
    )

    assert masked == {"items": [{"name": "[MASKED_PERSON]", "count": 42}], "ok": True}


@pytest.mark.asyncio
async def test_switch_conversation_mode_waits_for_conversation_lock():
    conv_id = "conv-switch-lock"
    agent.conversations[conv_id] = [
        {"role": "system", "content": "old"},
        {"role": "user", "content": "hello"},
    ]
    agent.conversation_meta[conv_id] = {"mode": "general"}
    lock = await agent._get_conversation_lock(conv_id)

    try:
        async with lock:
            switch_task = asyncio.create_task(agent.switch_conversation_mode(conv_id, "userstory"))
            await asyncio.sleep(0)
            assert not switch_task.done()

        assert await switch_task is True
        assert agent.conversation_meta[conv_id]["mode"] == "userstory"
        assert agent.conversations[conv_id][0]["role"] == "system"
        assert agent.conversations[conv_id][0]["content"] == agent.get_userstory_system_prompt()
    finally:
        agent.conversations.pop(conv_id, None)
        agent.conversation_meta.pop(conv_id, None)
        agent._conversation_locks.pop(conv_id, None)


@pytest.mark.asyncio
async def test_agent_chat_returns_generic_error_on_timeout(monkeypatch):
    async def fake_ensure_conversation(conv_id, _mode, _partition_key):
        agent.conversations[conv_id] = [{"role": "system", "content": "system"}]
        return conv_id

    async def fake_noop(*_args, **_kwargs):
        return None

    async def fake_build_llm_messages(*_args, **_kwargs):
        return [{"role": "system", "content": "system"}]

    async def fake_llm_with_fallback(*_args, **_kwargs):
        raise asyncio.TimeoutError("sensitive/path/secret")

    monkeypatch.setattr(agent, "_ensure_conversation", fake_ensure_conversation)
    monkeypatch.setattr(agent, "_ensure_uploaded_files_loaded", fake_noop)
    monkeypatch.setattr(agent, "_inject_file_context", fake_noop)
    monkeypatch.setattr(agent, "_build_llm_messages", fake_build_llm_messages)
    monkeypatch.setattr(agent, "llm_with_fallback", fake_llm_with_fallback)
    monkeypatch.setattr(agent, "get_all_tool_definitions", lambda: [])

    request = AgentChatRequest(question="olá", conversation_id="conv-timeout")
    try:
        response = await agent.agent_chat(request, {"sub": "tester"})
    finally:
        agent.conversations.pop("conv-timeout", None)
        agent.conversation_meta.pop("conv-timeout", None)
        agent._conversation_locks.pop("conv-timeout", None)

    assert response.answer == "Ocorreu um erro inesperado. Por favor tenta novamente."
    assert "sensitive/path/secret" not in response.answer


@pytest.mark.asyncio
async def test_refresh_polymorphic_pending_state_sets_pending_selection(monkeypatch):
    conv_id = "conv-poly-pending"

    async def fake_uploaded_files(_conv_id):
        return [
            {
                "filename": "Tbl_Contact_Detail.xlsx",
                "polymorphic_schema": {
                    "is_polymorphic": True,
                    "pivot_column": "transaction_Id",
                    "pivot_profiles": {
                        "871": {"row_count": 2},
                        "872": {"row_count": 2},
                        "873": {"row_count": 2},
                    },
                },
            }
        ]

    monkeypatch.setattr(agent, "_get_uploaded_files_async", fake_uploaded_files)

    try:
        await agent._refresh_polymorphic_pending_state(
            conv_id,
            "Analisa novamente os dados deste dataset",
            "Indica qual transaction_Id queres analisar: 871, 872, 873.",
        )
        meta = await agent._get_conversation_meta(conv_id)
    finally:
        agent.conversation_meta.pop(conv_id, None)

    pending = meta.get(agent._PENDING_POLYMORPHIC_SELECTION_KEY)
    assert pending["pivot_column"] == "transaction_Id"
    assert pending["table_name"] == "Tbl_Contact_Detail.xlsx"
    assert pending["available_values"] == ["871", "872", "873"]


@pytest.mark.asyncio
async def test_prepare_polymorphic_followup_question_rewrites_short_reply():
    conv_id = "conv-poly-followup"
    await agent._set_conversation_meta(
        conv_id,
        {
            agent._PENDING_POLYMORPHIC_SELECTION_KEY: {
                "table_name": "Tbl_Contact_Detail.xlsx",
                "pivot_column": "transaction_Id",
                "available_values": ["871", "872", "873"],
                "original_question": "Analisa novamente os dados do dataset",
            }
        },
    )

    try:
        rewritten, clarification = await agent._prepare_polymorphic_followup_question(conv_id, "871")
        meta = await agent._get_conversation_meta(conv_id)
    finally:
        agent.conversation_meta.pop(conv_id, None)

    assert clarification == ""
    assert "transaction_Id=871" in rewritten
    assert meta.get(agent._PENDING_POLYMORPHIC_SELECTION_KEY) is None


@pytest.mark.asyncio
async def test_prepare_polymorphic_followup_question_reprompts_invalid_short_reply():
    conv_id = "conv-poly-invalid"
    await agent._set_conversation_meta(
        conv_id,
        {
            agent._PENDING_POLYMORPHIC_SELECTION_KEY: {
                "table_name": "Tbl_Contact_Detail.xlsx",
                "pivot_column": "transaction_Id",
                "available_values": ["871", "872"],
                "original_question": "Analisa novamente os dados do dataset",
            }
        },
    )

    try:
        rewritten, clarification = await agent._prepare_polymorphic_followup_question(conv_id, "999")
    finally:
        agent.conversation_meta.pop(conv_id, None)

    assert rewritten == ""
    assert "transaction_Id" in clarification
    assert "871" in clarification
