from __future__ import annotations

import pytest

import agent
import app
from tabular_loader import _infer_generic_value_type, detect_polymorphic_schema


def _polymorphic_columns() -> list[str]:
    return [
        "transaction_Id",
        "channel_Id",
        "campo_1",
        "campo_2",
        "campo_3",
        "legacy_1",
        "legacy_2",
        "legacy_3",
    ]


def _polymorphic_records() -> list[dict]:
    return [
        {
            "transaction_Id": "871",
            "channel_Id": "mobile",
            "campo_1": "550e8400-e29b-41d4-a716-446655440000",
            "campo_2": "2026-03-01T10:00:00+00:00",
            "campo_3": "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=",
            "legacy_1": "",
            "legacy_2": "",
            "legacy_3": "",
        },
        {
            "transaction_Id": "871",
            "channel_Id": "mobile",
            "campo_1": "123e4567-e89b-12d3-a456-426614174000",
            "campo_2": "2026-03-02T11:00:00+00:00",
            "campo_3": "YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo=",
            "legacy_1": "",
            "legacy_2": "",
            "legacy_3": "",
        },
        {
            "transaction_Id": "872",
            "channel_Id": "web",
            "campo_1": "100.5",
            "campo_2": "true",
            "campo_3": "",
            "legacy_1": "",
            "legacy_2": "",
            "legacy_3": "",
        },
        {
            "transaction_Id": "872",
            "channel_Id": "web",
            "campo_1": "212.3",
            "campo_2": "false",
            "campo_3": "",
            "legacy_1": "",
            "legacy_2": "",
            "legacy_3": "",
        },
    ]


def _polymorphic_column_types() -> dict[str, str]:
    return {
        "transaction_Id": "text",
        "channel_Id": "text",
        "campo_1": "text",
        "campo_2": "text",
        "campo_3": "text",
        "legacy_1": "text",
        "legacy_2": "text",
        "legacy_3": "text",
    }


class TestPolymorphicDetection:
    def test_detects_polymorphic_pattern_with_campo_columns(self):
        result = detect_polymorphic_schema(
            _polymorphic_columns(),
            _polymorphic_records(),
            _polymorphic_column_types(),
            row_count=4,
        )

        assert result is not None
        assert result["is_polymorphic"] is True
        assert result["pivot_column"] == "transaction_Id"

    def test_returns_none_for_clean_dataset(self):
        result = detect_polymorphic_schema(
            ["Date", "Revenue", "Margin"],
            [
                {"Date": "2026-01-01", "Revenue": "10", "Margin": "2"},
                {"Date": "2026-01-02", "Revenue": "20", "Margin": "3"},
            ],
            {"Date": "date", "Revenue": "numeric", "Margin": "numeric"},
            row_count=2,
        )

        assert result is None

    def test_generates_per_pivot_profiles_and_empty_columns(self):
        result = detect_polymorphic_schema(
            _polymorphic_columns(),
            _polymorphic_records(),
            _polymorphic_column_types(),
            row_count=4,
        )

        assert result is not None
        assert set(result["empty_columns"]) == {"legacy_1", "legacy_2", "legacy_3"}
        assert set(result["universal_columns"]) >= {"transaction_Id", "channel_Id"}
        assert result["pivot_profiles"]["871"]["row_count"] == 2
        assert result["pivot_profiles"]["871"]["filled_generics"]["campo_1"]["inferred_type"] == "uuid"
        assert result["pivot_profiles"]["872"]["filled_generics"]["campo_2"]["inferred_type"] == "boolean"

    def test_infers_uuid_type(self):
        inferred = _infer_generic_value_type(
            [
                "550e8400-e29b-41d4-a716-446655440000",
                "123e4567-e89b-12d3-a456-426614174000",
            ]
        )
        assert inferred == "uuid"

    def test_infers_base64_type(self):
        inferred = _infer_generic_value_type(
            [
                "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=",
                "YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo=",
            ]
        )
        assert inferred == "base64_encoded"


class TestPolymorphicUploadIntegration:
    @pytest.mark.asyncio
    async def test_extract_upload_entry_persists_polymorphic_schema(self, monkeypatch):
        preview = {
            "columns": _polymorphic_columns(),
            "row_count": 4,
            "data_text": "transaction_Id\tchannel_Id\tcampo_1",
            "delimiter": "\t",
            "col_analysis": [{"name": "campo_1", "type": "text", "sample": ["sample"]}],
            "truncated": False,
            "sample_records": _polymorphic_records(),
            "column_types": _polymorphic_column_types(),
        }
        monkeypatch.setattr(app, "load_tabular_preview", lambda *_args, **_kwargs: preview)
        monkeypatch.setattr(
            app,
            "load_tabular_dataset",
            lambda *_args, **_kwargs: {
                "columns": _polymorphic_columns(),
                "records": _polymorphic_records(),
                "row_count": 4,
            },
        )

        store_entry, result_payload = await app._extract_upload_entry("polymorphic.csv", b"csv", "text/csv")

        assert store_entry["polymorphic_schema"]["is_polymorphic"] is True
        assert store_entry["polymorphic_schema"]["pivot_column"] == "transaction_Id"
        assert result_payload["polymorphic"] is True
        assert result_payload["pivot_values_count"] == 2


class TestPolymorphicAgentContext:
    @pytest.mark.asyncio
    async def test_ensure_uploaded_files_loaded_rehydrates_polymorphic_schema(self, monkeypatch):
        conv_id = "conv-poly-load"

        async def fake_table_query(*_args, **_kwargs):
            return [
                {
                    "Filename": "Tbl_Contact_Detail.xlsx",
                    "UserSub": "tester",
                    "UploadedAt": "2026-03-08T10:00:00+00:00",
                    "PreviewText": "sample",
                    "PolymorphicSummary": "Detetado padrão polimórfico com pivot transaction_Id.",
                    "PivotColumn": "transaction_Id",
                    "PivotValuesCount": 2,
                }
            ]

        monkeypatch.setattr(agent, "table_query", fake_table_query)

        try:
            await agent._ensure_uploaded_files_loaded(conv_id, user_sub="tester")
            files = await agent._get_uploaded_files_async(conv_id)
        finally:
            agent.uploaded_files_store.pop(conv_id, None)

        assert files[0]["polymorphic_schema"]["is_polymorphic"] is True
        assert files[0]["polymorphic_schema"]["pivot_column"] == "transaction_Id"

    @pytest.mark.asyncio
    async def test_inject_file_context_includes_polymorphic_block_and_dictionary(self, monkeypatch):
        conv_id = "conv-poly-context"
        files = [
            {
                "filename": "Tbl_Contact_Detail.xlsx",
                "data_text": "amostra",
                "row_count": 4,
                "col_names": _polymorphic_columns(),
                "col_analysis": [],
                "truncated": False,
                "polymorphic_schema": {
                    "is_polymorphic": True,
                    "pivot_column": "transaction_Id",
                    "summary_text": "Detetado padrão polimórfico com 3 colunas genéricas.",
                },
            }
        ]

        async def fake_uploaded_files(_conv_id):
            return files

        async def fake_get_dictionary(_table_name, pivot_value="", top=500):
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

        monkeypatch.setattr(agent, "_get_uploaded_files_async", fake_uploaded_files)
        monkeypatch.setattr(agent, "get_dictionary", fake_get_dictionary)

        messages = []
        try:
            await agent._inject_file_context(conv_id, messages)
        finally:
            agent.conversation_meta.pop(conv_id, None)

        assert messages
        content = messages[-1]["content"]
        assert "## DATASETS POLIMÓRFICOS DETECTADOS" in content
        assert "transaction_Id=871" in content
        assert "session_id" in content
