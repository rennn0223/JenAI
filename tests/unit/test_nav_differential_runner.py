from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from jenai.acceptance.nav_differential import PairClassification, Pose2D
from jenai.acceptance.nav_differential_runner import (
    DIFFERENTIAL_EXECUTION_CONFIRMATION,
    DifferentialCaptureOptions,
    DifferentialMode,
    ResetPolicy,
    _base_artifact,
    _goal_ids,
    _median_pose,
    compare_differential_artifacts,
    load_differential_artifact,
)


def _goal_status(uuid: list[int], status: int) -> dict[str, object]:
    return {
        "goal_info": {
            "goal_id": {"uuid": uuid},
            "stamp": {"sec": 12, "nanosec": 34},
        },
        "status": status,
    }


def test_action_status_extracts_only_active_goal_uuids() -> None:
    message = {
        "status_list": [
            _goal_status([1] * 16, 2),
            _goal_status([2] * 16, 4),
        ]
    }

    assert _goal_ids(message, active_only=True) == {"01" * 16}
    assert _goal_ids(message, active_only=False) == {"01" * 16, "02" * 16}


def test_live_capture_requires_exact_motion_confirmation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exact confirmation text"):
        DifferentialCaptureOptions(
            output=tmp_path / "run.json",
            location="Dock",
            pair_id="pair-01",
            mode=DifferentialMode.R1_BRIDGE_NAV2,
            simulation_epoch="epoch-01",
            reset_policy=ResetPolicy.NAV2_RESTART,
            execute=True,
            confirmation="yes",
        )

    scene = tmp_path / "warehouse.usd"
    scene.write_text("#usda 1.0", encoding="utf-8")
    options = DifferentialCaptureOptions(
        output=tmp_path / "run.json",
        location="Dock",
        pair_id="pair-01",
        mode=DifferentialMode.R1_BRIDGE_NAV2,
        simulation_epoch="epoch-01",
        reset_policy=ResetPolicy.NAV2_RESTART,
        execute=True,
        expected_source_root=tmp_path,
        expected_git_sha="1" * 40,
        confirmation=DIFFERENTIAL_EXECUTION_CONFIRMATION,
        scene_path=scene,
        live_scene_sha256="a" * 64,
    )
    assert options.execute is True


def test_live_preflight_requires_live_identity_without_motion_confirmation(
    tmp_path: Path,
) -> None:
    scene = tmp_path / "warehouse.usd"
    scene.write_text("#usda 1.0", encoding="utf-8")
    options = DifferentialCaptureOptions(
        output=tmp_path / "preflight.json",
        location="Dock",
        pair_id="pair-01",
        mode=DifferentialMode.R1_BRIDGE_NAV2,
        simulation_epoch="epoch-01",
        reset_policy=ResetPolicy.NAV2_RESTART,
        live_preflight=True,
        expected_source_root=tmp_path,
        expected_git_sha="1" * 40,
        scene_path=scene,
        live_scene_sha256="a" * 64,
    )

    assert options.live_preflight is True
    assert options.execute is False
    assert options.confirmation == ""


def test_live_preflight_rejects_motion_mode_or_confirmation(tmp_path: Path) -> None:
    scene = tmp_path / "warehouse.usd"
    scene.write_text("#usda 1.0", encoding="utf-8")
    values = {
        "output": tmp_path / "preflight.json",
        "location": "Dock",
        "pair_id": "pair-01",
        "mode": DifferentialMode.R1_BRIDGE_NAV2,
        "simulation_epoch": "epoch-01",
        "reset_policy": ResetPolicy.NAV2_RESTART,
        "live_preflight": True,
        "expected_source_root": tmp_path,
        "expected_git_sha": "1" * 40,
        "scene_path": scene,
        "live_scene_sha256": "a" * 64,
    }

    with pytest.raises(ValueError, match="mutually exclusive"):
        DifferentialCaptureOptions(
            **values,
            execute=True,
            confirmation=DIFFERENTIAL_EXECUTION_CONFIRMATION,
        )
    with pytest.raises(ValueError, match="must not include motion confirmation"):
        DifferentialCaptureOptions(**values, confirmation="not-needed")
    with pytest.raises(ValueError, match="R1_bridge_nav2"):
        DifferentialCaptureOptions(**{**values, "mode": DifferentialMode.R2_JENAI_NO_RETRY})


def test_live_preflight_requires_reviewed_runtime_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute USD scene path"):
        DifferentialCaptureOptions(
            output=tmp_path / "preflight.json",
            location="Dock",
            pair_id="pair-01",
            mode=DifferentialMode.R1_BRIDGE_NAV2,
            simulation_epoch="epoch-01",
            reset_policy=ResetPolicy.NAV2_RESTART,
            live_preflight=True,
        )


def test_loader_rejects_contradictory_live_preflight_claims(tmp_path: Path) -> None:
    options = DifferentialCaptureOptions(
        output=tmp_path / "base.json",
        location="Dock",
        pair_id="pair-01",
        mode=DifferentialMode.R1_BRIDGE_NAV2,
        simulation_epoch="epoch-01",
        reset_policy=ResetPolicy.NAV2_RESTART,
    )
    base = _base_artifact(options)
    base.update(
        {
            "live_preflight_requested": True,
            "overall": "preflight_only",
            "finished_at": "2026-08-01T00:00:00+00:00",
            "dispatch_observations": [
                {
                    "nav_send_forwarded_host_monotonic_ns": None,
                    "state_before_forward": {"status": "PASS", "failures": []},
                }
            ],
        }
    )
    invalid_payloads = [
        {**copy.deepcopy(base), "execution_requested": True},
        {**copy.deepcopy(base), "motion_attempted": True},
        {
            **copy.deepcopy(base),
            "dispatch_observations": [
                {
                    "nav_send_forwarded_host_monotonic_ns": 123,
                    "state_before_forward": {"status": "PASS", "failures": []},
                }
            ],
        },
    ]

    for index, payload in enumerate(invalid_payloads):
        path = tmp_path / f"invalid-{index}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="live preflight"):
            load_differential_artifact(path)


def test_median_pose_uses_circular_yaw_mean() -> None:
    samples = [
        {"pose": {"x": 1.0, "y": 2.0, "yaw": 3.13}},
        {"pose": {"x": 1.2, "y": 2.2, "yaw": -3.13}},
        {"error": "missing"},
    ]

    pose = _median_pose(samples)

    assert pose is not None
    assert pose.x == pytest.approx(1.1)
    assert pose.y == pytest.approx(2.1)
    assert abs(abs(pose.yaw) - 3.141592653589793) < 0.02


def _artifact(
    *,
    mode: str,
    runtime: str = "runtime",
    epoch: str = "epoch",
    status: str = "succeeded",
    final_x: float = 1.0,
) -> dict[str, object]:
    goal = {
        "frame_id": "map",
        "x": 1.0,
        "y": 2.0,
        "yaw": 0.0,
        "qx": 0.0,
        "qy": 0.0,
        "qz": 0.0,
        "qw": 1.0,
        "stamp_ns": None,
        "clock_domain": "ros",
        "simulation_epoch": epoch,
        "stamp_fresh": None,
    }
    return {
        "schema_version": 1,
        "run_id": f"run-{mode}",
        "pair_id": "pair-01",
        "mode": mode,
        "reset_policy": "nav2_restart",
        "execution_requested": True,
        "measurement_contract": {
            "preflight_sample_s": 1.0,
            "final_sample_s": 2.0,
            "final_window_start_delay_s": 0.0,
            "sample_interval_s": 0.2,
            "max_topic_age_s": 1.0,
            "max_calibration_residual_m": 0.02,
            "min_final_pose_samples": 10,
            "final_wall_timeout_s": 15.0,
            "max_start_speed_mps": 0.02,
            "max_start_yaw_rate_rps": 0.03,
            "max_covariance_xy": 0.1,
        },
        "started_at": "2026-07-31T00:00:00+00:00",
        "finished_at": "2026-07-31T00:01:00+00:00",
        "runtime_identity": {"fingerprint": runtime},
        "checks": [],
        "overall": "captured",
        "t0_scenario_start": {
            "status": "PASS",
            "simulation_epoch": epoch,
            "map_to_base": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            "amcl_covariance_xy": 0.01,
            "stationary": True,
            "active_goal_ids": [],
        },
        "canonical_goal": goal,
        "t1_goal_dispatch": {
            "status": "PASS",
            "actual_goal": goal,
        },
        "final_observation_window": {"status": "PASS"},
        "cleanup": {"status": "PASS"},
        "final_map_pose_median": {"x": final_x, "y": 2.0, "yaw": 0.0},
        "final_ground_truth_map_median": None,
        "execution_status": status,
    }


def test_artifact_comparison_excludes_runtime_mismatch(
    differential_artifact_factory,
) -> None:
    report = compare_differential_artifacts(
        differential_artifact_factory(mode="R1_bridge_nav2", runtime="runtime-a"),
        differential_artifact_factory(mode="R2_jenai_no_retry", runtime="runtime-b"),
    )

    assert report["included"] is False
    assert report["classifications"] == [
        PairClassification.RUNTIME_STACK_IDENTITY_DIFFERENCE,
        PairClassification.PAIRING_GATE_FAILED,
    ]


def test_artifact_comparison_detects_jenai_verdict_only_difference(
    differential_artifact_factory,
) -> None:
    report = compare_differential_artifacts(
        differential_artifact_factory(mode="R1_bridge_nav2", status="succeeded"),
        differential_artifact_factory(mode="R2_jenai_no_retry", status="endpoint_mismatch"),
    )

    assert report["included"] is True
    assert report["classifications"] == [PairClassification.JENAI_VERDICT_ONLY_DIFFERENCE]
    assert report["included"] is True
    assert report["classifications"] == [PairClassification.JENAI_VERDICT_ONLY_DIFFERENCE]


def test_pose_model_used_by_artifacts_remains_finite() -> None:
    with pytest.raises(ValueError):
        Pose2D(x=float("nan"), y=0.0, yaw=0.0)
