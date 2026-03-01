"""Tests para tool call metrics accumulator (SPEC-12)."""

from tool_metrics import ToolMetrics


class TestToolMetrics:
    def test_record_and_snapshot(self):
        m = ToolMetrics()
        m.record("query_workitems", 150, "ok")
        m.record("query_workitems", 200, "ok")
        m.record("query_workitems", 50, "exception")
        snap = m.snapshot()
        assert "query_workitems" in snap
        entry = snap["query_workitems"]
        assert entry["call_count"] == 3
        assert entry["error_count"] == 1
        assert entry["blocked_count"] == 0
        assert entry["avg_ms"] == round((150 + 200 + 50) / 3)
        assert entry["p50_ms"] == 150
        assert entry["max_ms"] == 200

    def test_blocked_count(self):
        m = ToolMetrics()
        m.record("search_workitems", 5, "blocked")
        m.record("search_workitems", 100, "ok")
        snap = m.snapshot()
        assert snap["search_workitems"]["blocked_count"] == 1
        assert snap["search_workitems"]["call_count"] == 2

    def test_empty_snapshot(self):
        m = ToolMetrics()
        snap = m.snapshot()
        assert snap == {}

    def test_reset(self):
        m = ToolMetrics()
        m.record("test_tool", 100, "ok")
        assert m.snapshot() != {}
        m.reset()
        assert m.snapshot() == {}

    def test_latency_cap(self):
        m = ToolMetrics(max_latencies=10)
        for i in range(50):
            m.record("tool_a", i * 10, "ok")
        snap = m.snapshot()
        assert snap["tool_a"]["recent_samples"] == 10
        assert snap["tool_a"]["call_count"] == 50

    def test_multiple_tools(self):
        m = ToolMetrics()
        m.record("tool_a", 100, "ok")
        m.record("tool_b", 200, "ok")
        m.record("tool_c", 300, "exception")
        snap = m.snapshot()
        assert len(snap) == 3
        assert snap["tool_a"]["call_count"] == 1
        assert snap["tool_c"]["error_count"] == 1
