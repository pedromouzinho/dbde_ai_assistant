"""Tests para Vision Tier patch (SPEC-22)."""

import inspect


class TestVisionTierConfig:
    def test_vision_tier_env_var_exists(self):
        """LLM_TIER_VISION deve existir em config."""
        from config import LLM_TIER_VISION

        assert LLM_TIER_VISION is not None
        assert ":" in LLM_TIER_VISION  # formato provider:model

    def test_vision_enabled_flag_exists(self):
        """VISION_ENABLED deve existir em config."""
        from config import VISION_ENABLED

        assert isinstance(VISION_ENABLED, bool)

    def test_vision_in_tier_map(self):
        """Tier 'vision' deve estar no tier_map de get_provider."""
        import llm_provider

        src = inspect.getsource(llm_provider.get_provider)
        assert '"vision"' in src or "'vision'" in src

    def test_screenshot_tool_uses_vision_tier(self):
        """tool_screenshot_to_us deve usar tier='vision'."""
        import tools

        src = inspect.getsource(tools.tool_screenshot_to_us)
        assert 'tier="vision"' in src or "tier='vision'" in src
        assert 'tier="standard"' not in src  # nao deve usar standard
