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
from jenai.config.models import AppConfig


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
