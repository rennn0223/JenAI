"""config/.env path resolution and loading; build_minimal_config; save."""

from __future__ import annotations

import os
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w
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


def default_env_file_path() -> Path:
    override = os.environ.get("JENAI_ENV_FILE")
    if override:
        return Path(override).expanduser()
    return default_config_path().parent / ".env"


@dataclass(frozen=True)
class EnvFileResult:
    path: Path
    found: bool
    explicit: bool
    loaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def load_env_file(path: Path | None = None) -> EnvFileResult:
    """Load KEY=VALUE pairs from the JenAI env file into os.environ.

    Variables already present in the environment always win, so a shell export
    can still override the file. Every entry point goes through this (CLI, TUI,
    WebUI, doctor) so API keys behave the same no matter how JenAI is launched.
    """
    explicit = path is not None or "JENAI_ENV_FILE" in os.environ
    env_path = path or default_env_file_path()
    try:
        text = env_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return EnvFileResult(path=env_path, found=False, explicit=explicit)

    loaded: list[str] = []
    skipped: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key in os.environ:
            skipped.append(key)
            continue
        os.environ[key] = value
        loaded.append(key)

    return EnvFileResult(
        path=env_path, found=True, explicit=explicit, loaded=loaded, skipped=skipped
    )


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
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_input=False)
        )
        raise ConfigError(f"Config file has invalid JenAI settings: {details}") from exc


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    config_path = path or default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = config.model_dump(mode="python", exclude_none=True)
    rendered = tomli_w.dumps(payload)
    fd, tmp_name = tempfile.mkstemp(
        dir=config_path.parent, prefix=f".{config_path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(rendered)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, config_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return config_path


def build_minimal_config(
    *,
    provider_name: str,
    provider: str,
    default_model: str,
    base_url: str | None = None,
    api_key_env: str | None = None,
    locations_path: str = "locations.toml",
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
        locations_path=locations_path,
        created_by_setup=True,
    )
