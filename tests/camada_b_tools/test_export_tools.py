"""Camada B — testes de export (chart + file generation)."""

from __future__ import annotations

import io
import zipfile

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

    async def test_generate_file_csv_has_utf8_bom(self):
        from tools_export import get_generated_file, tool_generate_file

        rows = [
            {"id": 1, "title": "São Paulo", "status": "Active"},
            {"id": 2, "title": "André ☕", "status": "New"},
        ]
        columns = ["id", "title", "status"]

        result = await tool_generate_file(format="csv", title="Export Test", data=rows, columns=columns)
        assert result.get("file_generated") is True, result
        download_id = result.get("_file_download", {}).get("download_id")
        assert download_id

        entry = await get_generated_file(download_id)
        assert entry and entry.get("content")
        assert entry["content"][:3] == b"\xef\xbb\xbf"

    async def test_generate_file_xlsx_has_header_bold_and_zebra(self):
        openpyxl = pytest.importorskip("openpyxl")
        from tools_export import get_generated_file, tool_generate_file

        rows = [
            {"id": 1, "title": "US A", "status": "Active"},
            {"id": 2, "title": "US B", "status": "New"},
            {"id": 3, "title": "US C", "status": "Closed"},
        ]
        result = await tool_generate_file(format="xlsx", title="Export Test", data=rows, columns=["id", "title", "status"])
        assert result.get("file_generated") is True, result
        download_id = result.get("_file_download", {}).get("download_id")
        assert download_id

        entry = await get_generated_file(download_id)
        wb = openpyxl.load_workbook(io.BytesIO(entry["content"]))
        ws = wb.active
        assert ws["A4"].font.bold is True
        assert ws["A6"].fill.fill_type == "solid"

    async def test_generate_file_pdf_uses_brand_color(self, monkeypatch):
        import export_engine
        from tools_export import tool_generate_file

        seen: dict[str, str] = {}
        original_hex_to_rgb = export_engine._hex_to_rgb

        def _spy_hex_to_rgb(hex_color: str, fallback):
            seen["hex"] = hex_color
            return original_hex_to_rgb(hex_color, fallback)

        monkeypatch.setattr(export_engine, "EXPORT_BRAND_COLOR", "#13579B", raising=False)
        monkeypatch.setattr(export_engine, "_hex_to_rgb", _spy_hex_to_rgb)

        result = await tool_generate_file(
            format="pdf",
            title="Export Test",
            data=[{"id": 1, "title": "US", "status": "Active"}],
            columns=["id", "title", "status"],
        )
        assert result.get("file_generated") is True, result
        assert seen.get("hex") == "#13579B"

    async def test_generate_file_docx_output(self):
        from tools_export import get_generated_file, tool_generate_file

        rows = [
            {"id": 1, "title": "São Paulo", "status": "Active"},
            {"id": 2, "title": "André ☕", "status": "New"},
        ]
        result = await tool_generate_file(format="docx", title="Export Test", data=rows, columns=["id", "title", "status"])
        assert result.get("file_generated") is True, result
        download = result.get("_file_download", {})
        assert download.get("format") == "docx"
        assert download.get("mime_type") == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

        download_id = download.get("download_id")
        assert download_id
        entry = await get_generated_file(download_id)
        assert entry and entry.get("content")
        with zipfile.ZipFile(io.BytesIO(entry["content"])) as zf:
            assert "word/document.xml" in zf.namelist()

    async def test_generate_file_html_output(self):
        from tools_export import get_generated_file, tool_generate_file

        rows = [{"id": 1, "title": "US A", "status": "Active"}]
        result = await tool_generate_file(format="html", title="Export Test", data=rows, columns=["id", "title", "status"])
        assert result.get("file_generated") is True, result
        download = result.get("_file_download", {})
        assert download.get("format") == "html"
        assert download.get("mime_type") == "text/html"

        entry = await get_generated_file(download.get("download_id"))
        html = entry["content"].decode("utf-8", errors="replace")
        assert "<html" in html.lower()
        assert "Export Test" in html

    async def test_generate_file_invalid_format_lists_supported(self):
        from tools_export import tool_generate_file

        rows = [{"id": 1, "title": "US A"}]
        result = await tool_generate_file(format="xyz", title="X", data=rows, columns=["id", "title"])
        assert "error" in result
        assert "csv" in result["error"]
        assert "html" in result["error"]

    async def test_generate_file_rows_cap_metadata(self, monkeypatch):
        import tools_export
        from tools_export import tool_generate_file

        monkeypatch.setattr(tools_export, "EXPORT_FILE_ROW_CAP", 2)
        monkeypatch.setattr(tools_export, "EXPORT_FILE_ROW_CAP_MAX", 100)

        rows = [
            {"id": 1, "title": "A"},
            {"id": 2, "title": "B"},
            {"id": 3, "title": "C"},
        ]
        result = await tool_generate_file(format="csv", title="Cap", data=rows, columns=["id", "title"])
        assert result.get("file_generated") is True, result
        assert result.get("rows") == 2
        assert result.get("rows_total") == 3
        assert result.get("rows_capped") is True
        assert "cap_warning" in result

    async def test_generate_file_empty_data_handling(self):
        from tools_export import tool_generate_file

        result = await tool_generate_file(format="csv", title="Empty", data=[], columns=["a"])
        assert "error" in result

    async def test_generate_chart_empty_data_rejected(self):
        from tools_export import tool_generate_chart

        result = await tool_generate_chart(chart_type="bar", title="Vazio", x_values=[], y_values=[])
        assert result.get("chart_generated") is False
        assert "error" in result
