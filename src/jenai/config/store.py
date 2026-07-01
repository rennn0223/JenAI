from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from jenai.config.models import AppConfig, ProviderProfile
from jenai.schemas import ModelBindings


class ConfigError(Exception):
    """Raised when a JenAI config file cannot be read or validated."""


def default_config_path() -> Path:
    override = os.environ.get("JENAI_CONFIG")
    if override:
        return Path(override).expanduser()

    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "JenAI" / "config.toml"

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "jenai" / "config.toml"

    return Path.home() / ".config" / "jenai" / "config.toml"


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or default_config_path()
    try:
        raw = config_path.read_bytes()
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {config_path}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read config file: {config_path}") from exc

    try:
        data = tomllib.loads(raw.decode("utf-8"))
        return AppConfig.model_validate(data)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Config file is not valid TOML: {config_path}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"Config file must be UTF-8: {config_path}") from exc
    except ValidationError as exc:
        raise ConfigError(f"Config file has invalid JenAI settings: {exc}") from exc


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    config_path = path or default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_to_toml(config.model_dump(mode="python")), encoding="utf-8")
    return config_path


def build_minimal_config(
    *,
    provider_name: str,
    provider: str,
    default_model: str,
    base_url: str | None = None,
    api_key_env: str | None = None,
) -> AppConfig:
    profile = ProviderProfile(
        name=provider_name,
        provider=provider,
        base_url=base_url or None,
        api_key_env=api_key_env or None,
    )
    bindings = ModelBindings(
        chat=default_model,
        plan=default_model,
        vision=default_model,
        route=default_model,
        default=default_model,
    )
    return AppConfig(
        active_provider=provider_name,
        provider_profiles={provider_name: profile},
        model_bindings=bindings,
        locations_path="locations.toml",
        created_by_setup=True,
    )


def _to_toml(data: dict[str, Any]) -> str:
    lines: list[str] = []
    scalar_items: dict[str, Any] = {}

    for key, value in data.items():
        if isinstance(value, dict):
            continue
        scalar_items[key] = value

    for key, value in scalar_items.items():
        lines.append(f"{key} = {_format_toml_value(value)}")

    provider_profiles = data.get("provider_profiles") or {}
    for profile_name, profile in provider_profiles.items():
        lines.append("")
        lines.append(f"[provider_profiles.{_quote_key(profile_name)}]")
        for key, value in profile.items():
            lines.append(f"{key} = {_format_toml_value(value)}")

    model_bindings = data.get("model_bindings")
    if model_bindings:
        lines.append("")
        lines.append("[model_bindings]")
        for key, value in model_bindings.items():
            lines.append(f"{key} = {_format_toml_value(value)}")

    return "\n".join(lines) + "\n"


def _quote_key(key: str) -> str:
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _format_toml_value(value: Any) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

