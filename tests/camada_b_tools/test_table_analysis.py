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

    def test_query_metrics_are_extracted_from_prompt(self):
        """Pedidos multi-métrica devem ser inferidos da query."""
        from tools import _extract_metric_requests_from_query

        metrics = _extract_metric_requests_from_query(
            "faz resumo estatístico com mínimo, máximo, média e desvio padrão"
        )
        assert "min" in metrics
        assert "max" in metrics
        assert "mean" in metrics
        assert "std" in metrics

    def test_build_column_profiles_detects_numeric_and_text(self):
        """Perfis de colunas devem distinguir colunas numéricas e textuais."""
        from tools import _build_column_profiles

        records = [
            {"Close": "10.5", "Agent": "A1"},
            {"Close": "12.0", "Agent": "A1"},
            {"Close": "9.0", "Agent": "B2"},
        ]
        profiles = _build_column_profiles(records, ["Close", "Agent"])
        by_name = {p["name"]: p for p in profiles}

        assert by_name["Close"]["type"] == "numeric"
        assert by_name["Close"]["min"] == 9.0
        assert by_name["Close"]["max"] == 12.0
        assert by_name["Agent"]["type"] == "text"
        assert by_name["Agent"]["distinct_count"] == 2
