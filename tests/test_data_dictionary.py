from __future__ import annotations

import re

import pytest

import data_dictionary
import tools


def _extract_filter_value(filter_expr: str, field: str) -> str | None:
    match = re.search(rf"{field} eq '([^']*)'", filter_expr)
    if not match:
        return None
    return match.group(1).replace("''", "'")


def _fake_data_dictionary_storage(monkeypatch):
    state: dict[tuple[str, str], dict] = {}

    async def fake_table_query(_table_name, filter_expr, top=500):
        assert _table_name == "DataDictionary"
        pk = _extract_filter_value(filter_expr, "PartitionKey")
        rk = _extract_filter_value(filter_expr, "RowKey")
        pivot_value = _extract_filter_value(filter_expr, "PivotValue")
        rows = list(state.values())
        if pk is not None:
            rows = [row for row in rows if row["PartitionKey"] == pk]
        if rk is not None:
            rows = [row for row in rows if row["RowKey"] == rk]
        if pivot_value is not None:
            rows = [row for row in rows if row.get("PivotValue") == pivot_value]
        return rows[:top]

    async def fake_table_insert(_table_name, entity):
        assert _table_name == "DataDictionary"
        state[(entity["PartitionKey"], entity["RowKey"])] = dict(entity)
        return True

    async def fake_table_merge(_table_name, entity):
        assert _table_name == "DataDictionary"
        current = dict(state.get((entity["PartitionKey"], entity["RowKey"]), {}))
        current.update(entity)
        state[(entity["PartitionKey"], entity["RowKey"])] = current
        return True

    monkeypatch.setattr(data_dictionary, "table_query", fake_table_query)
    monkeypatch.setattr(data_dictionary, "table_insert", fake_table_insert)
    monkeypatch.setattr(data_dictionary, "table_merge", fake_table_merge)
    return state


class TestDataDictionary:
    def test_normalize_table_name_strips_extension(self):
        assert data_dictionary.normalize_table_name("Tbl_Contact_Detail.xlsx") == "tbl_contact_detail"

    @pytest.mark.asyncio
    async def test_save_and_retrieve_mapping(self, monkeypatch):
        _fake_data_dictionary_storage(monkeypatch)

        ok = await data_dictionary.save_mapping(
            "Tbl_Contact_Detail.xlsx",
            pivot_column="transaction_Id",
            pivot_value="871",
            column_name="campo_1",
            mapped_name="session_id",
            description="UUID da sessão",
            data_type="uuid",
            updated_by="tester",
        )
        entries = await data_dictionary.get_dictionary("Tbl_Contact_Detail.xlsx")

        assert ok is True
        assert entries == [
            {
                "pivot_value": "871",
                "column_name": "campo_1",
                "mapped_name": "session_id",
                "description": "UUID da sessão",
                "data_type": "uuid",
                "pivot_column": "transaction_Id",
            }
        ]

    @pytest.mark.asyncio
    async def test_save_mappings_batch(self, monkeypatch):
        _fake_data_dictionary_storage(monkeypatch)

        count = await data_dictionary.save_mappings_batch(
            "Tbl_Contact_Detail.xlsx",
            [
                {"pivot_value": "871", "column_name": "campo_1", "mapped_name": "session_id", "data_type": "uuid"},
                {"pivot_value": "__global__", "column_name": "channel_Id", "mapped_name": "channel_id", "data_type": "text"},
            ],
            pivot_column="transaction_Id",
            updated_by="tester",
        )
        entries = await data_dictionary.get_dictionary("Tbl_Contact_Detail.xlsx")

        assert count == 2
        assert len(entries) == 2

    def test_format_dictionary_for_prompt(self):
        formatted = data_dictionary.format_dictionary_for_prompt(
            [
                {
                    "pivot_value": "__global__",
                    "column_name": "channel_Id",
                    "mapped_name": "channel_id",
                    "description": "Canal técnico",
                    "data_type": "text",
                    "pivot_column": "transaction_Id",
                },
                {
                    "pivot_value": "871",
                    "column_name": "campo_1",
                    "mapped_name": "session_id",
                    "description": "UUID da sessão",
                    "data_type": "uuid",
                    "pivot_column": "transaction_Id",
                },
            ],
            table_name="Tbl_Contact_Detail.xlsx",
        )

        assert "Dicionário de dados" in formatted
        assert "### Mapeamentos globais" in formatted
        assert "### transaction_Id=871" in formatted
        assert "session_id" in formatted


class TestDataDictionaryTools:
    @pytest.mark.asyncio
    async def test_tool_update_data_dictionary(self, monkeypatch):
        captured = {}

        async def fake_save_mappings_batch(table_name, mappings, *, pivot_column="", updated_by=""):
            captured["table_name"] = table_name
            captured["mappings"] = mappings
            captured["pivot_column"] = pivot_column
            captured["updated_by"] = updated_by
            return 2

        monkeypatch.setattr(tools, "save_mappings_batch", fake_save_mappings_batch)

        result = await tools.tool_update_data_dictionary(
            table_name="Tbl_Contact_Detail.xlsx",
            pivot_column="transaction_Id",
            mappings=[{"pivot_value": "871", "column_name": "campo_1", "mapped_name": "session_id"}],
            conv_id="conv-1",
            user_sub="tester",
        )

        assert result["status"] == "ok"
        assert result["saved_count"] == 2
        assert captured["table_name"] == "Tbl_Contact_Detail.xlsx"
        assert captured["pivot_column"] == "transaction_Id"
        assert captured["updated_by"] == "tester"

    @pytest.mark.asyncio
    async def test_tool_get_data_dictionary(self, monkeypatch):
        async def fake_get_dictionary(table_name, pivot_value="", top=500):
            assert table_name == "Tbl_Contact_Detail.xlsx"
            assert pivot_value == ""
            assert top == 500
            return [
                {
                    "pivot_value": "871",
                    "column_name": "campo_1",
                    "mapped_name": "session_id",
                    "description": "UUID da sessão",
                    "data_type": "uuid",
                    "pivot_column": "transaction_Id",
                }
            ]

        monkeypatch.setattr(tools, "get_data_dictionary_entries", fake_get_dictionary)
        monkeypatch.setattr(
            tools,
            "format_data_dictionary_for_prompt",
            lambda entries, table_name="": f"DICT::{table_name}::{entries[0]['mapped_name']}",
        )

        result = await tools.tool_get_data_dictionary("Tbl_Contact_Detail.xlsx", conv_id="conv-1")

        assert result["status"] == "ok"
        assert result["entries_count"] == 1
        assert result["formatted"] == "DICT::Tbl_Contact_Detail.xlsx::session_id"

    def test_agent_system_prompt_mentions_polymorphic_dictionary_flow(self):
        prompt = tools.get_agent_system_prompt()

        assert "update_data_dictionary" in prompt
        assert "get_data_dictionary" in prompt
        assert "DADOS POLIMÓRFICOS" in prompt
