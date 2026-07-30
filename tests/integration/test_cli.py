from __future__ import annotations

import json
import os
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


def test_onboard_backs_up_config_and_preserves_user_data(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("old config\n", encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text("NVIDIA_API_KEY=secret\n", encoding="utf-8")
    locations_path = tmp_path / "locations.toml"
    locations_path.write_text("[[locations]]\nname='Dock'\n", encoding="utf-8")

    def fake_wizard(path: Path) -> Path:
        path.write_text("new config\n", encoding="utf-8")
        return path

    monkeypatch.setattr("jenai.cli.main.run_setup_wizard", fake_wizard)

    result = runner.invoke(app, ["onboard", "--config", str(config_path), "--yes"])

    assert result.exit_code == 0
    assert config_path.read_text(encoding="utf-8") == "new config\n"
    backups = list(tmp_path.glob("config.toml.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "old config\n"
    assert backups[0].stat().st_mode & 0o777 == 0o600
    assert env_path.read_text(encoding="utf-8") == "NVIDIA_API_KEY=secret\n"
    assert locations_path.read_text(encoding="utf-8") == "[[locations]]\nname='Dock'\n"


def test_onboard_cancel_changes_nothing(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("keep me\n", encoding="utf-8")

    def unexpected_wizard(path: Path) -> Path:
        raise AssertionError("wizard must not run after cancellation")

    monkeypatch.setattr("jenai.cli.main.run_setup_wizard", unexpected_wizard)
    result = runner.invoke(app, ["onboard", "--config", str(config_path)], input="n\n")

    assert result.exit_code == 0
    assert config_path.read_text(encoding="utf-8") == "keep me\n"
    assert list(tmp_path.glob("config.toml.bak-*")) == []
    assert "nothing changed" in result.stdout


def test_onboard_without_config_starts_wizard_without_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.toml"
    called: list[Path] = []

    def fake_wizard(path: Path) -> Path:
        called.append(path)
        path.write_text("created\n", encoding="utf-8")
        return path

    monkeypatch.setattr("jenai.cli.main.run_setup_wizard", fake_wizard)
    result = runner.invoke(app, ["onboard", "--config", str(config_path)])

    assert result.exit_code == 0
    assert called == [config_path]
    assert config_path.read_text(encoding="utf-8") == "created\n"
    assert list(tmp_path.glob("config.toml.bak-*")) == []


def test_scaffold_build_repairs_once_and_reports_success(tmp_path: Path, monkeypatch) -> None:
    from jenai.tools import ros2_pkg_core
    from jenai.tools.ros2_pkg_core import PackagePlan

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
    plan = PackagePlan(
        package_name="demo_pkg",
        description="Demo package",
        node_name="demo_node",
        node_code="def main():\n    pass\n",
        dependencies=["rclpy"],
    )
    build_results = iter([(False, "SyntaxError"), (True, "built")])
    build_calls: list[str] = []

    async def generate(_config, _spec):
        return plan

    async def repair(_config, original, log):
        assert original is plan
        assert log == "SyntaxError"
        return original.model_copy(update={"node_code": "# repaired\n"})

    def build(_workspace, package_name):
        build_calls.append(package_name)
        return next(build_results)

    monkeypatch.setattr(ros2_pkg_core, "generate_package_plan", generate)
    monkeypatch.setattr(ros2_pkg_core, "repair_node", repair)
    monkeypatch.setattr(ros2_pkg_core, "build_package", build)

    workspace = tmp_path / "ros_ws"
    result = runner.invoke(
        app,
        [
            "scaffold",
            "make a demo",
            "--config",
            str(config_path),
            "--ws",
            str(workspace),
            "--build",
        ],
        input="y\n",
    )

    assert result.exit_code == 0
    assert build_calls == ["demo_pkg", "demo_pkg"]
    assert "Build succeeded" in result.stdout
    assert (workspace / "src/demo_pkg/demo_pkg/demo_node.py").read_text() == "# repaired\n"


def test_first_main_wizard_continues_into_tui(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    configured = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="gpt-test",
        api_key_env="",
    )
    calls: dict[str, object] = {}

    def fake_wizard(path: Path) -> Path:
        calls["wizard"] = path
        save_config(configured, path)
        return path

    def fake_run_tui(config, *, config_path, doctor_result):
        calls["provider"] = config.active_provider
        calls["tui_path"] = config_path
        calls["doctor"] = doctor_result

    monkeypatch.setattr("jenai.cli.main.run_setup_wizard", fake_wizard)
    monkeypatch.setattr("jenai.cli.main.run_tui", fake_run_tui)

    result = runner.invoke(app, ["--config", str(config_path)])

    assert result.exit_code == 0
    assert calls["wizard"] == config_path
    assert calls["provider"] == "test"
    assert calls["tui_path"] == config_path
    assert calls["doctor"].items
    assert "Config written to" in result.stdout


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


def test_main_missing_adjacent_env_does_not_claim_override_is_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "custom" / "config.toml"
    save_config(
        build_minimal_config(
            provider_name="test",
            provider="openai",
            default_model="gpt-test",
            api_key_env="",
        ),
        config_path,
    )
    monkeypatch.delenv("JENAI_ENV_FILE", raising=False)
    monkeypatch.setattr("jenai.cli.main.run_tui", lambda *args, **kwargs: None)

    result = runner.invoke(app, ["--config", str(config_path)])

    assert result.exit_code == 0
    assert "JENAI_ENV_FILE points to a missing file" not in result.output


def test_main_reports_explicit_missing_env_override(tmp_path: Path, monkeypatch) -> None:
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
    monkeypatch.setenv("JENAI_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setattr("jenai.cli.main.run_tui", lambda *args, **kwargs: None)

    result = runner.invoke(app, ["--config", str(config_path)])

    assert result.exit_code == 0
    assert "JENAI_ENV_FILE points to a missing file" in result.output


def test_main_loads_env_next_to_an_explicit_config(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "custom" / "config.toml"
    env_name = "JENAI_TEST_CUSTOM_CONFIG_KEY"
    save_config(
        build_minimal_config(
            provider_name="test",
            provider="openai",
            default_model="gpt-test",
            api_key_env=env_name,
        ),
        config_path,
    )
    (config_path.parent / ".env").write_text(f"{env_name}=secret\n", encoding="utf-8")
    monkeypatch.delenv(env_name, raising=False)
    observed: dict[str, str | None] = {}

    def fake_run_tui(config, *, config_path, doctor_result):
        observed["key"] = os.environ.get(env_name)

    monkeypatch.setattr("jenai.cli.main.run_tui", fake_run_tui)

    try:
        result = runner.invoke(app, ["--config", str(config_path)])
    finally:
        os.environ.pop(env_name, None)

    assert result.exit_code == 0
    assert observed["key"] == "secret"


def test_subcommand_loads_env_next_to_its_explicit_config(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "custom" / "config.toml"
    env_name = "JENAI_TEST_SUBCOMMAND_CONFIG_KEY"
    save_config(
        build_minimal_config(
            provider_name="test",
            provider="openai",
            default_model="gpt-test",
            api_key_env=env_name,
        ),
        config_path,
    )
    (config_path.parent / ".env").write_text(f"{env_name}=secret\n", encoding="utf-8")
    monkeypatch.delenv(env_name, raising=False)
    try:
        result = runner.invoke(app, ["models", "--config", str(config_path)])
        observed = os.environ.get(env_name)
    finally:
        os.environ.pop(env_name, None)
    assert result.exit_code == 0
    assert observed == "secret"


def test_subcommand_custom_env_is_not_shadowed_by_default_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    env_name = "JENAI_TEST_ENV_PRECEDENCE_KEY"
    default_config = tmp_path / "default" / "config.toml"
    custom_config = tmp_path / "custom" / "config.toml"
    for path in (default_config, custom_config):
        save_config(
            build_minimal_config(
                provider_name="test",
                provider="openai",
                default_model="gpt-test",
                api_key_env=env_name,
            ),
            path,
        )
    (default_config.parent / ".env").write_text(f"{env_name}=default-secret\n", encoding="utf-8")
    (custom_config.parent / ".env").write_text(f"{env_name}=custom-secret\n", encoding="utf-8")
    monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setattr("jenai.cli.main.default_config_path", lambda: default_config)
    try:
        result = runner.invoke(app, ["models", "--config", str(custom_config)])
        observed = os.environ.get(env_name)
    finally:
        os.environ.pop(env_name, None)

    assert result.exit_code == 0
    assert observed == "custom-secret"


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
    # Saved coordinates are never sent without an explicitly active Site Profile.
    assert "blocked" in result.stdout
    assert "Site Profile" in result.stdout


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


def test_help_command_lists_cli_and_oneshot_recipes() -> None:
    result = runner.invoke(app, ["help"])

    assert result.exit_code == 0
    # Every subcommand family shows up, plus the one-shot recipes section.
    for needle in ("JenAI doctor", "JenAI web", "JenAI daemon", "一鍵常用", "/stop"):
        assert needle in result.stdout


def test_site_activate_status_and_deactivate(tmp_path: Path) -> None:
    from jenai.adapters.locations import save_locations
    from jenai.config import load_config
    from jenai.schemas import Location, Pose2D

    config_path = tmp_path / "config.toml"
    save_config(
        build_minimal_config(
            provider_name="test",
            provider="openai",
            default_model="model",
            api_key_env="",
        ),
        config_path,
    )
    save_locations(
        [
            Location(name="Inspection A", pose=Pose2D(x=1.0, y=2.0, yaw=0.0)),
            Location(name="Home", pose=Pose2D(x=0.0, y=0.0, yaw=0.0)),
        ],
        tmp_path / "locations.toml",
    )
    profile = tmp_path / "site.toml"
    profile.write_text(
        """
[site]
site_id = "warehouse"
display_name = "Warehouse"
version = "1"
map_sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
locations_path = "locations.toml"
validated_routes = ["Inspection A", "Home"]
home_location = "Home"

[[site.patrol_areas]]
area_id = "inspection"
display_name = "Inspection"
inspection_locations = ["Inspection A"]
""".strip(),
        encoding="utf-8",
    )

    activated = runner.invoke(
        app,
        [
            "site",
            "activate",
            str(profile),
            "--config",
            str(config_path),
        ],
    )

    assert activated.exit_code == 0
    assert "Activated Site Profile" in activated.stdout
    loaded = load_config(config_path)
    assert loaded.site.active is True
    assert loaded.site.locations_sha256 != "0" * 64

    status = runner.invoke(
        app,
        ["site", "status", "--config", str(config_path), "--json"],
    )
    assert status.exit_code == 0
    payload = json.loads(status.stdout)
    assert payload["site_id"] == "warehouse"
    assert payload["patrol_areas"][0]["area_id"] == "inspection"

    deactivated = runner.invoke(
        app,
        ["site", "deactivate", "--config", str(config_path), "--yes"],
    )
    assert deactivated.exit_code == 0
    assert load_config(config_path).site.active is False


def test_site_activate_rejects_unknown_area_location_without_writing(
    tmp_path: Path,
) -> None:
    from jenai.adapters.locations import save_locations
    from jenai.config import load_config
    from jenai.schemas import Location, Pose2D

    config_path = tmp_path / "config.toml"
    save_config(
        build_minimal_config(
            provider_name="test",
            provider="openai",
            default_model="model",
            api_key_env="",
        ),
        config_path,
    )
    save_locations(
        [Location(name="Home", pose=Pose2D(x=0.0, y=0.0, yaw=0.0))],
        tmp_path / "locations.toml",
    )
    profile = tmp_path / "invalid-site.toml"
    profile.write_text(
        """
[site]
site_id = "warehouse"
display_name = "Warehouse"
map_sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
locations_path = "locations.toml"
validated_routes = ["Home"]
home_location = "Home"

[[site.patrol_areas]]
area_id = "missing"
display_name = "Missing"
inspection_locations = ["Not Registered"]
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "site",
            "activate",
            str(profile),
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 1
    assert "unknown location" in result.stderr
    assert load_config(config_path).site.active is False


def test_site_map_identity_reports_live_fingerprint(monkeypatch) -> None:
    from types import SimpleNamespace

    observed = SimpleNamespace(
        algorithm="sha256-occupancy-grid-v1",
        digest="c" * 64,
        frame_id="map",
        width=100,
        height=80,
        resolution=0.05,
        origin_x=-1.0,
        origin_y=-2.0,
        origin_yaw=0.0,
        source="/map",
    )
    monkeypatch.setattr(
        "jenai.cli.site.read_live_map_identity",
        lambda: observed,
    )

    result = runner.invoke(app, ["site", "map-identity", "--json"])
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert payload["digest"] == "c" * 64
    assert payload["width"] == 100
    assert payload["source"] == "/map"


def test_site_init_creates_inactive_reviewable_draft(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from jenai.adapters.locations import save_locations
    from jenai.config import load_config
    from jenai.schemas import Location, Pose2D
    from jenai.site_profiles import load_site_profile_document

    config_path = tmp_path / "config.toml"
    save_config(
        build_minimal_config(
            provider_name="test",
            provider="openai",
            default_model="model",
            api_key_env="",
        ),
        config_path,
    )
    save_locations(
        [
            Location(
                name="Dock",
                tags=["dock"],
                pose=Pose2D(x=0.0, y=0.0, yaw=0.0),
            ),
            Location(name="Inspection A", pose=Pose2D(x=1.0, y=1.0, yaw=0.0)),
        ],
        tmp_path / "locations.toml",
    )
    monkeypatch.setattr(
        "jenai.cli.site.read_live_map_identity",
        lambda: SimpleNamespace(digest="d" * 64, frame_id="map"),
    )
    profile_path = tmp_path / "draft.toml"

    result = runner.invoke(
        app,
        [
            "site",
            "init",
            str(profile_path),
            "--config",
            str(config_path),
            "--site-id",
            "warehouse",
            "--name",
            "Warehouse",
            "--scene",
            "Isaac Sim Warehouse",
        ],
    )

    assert result.exit_code == 0
    assert "inactive Site Profile draft" in result.stdout
    profile = load_site_profile_document(profile_path)
    assert profile.site_id == "warehouse"
    assert profile.map_sha256 == "d" * 64
    assert profile.home_location == "Dock"
    assert profile.dock_location == "Dock"
    assert profile.patrol_areas[0].inspection_locations == ["Inspection A"]
    assert load_config(config_path).site.active is False

    second = runner.invoke(
        app,
        [
            "site",
            "init",
            str(profile_path),
            "--config",
            str(config_path),
        ],
    )
    assert second.exit_code == 1
    assert "already exists" in second.stderr
