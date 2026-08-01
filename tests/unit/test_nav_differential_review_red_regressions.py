from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from jenai.acceptance import nav_differential_runner as runner
from jenai.acceptance.nav_differential_runner import (
    DifferentialCaptureOptions,
    DifferentialMode,
    ResetPolicy,
)
from jenai.config.models import AppConfig

ArtifactFactory = Callable[..., dict[str, object]]


def _shift_host_timestamp(value: object, delta_ns: int) -> None:
    if not isinstance(value, dict):
        return
    for key in (
        "host_monotonic_ns",
        "request_host_monotonic_ns",
        "requested_host_monotonic_ns",
        "completed_host_monotonic_ns",
        "observed_host_monotonic_ns",
    ):
        timestamp = value.get(key)
        if type(timestamp) is int:
            value[key] = timestamp + delta_ns


def _delay_r2_post_terminal_observation(
    artifact: dict[str, Any],
    *,
    delta_ns: int,
) -> None:
    """Keep one artifact self-consistent while delaying its post-terminal evidence."""

    timeline = cast(dict[str, Any], artifact["t1_goal_dispatch"])
    timeline["return_host_monotonic_ns"] = (
        cast(int, timeline["return_host_monotonic_ns"]) + delta_ns
    )

    window = cast(dict[str, Any], artifact["final_observation_window"])
    window["start_host_monotonic_ns"] = cast(int, window["start_host_monotonic_ns"]) + delta_ns
    window["end_host_monotonic_ns"] = cast(int, window["end_host_monotonic_ns"]) + delta_ns
    for key in (
        "clock_samples",
        "map_pose_samples",
        "map_pose_attempts",
        "amcl_samples",
        "valid_amcl_samples",
        "odom_samples",
        "valid_odom_samples",
        "ground_truth_samples",
        "verified_ground_truth_samples",
    ):
        for sample in cast(list[object], window[key]):
            _shift_host_timestamp(sample, delta_ns)

    for sample in cast(list[object], artifact["final_map_pose_samples"]):
        _shift_host_timestamp(sample, delta_ns)
    for observation in cast(list[dict[str, Any]], artifact["pose_observations"]):
        if observation.get("purpose") in {"r2_completion_verdict", "final_window"}:
            _shift_host_timestamp(observation, delta_ns)

    complete_streams = cast(dict[str, list[dict[str, Any]]], artifact["topic_samples"])
    terminal_ns = cast(
        int,
        cast(dict[str, Any], artifact["nav2_terminal"])["observed_host_monotonic_ns"],
    )
    for samples in complete_streams.values():
        for sample in samples:
            timestamp = sample.get("host_monotonic_ns")
            if type(timestamp) is int and timestamp > terminal_ns:
                sample["host_monotonic_ns"] = timestamp + delta_ns

    _shift_host_timestamp(artifact["terminal_map_identity_checkpoint"], delta_ns)
    _shift_host_timestamp(artifact["post_final_window_map_identity_checkpoint"], delta_ns)
    _shift_host_timestamp(artifact["post_final_window_runtime_stack_checkpoint"], delta_ns)
    _shift_host_timestamp(artifact["post_cleanup_input_continuity"], delta_ns)


def test_pair_comparison_rejects_different_terminal_relative_window_start(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    """R1 and R2 cannot compare different post-terminal settling intervals."""

    left = cast(
        dict[str, Any],
        differential_artifact_factory(mode="R1_bridge_nav2"),
    )
    right = cast(
        dict[str, Any],
        differential_artifact_factory(mode="R2_jenai_no_retry"),
    )
    _delay_r2_post_terminal_observation(right, delta_ns=500_000_000)

    report = runner.compare_differential_artifacts(left, right)

    assert report["included"] is False
    assert "terminal" in str(report["detail"]).lower()


def test_persisted_runtime_identity_excludes_raw_parameter_and_process_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = "do-not-persist-runtime-secret"
    raw_parameter_dump = f"/amcl:\\n  ros__parameters:\\n    credential: {secret}"
    raw_process_argv = f"4242 ros2 run vendor node --api-token={secret}"

    def command_output(command: list[str], **_: object) -> str:
        if command[:3] == ["ros2", "param", "dump"]:
            return raw_parameter_dump
        if command[:2] == ["pgrep", "-af"]:
            return raw_process_argv
        return ""

    monkeypatch.setattr(runner, "_command_output", command_output)
    monkeypatch.setattr(runner, "_controller_odom_topic", lambda **_: "/chassis/odom")
    monkeypatch.setattr(runner, "_nav2_process_generation", lambda _session: None)

    config_path = tmp_path / "config.toml"
    config_path.write_text('version = "0.1.0"\n', encoding="utf-8")
    options = DifferentialCaptureOptions(
        output=tmp_path / "capture.json",
        location="Dock",
        pair_id="pair-secret-safe-identity",
        mode=DifferentialMode.R1_BRIDGE_NAV2,
        simulation_epoch="epoch-secret-safe-identity",
        reset_policy=ResetPolicy.NAV2_RESTART,
    )
    artifact = runner._base_artifact(options)
    artifact["runtime_identity"] = runner._runtime_identity(
        AppConfig(deployment_mode="simulation"),
        config_path,
        reviewed_git_sha=None,
        expected_source_root=None,
        scene_path=None,
        live_scene_sha256=None,
        simulation_epoch=options.simulation_epoch,
    )
    artifact["overall"] = "preflight_only"
    artifact["finished_at"] = "2026-07-31T00:00:00+00:00"

    runner._write_capture_artifact(options.output, artifact)
    serialized = options.output.read_text(encoding="utf-8")
    persisted_identity = cast(dict[str, Any], json.loads(serialized)["runtime_identity"])

    assert secret not in serialized
    assert raw_parameter_dump not in serialized
    assert raw_process_argv not in serialized
    assert (
        persisted_identity["runtime_parameter_sha256"]["/amcl"]
        == hashlib.sha256(raw_parameter_dump.encode("utf-8")).hexdigest()
    )


def test_unreadable_source_continuity_fails_closed_and_persists_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    config_path = source_root / "config.toml"
    locations_path = source_root / "locations.json"
    bridge_path = source_root / "ros_bridge.py"
    for path in (config_path, locations_path, bridge_path):
        path.write_text("reviewed\n", encoding="utf-8")

    real_sha256 = runner._sha256

    def sha256_with_unreadable_config(path: Path | None) -> str | None:
        if path == config_path:
            raise PermissionError("config became unreadable")
        return real_sha256(path)

    monkeypatch.setattr(runner, "_sha256", sha256_with_unreadable_config)
    monkeypatch.setattr(
        runner,
        "_command_output",
        lambda command, **_: "a" * 40 if command[-2:] == ["rev-parse", "HEAD"] else "",
    )

    options = DifferentialCaptureOptions(
        output=tmp_path / "capture.json",
        location="Dock",
        pair_id="pair-unreadable-source",
        mode=DifferentialMode.R1_BRIDGE_NAV2,
        simulation_epoch="epoch-unreadable-source",
        reset_policy=ResetPolicy.NAV2_RESTART,
    )
    artifact = runner._base_artifact(options)
    artifact["runtime_identity"] = {
        "source_root": str(source_root),
        "config_path": str(config_path),
        "config_sha256": "1" * 64,
        "locations_path": str(locations_path),
        "locations_sha256": real_sha256(locations_path),
        "bridge_script_path": str(bridge_path),
        "bridge_script_sha256": real_sha256(bridge_path),
        "git_sha": "a" * 40,
        "git_dirty": False,
    }
    artifact["overall"] = "insufficient_evidence"

    persisted = asyncio.run(
        runner._finalize_capture(
            artifact,
            options,
            bridge=None,
            config=None,
            watch_ids=[],
            heartbeat=None,
            motion_attempted=True,
        )
    )

    assert options.output.is_file()
    continuity = cast(dict[str, Any], persisted["post_cleanup_input_continuity"])
    assert continuity["status"] == "FAIL"
    assert continuity["failures"]


def test_live_preflight_post_cleanup_drift_is_not_eligible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    scene = source_root / "warehouse.usd"
    config_path = source_root / "config.toml"
    locations_path = source_root / "locations.json"
    bridge_path = source_root / "ros_bridge.py"
    for path in (scene, config_path, locations_path, bridge_path):
        path.write_text("reviewed\n", encoding="utf-8")

    real_sha256 = runner._sha256

    def sha256_with_changed_config(path: Path | None) -> str | None:
        if path == config_path:
            return "f" * 64
        return real_sha256(path)

    monkeypatch.setattr(runner, "_sha256", sha256_with_changed_config)
    monkeypatch.setattr(
        runner,
        "_command_output",
        lambda command, **_: "a" * 40 if command[-2:] == ["rev-parse", "HEAD"] else "",
    )

    options = DifferentialCaptureOptions(
        output=tmp_path / "live-preflight.json",
        location="Dock",
        pair_id="pair-live-preflight-drift",
        mode=DifferentialMode.R1_BRIDGE_NAV2,
        simulation_epoch="epoch-live-preflight-drift",
        reset_policy=ResetPolicy.FULL_CLEAN,
        live_preflight=True,
        expected_source_root=source_root,
        expected_git_sha="a" * 40,
        scene_path=scene,
        live_scene_sha256="b" * 64,
    )
    artifact = runner._base_artifact(options)
    artifact["runtime_identity"] = {
        "source_root": str(source_root),
        "config_path": str(config_path),
        "config_sha256": real_sha256(config_path),
        "locations_path": str(locations_path),
        "locations_sha256": real_sha256(locations_path),
        "bridge_script_path": str(bridge_path),
        "bridge_script_sha256": real_sha256(bridge_path),
        "git_sha": "a" * 40,
        "git_dirty": False,
    }
    artifact["overall"] = "preflight_only"
    artifact["checks"].append(
        {
            "id": "live_preflight",
            "status": "PASS",
            "detail": "All live pre-dispatch gates passed without forwarding a goal.",
        }
    )

    persisted = asyncio.run(
        runner._finalize_capture(
            artifact,
            options,
            bridge=None,
            config=None,
            watch_ids=[],
            heartbeat=None,
            motion_attempted=False,
        )
    )

    assert persisted["post_cleanup_input_continuity"]["status"] == "FAIL"
    assert persisted["overall"] == "insufficient_evidence"


def test_live_preflight_nav2_generation_drift_is_not_eligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact: dict[str, Any] = {
        "live_preflight_requested": True,
        "overall": "preflight_only",
        "checks": [],
        "runtime_identity": {
            "nav2_tmux_session": "jenai-nav2",
            "nav2_process_generation": {"generation": "before"},
        },
    }
    monkeypatch.setattr(
        runner,
        "_nav2_process_generation",
        lambda _session: {"generation": "after"},
    )

    runner._record_end_generation(artifact)

    assert artifact["checks"][-1]["id"] == "nav2_process_generation_end"
    assert artifact["checks"][-1]["status"] == "FAIL"
    assert artifact["overall"] == "insufficient_evidence"
