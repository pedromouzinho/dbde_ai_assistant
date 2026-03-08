from __future__ import annotations

import io
import types

import openpyxl
import pytest

import app
import tabular_loader


def _sample_csv_bytes() -> bytes:
    return (
        "Date;Category;Revenue;Margin\n"
        "2026-01-01;A;10;2\n"
        "2026-01-02;B;20;3\n"
        "2026-01-03;A;30;4\n"
    ).encode("utf-8")


def _sample_xlsx_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Date", "Category", "Revenue"])
    sheet.append(["2026-01-01", "A", 10])
    sheet.append(["2026-01-02", "B", 20])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class TestTabularLoader:
    def test_csv_preview_detects_columns_types_and_row_count(self):
        preview = tabular_loader.load_tabular_preview(_sample_csv_bytes(), "sample.csv")

        assert preview["columns"] == ["Date", "Category", "Revenue", "Margin"]
        assert preview["row_count"] == 3
        assert preview["delimiter"] == ";"
        assert preview["column_types"]["Revenue"] == "numeric"
        assert preview["column_types"]["Date"] == "date"
        assert "2026-01-01" in preview["data_text"]

    def test_xlsx_dataset_reads_records(self):
        dataset = tabular_loader.load_tabular_dataset(_sample_xlsx_bytes(), "sample.xlsx")

        assert dataset["row_count"] == 2
        assert dataset["rows_loaded"] == 2
        assert dataset["columns"] == ["Date", "Category", "Revenue"]
        assert dataset["records"][0]["Revenue"] == "10"

    def test_xlsb_preview_uses_reader_without_materializing_everything(self, monkeypatch):
        pytest.importorskip("pyxlsb")

        class _Cell:
            def __init__(self, value):
                self.v = value

        class _Sheet:
            def rows(self):
                yield [_Cell("Date"), _Cell("Revenue")]
                yield [_Cell("2026-01-01"), _Cell(10)]
                yield [_Cell("2026-01-02"), _Cell(20)]

        class _Workbook:
            def get_sheet(self, idx):
                assert idx == 1
                return _Sheet()

            def close(self):
                return None

        monkeypatch.setattr("pyxlsb.open_workbook", lambda _path: _Workbook())
        preview = tabular_loader.load_tabular_preview(b"PK\x03\x04fake", "sample.xlsb")

        assert preview["columns"] == ["Date", "Revenue"]
        assert preview["row_count"] == 2
        assert preview["column_types"]["Revenue"] == "numeric"


class TestUploadLimitsAndExtraction:
    def test_tabular_upload_limits_are_extension_specific(self):
        csv_limit = app._max_upload_bytes_for_file("emails.csv")
        xlsx_limit = app._max_upload_bytes_for_file("report.xlsx")
        xlsb_limit = app._max_upload_bytes_for_file("report.xlsb")

        assert csv_limit > app.MAX_UPLOAD_FILE_BYTES
        assert xlsx_limit > app.MAX_UPLOAD_FILE_BYTES
        assert xlsb_limit >= xlsx_limit

    @pytest.mark.asyncio
    async def test_extract_upload_entry_accepts_xlsb(self, monkeypatch):
        monkeypatch.setattr(
            app,
            "load_tabular_preview",
            lambda _content, _filename: {
                "columns": ["Date", "Revenue"],
                "row_count": 2,
                "data_text": "Date\tRevenue\n2026-01-01\t10",
                "delimiter": "\t",
                "col_analysis": [{"name": "Revenue", "type": "numeric", "sample": ["10"]}],
                "truncated": False,
            },
        )

        store_entry, result_payload = await app._extract_upload_entry(
            "sample.xlsb",
            b"PK\x03\x04fake-content",
            "application/vnd.ms-excel.sheet.binary.macroenabled.12",
        )

        assert store_entry["col_names"] == ["Date", "Revenue"]
        assert store_entry["row_count"] == 2
        assert result_payload["rows"] == 2


class TestUploadedTableCharting:
    def test_chart_tool_is_registered(self):
        from tools import _TOOL_DEFINITION_BY_NAME

        tool_def = _TOOL_DEFINITION_BY_NAME.get("chart_uploaded_table")
        assert tool_def is not None
        params = tool_def["function"]["parameters"]["properties"]
        assert "chart_type" in params
        assert "x_column" in params
        assert "y_column" in params

    @pytest.mark.asyncio
    async def test_chart_uploaded_table_generates_artifacts(self, monkeypatch):
        import tools

        sample_bytes = _sample_csv_bytes()

        async def _fake_table_query(*_args, **_kwargs):
            return [
                {
                    "Filename": "sample.csv",
                    "RawBlobRef": "raw/sample.csv",
                    "UploadedAt": "2026-03-08T10:00:00+00:00",
                }
            ]

        async def _fake_blob_download_bytes(_container, _blob_name):
            return sample_bytes

        monkeypatch.setattr(tools, "table_query", _fake_table_query)
        monkeypatch.setattr(tools, "blob_download_bytes", _fake_blob_download_bytes)

        result = await tools.tool_chart_uploaded_table(
            query="faz um gráfico de barras da revenue por category",
            conv_id="conv-1",
            chart_type="bar",
            x_column="Category",
            y_column="Revenue",
            agg="sum",
        )

        assert result["success"] is True
        assert result["source"] == "uploaded_table_chart"
        artifact_names = {item["filename"] for item in result["generated_artifacts"]}
        assert "uploaded_table_chart.html" in artifact_names
        assert "uploaded_table_chart.svg" in artifact_names
        assert "uploaded_table_chart_data.csv" in artifact_names
        assert result["chart_spec"]["x_column"] == "Category"
        assert result["chart_spec"]["y_column"] == "Revenue"

    def test_chart_spec_clears_nonexistent_columns(self):
        from tools import _build_uploaded_table_chart_spec

        preview = {
            "columns": ["Date", "Revenue"],
            "sample_records": [{"Date": "2026-01-01", "Revenue": "10"}],
            "column_types": {"Date": "date", "Revenue": "numeric"},
            "row_count": 1,
        }
        spec = _build_uploaded_table_chart_spec(
            "gráfico de barras",
            preview,
            chart_type="bar",
            x_column="ColunaNaoExiste",
            y_column="OutraInexistente",
        )

        assert spec["x_column"] != "ColunaNaoExiste"
        assert spec["y_column"] != "OutraInexistente"

    def test_chart_code_template_produces_valid_python(self):
        from tools import _build_uploaded_table_chart_code

        spec = {
            "chart_type": "bar",
            "x_column": "Category",
            "y_column": "Revenue",
            "series_column": "",
            "agg": "sum",
            "top_n": 20,
            "max_points": 2000,
            "x_kind": "",
        }
        code = _build_uploaded_table_chart_code("sample.csv", spec, "test query")

        compile(code, "<chart_template>", "exec")
        assert "sample.csv" in code

    def test_code_interpreter_knows_xlsb_mime(self):
        from code_interpreter import _guess_mime

        assert _guess_mime("sample.xlsb") == "application/vnd.ms-excel.sheet.binary.macroenabled.12"
