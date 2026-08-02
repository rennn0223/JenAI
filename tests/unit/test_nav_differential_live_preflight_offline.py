from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

from jenai.acceptance.nav_differential import GroundTruthCalibration
from jenai.acceptance.nav_differential_runner import (
    _apply_runtime_fingerprint,
    _calibration_payload_sha256,
    validate_live_preflight_artifact,
)


def _live_preflight_artifact(factory: Any) -> dict[str, Any]:
    artifact = cast(
        dict[str, Any],
        factory(mode="R1_bridge_nav2", pair_id="live-preflight-offline"),
    )
    timeline = cast(dict[str, Any], artifact.pop("t1_goal_dispatch"))
    dispatch = deepcopy(cast(list[dict[str, Any]], timeline["dispatch_observations"])[0])
    dispatch["nav_send_forwarded_host_monotonic_ns"] = None
    dispatch["forward_completed_host_monotonic_ns"] = None
    dispatch["forward_suppressed"] = "live_preflight"
    dispatch["tag"] = "nav_diff_R1_bridge_nav2_live_preflight"
    dispatch["goal_comparison"] = {
        "equivalent": True,
        "frame_equal": True,
        "position_delta_m": 0.0,
        "quaternion_angular_distance_rad": 0.0,
        "timestamp_compatible": True,
    }
    artifact.update(
        {
            "execution_requested": False,
            "live_preflight_requested": True,
            "motion_attempted": False,
            "overall": "preflight_only",
            "t1_pre_dispatch": deepcopy(timeline["state_before_forward"]),
            "dispatch_observations": [dispatch],
            "checks": [
                {
                    "id": "live_preflight",
                    "status": "PASS",
                    "detail": "All live pre-dispatch gates passed without forwarding a goal.",
                }
            ],
            "cleanup": {
                "status": "PASS",
                "failures": [],
                "final_halt": {
                    "status": "SKIP",
                    "detail": "No motion was attempted.",
                },
                "primary_halt": {
                    "status": "SKIP",
                    "detail": "No motion was attempted.",
                },
                "rescue_bridge": None,
                "unwatch": {"status": "PASS", "failures": []},
                "bridge_shutdown": {"status": "PASS"},
            },
        }
    )
    artifact["final_halt"] = deepcopy(cast(dict[str, Any], artifact["cleanup"])["final_halt"])
    for field in (
        "execution_status",
        "final_ground_truth_map_median",
        "final_map_pose_median",
        "final_map_pose_samples",
        "final_observation_window",
        "ground_truth_samples",
        "jenai_result",
        "nav2_terminal",
    ):
        artifact.pop(field, None)
    cast(dict[str, Any], artifact["measurement_contract"])["preflight_sample_s"] = 1e-8
    states = [
        cast(dict[str, Any], artifact["t0_scenario_start"]),
        cast(dict[str, Any], artifact["t1_pre_dispatch"]),
        cast(dict[str, Any], dispatch["state_before_forward"]),
    ]
    for state in states:
        state["action_status_source"] = {
            "fresh": True,
            "observation": "no_status_observed",
            "cutoff_host_monotonic_ns": state["cutoff_host_monotonic_ns"],
            "evaluated_host_monotonic_ns": state["evaluated_host_monotonic_ns"],
        }
    artifact["topic_samples"] = deepcopy(artifact["topic_samples_at_dispatch_end"])
    for snapshot_name in ("topic_samples", "topic_samples_at_dispatch_end"):
        streams = cast(dict[str, list[dict[str, Any]]], artifact[snapshot_name])
        streams["action_status"] = []
    artifact["pose_observations"] = [
        observation
        for observation in cast(list[dict[str, Any]], artifact["pose_observations"])
        if observation["purpose"] in {"t0_start", "t1_pre_dispatch"}
    ]
    return artifact


def test_live_preflight_artifact_is_rederived_from_raw_evidence(
    differential_artifact_factory: Any,
) -> None:
    artifact = _live_preflight_artifact(differential_artifact_factory)

    report = validate_live_preflight_artifact(artifact)

    assert report == {
        "schema_version": 1,
        "valid": True,
        "failures": [],
    }


def test_live_preflight_accepts_pose_lead_within_measurement_interval(
    differential_artifact_factory: Any,
) -> None:
    artifact = _live_preflight_artifact(differential_artifact_factory)
    for observation in cast(list[dict[str, Any]], artifact["pose_observations"]):
        observation["result"]["stamp_ns"] = observation["completed_clock_ns"] + 100_000_000

    report = validate_live_preflight_artifact(artifact)

    assert report["valid"] is True
    assert report["failures"] == []


def test_live_preflight_rejects_pose_lead_beyond_measurement_interval(
    differential_artifact_factory: Any,
) -> None:
    artifact = _live_preflight_artifact(differential_artifact_factory)
    observation = cast(list[dict[str, Any]], artifact["pose_observations"])[0]
    observation["result"]["stamp_ns"] = observation["completed_clock_ns"] + 300_000_000

    report = validate_live_preflight_artifact(artifact)

    assert report["valid"] is False
    assert "pose_observation_transform_from_future" in report["failures"]


def test_live_preflight_rejects_tampered_dispatch_goal(
    differential_artifact_factory: Any,
) -> None:
    artifact = _live_preflight_artifact(differential_artifact_factory)
    dispatch = cast(list[dict[str, Any]], artifact["dispatch_observations"])[0]
    cast(dict[str, Any], dispatch["actual_goal"])["x"] = 99.0

    report = validate_live_preflight_artifact(artifact)

    assert report["valid"] is False
    assert "canonical_goal_binding" in report["failures"]


def test_live_preflight_rejects_t1_copy_drift(
    differential_artifact_factory: Any,
) -> None:
    artifact = _live_preflight_artifact(differential_artifact_factory)
    dispatch = cast(list[dict[str, Any]], artifact["dispatch_observations"])[0]
    nested = cast(dict[str, Any], dispatch["state_before_forward"])
    cast(dict[str, Any], nested["input_continuity"])["status"] = "FAIL"

    report = validate_live_preflight_artifact(artifact)

    assert report["valid"] is False
    assert "t1_state_copy_mismatch" in report["failures"]


def test_live_preflight_rejects_tampered_command_tag(
    differential_artifact_factory: Any,
) -> None:
    artifact = _live_preflight_artifact(differential_artifact_factory)
    dispatch = cast(list[dict[str, Any]], artifact["dispatch_observations"])[0]
    dispatch["tag"] = "nav_diff_R1_bridge_nav2_other"

    report = validate_live_preflight_artifact(artifact)

    assert report["valid"] is False
    assert "command_tag_binding" in report["failures"]


def test_live_preflight_rejects_incomplete_cleanup(
    differential_artifact_factory: Any,
) -> None:
    artifact = _live_preflight_artifact(differential_artifact_factory)
    cast(dict[str, Any], artifact["cleanup"]).pop("unwatch")

    report = validate_live_preflight_artifact(artifact)

    assert report["valid"] is False
    assert "cleanup" in report["failures"]


def test_live_preflight_rejects_missing_final_halt_alias(
    differential_artifact_factory: Any,
) -> None:
    artifact = _live_preflight_artifact(differential_artifact_factory)
    artifact.pop("final_halt")

    report = validate_live_preflight_artifact(artifact)

    assert report["valid"] is False
    assert "final_halt_alias" in report["failures"]


def test_live_preflight_rejects_tampered_final_halt_alias(
    differential_artifact_factory: Any,
) -> None:
    artifact = _live_preflight_artifact(differential_artifact_factory)
    cast(dict[str, Any], artifact["final_halt"])["status"] = "PASS"

    report = validate_live_preflight_artifact(artifact)

    assert report["valid"] is False
    assert "final_halt_alias" in report["failures"]


def test_live_preflight_rejects_tampered_runtime_checkpoint(
    differential_artifact_factory: Any,
) -> None:
    artifact = _live_preflight_artifact(differential_artifact_factory)
    t1 = cast(dict[str, Any], artifact["t1_pre_dispatch"])
    checkpoint = cast(dict[str, Any], t1["runtime_stack_checkpoint"])
    cast(dict[str, Any], checkpoint["observed"])["amcl_resample_interval"] = 99

    report = validate_live_preflight_artifact(artifact)

    assert report["valid"] is False
    assert "pre_dispatch_runtime_stack" in report["failures"]


def test_live_preflight_rejects_missing_observed_empty_status_stream(
    differential_artifact_factory: Any,
) -> None:
    artifact = _live_preflight_artifact(differential_artifact_factory)
    cast(dict[str, Any], artifact["topic_samples_at_dispatch_end"]).pop("action_status")

    report = validate_live_preflight_artifact(artifact)

    assert report["valid"] is False
    assert "dispatch_topic_samples_action_status_missing" in report["failures"]


def _configure_empty_ground_truth_streams(artifact: dict[str, Any]) -> None:
    identity = cast(dict[str, Any], artifact["runtime_identity"])
    calibration = GroundTruthCalibration(
        status="GROUND_TRUTH_UNAVAILABLE",
        scene_sha256=str(identity["scene_sha256"]),
        map_sha256=str(identity["site_map_sha256"]),
        source="configured topic without verified world-to-map calibration",
    )
    calibration_digest = _calibration_payload_sha256(calibration)
    artifact["ground_truth_calibration"] = calibration.model_dump(mode="json")
    identity["ground_truth_calibration_effective_sha256"] = calibration_digest
    _apply_runtime_fingerprint(identity)
    contract = cast(dict[str, Any], artifact["measurement_contract"])
    contract["ground_truth_topic"] = "/isaac/ground_truth"
    contract["ground_truth_type"] = "geometry_msgs/msg/PoseStamped"
    contract["ground_truth_calibration_sha256"] = calibration_digest
    stream_contract = cast(dict[str, Any], artifact["topic_stream_contract"])
    stream_contract["ground_truth"] = {
        "topic": "/isaac/ground_truth",
        "message_type": "geometry_msgs/msg/PoseStamped",
        "qos_profile": "sensor_data",
    }
    for snapshot_name in ("topic_samples", "topic_samples_at_dispatch_end"):
        streams = cast(dict[str, list[dict[str, Any]]], artifact[snapshot_name])
        streams["ground_truth"] = []


def test_live_preflight_preserves_configured_empty_ground_truth_stream(
    differential_artifact_factory: Any,
) -> None:
    artifact = _live_preflight_artifact(differential_artifact_factory)
    _configure_empty_ground_truth_streams(artifact)

    report = validate_live_preflight_artifact(artifact)

    assert report["valid"] is True
    assert report["failures"] == []


def test_live_preflight_rejects_missing_configured_ground_truth_stream(
    differential_artifact_factory: Any,
) -> None:
    artifact = _live_preflight_artifact(differential_artifact_factory)
    _configure_empty_ground_truth_streams(artifact)
    cast(dict[str, Any], artifact["topic_samples_at_dispatch_end"]).pop("ground_truth")

    report = validate_live_preflight_artifact(artifact)

    assert report["valid"] is False
    assert "dispatch_topic_samples_ground_truth_missing" in report["failures"]
