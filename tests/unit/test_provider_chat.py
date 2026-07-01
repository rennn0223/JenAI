from __future__ import annotations

import pytest

from jenai.config.store import build_minimal_config
from jenai.providers.chat import ProviderChatError, _api_key, _chat_model


def test_api_key_is_read_from_configured_env(monkeypatch) -> None:
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="gpt-test",
        api_key_env="JENAI_TEST_KEY",
    )
    profile = config.provider_profiles["test"]
    monkeypatch.setenv("JENAI_TEST_KEY", "secret")

    assert _api_key(profile) == "secret"


def test_missing_api_key_raises_provider_error(monkeypatch) -> None:
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="gpt-test",
        api_key_env="JENAI_TEST_KEY",
    )
    profile = config.provider_profiles["test"]
    monkeypatch.delenv("JENAI_TEST_KEY", raising=False)

    with pytest.raises(ProviderChatError, match="JENAI_TEST_KEY"):
        _api_key(profile)


def test_nvidia_model_alias_is_resolved() -> None:
    config = build_minimal_config(
        provider_name="test",
        provider="NVIDIA",
        default_model="Nemotron3",
        api_key_env="JENAI_TEST_KEY",
    )
    profile = config.provider_profiles["test"]

    assert _chat_model(config, profile) == "nvidia/nemotron-3-nano-30b-a3b"
