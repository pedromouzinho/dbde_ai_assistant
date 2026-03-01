"""Per-tier token quota enforcement."""

from __future__ import annotations

import threading
import time
from typing import Any


class TokenQuota:
    """Thread-safe token quota tracker with hourly and daily windows."""

    def __init__(self, hourly_limit: int = 0, daily_limit: int = 0):
        self._lock = threading.Lock()
        self._hourly_limit = max(0, int(hourly_limit or 0))
        self._daily_limit = max(0, int(daily_limit or 0))
        # Buckets: {window_key: token_count}
        self._hourly: dict[str, int] = {}
        self._daily: dict[str, int] = {}

    @staticmethod
    def _hour_key() -> str:
        t = time.gmtime()
        return f"{t.tm_year}-{t.tm_yday:03d}-{t.tm_hour:02d}"

    @staticmethod
    def _day_key() -> str:
        t = time.gmtime()
        return f"{t.tm_year}-{t.tm_yday:03d}"

    def check(self) -> tuple[bool, str]:
        """Check if quota allows a request. Returns (allowed, reason)."""
        if not self._hourly_limit and not self._daily_limit:
            return True, ""
        with self._lock:
            hk = self._hour_key()
            dk = self._day_key()
            hourly_used = self._hourly.get(hk, 0)
            daily_used = self._daily.get(dk, 0)
            if self._hourly_limit and hourly_used >= self._hourly_limit:
                return False, f"Hourly token limit reached ({self._hourly_limit})"
            if self._daily_limit and daily_used >= self._daily_limit:
                return False, f"Daily token limit reached ({self._daily_limit})"
            return True, ""

    def record(self, tokens: int) -> None:
        """Record token usage."""
        try:
            amount = int(tokens or 0)
        except Exception:
            amount = 0
        if amount <= 0:
            return
        with self._lock:
            hk = self._hour_key()
            dk = self._day_key()
            self._hourly[hk] = self._hourly.get(hk, 0) + amount
            self._daily[dk] = self._daily.get(dk, 0) + amount
            self._evict_locked()

    def _evict_locked(self) -> None:
        """Remove stale buckets to prevent memory growth."""
        t = time.gmtime()
        # Keep at most last 48 hour buckets and last 8 day buckets.
        # This is enough for check/snapshot and prevents unbounded growth.
        hour_candidates = []
        day_candidates = []
        for k in self._hourly:
            try:
                year, yday, hour = str(k).split("-")
                hour_candidates.append((int(year), int(yday), int(hour), k))
            except Exception:
                continue
        for k in self._daily:
            try:
                year, yday = str(k).split("-")
                day_candidates.append((int(year), int(yday), k))
            except Exception:
                continue

        hour_candidates.sort()
        day_candidates.sort()

        if len(hour_candidates) > 48:
            keep = {item[3] for item in hour_candidates[-48:]}
            self._hourly = {k: v for k, v in self._hourly.items() if k in keep}
        if len(day_candidates) > 8:
            keep = {item[2] for item in day_candidates[-8:]}
            self._daily = {k: v for k, v in self._daily.items() if k in keep}

    def snapshot(self) -> dict[str, Any]:
        """Return current usage snapshot."""
        with self._lock:
            hk = self._hour_key()
            dk = self._day_key()
            return {
                "hourly_used": self._hourly.get(hk, 0),
                "hourly_limit": self._hourly_limit,
                "daily_used": self._daily.get(dk, 0),
                "daily_limit": self._daily_limit,
                "hour_key": hk,
                "day_key": dk,
            }

    def reset(self) -> None:
        with self._lock:
            self._hourly.clear()
            self._daily.clear()


class TokenQuotaManager:
    """Manages quotas for multiple tiers."""

    def __init__(self, config: dict[str, dict[str, int]]):
        self._quotas: dict[str, TokenQuota] = {}
        for tier, limits in (config or {}).items():
            self._quotas[str(tier)] = TokenQuota(
                hourly_limit=int((limits or {}).get("hourly", 0) or 0),
                daily_limit=int((limits or {}).get("daily", 0) or 0),
            )

    def check(self, tier: str) -> tuple[bool, str]:
        q = self._quotas.get(str(tier))
        if not q:
            return True, ""
        return q.check()

    def record(self, tier: str, tokens: int) -> None:
        q = self._quotas.get(str(tier))
        if q:
            q.record(tokens)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {tier: q.snapshot() for tier, q in sorted(self._quotas.items())}

    def reset(self, tier: str = "") -> None:
        if tier and tier in self._quotas:
            self._quotas[tier].reset()
        elif not tier:
            for q in self._quotas.values():
                q.reset()


# Singleton — initialised in app.py startup or lazily
token_quota_manager: TokenQuotaManager | None = None
