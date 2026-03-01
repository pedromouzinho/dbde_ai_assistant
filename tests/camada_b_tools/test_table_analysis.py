"""Tests para full_points e downsample (SPEC-15)."""

import inspect


class TestFullPointsParam:
    def test_full_points_in_tool_definition(self):
        """Verificar que full_points está no schema da tool."""
        from tools import _TOOL_DEFINITION_BY_NAME

        tool_def = _TOOL_DEFINITION_BY_NAME.get("analyze_uploaded_table")
        assert tool_def is not None
        params = tool_def["function"]["parameters"]["properties"]
        assert "full_points" in params
        assert params["full_points"]["type"] == "boolean"

    def test_chart_max_points_constant_exists(self):
        """Verificar que CHART_MAX_POINTS está definido."""
        from tools import CHART_MAX_POINTS

        assert isinstance(CHART_MAX_POINTS, int)
        assert CHART_MAX_POINTS >= 1000

    def test_tool_accepts_full_points_param(self):
        """Verificar que a função aceita full_points sem erro."""
        from tools import tool_analyze_uploaded_table

        sig = inspect.signature(tool_analyze_uploaded_table)
        assert "full_points" in sig.parameters
        assert sig.parameters["full_points"].default is False
