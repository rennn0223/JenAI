from __future__ import annotations

import pytest
from agents import OpenAIChatCompletionsModel, OpenAIResponsesModel

from jenai.config.store import build_minimal_config
from jenai.providers.agent_model import build_agent_model
from jenai.providers.chat import ProviderChatError


def test_build_agent_model_resolves_client_and_model(monkeypatch) -> None:
    config = build_minimal_config(
        provider_name="test",
        provider="NVIDIA",
        default_model="Nemotron3",
        api_key_env="JENAI_TEST_KEY",
        base_url="https://integrate.api.nvidia.com/v1",
    )
    monkeypatch.setenv("JENAI_TEST_KEY", "secret")

    model = build_agent_model(config, binding="chat")

    assert isinstance(model, OpenAIChatCompletionsModel)
    assert model.model == "nvidia/nemotron-3-nano-30b-a3b"
    assert str(model._client.base_url) == "https://integrate.api.nvidia.com/v1/"
    assert model._client.api_key == "secret"


def test_build_agent_model_falls_back_to_default_binding(monkeypatch) -> None:
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="gpt-test",
        api_key_env="JENAI_TEST_KEY",
    )
    monkeypatch.setenv("JENAI_TEST_KEY", "secret")

    # An unrecognized binding name falls back to `model_bindings.default` rather
    # than raising, matching `resolved_model`'s `getattr(..., None) or default`.
    model = build_agent_model(config, binding="unknown_binding")  # type: ignore[arg-type]

    assert isinstance(model, OpenAIResponsesModel)
    assert model.model == "gpt-test"


def test_openai_compatible_base_url_uses_chat_completions(monkeypatch) -> None:
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="local-model",
        api_key_env="JENAI_TEST_KEY",
        base_url="http://localhost:11434/v1",
    )
    monkeypatch.setenv("JENAI_TEST_KEY", "secret")

    model = build_agent_model(config)

    assert isinstance(model, OpenAIChatCompletionsModel)


def test_build_agent_model_missing_api_key_raises(monkeypatch) -> None:
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="gpt-test",
        api_key_env="JENAI_TEST_KEY",
    )
    monkeypatch.delenv("JENAI_TEST_KEY", raising=False)

    with pytest.raises(ProviderChatError):
        build_agent_model(config)
