"""Camada B — testes de export (chart + file generation)."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestExportTools:
    async def test_generate_chart_bar(self):
        from tools_export import tool_generate_chart

        result = await tool_generate_chart(
            chart_type="bar",
            title="Bugs por estado",
            x_values=["New", "Active", "Closed"],
            y_values=[10, 5, 7],
            x_label="Estado",
            y_label="Quantidade",
        )
        assert result.get("chart_generated") is True
        assert result.get("_chart", {}).get("data")

    async def test_generate_file_csv_xlsx_pdf(self):
        from tools_export import tool_generate_file

        rows = [
            {"id": 1, "title": "São Paulo", "status": "Active"},
            {"id": 2, "title": "André ☕", "status": "New"},
        ]
        columns = ["id", "title", "status"]

        for fmt in ("csv", "xlsx", "pdf"):
            result = await tool_generate_file(format=fmt, title="Export Test", data=rows, columns=columns)
            assert result.get("file_generated") is True, f"failed for {fmt}: {result}"
            download = result.get("_file_download", {})
            assert download.get("size_bytes", 0) > 0
            assert download.get("format") == fmt

    async def test_generate_file_empty_data_handling(self):
        from tools_export import tool_generate_file

        result = await tool_generate_file(format="csv", title="Empty", data=[], columns=["a"])
        assert "error" in result

    async def test_generate_chart_empty_data_graceful(self):
        from tools_export import tool_generate_chart

        result = await tool_generate_chart(chart_type="bar", title="Vazio", x_values=[], y_values=[])
        assert result.get("chart_generated") is True
        assert result.get("data_points") == 0
