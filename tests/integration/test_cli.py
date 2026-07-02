from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from jenai.cli.main import app
from jenai.config import save_config
from jenai.config.store import build_minimal_config

runner = CliRunner()


def test_version_command() -> None:
    from jenai import __version__

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    # Assert against the real package version so this can't drift on a bump.
    assert f"JenAI {__version__}" in result.stdout
    assert __version__ != "0.0.0+dev"  # metadata resolved (package is installed)


def test_doctor_json_command(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    save_config(
        build_minimal_config(
            provider_name="test",
            provider="openai",
            default_model="gpt-test",
            api_key_env="",
        ),
        config_path,
    )

    result = runner.invoke(app, ["doctor", "--config", str(config_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["overall"] in {"pass", "warn", "fail"}
    assert isinstance(payload["items"], list)


def test_main_command_starts_tui(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    save_config(
        build_minimal_config(
            provider_name="test",
            provider="openai",
            default_model="gpt-test",
            api_key_env="",
        ),
        config_path,
    )
    started = {}

    def fake_run_tui(config, *, config_path, doctor_result):
        started["active_provider"] = config.active_provider
        started["config_path"] = config_path
        started["doctor_result"] = doctor_result

    monkeypatch.setattr("jenai.cli.main.run_tui", fake_run_tui)

    result = runner.invoke(app, ["--config", str(config_path)])

    assert result.exit_code == 0
    assert started["active_provider"] == "test"
    assert started["config_path"] == config_path
    assert started["doctor_result"].items


def _config_with_locations(tmp_path: Path) -> Path:
    from jenai.adapters.locations import save_locations
    from jenai.schemas import Location, Pose2D

    config_path = tmp_path / "config.toml"
    save_config(
        build_minimal_config(
            provider_name="test",
            provider="openai",
            default_model="gpt-test",
            api_key_env="",
        ),
        config_path,
    )
    save_locations(
        [
            Location(
                name="Engineering Building",
                aliases=["engineering"],
                frame_id="map",
                pose=Pose2D(x=0, y=0, yaw=0),
            )
        ],
        tmp_path / "locations.toml",
    )
    return config_path


def test_loc_list_command(tmp_path: Path) -> None:
    config_path = _config_with_locations(tmp_path)

    result = runner.invoke(app, ["loc", "list", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Engineering Building" in result.stdout


def test_loc_show_command(tmp_path: Path) -> None:
    config_path = _config_with_locations(tmp_path)

    result = runner.invoke(app, ["loc", "show", "engineering", "--config", str(config_path)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["name"] == "Engineering Building"


def test_loc_show_missing_location_exits_nonzero(tmp_path: Path) -> None:
    config_path = _config_with_locations(tmp_path)

    result = runner.invoke(app, ["loc", "show", "nowhere", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "not found" in result.stdout


def test_route_command_prompts_and_executes(tmp_path: Path, monkeypatch) -> None:
    config_path = _config_with_locations(tmp_path)
    from jenai.schemas import Location, Pose2D

    goal = Location(name="Mechanical Hall", frame_id="map", pose=Pose2D(x=1, y=1, yaw=0))
    from jenai.adapters.locations import load_locations, save_locations

    locations = load_locations(tmp_path / "locations.toml")
    save_locations([*locations, goal], tmp_path / "locations.toml")

    result = runner.invoke(
        app,
        [
            "route",
            "from Engineering Building to Mechanical Hall",
            "--config",
            str(config_path),
        ],
        input="y\n",
    )

    assert result.exit_code == 0
    # No navigation backend wired: the CLI honestly reports "unavailable", not success.
    assert "unavailable" in result.stdout


def test_route_command_cancelled_by_user(tmp_path: Path) -> None:
    config_path = _config_with_locations(tmp_path)
    from jenai.adapters.locations import load_locations, save_locations
    from jenai.schemas import Location, Pose2D

    goal = Location(name="Mechanical Hall", frame_id="map", pose=Pose2D(x=1, y=1, yaw=0))
    locations = load_locations(tmp_path / "locations.toml")
    save_locations([*locations, goal], tmp_path / "locations.toml")

    result = runner.invoke(
        app,
        [
            "route",
            "from Engineering Building to Mechanical Hall",
            "--config",
            str(config_path),
        ],
        input="n\n",
    )

    assert result.exit_code == 0
    assert "Cancelled" in result.stdout
