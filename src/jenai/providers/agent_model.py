"""Model bindings for the openai-agents SDK."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any, Literal

from agents import Model, OpenAIChatCompletionsModel, OpenAIResponsesModel
from openai import AsyncOpenAI

from jenai.config.models import AppConfig
from jenai.providers.chat import _active_profile, _api_key, resolved_model

ModelBinding = Literal["chat", "plan", "vision", "route", "default"]

# Bound one provider generation, not the complete agent workflow. Navigation
# and other long-running tools have their own timeouts; this prevents a local
# OpenAI-compatible endpoint from leaving the TUI spinner alive forever after
# a tool result. Disable SDK retries so the wall-clock bound is meaningful.
AGENT_REQUEST_TIMEOUT_SECONDS = 180.0


class ModelGenerationTimeoutError(TimeoutError):
    """One LLM generation exceeded JenAI's total wall-clock budget."""


class GenerationTimeoutModel(Model):
    """Transparent total timeout around each SDK model generation.

    HTTP read timeouts reset whenever a provider emits another chunk. Local
    reasoning models may therefore stream hidden reasoning forever while the
    TUI shows only a spinner. This wrapper bounds the complete response stream
    while leaving navigation and other tool-specific timeouts untouched.
    """

    def __init__(self, delegate: Model, timeout_seconds: float) -> None:
        self.delegate = delegate
        self.timeout_seconds = timeout_seconds

    def get_retry_advice(self, request):
        return self.delegate.get_retry_advice(request)

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await self.delegate.get_response(*args, **kwargs)
        except TimeoutError as exc:
            raise ModelGenerationTimeoutError(
                f"Model generation exceeded {self.timeout_seconds:g} seconds."
            ) from exc

    async def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        try:
            async with aclosing(self.delegate.stream_response(*args, **kwargs)) as stream:
                async with asyncio.timeout(self.timeout_seconds):
                    async for event in stream:
                        yield event
        except TimeoutError as exc:
            raise ModelGenerationTimeoutError(
                f"Model generation exceeded {self.timeout_seconds:g} seconds."
            ) from exc


def make_agent_client(config: AppConfig) -> AsyncOpenAI:
    """One AsyncOpenAI client for the active profile.

    Every binding in an agent graph (chat/route/vision) shares the same
    api-key/base-url and differs only by model name, so a multi-agent `/run` can
    reuse a single client — and thus one httpx connection pool — instead of
    opening (and leaking) one per specialist.
    """
    profile = _active_profile(config)
    return AsyncOpenAI(
        api_key=_api_key(profile),
        base_url=profile.base_url or None,
        timeout=AGENT_REQUEST_TIMEOUT_SECONDS,
        max_retries=0,
    )


def build_agent_model(
    config: AppConfig,
    *,
    binding: ModelBinding = "chat",
    client: AsyncOpenAI | None = None,
) -> Model:
    """Build an `agents` SDK model wired to the active provider profile.

    Reuses the same profile/api-key/model-alias resolution as `providers.chat.ask_provider`
    so the agent runtime and the plain one-shot chat path never drift. Pass
    `client` to share one AsyncOpenAI across several models (see `make_agent_client`).
    """
    profile = _active_profile(config)
    model_name = resolved_model(config, profile, binding)
    client = client or make_agent_client(config)
    if profile.provider.lower() == "openai" and not profile.base_url:
        delegate = OpenAIResponsesModel(model=model_name, openai_client=client)
    else:
        delegate = OpenAIChatCompletionsModel(model=model_name, openai_client=client)
    return GenerationTimeoutModel(delegate, AGENT_REQUEST_TIMEOUT_SECONDS)
