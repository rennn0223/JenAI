from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

import jenai.acceptance.nav_differential_runner as runner
from jenai.acceptance.nav_differential import (
    CanonicalGoal,
    GroundTruthCalibration,
    PairClassification,
    Pose2D,
)
from jenai.acceptance.nav_differential_runner import (
    DifferentialCaptureOptions,
    DifferentialMode,
    ResetPolicy,
    _apply_runtime_fingerprint,
    _goal_ids,
    _initial_state,
    _latest_action_status_evidence,
    _ObservedNavBridge,
    _runtime_identity_failures,
    _sample_final_observation_window,
    _TopicRecorder,
    compare_differential_artifacts,
)
from jenai.bridge import BridgeError
from jenai.config.models import AppConfig


def _options(tmp_path: Path, **overrides: Any) -> DifferentialCaptureOptions:
    values: dict[str, Any] = {
        "output": tmp_path / "capture.json",
        "location": "Dock",
        "pair_id": "pair-01",
        "mode": DifferentialMode.R1_BRIDGE_NAV2,
        "simulation_epoch": "epoch-01",
        "reset_policy": ResetPolicy.NAV2_RESTART,
    }
    values.update(overrides)
    return DifferentialCaptureOptions(**values)


def _stamp(seconds: int, nanoseconds: int = 0) -> dict[str, int]:
    return {"sec": seconds, "nanosec": nanoseconds}


def _clock_message(seconds: int, nanoseconds: int = 0) -> dict[str, Any]:
    return {"clock": _stamp(seconds, nanoseconds)}


def _pose_message(
    *,
    stamp: dict[str, int],
    covariance: float = 0.01,
    frame_id: str = "map",
) -> dict[str, Any]:
    covariance_values = [0.0] * 36
    covariance_values[0] = covariance
    covariance_values[7] = covariance
    return {
        "header": {"stamp": stamp, "frame_id": frame_id},
        "pose": {
            "pose": {
                "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            },
            "covariance": covariance_values,
        },
    }


def _odom_message(*, stamp: dict[str, int]) -> dict[str, Any]:
    message = _pose_message(stamp=stamp)
    message["twist"] = {
        "twist": {
            "linear": {"x": 0.0, "y": 0.0, "z": 0.0},
            "angular": {"x": 0.0, "y": 0.0, "z": 0.0},
        }
    }
    return message


def _ground_truth_message(*, stamp: dict[str, int]) -> dict[str, Any]:
    return {
        "header": {"stamp": stamp, "frame_id": "world"},
        "pose": {
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
            "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        },
    }


def _status_entry(status: object = 2) -> dict[str, Any]:
    return {
        "goal_info": {
            "goal_id": {"uuid": [1] * 16},
            "stamp": _stamp(10),
        },
        "status": status,
    }


def _recorder(*samples: tuple[int, dict[str, Any]]) -> _TopicRecorder:
    recorder = _TopicRecorder()
    recorder.samples = [
        {"host_monotonic_ns": host_ns, "message": message} for host_ns, message in samples
    ]
    return recorder


def test_t1_requires_new_clock_amcl_and_odom_after_invocation(
    tmp_path: Path,
) -> None:
    now = time.monotonic_ns()
    cutoff = now - 1_000_000
    clock = _recorder(
        (cutoff - 4_000_000, _clock_message(10)),
        (cutoff - 3_000_000, _clock_message(11)),
    )
    amcl = _recorder((cutoff - 2_000_000, _pose_message(stamp=_stamp(11))))
    odom = _recorder((cutoff - 1_000_000, _odom_message(stamp=_stamp(11))))
    action_status = _recorder((now - 100_000, {"status_list": []}))

    state = _initial_state(
        pose=Pose2D(x=0.0, y=0.0, yaw=0.0),
        clock=clock,
        amcl=amcl,
        odom=odom,
        action_status=action_status,
        options=_options(tmp_path),
        cutoff_host_monotonic_ns=cutoff,
    )

    assert state["status"] == "FAIL"
    failures = tuple(str(item) for item in state["failures"])
    assert any("clock" in item for item in failures)
    assert any("amcl" in item for item in failures)
    assert any("odom" in item for item in failures)


def test_t1_uses_current_ros_clock_for_amcl_and_odom_age(tmp_path: Path) -> None:
    now = time.monotonic_ns()
    cutoff = now - 10_000_000
    clock = _recorder(
        (now - 9_000_000, _clock_message(10)),
        (now - 8_000_000, _clock_message(10, 100_000_000)),
        (now - 1_000_000, _clock_message(12)),
    )
    amcl = _recorder((now - 7_000_000, _pose_message(stamp=_stamp(10, 100_000_000))))
    odom = _recorder((now - 6_000_000, _odom_message(stamp=_stamp(10, 100_000_000))))
    action_status = _recorder((now - 500_000, {"status_list": []}))

    state = _initial_state(
        pose=Pose2D(x=0.0, y=0.0, yaw=0.0),
        clock=clock,
        amcl=amcl,
        odom=odom,
        action_status=action_status,
        options=_options(tmp_path, max_topic_age_s=1.0),
        cutoff_host_monotonic_ns=cutoff,
    )

    assert state["status"] == "FAIL"
    failures = tuple(str(item) for item in state["failures"])
    assert any("amcl" in item and "stale" in item for item in failures)
    assert any("odom" in item and "stale" in item for item in failures)


@pytest.mark.parametrize("invalid_status", [None, "2", True, -1, 7])
def test_action_status_rejects_missing_or_non_goal_status_values(
    invalid_status: object,
) -> None:
    now = time.monotonic_ns()
    entry = _status_entry(invalid_status)
    if invalid_status is None:
        entry.pop("status")
    recorder = _recorder((now, {"status_list": [entry]}))

    evidence = _latest_action_status_evidence(recorder, max_age_s=1.0)

    assert evidence is not None
    assert evidence["schema_valid"] is False
    assert evidence["fresh"] is False


def test_action_status_accepts_integer_executing_and_marks_it_active() -> None:
    now = time.monotonic_ns()
    message = {"status_list": [_status_entry(2)]}
    recorder = _recorder((now, message))

    evidence = _latest_action_status_evidence(recorder, max_age_s=1.0)

    assert evidence is not None
    assert evidence["schema_valid"] is True
    assert evidence["fresh"] is True
    assert _goal_ids(message, active_only=True) == {"01" * 16}


def test_observed_bridge_rejects_goal_different_from_canonical_before_forward() -> None:
    forwarded = False
    expected = CanonicalGoal.from_yaw(
        frame_id="map",
        x=1.0,
        y=2.0,
        yaw=0.0,
        clock_domain="ros",
        simulation_epoch="epoch-01",
    )

    class Delegate:
        async def nav_send(
            self,
            x: float,
            y: float,
            yaw: float = 0.0,
            frame_id: str = "map",
            tag: str = "",
        ) -> None:
            del x, y, yaw, frame_id, tag
            nonlocal forwarded
            forwarded = True

    async def observe(
        goal: CanonicalGoal,
        tag: str,
        invoked_ns: int,
    ) -> dict[str, Any]:
        del goal, tag, invoked_ns
        return {"status": "PASS", "failures": []}

    async def exercise() -> None:
        bridge = _ObservedNavBridge(
            cast(Any, Delegate()),
            simulation_epoch="epoch-01",
            on_nav_send=observe,
            expected_goal=expected,
        )
        await bridge.nav_send(1.25, 2.0, 0.0, frame_id="map", tag="navdiff-test")

    with pytest.raises(BridgeError, match="goal"):
        asyncio.run(exercise())
    assert forwarded is False


def _measurement_contract() -> dict[str, Any]:
    return {
        "preflight_sample_s": 1.0,
        "final_sample_s": 2.0,
        "sample_interval_s": 0.2,
        "max_topic_age_s": 1.0,
        "max_calibration_residual_m": 0.02,
        "min_final_pose_samples": 10,
        "min_final_state_samples": 3,
        "min_final_ground_truth_samples": 3,
        "final_wall_timeout_s": 15.0,
        "max_start_speed_mps": 0.02,
        "max_start_yaw_rate_rps": 0.03,
        "max_covariance_xy": 0.1,
    }


def _runtime_identity() -> dict[str, Any]:
    git_sha = "1" * 40
    source_root = "/tmp/JenAI-reviewed"
    generation = {
        "boot_id": "12345678-1234-5678-1234-567812345678",
        "session": "nav2",
        "session_id": "$1",
        "session_created": 1000,
        "pane_id": "%1",
        "pane_pid": 101,
        "pane_start_ticks": 1001,
        "processes": [
            {
                "pid": 101,
                "ppid": 1,
                "start_ticks": 1001,
                "cmdline_sha256": "5" * 64,
            },
            {
                "pid": 102,
                "ppid": 101,
                "start_ticks": 1002,
                "cmdline_sha256": "6" * 64,
            },
        ],
    }
    identity: dict[str, Any] = {
        "git_sha": git_sha,
        "git_dirty": False,
        "source_root": source_root,
        "expected_source_root": source_root,
        "expected_git_sha": git_sha,
        "expected_git_dirty": False,
        "reviewed_git_sha": git_sha,
        "jenai_import_path": f"{source_root}/src/jenai/__init__.py",
        "deployment_mode": "simulation",
        "config_sha256": "a" * 64,
        "site_map_sha256": "b" * 64,
        "site_locations_sha256": "c" * 64,
        "locations_sha256": "c" * 64,
        "nav_params_sha256": "d" * 64,
        "scene_sha256": "e" * 64,
        "live_scene_sha256": "e" * 64,
        "live_map_sha256": "b" * 64,
        "site_map_frame": "map",
        "live_map_frame": "map",
        "bridge_domain_id": "7",
        "controller_lifecycle": "active [3]",
        "planner_lifecycle": "active [3]",
        "bt_navigator_lifecycle": "active [3]",
        "node_name_counts": {
            "/amcl": 1,
            "/controller_server": 1,
            "/planner_server": 1,
            "/bt_navigator": 1,
        },
        "navigate_to_pose_action_count": 1,
        "controller_odom_topic": "/chassis/odom",
        "nav2_process_generation": generation,
        "nav2_process_generation_end": deepcopy(generation),
        "runtime_parameter_sha256": {
            "/amcl": "1" * 64,
            "/controller_server": "2" * 64,
            "/planner_server": "3" * 64,
            "/bt_navigator": "4" * 64,
        },
    }
    _apply_runtime_fingerprint(identity)
    return identity


def _state_evidence(
    *,
    pose: Pose2D | None = None,
    covariance: float = 0.01,
    cutoff_host_ns: int = 80,
    evaluated_host_ns: int = 100,
) -> dict[str, Any]:
    state_pose = (pose or Pose2D(x=0.0, y=0.0, yaw=0.0)).model_dump(mode="json")
    source_host_ns = evaluated_host_ns - 5
    return {
        "status": "PASS",
        "failures": [],
        "simulation_epoch": "epoch-01",
        "cutoff_host_monotonic_ns": cutoff_host_ns,
        "evaluated_host_monotonic_ns": evaluated_host_ns,
        "map_to_base": state_pose,
        "amcl_pose": state_pose,
        "odom_pose": state_pose,
        "amcl_covariance_xy": covariance,
        "linear_velocity_mps": 0.0,
        "angular_velocity_rps": 0.0,
        "stationary": True,
        "active_goal_ids": [],
        "clock_evidence": [
            {"host_monotonic_ns": cutoff_host_ns + 5, "clock_ns": 10_000_000_000},
            {"host_monotonic_ns": evaluated_host_ns, "clock_ns": 11_000_000_000},
        ],
        "clock_samples_ns": [10_000_000_000, 11_000_000_000],
        "clock_advancing": True,
        "clock_backwards": False,
        "amcl_source": {
            "host_monotonic_ns": source_host_ns,
            "host_age_ns": 5,
            "source_stamp_ns": 10_500_000_000,
            "sample_clock_ns": 10_500_000_000,
            "capture_clock_ns": 11_000_000_000,
            "source_age_ns": 500_000_000,
            "fresh": True,
        },
        "odom_source": {
            "host_monotonic_ns": source_host_ns,
            "host_age_ns": 5,
            "source_stamp_ns": 10_500_000_000,
            "sample_clock_ns": 10_500_000_000,
            "capture_clock_ns": 11_000_000_000,
            "source_age_ns": 500_000_000,
            "fresh": True,
        },
        "action_status_source": {
            "host_monotonic_ns": source_host_ns,
            "host_age_ns": 5,
            "fresh": True,
            "schema_valid": True,
        },
    }


def _stream_stamps() -> tuple[int, int, int]:
    return (10_000_000_000, 11_000_000_000, 12_000_000_000)


def _amcl_samples() -> list[dict[str, Any]]:
    return [
        {
            "source_stamp_ns": stamp,
            "fresh": True,
            "pose": {"x": 1.0, "y": 2.0, "yaw": 0.0},
            "covariance_xy": 0.01,
        }
        for stamp in _stream_stamps()
    ]


def _odom_samples() -> list[dict[str, Any]]:
    return [
        {
            "source_stamp_ns": stamp,
            "fresh": True,
            "pose": {"x": 1.0, "y": 2.0, "yaw": 0.0},
            "linear_velocity_mps": 0.0,
            "angular_velocity_rps": 0.0,
        }
        for stamp in _stream_stamps()
    ]


def _artifact(
    *,
    mode: str,
    t1_pose: Pose2D | None = None,
    t1_covariance: float = 0.01,
    runtime_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    goal = CanonicalGoal.from_yaw(
        frame_id="map",
        x=1.0,
        y=2.0,
        yaw=0.0,
        clock_domain="ros",
        simulation_epoch="epoch-01",
    ).model_dump(mode="json")
    map_clock_stamps = [10_000_000_000 + (index * 2_000_000_000) // 9 for index in range(10)]
    t1_state = _state_evidence(
        pose=t1_pose,
        covariance=t1_covariance,
        cutoff_host_ns=110,
        evaluated_host_ns=130,
    )
    artifact: dict[str, Any] = {
        "schema_version": 1,
        "run_id": f"run-{mode}",
        "pair_id": "pair-01",
        "mode": mode,
        "reset_policy": "nav2_restart",
        "execution_requested": True,
        "measurement_contract": _measurement_contract(),
        "started_at": "2026-07-31T00:00:00+00:00",
        "finished_at": "2026-07-31T00:01:00+00:00",
        "runtime_identity": (_runtime_identity() if runtime_identity is None else runtime_identity),
        "canonical_goal": goal,
        "checks": [],
        "overall": "captured",
        "t0_scenario_start": _state_evidence(),
        "t1_goal_dispatch": {
            "status": "PASS",
            "failures": [],
            "dispatch_count": 1,
            "actual_goal": goal,
            "state_before_forward": deepcopy(t1_state),
            "request_host_monotonic_ns": 100,
            "return_host_monotonic_ns": 160,
            "dispatch_observations": [
                {
                    "nav_send_invoked_host_monotonic_ns": 110,
                    "nav_send_forwarded_host_monotonic_ns": 120,
                    "forward_completed_host_monotonic_ns": 130,
                    "actual_goal": goal,
                    "state_before_forward": deepcopy(t1_state),
                }
            ],
            "nav_send_forwarded_host_monotonic_ns": 120,
            "accepted_goal_observations": [
                {
                    "goal_uuid": "01" * 16,
                    "status": 2,
                    "goal_stamp_ns": 10_500_000_000,
                    "capture_clock_ns": 11_000_000_000,
                    "goal_stamp_age_ns": 500_000_000,
                    "goal_stamp_fresh": True,
                    "observed_host_monotonic_ns": 140,
                }
            ],
            "accepted_goal_uuid": "01" * 16,
            "goal_uuid_evidence": "INFERRED_UNIQUE_ACTION_STATUS",
        },
        "nav2_terminal": {"status": "succeeded", "observed_host_monotonic_ns": 150},
        "final_observation_window": {
            "status": "PASS",
            "failures": [],
            "terminal_host_monotonic_ns": 150,
            "start_host_monotonic_ns": 170,
            "end_host_monotonic_ns": 200,
            "start_clock_ns": 10_000_000_000,
            "end_clock_ns": 12_000_000_000,
            "required_duration_ns": 2_000_000_000,
            "coverage_slack_ns": 400_000_000,
            "clock_backwards": False,
            "clock_samples": [
                {"host_monotonic_ns": 170, "clock_ns": 10_000_000_000},
                {"host_monotonic_ns": 200, "clock_ns": 12_000_000_000},
            ],
            "map_pose_samples": [
                {
                    "fresh": True,
                    "capture_clock_ns": stamp,
                    "pose": {"x": 1.0, "y": 2.0, "yaw": 0.0},
                }
                for stamp in map_clock_stamps
            ],
            "valid_amcl_samples": _amcl_samples(),
            "valid_odom_samples": _odom_samples(),
            "verified_ground_truth_samples": [],
            "ground_truth_required": False,
            "stationary": True,
        },
        "cleanup": {
            "status": "PASS",
            "failures": [],
            "final_halt": {
                "status": "PASS",
                "zero_velocity_command_published": True,
                "navigation_cancel_requested": True,
                "navigation_cancel_acknowledged": True,
            },
            "unwatch": {"status": "PASS", "failures": []},
            "bridge_shutdown": {"status": "PASS"},
        },
        "final_map_pose_median": {"x": 1.0, "y": 2.0, "yaw": 0.0},
        "final_ground_truth_map_median": None,
        "execution_status": "succeeded",
    }
    if mode == "R2_jenai_no_retry":
        artifact["jenai_result"] = {
            "execution_status": "succeeded",
            "effective_experimental_config": {"nav_endpoint_retry_limit": 0},
            "navigation_attempts": [{"tag": "attempt-1"}],
        }
    return artifact


def test_t1_dispatch_pose_summary_without_raw_observation_is_ineligible(
    differential_artifact_factory: Any,
) -> None:
    left = cast(dict[str, Any], differential_artifact_factory(mode="R1_bridge_nav2"))
    right = cast(dict[str, Any], differential_artifact_factory(mode="R2_jenai_no_retry"))
    timeline = cast(dict[str, Any], right["t1_goal_dispatch"])
    public_state = cast(dict[str, Any], timeline["state_before_forward"])
    observations = cast(list[dict[str, Any]], timeline["dispatch_observations"])
    nested_state = cast(dict[str, Any], observations[0]["state_before_forward"])
    for state in (public_state, nested_state):
        state["map_to_base"] = {"x": 0.10, "y": 0.0, "yaw": 0.0}

    report = compare_differential_artifacts(left, right)

    assert report["included"] is False
    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


def test_t1_covariance_above_contract_is_insufficient_evidence() -> None:
    report = compare_differential_artifacts(
        _artifact(mode="R1_bridge_nav2"),
        _artifact(mode="R2_jenai_no_retry", t1_covariance=0.20),
    )

    assert report["included"] is False
    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


@pytest.mark.parametrize(
    "invalid_identity",
    [
        {},
        {"fingerprint": ""},
        {"fingerprint": 123},
    ],
)
def test_captured_artifact_with_invalid_runtime_identity_is_not_comparable(
    invalid_identity: dict[str, Any],
) -> None:
    report = compare_differential_artifacts(
        _artifact(mode="R1_bridge_nav2", runtime_identity=invalid_identity),
        _artifact(mode="R2_jenai_no_retry"),
    )

    assert report["included"] is False
    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


def _verified_calibration() -> GroundTruthCalibration:
    return GroundTruthCalibration(
        status="VERIFIED",
        scene_sha256="e" * 64,
        map_sha256="b" * 64,
        source="review-regression",
        world_frame_id="world",
        map_frame_id="map",
        translation_x_m=0.0,
        translation_y_m=0.0,
        rotation_yaw_rad=0.0,
        calibration_method="test identity transform",
        residual_m=0.0,
    )


def _window_recorders(
    *,
    sample_hosts: list[int],
) -> dict[str, _TopicRecorder]:
    clock = _recorder(
        (100, _clock_message(10)),
        (200, _clock_message(11)),
        (300, _clock_message(12)),
    )
    return {
        "clock": clock,
        "amcl": _recorder(*[(host, _pose_message(stamp=_stamp(10))) for host in sample_hosts]),
        "odom": _recorder(*[(host, _odom_message(stamp=_stamp(10))) for host in sample_hosts]),
        "action_status": _recorder(),
        "ground_truth": _recorder(
            *[(host, _ground_truth_message(stamp=_stamp(10))) for host in sample_hosts]
        ),
    }


def _map_attempts(count: int) -> list[dict[str, Any]]:
    return [
        {
            "fresh": True,
            "capture_clock_ns": 10_000_000_000 + index * 100_000_000,
            "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        }
        for index in range(count)
    ]


def test_final_window_requires_minimum_amcl_odom_and_ground_truth_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_collect(
        bridge: object,
        config: AppConfig,
        options: DifferentialCaptureOptions,
        clock: _TopicRecorder,
    ) -> tuple[int, int, int, int, bool, list[dict[str, Any]], list[str]]:
        del bridge, config, options, clock
        return 100, 300, 10_000_000_000, 12_000_000_000, False, _map_attempts(3), []

    monkeypatch.setattr(runner, "_collect_final_map_window", fake_collect)
    options = _options(
        tmp_path,
        final_sample_s=2.0,
        min_final_pose_samples=3,
        calibration_path=tmp_path / "calibration.json",
        ground_truth_topic="/ground_truth",
    )

    result = asyncio.run(
        _sample_final_observation_window(
            cast(Any, object()),
            AppConfig(),
            options,
            _window_recorders(sample_hosts=[150]),
            terminal_host_ns=90,
            calibration=_verified_calibration(),
        )
    )

    assert result["status"] == "FAIL"
    failures = tuple(str(item) for item in result["failures"])
    assert any("amcl" in item and "insufficient" in item for item in failures)
    assert any("odom" in item and "insufficient" in item for item in failures)
    assert any("ground_truth" in item and "insufficient" in item for item in failures)


def test_final_evidence_must_cover_tail_of_ros_time_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_collect(
        bridge: object,
        config: AppConfig,
        options: DifferentialCaptureOptions,
        clock: _TopicRecorder,
    ) -> tuple[int, int, int, int, bool, list[dict[str, Any]], list[str]]:
        del bridge, config, options, clock
        return 100, 300, 10_000_000_000, 12_000_000_000, False, _map_attempts(3), []

    monkeypatch.setattr(runner, "_collect_final_map_window", fake_collect)
    options = _options(
        tmp_path,
        final_sample_s=2.0,
        min_final_pose_samples=3,
        calibration_path=tmp_path / "calibration.json",
        ground_truth_topic="/ground_truth",
    )

    result = asyncio.run(
        _sample_final_observation_window(
            cast(Any, object()),
            AppConfig(),
            options,
            _window_recorders(sample_hosts=[110, 120, 130]),
            terminal_host_ns=90,
            calibration=_verified_calibration(),
        )
    )

    assert result["status"] == "FAIL"
    failures = tuple(str(item) for item in result["failures"])
    assert any("amcl" in item and ("tail" in item or "coverage" in item) for item in failures)
    assert any("odom" in item and ("tail" in item or "coverage" in item) for item in failures)
    assert any(
        "ground_truth" in item and ("tail" in item or "coverage" in item) for item in failures
    )


def test_runtime_identity_rejects_wrong_worktree_source_root() -> None:
    identity = _runtime_identity()
    identity["source_root"] = "/tmp/JenAI-other"

    failures = _runtime_identity_failures(identity)

    assert "source_root_mismatch" in failures


def _comparison_for_left(
    left: dict[str, Any],
    differential_artifact_factory: Any,
) -> dict[str, Any]:
    return compare_differential_artifacts(
        left,
        differential_artifact_factory(mode="R2_jenai_no_retry"),
    )


def test_comparison_rejects_map_samples_without_valid_pose(
    differential_artifact_factory: Any,
) -> None:
    left = differential_artifact_factory(mode="R1_bridge_nav2")
    window = cast(dict[str, Any], left["final_observation_window"])
    window["map_pose_samples"] = [
        {"fresh": True, "capture_clock_ns": 10_000_000_000 + index} for index in range(10)
    ]

    report = _comparison_for_left(left, differential_artifact_factory)

    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


@pytest.mark.parametrize("stream", ["valid_amcl_samples", "valid_odom_samples"])
def test_comparison_rejects_incomplete_localization_stream(
    stream: str,
    differential_artifact_factory: Any,
) -> None:
    left = differential_artifact_factory(mode="R1_bridge_nav2")
    window = cast(dict[str, Any], left["final_observation_window"])
    window[stream] = [
        {"source_stamp_ns": stamp, "fresh": True}
        for stamp in (10_000_000_000, 11_000_000_000, 12_000_000_000)
    ]

    report = _comparison_for_left(left, differential_artifact_factory)

    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


def test_comparison_rejects_final_median_not_derived_from_samples(
    differential_artifact_factory: Any,
) -> None:
    left = differential_artifact_factory(mode="R1_bridge_nav2")
    left["final_map_pose_median"] = {"x": 99.0, "y": 2.0, "yaw": 0.0}

    report = _comparison_for_left(left, differential_artifact_factory)

    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


def test_final_map_samples_must_cover_ros_window_tail(
    differential_artifact_factory: Any,
) -> None:
    left = differential_artifact_factory(mode="R1_bridge_nav2")
    window = cast(dict[str, Any], left["final_observation_window"])
    samples = cast(list[dict[str, Any]], window["map_pose_samples"])
    for index, sample in enumerate(samples):
        sample["capture_clock_ns"] = 10_000_000_000 + index * 10_000_000

    report = _comparison_for_left(left, differential_artifact_factory)

    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


def test_duplicate_source_stamps_do_not_satisfy_final_window(
    differential_artifact_factory: Any,
) -> None:
    left = differential_artifact_factory(mode="R1_bridge_nav2")
    window = cast(dict[str, Any], left["final_observation_window"])
    samples = cast(list[dict[str, Any]], window["valid_amcl_samples"])
    for sample in samples:
        sample["source_stamp_ns"] = 10_000_000_000

    report = _comparison_for_left(left, differential_artifact_factory)

    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


def test_host_window_does_not_replace_ros_clock_coverage(
    differential_artifact_factory: Any,
) -> None:
    left = differential_artifact_factory(mode="R1_bridge_nav2")
    window = cast(dict[str, Any], left["final_observation_window"])
    window["clock_samples"] = [
        {"host_monotonic_ns": 170, "clock_ns": 10_000_000_000},
        {"host_monotonic_ns": 200, "clock_ns": 10_100_000_000},
    ]

    report = _comparison_for_left(left, differential_artifact_factory)

    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


def _verified_calibration_payload() -> dict[str, Any]:
    return GroundTruthCalibration(
        status="VERIFIED",
        scene_sha256="e" * 64,
        map_sha256="b" * 64,
        source="review fixture",
        world_frame_id="world",
        map_frame_id="map",
        translation_x_m=0.0,
        translation_y_m=0.0,
        rotation_yaw_rad=0.0,
        calibration_method="fixture",
        residual_m=0.0,
    ).model_dump(mode="json")


def test_verified_calibration_cannot_disable_ground_truth_requirement(
    differential_artifact_factory: Any,
) -> None:
    left = differential_artifact_factory(mode="R1_bridge_nav2")
    left["ground_truth_calibration"] = _verified_calibration_payload()

    report = _comparison_for_left(left, differential_artifact_factory)

    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


def test_unverified_ground_truth_median_cannot_classify_actual_endpoint(
    differential_artifact_factory: Any,
) -> None:
    left = differential_artifact_factory(mode="R1_bridge_nav2")
    left["final_ground_truth_map_median"] = {"x": 99.0, "y": 2.0, "yaw": 0.0}

    report = _comparison_for_left(left, differential_artifact_factory)

    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


def test_impossible_lifecycle_order_is_ineligible(
    differential_artifact_factory: Any,
) -> None:
    left = differential_artifact_factory(mode="R1_bridge_nav2")
    terminal = cast(dict[str, Any], left["nav2_terminal"])
    window = cast(dict[str, Any], left["final_observation_window"])
    terminal["observed_host_monotonic_ns"] = 1
    window["terminal_host_monotonic_ns"] = 1

    report = _comparison_for_left(left, differential_artifact_factory)

    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


def test_blank_reviewed_revisions_are_ineligible(
    differential_artifact_factory: Any,
) -> None:
    left = differential_artifact_factory(mode="R1_bridge_nav2")
    identity = cast(dict[str, Any], left["runtime_identity"])
    for field in ("git_sha", "expected_git_sha", "reviewed_git_sha"):
        identity[field] = ""
    _apply_runtime_fingerprint(identity)

    report = _comparison_for_left(left, differential_artifact_factory)

    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


def test_non_sha_runtime_parameter_snapshot_is_ineligible(
    differential_artifact_factory: Any,
) -> None:
    left = differential_artifact_factory(mode="R1_bridge_nav2")
    identity = cast(dict[str, Any], left["runtime_identity"])
    parameters = cast(dict[str, Any], identity["runtime_parameter_sha256"])
    parameters["/amcl"] = "not-a-sha"
    _apply_runtime_fingerprint(identity)

    report = _comparison_for_left(left, differential_artifact_factory)

    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


def test_runtime_fingerprint_changes_with_site_map_frame() -> None:
    identity = _runtime_identity()
    original = identity["fingerprint"]
    identity["site_map_frame"] = "other_map"
    _apply_runtime_fingerprint(identity)

    assert identity["fingerprint"] != original
