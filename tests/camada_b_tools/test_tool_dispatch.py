"""
Camada B — Tool Dispatch: O registry resolve todas as ferramentas correctamente?
"""

from __future__ import annotations

import os
import sys

import pytest

# Adicionar root do projecto ao path para importar módulos reais
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

EXPECTED_TOOLS = [
    "query_workitems",
    "search_workitems",
    "search_website",
    "search_uploaded_document",
    "analyze_patterns",
    "generate_user_stories",
    "query_hierarchy",
    "compute_kpi",
    "create_workitem",
    "refine_workitem",
    "generate_chart",
    "chart_uploaded_table",
    "run_code",
    "generate_file",
]

OPTIONAL_TOOLS = ["search_figma", "search_miro"]


def _load_registry_or_skip():
    try:
        import tools  # noqa: F401 - garante registo das tools no registry
        from tool_registry import execute_tool, get_all_tool_definitions, get_registered_tool_names

        return execute_tool, get_all_tool_definitions, get_registered_tool_names
    except Exception as exc:
        pytest.skip(f"tool_registry indisponível neste ambiente: {exc}")


class TestToolDispatch:
    def test_expected_tools_have_definitions(self):
        execute_tool, get_all_tool_definitions, get_registered_tool_names = _load_registry_or_skip()
        _ = execute_tool
        registered = get_registered_tool_names()
        definitions = get_all_tool_definitions()
        def_names = [d.get("function", {}).get("name") for d in definitions]

        missing = [tool for tool in EXPECTED_TOOLS if tool not in registered]
        assert not missing, f"Ferramentas esperadas não registadas: {missing}"

        missing_defs = [tool for tool in EXPECTED_TOOLS if tool not in def_names]
        assert not missing_defs, f"Ferramentas sem definição OpenAI: {missing_defs}"

    def test_no_duplicate_tools(self):
        _, get_all_tool_definitions, _ = _load_registry_or_skip()
        names = [d.get("function", {}).get("name") for d in get_all_tool_definitions()]
        names = [n for n in names if n]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        assert not duplicates, f"Ferramentas duplicadas: {duplicates}"

    def test_tool_definitions_have_required_fields(self):
        _, get_all_tool_definitions, _ = _load_registry_or_skip()
        for defn in get_all_tool_definitions():
            assert defn.get("type") == "function", f"Missing type for {defn}"
            func = defn.get("function", {})
            assert "name" in func, "Missing function.name"
            assert "description" in func, f"Missing function.description for {func.get('name')}"
            assert "parameters" in func, f"Missing function.parameters for {func.get('name')}"

    @pytest.mark.asyncio
    async def test_dispatch_unknown_tool_returns_error(self):
        execute_tool, _, _ = _load_registry_or_skip()
        result = await execute_tool("nonexistent_tool_xyz", {"arg": "value"})
        serialized = str(result).lower()
        assert "error" in serialized, "Expected error for unknown tool"
