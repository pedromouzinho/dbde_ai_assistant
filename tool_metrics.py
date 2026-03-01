"""In-memory tool call metrics accumulator."""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any


class ToolMetrics:
    """Thread-safe accumulator for tool call metrics."""

    def __init__(self, max_latencies: int = 500):
        self._lock = threading.Lock()
        self._max_latencies = max_latencies
        self._data: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "call_count": 0,
                "error_count": 0,
                "blocked_count": 0,
                "total_ms": 0,
                "latencies": [],
            }
        )

    def record(self, tool_name: str, duration_ms: int, status: str = "ok") -> None:
        with self._lock:
            entry = self._data[tool_name]
            entry["call_count"] += 1
            entry["total_ms"] += max(0, duration_ms)
            if status in ("exception", "tool_error"):
                entry["error_count"] += 1
            elif status == "blocked":
                entry["blocked_count"] += 1
            lats = entry["latencies"]
            lats.append(max(0, duration_ms))
            if len(lats) > self._max_latencies:
                entry["latencies"] = lats[-self._max_latencies:]

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            result = {}
            for tool_name, entry in self._data.items():
                lats = sorted(entry["latencies"]) if entry["latencies"] else []
                n = len(lats)
                result[tool_name] = {
                    "call_count": entry["call_count"],
                    "error_count": entry["error_count"],
                    "blocked_count": entry["blocked_count"],
                    "avg_ms": round(entry["total_ms"] / max(1, entry["call_count"])),
                    "p50_ms": lats[n // 2] if n else 0,
                    "p95_ms": lats[int(n * 0.95)] if n else 0,
                    "p99_ms": lats[int(n * 0.99)] if n else 0,
                    "max_ms": lats[-1] if n else 0,
                    "recent_samples": n,
                }
            return dict(sorted(result.items()))

    def reset(self) -> None:
        with self._lock:
            self._data.clear()


tool_metrics = ToolMetrics()
