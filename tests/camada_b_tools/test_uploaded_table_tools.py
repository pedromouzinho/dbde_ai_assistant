"""Camada B — testes da análise tabular em ficheiros carregados."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestUploadedTableTools:
    async def test_analyze_uploaded_table_group_by_year_mean(self, monkeypatch):
        import tools_upload

        csv_payload = (
            "Time,Close,TLT_Volume\n"
            "2020-01-01 09:30:00,50,100\n"
            "2020-01-02 09:30:00,60,300\n"
            "2021-01-01 09:30:00,70,500\n"
            "2021-01-02 09:30:00,80,700\n"
        ).encode("utf-8")

        async def _fake_table_query(table_name, query, top=100):
            _ = (table_name, query, top)
            return [
                {
                    "PartitionKey": "conv-1",
                    "RowKey": "job-1",
                    "UserSub": "user-1",
                    "Filename": "sample.csv",
                    "UploadedAt": "2026-02-27T10:00:00+00:00",
                    "RawBlobRef": "blob://uploads-raw/conv-1/job-1/raw/sample.csv",
                    "FullColStatsJson": '[{"name":"Close","type":"numeric"},{"name":"TLT_Volume","type":"numeric"}]',
                }
            ]

        async def _fake_blob_download_bytes(container, blob_name):
            _ = (container, blob_name)
            return csv_payload

        def _fake_parse_blob_ref(raw_ref):
            _ = raw_ref
            return "uploads-raw", "conv-1/job-1/raw/sample.csv"

        monkeypatch.setattr(tools_upload, "table_query", _fake_table_query)
        monkeypatch.setattr(tools_upload, "blob_download_bytes", _fake_blob_download_bytes)
        monkeypatch.setattr(tools_upload, "parse_blob_ref", _fake_parse_blob_ref)

        result = await tools_upload.tool_analyze_uploaded_table(
            query="volume médio de TLT por ano",
            conv_id="conv-1",
            user_sub="user-1",
            value_column="TLT_Volume",
            date_column="Time",
            group_by="year",
            agg="mean",
        )

        assert result.get("source") == "upload_table"
        assert result.get("group_by") == "year"
        assert result.get("value_column") == "TLT_Volume"
        assert result.get("groups") == [
            {"group": "2020", "value": 200.0, "count": 2},
            {"group": "2021", "value": 600.0, "count": 2},
        ]
        chart_ready = result.get("chart_ready") or {}
        assert chart_ready.get("x_values") == ["2020", "2021"]
        assert chart_ready.get("y_values") == [200.0, 600.0]

    async def test_analyze_uploaded_table_requires_conv_id(self):
        from tools_upload import tool_analyze_uploaded_table

        result = await tool_analyze_uploaded_table(query="média por ano", conv_id="")
        assert "error" in result
