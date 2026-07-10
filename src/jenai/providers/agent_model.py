"""Model bindings for the openai-agents SDK."""

from __future__ import annotations

from typing import Literal

from agents import Model, OpenAIChatCompletionsModel, OpenAIResponsesModel
from openai import AsyncOpenAI

from jenai.config.models import AppConfig
from jenai.providers.chat import _active_profile, _api_key, resolved_model

ModelBinding = Literal["chat", "plan", "vision", "route", "default"]


def make_agent_client(config: AppConfig) -> AsyncOpenAI:
    """One AsyncOpenAI client for the active profile.

    Every binding in an agent graph (chat/route/vision) shares the same
    api-key/base-url and differs only by model name, so a multi-agent `/run` can
    reuse a single client — and thus one httpx connection pool — instead of
    opening (and leaking) one per specialist.
    """
    profile = _active_profile(config)
    return AsyncOpenAI(api_key=_api_key(profile), base_url=profile.base_url or None)


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
        return OpenAIResponsesModel(model=model_name, openai_client=client)
    return OpenAIChatCompletionsModel(model=model_name, openai_client=client)
