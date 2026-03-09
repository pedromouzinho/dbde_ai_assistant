from __future__ import annotations

import importlib

import config


def _read_standard_tier(monkeypatch, **env_values) -> str:
    keys = [
        "LLM_TIER_STANDARD",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_FOUNDRY_RESOURCE",
        "ANTHROPIC_API_BASE",
    ]
    with monkeypatch.context() as m:
        for key in keys:
            m.delenv(key, raising=False)
            m.delenv(f"APPSETTING_{key}", raising=False)
        for key, value in env_values.items():
            m.setenv(key, value)
        value = importlib.reload(config).LLM_TIER_STANDARD
    importlib.reload(config)
    return value


def test_standard_tier_defaults_to_sonnet_when_foundry_is_configured(monkeypatch):
    assert _read_standard_tier(
        monkeypatch,
        ANTHROPIC_FOUNDRY_RESOURCE="ms-access-chabot-resource",
    ) == "anthropic:sonnet"


def test_standard_tier_falls_back_to_gpt5_mini_without_anthropic(monkeypatch):
    assert _read_standard_tier(monkeypatch) == "azure_openai:gpt-5-mini-dz"
