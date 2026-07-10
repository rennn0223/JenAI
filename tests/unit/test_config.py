from __future__ import annotations

from pathlib import Path

import pytest

from jenai.config import ConfigError, load_config, save_config
from jenai.config.models import ForbiddenZone
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


def test_config_round_trip_preserves_every_nested_safety_section(tmp_path: Path) -> None:
    config = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )
    config.route_adapter = "odom"
    config.twin.enabled = True
    config.twin.forbidden_zones = [
        ForbiddenZone(name="stairs", x_min=1.0, y_min=2.0, x_max=3.0, y_max=4.0)
    ]
    config.map_datum.lat = 25.033
    config.map_datum.lon = 121.5654
    config.avoidance.enabled = True
    config.avoidance.floor_ref = 1.6
    config.avoidance.floor_snapshot = "/tmp/floor.npy"

    path = tmp_path / "config.toml"
    save_config(config, path)
    loaded = load_config(path)

    assert loaded.model_dump(mode="json") == config.model_dump(mode="json")


@pytest.mark.parametrize(
    ("section", "body"),
    [
        ("vehicle", "max_linear = -1.0"),
        ("vehicle", "max_angular = 0.0"),
        ("avoidance", "stop_distance = 2.0\nslow_distance = 1.0"),
        ("avoidance", "band_lo = 0.8\nband_hi = 0.2"),
        ("avoidance", "sectors = 0"),
        ("twin", "nav_timeout_s = 0.0"),
    ],
)
def test_config_rejects_unsafe_numeric_settings(
    tmp_path: Path, section: str, body: str
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f"[{section}]\n{body}\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(path)


def test_config_rejects_literal_api_key_without_echoing_it(tmp_path: Path) -> None:
    secret = "nvapi-example-secret-that-must-not-appear"
    path = tmp_path / "config.toml"
    path.write_text(
        f'''active_provider = "nvidia"

[provider_profiles.nvidia]
name = "nvidia"
provider = "nvidia"
api_key_env = "{secret}"
''',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as caught:
        load_config(path)

    assert "api_key_env" in str(caught.value)
    assert secret not in str(caught.value)
