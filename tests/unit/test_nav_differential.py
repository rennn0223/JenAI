from __future__ import annotations

import math

import pytest

from jenai.acceptance.nav_differential import (
    CanonicalGoal,
    GroundTruthCalibration,
    PairClassification,
    PairingGate,
    Pose2D,
    classify_pair,
    compare_goals,
    evaluate_pairing_gate,
)


def test_canonical_goal_normalizes_frame_angle_and_quaternion_sign() -> None:
    positive = CanonicalGoal.from_yaw(
        frame_id="/map",
        x=1.0,
        y=2.0,
        yaw=math.pi + 0.2,
    )
    negative_quaternion = CanonicalGoal.from_quaternion(
        frame_id="map",
        x=1.0,
        y=2.0,
        qx=-positive.qx,
        qy=-positive.qy,
        qz=-positive.qz,
        qw=-positive.qw,
    )

    comparison = compare_goals(positive, negative_quaternion)

    assert positive.frame_id == "map"
    assert positive.yaw == pytest.approx(-math.pi + 0.2)
    assert comparison.equivalent is True
    assert comparison.quaternion_angular_distance_rad == pytest.approx(0.0)


def test_goal_timestamps_are_compared_by_epoch_and_freshness_not_equality() -> None:
    first = CanonicalGoal.from_yaw(
        frame_id="map",
        x=1.0,
        y=2.0,
        yaw=0.0,
        stamp_ns=10,
        clock_domain="ros",
        simulation_epoch="epoch-1",
    )
    second = CanonicalGoal.from_yaw(
        frame_id="map",
        x=1.0,
        y=2.0,
        yaw=0.0,
        stamp_ns=20,
        clock_domain="ros",
        simulation_epoch="epoch-1",
    )

    assert compare_goals(first, second).equivalent is True

    wrong_epoch = second.model_copy(update={"simulation_epoch": "epoch-2"})
    comparison = compare_goals(first, wrong_epoch)
    assert comparison.equivalent is False
    assert comparison.timestamp_compatible is False


def test_ground_truth_requires_verified_map_world_calibration() -> None:
    unavailable = GroundTruthCalibration(
        status="GROUND_TRUTH_UNAVAILABLE",
        scene_sha256="a" * 64,
        map_sha256="b" * 64,
        source="no calibrated transform",
    )
    assert unavailable.world_to_map(Pose2D(x=1.0, y=2.0, yaw=0.0)) is None

    calibrated = GroundTruthCalibration(
        status="VERIFIED",
        scene_sha256="a" * 64,
        map_sha256="b" * 64,
        source="fiducial survey",
        translation_x_m=1.0,
        translation_y_m=-1.0,
        rotation_yaw_rad=math.pi / 2,
        calibration_method="three-point rigid fit",
        residual_m=0.004,
    )
    transformed = calibrated.world_to_map(Pose2D(x=2.0, y=0.0, yaw=0.25))
    assert transformed is not None
    assert transformed.x == pytest.approx(1.0)
    assert transformed.y == pytest.approx(1.0)
    assert transformed.yaw == pytest.approx(math.pi / 2 + 0.25)


def test_pairing_gate_rejects_different_runtime_or_start_state() -> None:
    gate = evaluate_pairing_gate(
        left_runtime_fingerprint="runtime-a",
        right_runtime_fingerprint="runtime-b",
        left_epoch="epoch-1",
        right_epoch="epoch-1",
        left_start=Pose2D(x=0.0, y=0.0, yaw=0.0),
        right_start=Pose2D(x=0.2, y=0.0, yaw=0.0),
        left_covariance_xy=0.01,
        right_covariance_xy=0.01,
        left_stationary=True,
        right_stationary=True,
        left_active_goal=False,
        right_active_goal=False,
        max_start_position_delta_m=0.05,
        max_start_yaw_delta_rad=0.05,
        max_covariance_xy=0.1,
    )

    assert gate.status == PairingGate.FAILED
    assert "runtime_fingerprint" in gate.failures
    assert "start_position" in gate.failures


def test_classification_distinguishes_verdict_only_from_actual_endpoint() -> None:
    canonical = CanonicalGoal.from_yaw(frame_id="map", x=1.0, y=2.0, yaw=0.0)
    gate = evaluate_pairing_gate(
        left_runtime_fingerprint="runtime",
        right_runtime_fingerprint="runtime",
        left_epoch="epoch-1",
        right_epoch="epoch-1",
        left_start=Pose2D(x=0.0, y=0.0, yaw=0.0),
        right_start=Pose2D(x=0.0, y=0.0, yaw=0.0),
        left_covariance_xy=0.01,
        right_covariance_xy=0.01,
        left_stationary=True,
        right_stationary=True,
        left_active_goal=False,
        right_active_goal=False,
    )

    verdict_only = classify_pair(
        left_goal=canonical,
        right_goal=canonical,
        pairing_gate=gate,
        left_final_map=Pose2D(x=1.01, y=2.0, yaw=0.0),
        right_final_map=Pose2D(x=1.01, y=2.0, yaw=0.0),
        left_final_ground_truth=None,
        right_final_ground_truth=None,
        left_execution_status="succeeded",
        right_execution_status="endpoint_mismatch",
        endpoint_difference_threshold_m=0.05,
        localization_divergence_threshold_m=0.05,
    )
    assert verdict_only == [PairClassification.JENAI_VERDICT_ONLY_DIFFERENCE]

    actual_endpoint = classify_pair(
        left_goal=canonical,
        right_goal=canonical,
        pairing_gate=gate,
        left_final_map=Pose2D(x=1.0, y=2.0, yaw=0.0),
        right_final_map=Pose2D(x=1.2, y=2.0, yaw=0.0),
        left_final_ground_truth=Pose2D(x=1.0, y=2.0, yaw=0.0),
        right_final_ground_truth=Pose2D(x=1.2, y=2.0, yaw=0.0),
        left_execution_status="succeeded",
        right_execution_status="endpoint_mismatch",
        endpoint_difference_threshold_m=0.05,
        localization_divergence_threshold_m=0.05,
    )
    assert PairClassification.ACTUAL_ENDPOINT_DIFFERENCE in actual_endpoint


def test_classification_fails_closed_when_pair_is_not_comparable() -> None:
    goal = CanonicalGoal.from_yaw(frame_id="map", x=1.0, y=2.0, yaw=0.0)
    failed_gate = evaluate_pairing_gate(
        left_runtime_fingerprint="runtime",
        right_runtime_fingerprint="runtime",
        left_epoch="epoch-1",
        right_epoch="epoch-2",
        left_start=Pose2D(x=0.0, y=0.0, yaw=0.0),
        right_start=Pose2D(x=0.0, y=0.0, yaw=0.0),
        left_covariance_xy=0.01,
        right_covariance_xy=0.01,
        left_stationary=True,
        right_stationary=True,
        left_active_goal=False,
        right_active_goal=False,
    )

    classifications = classify_pair(
        left_goal=goal,
        right_goal=goal,
        pairing_gate=failed_gate,
        left_final_map=None,
        right_final_map=None,
        left_final_ground_truth=None,
        right_final_ground_truth=None,
        left_execution_status=None,
        right_execution_status=None,
    )

    assert classifications == [PairClassification.PAIRING_GATE_FAILED]
