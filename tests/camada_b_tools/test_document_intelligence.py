import pytest

import document_intelligence as di


def test_tables_to_markdown_basic():
    tables = [
        {
            "row_count": 2,
            "column_count": 2,
            "cells": [
                {"row": 0, "col": 0, "text": "Ano"},
                {"row": 0, "col": 1, "text": "Valor"},
                {"row": 1, "col": 0, "text": "2024"},
                {"row": 1, "col": 1, "text": "100"},
            ],
        }
    ]
    md = di.tables_to_markdown(tables)
    assert "**Tabela 1:**" in md
    assert "| Ano | Valor |" in md
    assert "| 2024 | 100 |" in md


def test_parse_result_extracts_counts():
    raw = {
        "content": "texto completo",
        "tables": [
            {
                "rowCount": 1,
                "columnCount": 1,
                "cells": [{"rowIndex": 0, "columnIndex": 0, "content": "x", "kind": "columnHeader"}],
            }
        ],
        "paragraphs": [{"role": "title", "content": "Titulo"}],
        "keyValuePairs": [{"key": {"content": "NIF"}, "value": {"content": "123"}}],
        "pages": [{"pageNumber": 1, "width": 100, "height": 200, "unit": "pixel", "words": [{"content": "a"}]}],
    }
    parsed = di._parse_result(raw)
    assert parsed["text"] == "texto completo"
    assert parsed["table_count"] == 1
    assert parsed["page_count"] == 1
    assert parsed["key_values"][0]["key"] == "NIF"
    assert parsed["paragraphs"][0]["role"] == "title"


@pytest.mark.asyncio
async def test_analyze_document_disabled(monkeypatch):
    monkeypatch.setattr(di, "DOC_INTEL_ENABLED", False)
    result = await di.analyze_document(b"%PDF-test", "doc.pdf")
    assert result["text"] == ""
    assert "nao configurado" in result["error"].lower()
