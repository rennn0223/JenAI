from __future__ import annotations

import math
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from jenai.acceptance.nav_differential import GroundTruthCalibration, PairClassification
from jenai.acceptance.nav_differential_runner import (
    DifferentialCaptureOptions,
    DifferentialMeasurementContract,
    DifferentialMode,
    ResetPolicy,
    _apply_runtime_fingerprint,
    _calibration_payload_sha256,
    _quaternion_yaw,
    compare_differential_artifacts,
)

ArtifactFactory = Callable[..., dict[str, object]]


def _artifact(factory: ArtifactFactory, *, mode: str) -> dict[str, Any]:
    return cast(dict[str, Any], factory(mode=mode))


def _comparison(
    factory: ArtifactFactory,
    *,
    left: dict[str, Any] | None = None,
    right: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return compare_differential_artifacts(
        left or _artifact(factory, mode="R1_bridge_nav2"),
        right or _artifact(factory, mode="R2_jenai_no_retry"),
    )


def _assert_insufficient(report: dict[str, Any]) -> None:
    assert report["included"] is False
    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


def test_sensor_quaternion_rejects_zero_norm() -> None:
    assert _quaternion_yaw({"x": 0.0, "y": 0.0, "z": 0.0, "w": 0.0}) is None


def test_sensor_quaternion_is_scale_invariant() -> None:
    yaw = 0.75
    scale = 9.0
    orientation = {
        "x": 0.0,
        "y": 0.0,
        "z": scale * math.sin(yaw / 2.0),
        "w": scale * math.cos(yaw / 2.0),
    }
    assert _quaternion_yaw(orientation) == pytest.approx(yaw)


def _verified_calibration(*, source: str = "calibration-a") -> GroundTruthCalibration:
    return GroundTruthCalibration(
        status="VERIFIED",
        scene_sha256="e" * 64,
        map_sha256="b" * 64,
        source=source,
        world_frame_id="world",
        map_frame_id="map",
        translation_x_m=0.0,
        translation_y_m=0.0,
        rotation_yaw_rad=0.0,
        calibration_method="second-review fixture",
        residual_m=0.0,
    )


def _add_verified_ground_truth(
    artifact: dict[str, Any],
    *,
    source: str = "calibration-a",
) -> None:
    calibration = _verified_calibration(source=source)
    artifact["ground_truth_calibration"] = calibration.model_dump(mode="json")
    contract = cast(dict[str, Any], artifact["measurement_contract"])
    contract.update(
        {
            "ground_truth_topic": "/isaac/ground_truth",
            "ground_truth_type": "geometry_msgs/msg/PoseStamped",
            "ground_truth_calibration_sha256": _calibration_payload_sha256(calibration),
        }
    )
    stream_contract = cast(dict[str, Any], artifact["topic_stream_contract"])
    stream_contract["ground_truth"] = {
        "topic": "/isaac/ground_truth",
        "message_type": "geometry_msgs/msg/PoseStamped",
    }

    dispatch_samples = [
        _ground_truth_raw_sample(35, 1_000_000_000, x=0.0, y=0.0),
        _ground_truth_raw_sample(135, 3_000_000_000, x=0.0, y=0.0),
    ]
    final_samples = [
        _ground_truth_raw_sample(host, stamp, x=1.0, y=2.0)
        for host, stamp in zip(
            (170, 190, 230),
            (10_000_000_000, 11_000_000_000, 12_000_000_000),
            strict=True,
        )
    ]
    dispatch_topics = cast(dict[str, Any], artifact["topic_samples_at_dispatch_end"])
    complete_topics = cast(dict[str, Any], artifact["topic_samples"])
    dispatch_topics["ground_truth"] = deepcopy(dispatch_samples)
    complete_topics["ground_truth"] = [*deepcopy(dispatch_samples), *deepcopy(final_samples)]

    window = cast(dict[str, Any], artifact["final_observation_window"])
    raw_window = [
        {
            "host_monotonic_ns": host,
            "host_age_ns": 0,
            "source_stamp_ns": stamp,
            "sample_clock_ns": stamp,
            "capture_clock_ns": stamp,
            "source_age_ns": 0,
            "fresh": True,
            "message": _ground_truth_message(stamp, x=1.0, y=2.0),
        }
        for host, stamp in zip(
            (170, 190, 230),
            (10_000_000_000, 11_000_000_000, 12_000_000_000),
            strict=True,
        )
    ]
    verified_samples = [
        {
            "host_monotonic_ns": host,
            "source_stamp_ns": stamp,
            "capture_clock_ns": stamp,
            "source_age_ns": 0,
            "fresh": True,
            "source_frame_id": "world",
            "world_pose": {"x": 1.0, "y": 2.0, "yaw": 0.0},
            "map_pose": {"x": 1.0, "y": 2.0, "yaw": 0.0},
        }
        for host, stamp in zip(
            (170, 190, 230),
            (10_000_000_000, 11_000_000_000, 12_000_000_000),
            strict=True,
        )
    ]
    window["ground_truth_samples"] = raw_window
    window["verified_ground_truth_samples"] = verified_samples
    window["ground_truth_required"] = True
    artifact["ground_truth_samples"] = deepcopy(verified_samples)
    artifact["final_ground_truth_map_median"] = {"x": 1.0, "y": 2.0, "yaw": 0.0}

    for index, state in enumerate(_state_copies(artifact)):
        stamp = 1_000_000_000 if index == 0 else 3_000_000_000
        host = 35 if index == 0 else 135
        evaluated = int(state["evaluated_host_monotonic_ns"])
        capture_clock = 2_000_000_000 if index == 0 else 4_000_000_000
        state.update(
            {
                "ground_truth_required": True,
                "ground_truth_source": {
                    "host_monotonic_ns": host,
                    "host_age_ns": evaluated - host,
                    "source_stamp_ns": stamp,
                    "sample_clock_ns": stamp,
                    "capture_clock_ns": capture_clock,
                    "source_age_ns": capture_clock - stamp,
                    "fresh": True,
                },
                "ground_truth_source_frame_id": "world",
                "ground_truth_world_pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
                "ground_truth_map_pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            }
        )


def _ground_truth_message(stamp_ns: int, *, x: float, y: float) -> dict[str, Any]:
    return {
        "header": {
            "stamp": {"sec": stamp_ns // 1_000_000_000, "nanosec": stamp_ns % 1_000_000_000},
            "frame_id": "world",
        },
        "pose": {
            "position": {"x": x, "y": y, "z": 0.0},
            "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        },
    }


def _ground_truth_raw_sample(
    host_ns: int,
    stamp_ns: int,
    *,
    x: float,
    y: float,
) -> dict[str, Any]:
    return {
        "host_monotonic_ns": host_ns,
        "message": _ground_truth_message(stamp_ns, x=x, y=y),
    }


def _set_ground_truth_start_pose(artifact: dict[str, Any], *, x: float) -> None:
    for stream_name in ("topic_samples_at_dispatch_end", "topic_samples"):
        streams = cast(dict[str, Any], artifact[stream_name])
        samples = cast(list[dict[str, Any]], streams["ground_truth"])
        for sample in samples[:2]:
            message = cast(dict[str, Any], sample["message"])
            pose = cast(dict[str, Any], message["pose"])
            position = cast(dict[str, Any], pose["position"])
            position["x"] = x
    for state in _state_copies(artifact):
        state["ground_truth_world_pose"] = {"x": x, "y": 0.0, "yaw": 0.0}
        state["ground_truth_map_pose"] = {"x": x, "y": 0.0, "yaw": 0.0}


def _downgrade_ground_truth_to_map_only(artifact: dict[str, Any]) -> None:
    artifact["ground_truth_calibration"] = GroundTruthCalibration(
        status="GROUND_TRUTH_UNAVAILABLE",
        scene_sha256="e" * 64,
        map_sha256="b" * 64,
        source="calibration identity could not be verified",
    ).model_dump(mode="json")
    for state in _state_copies(artifact):
        state.update(
            {
                "ground_truth_required": False,
                "ground_truth_source": None,
                "ground_truth_source_frame_id": None,
                "ground_truth_world_pose": None,
                "ground_truth_map_pose": None,
            }
        )
    window = cast(dict[str, Any], artifact["final_observation_window"])
    window["verified_ground_truth_samples"] = []
    window["ground_truth_required"] = False
    artifact["ground_truth_samples"] = []
    artifact["final_ground_truth_map_median"] = None


def _state_copies(artifact: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    timeline = cast(dict[str, Any], artifact["t1_goal_dispatch"])
    dispatch = cast(dict[str, Any], timeline["dispatch_observations"][0])
    return (
        cast(dict[str, Any], artifact["t0_scenario_start"]),
        cast(dict[str, Any], timeline["state_before_forward"]),
        cast(dict[str, Any], dispatch["state_before_forward"]),
    )


def test_stationary_flag_cannot_hide_moving_final_odom(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    window = cast(dict[str, Any], left["final_observation_window"])
    samples = cast(list[dict[str, Any]], window["valid_odom_samples"])
    samples[-1]["linear_velocity_mps"] = 0.25
    assert window["stationary"] is True

    _assert_insufficient(_comparison(differential_artifact_factory, left=left))


@pytest.mark.parametrize(
    "stream,stamp_field",
    [
        ("map_pose_samples", "capture_clock_ns"),
        ("valid_amcl_samples", "source_stamp_ns"),
        ("valid_odom_samples", "source_stamp_ns"),
        ("verified_ground_truth_samples", "source_stamp_ns"),
    ],
)
def test_every_final_sample_must_be_inside_declared_ros_window(
    stream: str,
    stamp_field: str,
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    if stream == "verified_ground_truth_samples":
        _add_verified_ground_truth(left)
    window = cast(dict[str, Any], left["final_observation_window"])
    samples = cast(list[dict[str, Any]], window[stream])
    samples[0][stamp_field] = 9_000_000_000

    _assert_insufficient(_comparison(differential_artifact_factory, left=left))


@pytest.mark.parametrize(
    "stream,field,value",
    [
        ("valid_amcl_samples", "pose", {"x": "invalid", "y": 2.0, "yaw": 0.0}),
        ("valid_odom_samples", "pose", {"x": 1.0, "y": None, "yaw": 0.0}),
        ("valid_odom_samples", "linear_velocity_mps", True),
        ("valid_odom_samples", "angular_velocity_rps", False),
    ],
)
def test_final_localization_samples_require_typed_pose_and_non_bool_velocity(
    stream: str,
    field: str,
    value: object,
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    window = cast(dict[str, Any], left["final_observation_window"])
    samples = cast(list[dict[str, Any]], window[stream])
    samples[1][field] = value

    _assert_insufficient(_comparison(differential_artifact_factory, left=left))


@pytest.mark.parametrize("state_index", [0, 1, 2], ids=["t0", "t1-public", "t1-dispatch"])
@pytest.mark.parametrize("source_name", ["amcl_source", "odom_source", "action_status_source"])
def test_state_topic_host_timestamp_must_not_precede_cutoff(
    state_index: int,
    source_name: str,
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    state = _state_copies(left)[state_index]
    source = cast(dict[str, Any], state[source_name])
    source["host_monotonic_ns"] = int(state["cutoff_host_monotonic_ns"]) - 1

    _assert_insufficient(_comparison(differential_artifact_factory, left=left))


@pytest.mark.parametrize("state_index", [0, 1, 2], ids=["t0", "t1-public", "t1-dispatch"])
@pytest.mark.parametrize("source_name", ["amcl_source", "odom_source", "action_status_source"])
def test_state_topic_host_age_must_match_evaluation_timestamp(
    state_index: int,
    source_name: str,
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    state = _state_copies(left)[state_index]
    source = cast(dict[str, Any], state[source_name])
    source["host_age_ns"] = int(source["host_age_ns"]) + 1

    _assert_insufficient(_comparison(differential_artifact_factory, left=left))


def test_t1_cutoff_must_not_precede_nav_send_invocation(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    timeline = cast(dict[str, Any], left["t1_goal_dispatch"])
    dispatch = cast(dict[str, Any], timeline["dispatch_observations"][0])
    invoked_ns = int(dispatch["nav_send_invoked_host_monotonic_ns"])
    for state in (
        cast(dict[str, Any], timeline["state_before_forward"]),
        cast(dict[str, Any], dispatch["state_before_forward"]),
    ):
        state["cutoff_host_monotonic_ns"] = invoked_ns - 1

    _assert_insufficient(_comparison(differential_artifact_factory, left=left))


def test_duplicated_t1_state_copies_must_agree(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    timeline = cast(dict[str, Any], left["t1_goal_dispatch"])
    dispatch = cast(dict[str, Any], timeline["dispatch_observations"][0])
    nested = cast(dict[str, Any], dispatch["state_before_forward"])
    nested["map_to_base"] = {"x": 99.0, "y": 0.0, "yaw": 0.0}

    _assert_insufficient(_comparison(differential_artifact_factory, left=left))


def test_uuid_evidence_rejects_unknown_goal_status(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    timeline = cast(dict[str, Any], left["t1_goal_dispatch"])
    accepted = cast(dict[str, Any], timeline["accepted_goal_observations"][0])
    accepted["status"] = 0

    _assert_insufficient(_comparison(differential_artifact_factory, left=left))


@pytest.mark.parametrize("field", ["goal_stamp_ns", "capture_clock_ns", "goal_stamp_age_ns"])
def test_uuid_evidence_requires_raw_stamp_clock_and_age(
    field: str,
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    timeline = cast(dict[str, Any], left["t1_goal_dispatch"])
    accepted = cast(dict[str, Any], timeline["accepted_goal_observations"][0])
    accepted.pop(field)

    _assert_insufficient(_comparison(differential_artifact_factory, left=left))


def test_nav2_terminal_rejects_arbitrary_status_text(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    terminal = cast(dict[str, Any], left["nav2_terminal"])
    terminal["status"] = "completed_by_magic"

    _assert_insufficient(_comparison(differential_artifact_factory, left=left))


def test_r2_rejects_nonzero_effective_retry(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    right = _artifact(differential_artifact_factory, mode="R2_jenai_no_retry")
    result = cast(dict[str, Any], right["jenai_result"])
    effective = cast(dict[str, Any], result["effective_experimental_config"])
    effective["nav_endpoint_retry_limit"] = 1

    _assert_insufficient(_comparison(differential_artifact_factory, right=right))


def test_r2_rejects_more_than_one_navigation_attempt(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    right = _artifact(differential_artifact_factory, mode="R2_jenai_no_retry")
    result = cast(dict[str, Any], right["jenai_result"])
    attempts = cast(list[dict[str, Any]], result["navigation_attempts"])
    attempts.append({"tag": "attempt-2"})

    _assert_insufficient(_comparison(differential_artifact_factory, right=right))


@pytest.mark.parametrize(
    ("mode", "tampered_status"),
    [
        ("R1_bridge_nav2", "endpoint_mismatch"),
        ("R2_jenai_no_retry", "endpoint_mismatch"),
    ],
)
def test_top_level_execution_status_must_match_mode_specific_evidence(
    mode: str,
    tampered_status: str,
    differential_artifact_factory: ArtifactFactory,
) -> None:
    artifact = _artifact(differential_artifact_factory, mode=mode)
    artifact["execution_status"] = tampered_status

    if mode == "R1_bridge_nav2":
        _assert_insufficient(_comparison(differential_artifact_factory, left=artifact))
    else:
        _assert_insufficient(_comparison(differential_artifact_factory, right=artifact))


def test_r2_execution_status_rejects_unknown_vocabulary_even_when_copies_agree(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    right = _artifact(differential_artifact_factory, mode="R2_jenai_no_retry")
    right["execution_status"] = "completed_by_magic"
    result = cast(dict[str, Any], right["jenai_result"])
    result["execution_status"] = "completed_by_magic"

    _assert_insufficient(_comparison(differential_artifact_factory, right=right))


def test_verified_ground_truth_sample_must_match_calibration_source_frame(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    _add_verified_ground_truth(left)
    window = cast(dict[str, Any], left["final_observation_window"])
    samples = cast(list[dict[str, Any]], window["verified_ground_truth_samples"])
    samples[0]["source_frame_id"] = "wrong_world"

    _assert_insufficient(_comparison(differential_artifact_factory, left=left))


def test_raw_backed_verified_ground_truth_pair_is_comparison_eligible(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    right = _artifact(differential_artifact_factory, mode="R2_jenai_no_retry")
    _add_verified_ground_truth(left)
    _add_verified_ground_truth(right)

    report = _comparison(differential_artifact_factory, left=left, right=right)

    assert report["included"] is True


def test_configured_but_unverified_ground_truth_remains_map_only_eligible(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    right = _artifact(differential_artifact_factory, mode="R2_jenai_no_retry")
    for artifact in (left, right):
        _add_verified_ground_truth(artifact)
        _downgrade_ground_truth_to_map_only(artifact)

    report = _comparison(differential_artifact_factory, left=left, right=right)

    assert report["included"] is True
    assert PairClassification.ACTUAL_ENDPOINT_DIFFERENCE not in report["classifications"]


def test_raw_final_ground_truth_tamper_is_rejected(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    _add_verified_ground_truth(left)
    streams = cast(dict[str, Any], left["topic_samples"])
    samples = cast(list[dict[str, Any]], streams["ground_truth"])
    message = cast(dict[str, Any], samples[-1]["message"])
    pose = cast(dict[str, Any], message["pose"])
    position = cast(dict[str, Any], pose["position"])
    position["x"] = 9.0

    _assert_insufficient(_comparison(differential_artifact_factory, left=left))


@pytest.mark.parametrize("sample_index", [0, 1], ids=["t0", "t1"])
@pytest.mark.parametrize("problem", ["missing_header", "stale", "wrong_frame"])
def test_raw_start_ground_truth_must_be_fresh_headered_and_in_calibrated_frame(
    sample_index: int,
    problem: str,
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    _add_verified_ground_truth(left)
    for stream_name in ("topic_samples_at_dispatch_end", "topic_samples"):
        streams = cast(dict[str, Any], left[stream_name])
        samples = cast(list[dict[str, Any]], streams["ground_truth"])
        message = cast(dict[str, Any], samples[sample_index]["message"])
        if problem == "missing_header":
            message.pop("header")
        else:
            header = cast(dict[str, Any], message["header"])
            if problem == "stale":
                header["stamp"] = {"sec": 0, "nanosec": 0}
            else:
                header["frame_id"] = "wrong_world"

    _assert_insufficient(_comparison(differential_artifact_factory, left=left))


@pytest.mark.parametrize("tamper", ["calibration", "digest", "message_type"])
def test_verified_ground_truth_contract_tamper_is_rejected(
    tamper: str,
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    _add_verified_ground_truth(left)
    if tamper == "calibration":
        calibration = cast(dict[str, Any], left["ground_truth_calibration"])
        calibration["translation_x_m"] = 0.25
    elif tamper == "digest":
        contract = cast(dict[str, Any], left["measurement_contract"])
        contract["ground_truth_calibration_sha256"] = "f" * 64
    else:
        streams = cast(dict[str, Any], left["topic_stream_contract"])
        ground_truth = cast(dict[str, Any], streams["ground_truth"])
        ground_truth["message_type"] = "geometry_msgs/msg/TransformStamped"

    _assert_insufficient(_comparison(differential_artifact_factory, left=left))


def test_ground_truth_schema_rejects_invalid_type_topic_and_frame(
    differential_artifact_factory: ArtifactFactory,
    tmp_path: Path,
) -> None:
    artifact = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    contract = cast(dict[str, Any], artifact["measurement_contract"])
    contract.update(
        {
            "ground_truth_topic": "/isaac/ground_truth",
            "ground_truth_type": "",
            "ground_truth_calibration_sha256": "f" * 64,
        }
    )
    with pytest.raises(ValidationError):
        DifferentialMeasurementContract.model_validate(contract)

    with pytest.raises(ValidationError):
        GroundTruthCalibration.model_validate(
            {
                **_verified_calibration().model_dump(mode="json"),
                "world_frame_id": " ",
            }
        )

    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(_verified_calibration().model_dump_json(), encoding="utf-8")
    options = DifferentialCaptureOptions(
        output=tmp_path / "artifact.json",
        location="Dock",
        pair_id="pair-01",
        mode=DifferentialMode.R1_BRIDGE_NAV2,
        simulation_epoch="epoch",
        reset_policy=ResetPolicy.NAV2_RESTART,
        calibration_path=calibration_path,
        ground_truth_topic="isaac/ground_truth",
    )
    assert options.ground_truth_topic == "/isaac/ground_truth"
    with pytest.raises(ValidationError):
        DifferentialCaptureOptions.model_validate(
            {
                **options.model_dump(),
                "ground_truth_topic": "/isaac/../ground_truth",
            }
        )


def test_pair_rejects_different_verified_ground_truth_sources(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    right = _artifact(differential_artifact_factory, mode="R2_jenai_no_retry")
    _add_verified_ground_truth(left, source="calibration-a")
    _add_verified_ground_truth(right, source="calibration-b")

    report = _comparison(differential_artifact_factory, left=left, right=right)

    assert report["included"] is False
    assert PairClassification.PAIRING_GATE_FAILED in report["classifications"]


def test_pair_rejects_physically_different_calibrated_start_poses(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    right = _artifact(differential_artifact_factory, mode="R2_jenai_no_retry")
    _add_verified_ground_truth(left)
    _add_verified_ground_truth(right)
    _set_ground_truth_start_pose(right, x=0.50)

    report = _comparison(differential_artifact_factory, left=left, right=right)

    assert report["included"] is False
    assert PairClassification.PAIRING_GATE_FAILED in report["classifications"]


def test_pair_rejects_nav2_process_generation_mismatch(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = _artifact(differential_artifact_factory, mode="R1_bridge_nav2")
    right = _artifact(differential_artifact_factory, mode="R2_jenai_no_retry")
    identity = cast(dict[str, Any], right["runtime_identity"])
    generation = cast(dict[str, Any], identity["nav2_process_generation"])
    processes = cast(list[dict[str, Any]], generation["processes"])
    processes[1]["start_ticks"] = 2002
    identity["nav2_process_generation_end"] = deepcopy(generation)
    _apply_runtime_fingerprint(identity)

    report = _comparison(differential_artifact_factory, left=left, right=right)

    assert report["included"] is False
    assert PairClassification.PAIRING_GATE_FAILED in report["classifications"]
