"""Tests para token quota enforcement (SPEC-14)."""

from token_quota import TokenQuota, TokenQuotaManager


class TestTokenQuota:
    def test_unlimited_always_allows(self):
        q = TokenQuota(hourly_limit=0, daily_limit=0)
        ok, reason = q.check()
        assert ok is True
        assert reason == ""

    def test_hourly_limit_enforced(self):
        q = TokenQuota(hourly_limit=100, daily_limit=0)
        q.record(80)
        ok, _ = q.check()
        assert ok is True
        q.record(30)
        ok, reason = q.check()
        assert ok is False
        assert "Hourly" in reason

    def test_daily_limit_enforced(self):
        q = TokenQuota(hourly_limit=0, daily_limit=200)
        q.record(200)
        ok, reason = q.check()
        assert ok is False
        assert "Daily" in reason

    def test_snapshot(self):
        q = TokenQuota(hourly_limit=1000, daily_limit=10000)
        q.record(500)
        snap = q.snapshot()
        assert snap["hourly_used"] == 500
        assert snap["daily_used"] == 500
        assert snap["hourly_limit"] == 1000
        assert snap["daily_limit"] == 10000

    def test_reset(self):
        q = TokenQuota(hourly_limit=100, daily_limit=1000)
        q.record(100)
        ok, _ = q.check()
        assert ok is False
        q.reset()
        ok, _ = q.check()
        assert ok is True


class TestTokenQuotaManager:
    def test_multi_tier(self):
        mgr = TokenQuotaManager(
            {
                "fast": {"hourly": 1000, "daily": 5000},
                "pro": {"hourly": 100, "daily": 500},
            }
        )
        mgr.record("fast", 500)
        mgr.record("pro", 100)
        ok_fast, _ = mgr.check("fast")
        ok_pro, reason = mgr.check("pro")
        assert ok_fast is True
        assert ok_pro is False
        assert "Hourly" in reason

    def test_unknown_tier_allowed(self):
        mgr = TokenQuotaManager({"fast": {"hourly": 100, "daily": 0}})
        ok, _ = mgr.check("unknown_tier")
        assert ok is True

    def test_snapshot_all_tiers(self):
        mgr = TokenQuotaManager(
            {
                "fast": {"hourly": 1000, "daily": 5000},
                "standard": {"hourly": 500, "daily": 2000},
            }
        )
        snap = mgr.snapshot()
        assert "fast" in snap
        assert "standard" in snap

    def test_reset_single_tier(self):
        mgr = TokenQuotaManager(
            {
                "fast": {"hourly": 100, "daily": 500},
                "pro": {"hourly": 100, "daily": 500},
            }
        )
        mgr.record("fast", 100)
        mgr.record("pro", 100)
        mgr.reset("fast")
        ok_fast, _ = mgr.check("fast")
        ok_pro, _ = mgr.check("pro")
        assert ok_fast is True
        assert ok_pro is False
