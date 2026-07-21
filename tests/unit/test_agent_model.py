from __future__ import annotations

import asyncio

import pytest
from agents import OpenAIChatCompletionsModel, OpenAIResponsesModel

from jenai.config.store import build_minimal_config
from jenai.providers.agent_model import (
    GenerationTimeoutModel,
    ModelGenerationTimeoutError,
    build_agent_model,
)
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

    assert isinstance(model, GenerationTimeoutModel)
    assert isinstance(model.delegate, OpenAIChatCompletionsModel)
    assert model.delegate.model == "nvidia/nemotron-3-nano-30b-a3b"
    assert str(model.delegate._client.base_url) == "https://integrate.api.nvidia.com/v1/"
    assert model.delegate._client.api_key == "secret"


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

    assert isinstance(model, GenerationTimeoutModel)
    assert isinstance(model.delegate, OpenAIResponsesModel)
    assert model.delegate.model == "gpt-test"


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

    assert isinstance(model, GenerationTimeoutModel)
    assert isinstance(model.delegate, OpenAIChatCompletionsModel)


def test_generation_timeout_bounds_get_response() -> None:
    class SlowModel:
        async def get_response(self, *args, **kwargs):
            await asyncio.sleep(1)

        def stream_response(self, *args, **kwargs):
            raise AssertionError("not used")

        def get_retry_advice(self, request):
            return None

    model = GenerationTimeoutModel(SlowModel(), timeout_seconds=0.01)  # type: ignore[arg-type]

    with pytest.raises(ModelGenerationTimeoutError, match="0.01 seconds"):
        asyncio.run(model.get_response())


def test_generation_timeout_bounds_continuously_streaming_reasoning() -> None:
    class SlowStreamingModel:
        async def get_response(self, *args, **kwargs):
            raise AssertionError("not used")

        async def stream_response(self, *args, **kwargs):
            while True:
                await asyncio.sleep(0.001)
                yield object()

        def get_retry_advice(self, request):
            return None

    model = GenerationTimeoutModel(
        SlowStreamingModel(),
        timeout_seconds=0.02,  # type: ignore[arg-type]
    )

    async def consume() -> None:
        async for _ in model.stream_response():
            pass

    with pytest.raises(ModelGenerationTimeoutError, match="0.02 seconds"):
        asyncio.run(consume())


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
