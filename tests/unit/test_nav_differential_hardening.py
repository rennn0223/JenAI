from __future__ import annotations

import asyncio
import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from jenai.acceptance.nav_differential import (
    CanonicalGoal,
    GroundTruthCalibration,
    PairClassification,
    PairingGate,
    PairingGateResult,
    Pose2D,
    classify_pair,
)
from jenai.acceptance.nav_differential_runner import (
    DifferentialCaptureOptions,
    DifferentialMode,
    ResetPolicy,
    _apply_runtime_fingerprint,
    _canonical_json_sha256,
    _cleanup_live_capture,
    _dispatch_timeline,
    _initial_state,
    _load_calibration,
    _ObservedNavBridge,
    _r2_execution_config,
    _runtime_identity_failures,
    _TopicRecorder,
    _valid_final_amcl,
    compare_differential_artifacts,
    load_differential_artifact,
)
from jenai.config.models import AppConfig, TwinProfile, VehicleProfile


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


def _pose_message(
    *,
    stamp: dict[str, int] | None,
    covariance: float = 0.01,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "pose": {
            "pose": {
                "position": {"x": 1.0, "y": 2.0, "z": 0.0},
                "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            },
            "covariance": [0.0] * 36,
        }
    }
    message["pose"]["covariance"][0] = covariance
    message["pose"]["covariance"][7] = covariance
    if stamp is not None:
        message["header"] = {"stamp": stamp, "frame_id": "map"}
    return message


def _odom_message(*, stamp: dict[str, int] | None) -> dict[str, Any]:
    message = _pose_message(stamp=stamp)
    message["twist"] = {
        "twist": {
            "linear": {"x": 0.0, "y": 0.0, "z": 0.0},
            "angular": {"x": 0.0, "y": 0.0, "z": 0.0},
        }
    }
    return message


def _start_recorders(
    *,
    amcl_stamp: dict[str, int] | None = None,
    odom_stamp: dict[str, int] | None = None,
    covariance: float = 0.01,
) -> tuple[_TopicRecorder, _TopicRecorder, _TopicRecorder, _TopicRecorder]:
    now = time.monotonic_ns()
    clock = _TopicRecorder()
    clock.samples = [
        {"host_monotonic_ns": now - 4_000_000, "message": {"clock": _stamp(10)}},
        {"host_monotonic_ns": now - 2_000_000, "message": {"clock": _stamp(11)}},
    ]
    amcl = _TopicRecorder()
    amcl.samples = [
        {
            "host_monotonic_ns": now - 1_000_000,
            "message": _pose_message(
                stamp=amcl_stamp if amcl_stamp is not None else _stamp(10, 500_000_000),
                covariance=covariance,
            ),
        }
    ]
    odom = _TopicRecorder()
    odom.samples = [
        {
            "host_monotonic_ns": now - 900_000,
            "message": _odom_message(
                stamp=odom_stamp if odom_stamp is not None else _stamp(10, 500_000_000)
            ),
        }
    ]
    action_status = _TopicRecorder()
    action_status.samples = [{"host_monotonic_ns": now - 500_000, "message": {"status_list": []}}]
    return clock, amcl, odom, action_status


def _initial_state_for(
    tmp_path: Path,
    *,
    amcl_stamp: dict[str, int] | None = None,
    odom_stamp: dict[str, int] | None = None,
    covariance: float = 0.01,
) -> dict[str, Any]:
    clock, amcl, odom, action_status = _start_recorders(
        amcl_stamp=amcl_stamp,
        odom_stamp=odom_stamp,
        covariance=covariance,
    )
    return _initial_state(
        pose=Pose2D(x=1.0, y=2.0, yaw=0.0),
        clock=clock,
        amcl=amcl,
        odom=odom,
        action_status=action_status,
        options=_options(tmp_path),
    )


def test_start_gate_rejects_headerless_amcl(tmp_path: Path) -> None:
    clock, amcl, odom, action_status = _start_recorders()
    cast(dict[str, Any], amcl.samples[0]["message"]).pop("header")

    state = _initial_state(
        pose=Pose2D(x=1.0, y=2.0, yaw=0.0),
        clock=clock,
        amcl=amcl,
        odom=odom,
        action_status=action_status,
        options=_options(tmp_path),
    )

    assert state["status"] == "FAIL"
    assert "amcl_stale_or_headerless" in state["failures"]


def test_start_gate_rejects_stale_odom(tmp_path: Path) -> None:
    state = _initial_state_for(tmp_path, odom_stamp=_stamp(8))

    assert state["status"] == "FAIL"
    assert "odom_stale_or_headerless" in state["failures"]


def test_start_gate_rejects_nonfinite_covariance(tmp_path: Path) -> None:
    state = _initial_state_for(tmp_path, covariance=float("nan"))

    assert state["status"] == "FAIL"
    assert "amcl_covariance" in state["failures"]
    assert state["amcl_covariance_xy"] is None


def _dispatch_observation(goal: CanonicalGoal) -> dict[str, Any]:
    return {
        "tag": "navdiff-test",
        "nav_send_invoked_host_monotonic_ns": 100,
        "nav_send_forwarded_host_monotonic_ns": 110,
        "forward_completed_host_monotonic_ns": 120,
        "actual_goal": goal.model_dump(mode="json"),
        "state_before_forward": {"status": "PASS", "failures": []},
    }


def test_dispatch_timeline_rejects_ambiguous_goal_uuids() -> None:
    goal = CanonicalGoal.from_yaw(frame_id="map", x=1.0, y=2.0, yaw=0.0)
    observations = [
        {
            "goal_uuid": "01" * 16,
            "goal_stamp_fresh": True,
            "observed_host_monotonic_ns": 130,
        },
        {
            "goal_uuid": "02" * 16,
            "goal_stamp_fresh": True,
            "observed_host_monotonic_ns": 140,
        },
    ]

    timeline = _dispatch_timeline(
        [_dispatch_observation(goal)],
        observations,
        {"status": "succeeded", "observed_host_monotonic_ns": 150},
        request_ns=90,
        returned_ns=160,
    )

    assert timeline["status"] == "FAIL"
    assert timeline["accepted_goal_uuid"] is None
    assert timeline["goal_uuid_evidence"] == "AMBIGUOUS_OR_STALE_ACTION_STATUS"
    assert "goal_uuid_ambiguous_or_stale" in timeline["failures"]


def test_observed_bridge_records_actual_goal_and_t1_before_forwarding() -> None:
    events: list[str] = []

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
            events.append("delegate")

    async def observe(
        goal: CanonicalGoal,
        tag: str,
        invoked_ns: int,
    ) -> dict[str, Any]:
        assert tag == "navdiff-test"
        assert invoked_ns > 0
        assert goal == CanonicalGoal.from_yaw(
            frame_id="map",
            x=1.25,
            y=-2.5,
            yaw=0.75,
            clock_domain="ros",
            simulation_epoch="epoch-01",
        )
        events.append("t1")
        return {"status": "PASS", "failures": [], "map_to_base": {"x": 0, "y": 0, "yaw": 0}}

    async def exercise() -> _ObservedNavBridge:
        bridge = _ObservedNavBridge(
            cast(Any, Delegate()),
            simulation_epoch="epoch-01",
            expected_goal=CanonicalGoal.from_yaw(
                frame_id="map",
                x=1.25,
                y=-2.5,
                yaw=0.75,
                clock_domain="ros",
                simulation_epoch="epoch-01",
            ),
            on_nav_send=observe,
        )
        await bridge.nav_send(1.25, -2.5, 0.75, frame_id="/map", tag="navdiff-test")
        return bridge

    observed = asyncio.run(exercise())

    assert events == ["t1", "delegate"]
    assert len(observed.observations) == 1
    observation = observed.observations[0]
    assert observation["actual_goal"]["frame_id"] == "map"
    assert observation["actual_goal"]["x"] == pytest.approx(1.25)
    assert (
        observation["nav_send_invoked_host_monotonic_ns"]
        <= observation["nav_send_forwarded_host_monotonic_ns"]
        <= observation["forward_completed_host_monotonic_ns"]
    )


def _artifact(*, mode: str, canonical_x: float, actual_x: float) -> dict[str, Any]:
    canonical = CanonicalGoal.from_yaw(
        frame_id="map",
        x=canonical_x,
        y=2.0,
        yaw=0.0,
        clock_domain="ros",
        simulation_epoch="epoch-01",
    ).model_dump(mode="json")
    actual = CanonicalGoal.from_yaw(
        frame_id="map",
        x=actual_x,
        y=2.0,
        yaw=0.0,
        clock_domain="ros",
        simulation_epoch="epoch-01",
    ).model_dump(mode="json")
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
        "runtime_identity": {"fingerprint": "same-runtime"},
        "canonical_goal": canonical,
        "checks": [],
        "overall": "captured",
        "t0_scenario_start": {
            "status": "PASS",
            "simulation_epoch": "epoch-01",
            "map_to_base": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            "amcl_covariance_xy": 0.01,
            "stationary": True,
            "active_goal_ids": [],
        },
        "t1_goal_dispatch": {"status": "PASS", "actual_goal": actual},
        "final_observation_window": {"status": "PASS"},
        "cleanup": {"status": "PASS"},
        "final_map_pose_median": {"x": 1.0, "y": 2.0, "yaw": 0.0},
        "execution_status": "succeeded",
    }


def test_comparison_rejects_within_artifact_goal_mismatch(
    differential_artifact_factory: Any,
) -> None:
    artifact = differential_artifact_factory(mode="R1_bridge_nav2", goal_x=1.0)
    timeline = cast(dict[str, Any], artifact["t1_goal_dispatch"])
    timeline["actual_goal"] = CanonicalGoal.from_yaw(
        frame_id="map",
        x=1.5,
        y=2.0,
        yaw=0.0,
        clock_domain="ros",
        simulation_epoch="epoch",
    ).model_dump(mode="json")

    report = compare_differential_artifacts(
        artifact,
        differential_artifact_factory(mode="R2_jenai_no_retry", goal_x=1.0),
    )

    assert report["included"] is False
    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


def test_comparison_uses_each_verified_actual_dispatch_goal(
    differential_artifact_factory: Any,
) -> None:
    report = compare_differential_artifacts(
        differential_artifact_factory(mode="R1_bridge_nav2", goal_x=1.0),
        differential_artifact_factory(mode="R2_jenai_no_retry", goal_x=1.5),
    )

    assert report["included"] is False
    assert report["classifications"] == [PairClassification.GOAL_PAYLOAD_DIFFERENCE]


def test_verdict_only_classification_is_exclusive_of_endpoint_evidence() -> None:
    goal = CanonicalGoal.from_yaw(frame_id="map", x=1.0, y=2.0, yaw=0.0)
    classifications = classify_pair(
        left_goal=goal,
        right_goal=goal,
        pairing_gate=PairingGateResult(
            status=PairingGate.PASSED,
            start_position_delta_m=0.0,
            start_yaw_delta_rad=0.0,
        ),
        left_final_map=Pose2D(x=1.0, y=2.0, yaw=0.0),
        right_final_map=Pose2D(x=1.0, y=2.0, yaw=0.0),
        left_final_ground_truth=Pose2D(x=1.0, y=2.0, yaw=0.0),
        right_final_ground_truth=Pose2D(x=1.2, y=2.0, yaw=0.0),
        left_execution_status="succeeded",
        right_execution_status="endpoint_mismatch",
    )

    assert PairClassification.ACTUAL_ENDPOINT_DIFFERENCE in classifications
    assert PairClassification.JENAI_VERDICT_ONLY_DIFFERENCE not in classifications


def test_artifact_loader_rejects_unknown_schema_version(tmp_path: Path) -> None:
    artifact = _artifact(mode="R1_bridge_nav2", canonical_x=1.0, actual_x=1.0)
    artifact["schema_version"] = 2
    path = tmp_path / "unknown-schema.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_differential_artifact(path)


def _calibration(
    *,
    scene_sha256: str,
    map_sha256: str,
    residual_m: float,
) -> GroundTruthCalibration:
    return GroundTruthCalibration(
        status="VERIFIED",
        scene_sha256=scene_sha256,
        map_sha256=map_sha256,
        source="known-landmarks",
        world_frame_id="world",
        map_frame_id="map",
        translation_x_m=0.0,
        translation_y_m=0.0,
        rotation_yaw_rad=0.0,
        calibration_method="three-landmark fit",
        residual_m=residual_m,
    )


@pytest.mark.parametrize(
    ("runtime_overrides", "residual_m", "expected_source"),
    [
        ({}, 0.021, "calibration residual exceeds"),
        ({"live_scene_sha256": "b" * 64}, 0.01, "live-stage"),
        ({"live_map_sha256": "b" * 64}, 0.01, "live-map"),
        ({"live_map_frame": "odom"}, 0.01, "identity/frame"),
    ],
)
def test_calibration_requires_residual_and_live_identity_trust(
    tmp_path: Path,
    runtime_overrides: dict[str, Any],
    residual_m: float,
    expected_source: str,
) -> None:
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(
        _calibration(
            scene_sha256="a" * 64,
            map_sha256="c" * 64,
            residual_m=residual_m,
        ).model_dump_json(),
        encoding="utf-8",
    )
    identity = {
        "scene_sha256": "a" * 64,
        "live_scene_sha256": "a" * 64,
        "site_map_sha256": "c" * 64,
        "live_map_sha256": "c" * 64,
        "site_map_frame": "map",
        "live_map_frame": "map",
        **runtime_overrides,
    }

    result = _load_calibration(
        _options(
            tmp_path,
            calibration_path=calibration_path,
            ground_truth_topic="/ground_truth",
        ),
        identity,
    )

    assert result.status == "GROUND_TRUTH_UNAVAILABLE"
    assert expected_source in result.source


def test_calibration_accepts_matching_live_identities(tmp_path: Path) -> None:
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(
        _calibration(
            scene_sha256="a" * 64,
            map_sha256="c" * 64,
            residual_m=0.01,
        ).model_dump_json(),
        encoding="utf-8",
    )

    result = _load_calibration(
        _options(
            tmp_path,
            calibration_path=calibration_path,
            ground_truth_topic="/ground_truth",
        ),
        {
            "scene_sha256": "a" * 64,
            "live_scene_sha256": "a" * 64,
            "site_map_sha256": "c" * 64,
            "live_map_sha256": "c" * 64,
            "site_map_frame": "map",
            "live_map_frame": "map",
        },
    )

    assert result.status == "VERIFIED"


def test_r2_override_is_temporary_and_uses_effective_vehicle_domain() -> None:
    base = AppConfig(
        vehicle=VehicleProfile(domain_id=7, nav_endpoint_retry_limit=1),
        twin=TwinProfile(enabled=True, domain_id=7),
    )

    execution, evidence = _r2_execution_config(base)

    assert base.vehicle.nav_endpoint_retry_limit == 1
    assert base.twin.enabled is True
    assert execution.vehicle.nav_endpoint_retry_limit == 0
    assert execution.twin.enabled is False
    assert evidence == {
        "nav_endpoint_retry_limit": 0,
        "twin_enabled_in_base_config": True,
        "twin_disabled_for_same_domain": True,
        "effective_twin_enabled": False,
        "effective_bridge_domain_id": "7",
    }


def _valid_runtime_identity(*, deployment_mode: str) -> dict[str, Any]:
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
    middleware = {
        "schema_version": 1,
        "pid": 4242,
        "rmw_implementation_requested": "rmw_fastrtps_cpp",
        "rmw_implementation_effective": "rmw_fastrtps_cpp",
        "python_executable": "/usr/bin/python3.12",
        "python_version": "3.12.3",
        "ros_domain_id": 7,
        "dds_config_mode": "middleware_default",
        "dds_bindings": {},
        "dds_config_sha256": _canonical_json_sha256({}),
    }
    middleware["descriptor_sha256"] = _canonical_json_sha256(middleware)
    identity: dict[str, Any] = {
        "git_sha": git_sha,
        "git_dirty": False,
        "source_root": source_root,
        "expected_source_root": source_root,
        "expected_git_sha": git_sha,
        "expected_git_dirty": False,
        "reviewed_git_sha": git_sha,
        "jenai_import_path": f"{source_root}/src/jenai/__init__.py",
        "python_executable": "/usr/bin/python3.12",
        "python_version": "3.12.3",
        "ros_middleware": middleware,
        "deployment_mode": deployment_mode,
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


def test_runtime_identity_blocks_non_simulation_capture() -> None:
    physical_failures = _runtime_identity_failures(
        _valid_runtime_identity(deployment_mode="physical")
    )
    simulation_failures = _runtime_identity_failures(
        _valid_runtime_identity(deployment_mode="simulation")
    )

    assert physical_failures == ["simulation_deployment_mode_required"]
    assert simulation_failures == []


@pytest.mark.parametrize("odom_topic", [None, "", "relative", "/bad topic"])
def test_runtime_identity_requires_valid_effective_controller_odom_topic(
    odom_topic: object,
) -> None:
    identity = _valid_runtime_identity(deployment_mode="simulation")
    identity["controller_odom_topic"] = odom_topic
    _apply_runtime_fingerprint(identity)

    assert "controller_odom_topic" in _runtime_identity_failures(identity)


def test_cleanup_returns_structured_failures_for_each_failed_step() -> None:
    class FailingBridge:
        running = True

        async def halt_with_evidence(self, topic: str, stamped: bool) -> None:
            del topic, stamped
            raise RuntimeError("halt unavailable")

        async def unwatch(self, watch_id: int) -> None:
            raise RuntimeError(f"unwatch {watch_id} unavailable")

        async def stop(self) -> None:
            raise RuntimeError("shutdown unavailable")

    cleanup = asyncio.run(
        _cleanup_live_capture(
            cast(Any, FailingBridge()),
            AppConfig(),
            [41],
            None,
            motion_attempted=True,
        )
    )

    assert cleanup["status"] == "FAIL"
    assert cleanup["final_halt"]["status"] == "FAIL"
    assert cleanup["unwatch"]["status"] == "FAIL"
    assert cleanup["bridge_shutdown"]["status"] == "FAIL"
    assert {failure["step"] for failure in cleanup["failures"]} == {
        "final_halt",
        "unwatch",
        "bridge_shutdown",
    }


def test_pairing_rejects_measurement_contract_mismatch(
    differential_artifact_factory: Any,
) -> None:
    left = differential_artifact_factory(mode="R1_bridge_nav2")
    right = differential_artifact_factory(mode="R2_jenai_no_retry")
    contract = cast(dict[str, Any], right["measurement_contract"])
    right["measurement_contract"] = {**contract, "max_topic_age_s": 2.0}

    report = compare_differential_artifacts(left, right)

    assert report["included"] is False
    assert "measurement_contract" in report["pairing_gate"]["failures"]


def test_runtime_identity_requires_complete_node_and_parameter_sets() -> None:
    identity = _valid_runtime_identity(deployment_mode="simulation")
    identity["node_name_counts"] = {}
    identity["runtime_parameter_sha256"] = {}

    failures = _runtime_identity_failures(identity)

    assert "nav2_node_uniqueness" in failures
    assert "runtime_parameter_snapshot" in failures


def test_final_amcl_requires_covariance_within_declared_threshold() -> None:
    samples = [
        {
            "fresh": True,
            "pose": {"x": 1.0, "y": 2.0, "yaw": 0.0},
            "covariance_xy": 0.11,
        }
    ]

    assert _valid_final_amcl(samples, max_covariance_xy=0.1) == []


def test_verified_calibration_rejects_blank_frame_identifier() -> None:
    with pytest.raises(ValidationError, match="non-empty frame identifiers"):
        GroundTruthCalibration(
            status="VERIFIED",
            scene_sha256="a" * 64,
            map_sha256="b" * 64,
            source="known-landmarks",
            world_frame_id="/",
            map_frame_id="map",
            translation_x_m=0.0,
            translation_y_m=0.0,
            rotation_yaw_rad=0.0,
            calibration_method="three-landmark fit",
            residual_m=0.01,
        )
