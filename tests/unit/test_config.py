from __future__ import annotations

from pathlib import Path

import pytest

from jenai.config import ConfigError, load_config, save_config
from jenai.config.store import build_minimal_config


def test_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = build_minimal_config(
        provider_name="local",
        provider="ollama",
        default_model="ollama/llama3.2",
        base_url="http://localhost:11434",
        api_key_env="",
    )

    save_config(config, path)
    loaded = load_config(path)

    assert loaded.is_complete()
    assert loaded.active_provider == "local"
    assert loaded.model_bindings is not None
    assert loaded.model_bindings.default == "ollama/llama3.2"
    assert loaded.provider_profiles["local"].base_url == "http://localhost:11434"


def test_missing_config_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.toml")


def test_incomplete_config_is_not_complete(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('version = "0.1.0"\n', encoding="utf-8")

    loaded = load_config(path)

    assert loaded.is_complete() is False



def test_vehicle_profile_defaults_and_round_trip(tmp_path: Path) -> None:
    config = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )
    # Defaults: an existing config without [vehicle] behaves like before.
    assert config.vehicle.type == "ackermann"
    assert config.vehicle.cmd_vel_topic == "/cmd_vel"
    assert config.vehicle.cmd_vel_stamped is False

    config.vehicle.cmd_vel_topic = "/leatherback/cmd_vel"
    config.vehicle.max_linear = 1.2
    path = tmp_path / "config.toml"
    save_config(config, path)
    loaded = load_config(path)

    assert loaded.vehicle.cmd_vel_topic == "/leatherback/cmd_vel"
    assert loaded.vehicle.max_linear == 1.2
    assert loaded.vehicle.type == "ackermann"
