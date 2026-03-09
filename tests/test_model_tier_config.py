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


def _read_config_values(monkeypatch, *keys, **env_values):
    with monkeypatch.context() as m:
        clear_keys = set(keys) | set(env_values.keys()) | {f"APPSETTING_{key}" for key in keys} | {
            f"APPSETTING_{key}" for key in env_values
        }
        for key in clear_keys:
            m.delenv(key, raising=False)
        for key, value in env_values.items():
            m.setenv(key, value)
        cfg = importlib.reload(config)
        values = tuple(getattr(cfg, key) for key in keys)
    importlib.reload(config)
    return values


def test_standard_tier_defaults_to_sonnet_when_foundry_is_configured(monkeypatch):
    assert _read_standard_tier(
        monkeypatch,
        ANTHROPIC_FOUNDRY_RESOURCE="ms-access-chabot-resource",
    ) == "anthropic:sonnet"


def test_standard_tier_falls_back_to_gpt5_mini_without_anthropic(monkeypatch):
    assert _read_standard_tier(monkeypatch) == "azure_openai:gpt-5-mini-dz"


def test_chat_deployment_default_matches_current_fast_azure_deployment(monkeypatch):
    (chat_deployment,) = _read_config_values(monkeypatch, "CHAT_DEPLOYMENT")
    assert chat_deployment == "gpt-4-1-dz"


def test_rerank_model_default_matches_foundry_model_name(monkeypatch):
    (rerank_model,) = _read_config_values(monkeypatch, "RERANK_MODEL")
    assert rerank_model == "cohere-rerank-v4-fast"


def test_allowed_origins_default_uses_only_current_public_host(monkeypatch):
    (allowed_origins,) = _read_config_values(monkeypatch, "ALLOWED_ORIGINS")
    assert allowed_origins == "https://millennium-ai-assistant.azurewebsites.net"


def test_legacy_fast_deployment_env_does_not_override_llm_tier_fast(monkeypatch):
    (tier_fast,) = _read_config_values(
        monkeypatch,
        "LLM_TIER_FAST",
        FAST_DEPLOYMENT="gpt-5-mini-dz",
    )
    assert tier_fast == "azure_openai:gpt-4-1-dz"
