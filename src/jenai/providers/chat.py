from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    OpenAIError,
)

from jenai.config.models import AppConfig, ProviderProfile


class ProviderChatError(Exception):
    """Raised when a provider-backed chat request cannot be completed."""


@dataclass(frozen=True)
class ChatResponse:
    content: str
    model: str
    provider: str


NVIDIA_MODEL_ALIASES = {
    "nemotron3": "nvidia/nemotron-3-nano-30b-a3b",
    "nemotron-3": "nvidia/nemotron-3-nano-30b-a3b",
    "nemotron-3-nano": "nvidia/nemotron-3-nano-30b-a3b",
    "nemotron-3-super": "nvidia/nemotron-3-super-120b-a12b",
    "nemotron-3-ultra": "nvidia/nemotron-3-ultra-550b-a55b",
    "qwen3-coder": "qwen/qwen3-coder-480b-a35b-instruct",
}

async def ask_provider(
    config: AppConfig,
    prompt: str,
    *,
    system_prompt: str | None = None,
) -> ChatResponse:
    profile = _active_profile(config)
    model = _chat_model(config, profile)
    api_key = _api_key(profile)

    messages = [
        {
            "role": "system",
            "content": system_prompt
            or (
                "You are JenAI, a concise terminal-first assistant for ROS2 robot "
                "workflows. Answer clearly and keep responses practical."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    try:
        # A fresh client per call keeps this safe across separate asyncio event
        # loops (the underlying httpx client is loop-bound) and closes its
        # connection pool immediately via `async with` instead of leaking it.
        async with AsyncOpenAI(api_key=api_key, base_url=profile.base_url or None) as client:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.2,
            )
    except (APIConnectionError, APITimeoutError) as exc:
        raise ProviderChatError(f"Could not reach provider endpoint: {exc}") from exc
    except APIStatusError as exc:
        if exc.status_code == 404:
            hint = ""
            if profile.provider.lower() == "nvidia":
                hint = (
                    " For NVIDIA, use full model ids like "
                    "'nvidia/nemotron-3-nano-30b-a3b' or "
                    "'qwen/qwen3-coder-480b-a35b-instruct'."
                )
            raise ProviderChatError(
                "Provider returned 404. Check the model id and base URL. "
                f"Sent model '{model}' to '{profile.base_url or 'provider default'}'.{hint}"
            ) from exc
        raise ProviderChatError(f"Provider API error ({exc.status_code}): {exc}") from exc
    except APIError as exc:
        raise ProviderChatError(f"Provider API error: {exc}") from exc
    except OpenAIError as exc:
        raise ProviderChatError(f"Provider request failed: {exc}") from exc

    content = response.choices[0].message.content if response.choices else None
    if not content:
        raise ProviderChatError("Provider returned an empty response.")

    return ChatResponse(
        content=content,
        model=model,
        provider=profile.name,
    )


async def list_provider_models(config: AppConfig) -> list[str]:
    """List model ids available on the active provider endpoint.

    Works against any OpenAI-compatible `/v1/models` — including a local
    Ollama server — so /model can offer real, currently-installed choices.
    """
    profile = _active_profile(config)
    api_key = _api_key(profile)

    try:
        async with AsyncOpenAI(api_key=api_key, base_url=profile.base_url or None) as client:
            page = await client.models.list()
    except (APIConnectionError, APITimeoutError) as exc:
        raise ProviderChatError(f"Could not reach provider endpoint: {exc}") from exc
    except APIStatusError as exc:
        raise ProviderChatError(f"Provider API error ({exc.status_code}): {exc}") from exc
    except OpenAIError as exc:
        raise ProviderChatError(f"Provider request failed: {exc}") from exc

    return sorted({model.id for model in page.data})


def _active_profile(config: AppConfig) -> ProviderProfile:
    if config.active_provider is None:
        raise ProviderChatError("No active provider is configured.")

    profile = config.active_profile()
    if profile is None:
        raise ProviderChatError(f"Active provider '{config.active_provider}' is missing.")

    return profile


def chat_model_name(config: AppConfig) -> str | None:
    """Return the configured chat model name, preferring `chat` over `default`.

    This is the same precedence `_chat_model` uses for the actual request, so
    display surfaces (TUI header, /status) can show the model that will really
    be used instead of independently re-deriving it.
    """
    if config.model_bindings is None:
        return None
    return config.model_bindings.chat or config.model_bindings.default


def _chat_model(config: AppConfig, profile: ProviderProfile | None = None) -> str:
    return resolved_model(config, profile, "chat")


def resolved_model(
    config: AppConfig,
    profile: ProviderProfile | None,
    binding: str = "chat",
) -> str:
    """Resolve a named model binding (chat/plan/vision/route/default) to a real model id.

    Falls back to `default` when the named binding is unset, and applies
    provider-specific alias resolution (e.g. NVIDIA short names).
    """
    if config.model_bindings is None:
        raise ProviderChatError("No model bindings are configured.")

    raw_model = getattr(config.model_bindings, binding, None) or config.model_bindings.default
    if not raw_model:
        raise ProviderChatError("No model bindings are configured.")

    return resolve_model_alias(raw_model, profile)


def resolve_model_alias(model: str, profile: ProviderProfile | None = None) -> str:
    provider = profile.provider.lower() if profile is not None else ""
    if provider != "nvidia":
        return model

    return NVIDIA_MODEL_ALIASES.get(model.strip().lower(), model)


def _api_key(profile: ProviderProfile) -> str:
    if not profile.api_key_env:
        # Local / keyless providers (e.g. Ollama, llama.cpp) need no real key,
        # but the OpenAI client still requires a non-empty string. Leaving
        # api_key_env blank in the profile opts into this keyless mode.
        return "not-needed"

    api_key = os.environ.get(profile.api_key_env)
    if not api_key:
        raise ProviderChatError(
            f"Environment variable {profile.api_key_env} is not set."
        )

    return api_key


async def ask_vision_json(
    config: AppConfig,
    prompt: str,
    image_data_url: str,
    *,
    binding: str = "vision",
) -> Any | None:
    """Send an image + prompt to the vision model and parse the reply as JSON.

    Returns None on any failure so vision callers can degrade gracefully.
    """
    try:
        profile = _active_profile(config)
        api_key = _api_key(profile)
        model = resolved_model(config, profile, binding)
    except ProviderChatError:
        return None

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        }
    ]
    try:
        async with AsyncOpenAI(api_key=api_key, base_url=profile.base_url or None) as client:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.0,
            )
        content = response.choices[0].message.content if response.choices else None
        if not content:
            return None
        return json.loads(content)
    except (OpenAIError, json.JSONDecodeError):
        return None


async def ask_json(config: AppConfig, prompt: str, *, binding: str = "chat") -> Any | None:
    """Make a single deterministic chat completion and parse its content as JSON.

    Returns None on any failure (missing config, network error, invalid JSON) so
    callers (schema summarization, route extraction) can degrade gracefully
    instead of raising.
    """
    try:
        profile = _active_profile(config)
        api_key = _api_key(profile)
        model = resolved_model(config, profile, binding)
    except ProviderChatError:
        return None

    try:
        async with AsyncOpenAI(api_key=api_key, base_url=profile.base_url or None) as client:
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
        content = response.choices[0].message.content if response.choices else None
        if not content:
            return None
        return json.loads(content)
    except (OpenAIError, json.JSONDecodeError):
        return None
