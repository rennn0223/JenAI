from __future__ import annotations

from typing import Literal

from agents import OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from jenai.config.models import AppConfig
from jenai.providers.chat import _active_profile, _api_key, resolved_model

ModelBinding = Literal["chat", "plan", "vision", "route", "default"]


def build_agent_model(
    config: AppConfig,
    *,
    binding: ModelBinding = "chat",
) -> OpenAIChatCompletionsModel:
    """Build an `agents` SDK model wired to the active provider profile.

    Reuses the same profile/api-key/model-alias resolution as `providers.chat.ask_provider`
    so the agent runtime and the plain one-shot chat path never drift.
    """
    profile = _active_profile(config)
    api_key = _api_key(profile)
    model_name = resolved_model(config, profile, binding)
    client = AsyncOpenAI(api_key=api_key, base_url=profile.base_url or None)
    return OpenAIChatCompletionsModel(model=model_name, openai_client=client)
