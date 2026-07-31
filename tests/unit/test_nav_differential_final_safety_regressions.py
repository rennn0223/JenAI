from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import jenai.acceptance.nav_differential_runner as runner
from jenai.acceptance.nav_differential import CanonicalGoal
from jenai.acceptance.nav_differential_runner import (
    DifferentialCaptureOptions,
    DifferentialMode,
    ResetPolicy,
)
from jenai.adapters.locations import save_locations
from jenai.config.models import AppConfig, SiteProfile
from jenai.config.store import save_config
from jenai.schemas import Location, Pose2D
from jenai.site_assets import fingerprint_locations_file


def _options(tmp_path: Path) -> DifferentialCaptureOptions:
    return DifferentialCaptureOptions(
        output=tmp_path / "capture.json",
        location="Dock",
        pair_id="pair-output-reservation",
        mode=DifferentialMode.R1_BRIDGE_NAV2,
        simulation_epoch="epoch-output-reservation",
        reset_policy=ResetPolicy.NAV2_RESTART,
    )


def test_capture_reserves_output_before_either_run_can_reach_motion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Two callers targeting one artifact path must not both reach motion."""

    options = _options(tmp_path)
    config = AppConfig(deployment_mode="simulation")
    goal = CanonicalGoal.from_yaw(
        frame_id="map",
        x=1.0,
        y=2.0,
        yaw=0.0,
        simulation_epoch=options.simulation_epoch,
    )
    motion_entries = 0
    first_motion_entry = asyncio.Event()
    release_motion = asyncio.Event()

    monkeypatch.setattr(
        runner,
        "_prepare_capture",
        lambda _options: (
            tmp_path / "config.toml",
            config,
            {"capability_id": "navigate", "goal": {}},
            goal,
            {"deployment_mode": "simulation"},
            None,
        ),
    )
    monkeypatch.setattr(runner, "_complete_without_live_bridge", lambda *args: False)

    async def fake_live_path(
        artifact: dict[str, Any],
        _options: DifferentialCaptureOptions,
        **kwargs: Any,
    ) -> None:
        nonlocal motion_entries
        resources = kwargs["resources"]
        resources.stage = "motion_dispatch"
        resources.motion_attempted = True
        motion_entries += 1
        first_motion_entry.set()
        await release_motion.wait()
        artifact["overall"] = "captured"

    monkeypatch.setattr(runner, "_capture_live_path", fake_live_path)

    async def exercise_race() -> list[object]:
        first = asyncio.create_task(runner.capture_navigation_differential(options))
        await first_motion_entry.wait()
        second = asyncio.create_task(runner.capture_navigation_differential(options))
        # Give the second capture enough scheduler turns to pass any non-atomic
        # ``Path.exists`` check and enter the same fake motion seam.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        release_motion.set()
        return await asyncio.gather(first, second, return_exceptions=True)

    results = asyncio.run(exercise_race())

    assert motion_entries == 1
    assert options.output.is_file()
    assert len(results) == 2


def test_persisted_runtime_identity_never_discloses_raw_dds_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Artifacts may retain DDS digests, never raw paths, URIs, or contents."""

    options = _options(tmp_path)
    profile_secret = "dds-profile-secret-value"
    profile_path = tmp_path / f"{profile_secret}.xml"
    profile_path.write_text(
        f"<profiles><transport password='{profile_secret}'/></profiles>",
        encoding="utf-8",
    )
    cyclone_uri = f"file:///{profile_secret}/cyclonedds.xml"
    monkeypatch.setenv("FASTRTPS_DEFAULT_PROFILES_FILE", str(profile_path))
    monkeypatch.setenv("CYCLONEDDS_URI", cyclone_uri)
    monkeypatch.setattr(runner, "_command_output", lambda *args, **kwargs: "")
    monkeypatch.setattr(runner, "_controller_odom_topic", lambda **kwargs: "/chassis/odom")
    monkeypatch.setattr(runner, "_nav2_process_generation", lambda _session: None)

    config_path = tmp_path / "config.toml"
    config_path.write_text('version = "0.1.0"\n', encoding="utf-8")
    identity = runner._runtime_identity(
        AppConfig(deployment_mode="simulation"),
        config_path,
        reviewed_git_sha=None,
        expected_source_root=None,
        scene_path=None,
        live_scene_sha256=None,
        simulation_epoch="epoch-dds-redaction",
    )
    artifact = runner._base_artifact(options)
    artifact["runtime_identity"] = identity
    artifact["overall"] = "preflight_only"
    artifact["finished_at"] = "2026-07-31T00:00:00+00:00"

    runner._write_capture_artifact(options.output, artifact)
    serialized = options.output.read_text(encoding="utf-8")

    assert str(profile_path) not in serialized
    assert cyclone_uri not in serialized
    assert profile_secret not in serialized
    assert hashlib.sha256(profile_path.read_bytes()).hexdigest() in serialized
    assert "dds_profile_sha256" in json.loads(serialized)["runtime_identity"]


def test_runtime_identity_uses_script_recorded_nav2_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    rendered_params = state_dir / "nav2-params.yaml.12345"
    rendered_params.write_text("controller_server: {}\n", encoding="utf-8")
    (state_dir / "nav2-override-path").write_text(
        f"{rendered_params}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JENAI_NAV2_STATE_DIR", str(state_dir))
    monkeypatch.delenv("JENAI_NAV2_OVERRIDE_PARAMS", raising=False)
    monkeypatch.setattr(runner, "_nav2_runtime_identity", lambda *args, **kwargs: {})
    config_path = tmp_path / "config.toml"
    config_path.write_text('version = "0.1.0"\n', encoding="utf-8")

    identity = runner._runtime_identity(
        AppConfig(deployment_mode="simulation"),
        config_path,
        reviewed_git_sha=None,
        expected_source_root=None,
        scene_path=None,
        live_scene_sha256=None,
        simulation_epoch="epoch-nav2-override",
    )

    assert identity["nav_params_path"] == str(rendered_params)
    assert identity["nav_params_sha256"] == hashlib.sha256(rendered_params.read_bytes()).hexdigest()


def test_nav2_state_dir_matches_launcher_default_without_xdg_runtime_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("JENAI_NAV2_STATE_DIR", raising=False)
    monkeypatch.setattr(runner.os, "getuid", lambda: 424242)

    assert runner._nav2_state_dir() == Path("/tmp/jenai-nav2-424242")


def test_nav_params_identity_does_not_fall_back_to_stale_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    stale_params = tmp_path / "nav2-params.yaml"
    stale_params.write_text("controller_server: {}\n", encoding="utf-8")
    monkeypatch.setenv("JENAI_NAV2_STATE_DIR", str(state_dir))
    monkeypatch.setenv("JENAI_NAV2_OVERRIDE_PARAMS", str(stale_params))

    assert runner._nav_params_path("nav2") is None


@pytest.mark.parametrize("capability_id", ["dock_approach", "area_patrol"])
def test_target_binding_rejects_non_navigate_capability_before_motion(
    capability_id: str,
) -> None:
    """This harness measures navigate only; other capabilities fail closed."""

    goal = CanonicalGoal.from_yaw(
        frame_id="map",
        x=1.0,
        y=2.0,
        yaw=0.0,
        simulation_epoch="epoch-target-binding",
    )

    with pytest.raises(ValueError, match="navigate"):
        runner._target_binding(
            requested_query="Dock",
            bound_action={
                "capability_id": capability_id,
                "goal": {"name": "Dock", "id": "loc-dock"},
            },
            goal=goal,
            locations_sha256="c" * 64,
        )


def test_prepare_capture_marks_regular_saved_location_as_navigate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The documented non-dock preflight must bind the navigate capability."""

    locations_path = tmp_path / "locations.toml"
    location = Location(
        name="map_left_down",
        frame_id="map",
        pose=Pose2D(x=-8.5, y=-7.5, yaw=0.785),
    )
    save_locations([location], locations_path)
    locations_sha256 = fingerprint_locations_file(locations_path)
    config_path = tmp_path / "config.toml"
    save_config(
        AppConfig(
            deployment_mode="simulation",
            route_adapter="nav2",
            locations_path="locations.toml",
            site=SiteProfile(
                site_id="warehouse",
                display_name="Warehouse",
                active=True,
                validated=True,
                map_sha256="a" * 64,
                map_frame="map",
                locations_path="locations.toml",
                locations_sha256=locations_sha256,
                validated_routes=[location.name],
            ),
        ),
        config_path,
    )
    monkeypatch.setattr(runner, "_runtime_identity", lambda *args, **kwargs: {})
    monkeypatch.setattr(runner, "_apply_runtime_fingerprint", lambda identity: None)
    options = DifferentialCaptureOptions(
        output=tmp_path / "capture.json",
        config_path=config_path,
        location=location.name,
        pair_id="pair-navigate-binding",
        mode=DifferentialMode.R1_BRIDGE_NAV2,
        simulation_epoch="epoch-navigate-binding",
        reset_policy=ResetPolicy.FULL_CLEAN,
    )

    _path, _config, bound_action, _goal, _identity, binding = runner._prepare_capture(options)

    assert bound_action["capability_id"] == "navigate"
    assert binding.capability_id == "navigate"
