"""Observation-only models for Isaac Nav2/JenAI differential experiments.

This module deliberately contains no ROS imports and no navigation policy.  It
normalizes evidence produced by independent runtime adapters, decides whether
two runs are comparable, and classifies only differences supported by the
recorded evidence.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _normalized_frame_id(frame_id: str) -> str:
    normalized = frame_id.strip().lstrip("/")
    if not normalized:
        raise ValueError("frame_id must not be blank")
    return normalized


def _normalized_angle(angle: float) -> float:
    if not math.isfinite(angle):
        raise ValueError("angle must be finite")
    return math.atan2(math.sin(angle), math.cos(angle))


class Pose2D(BaseModel):
    """One finite planar pose expressed in an explicitly named frame."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    yaw: float = Field(allow_inf_nan=False)


class CanonicalGoal(BaseModel):
    """A comparison-safe representation of one Nav2 goal.

    ROS header stamps are evidence about clock domain, epoch and freshness.
    They are intentionally not part of numeric goal equality.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    frame_id: str
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    yaw: float = Field(allow_inf_nan=False)
    qx: float = Field(allow_inf_nan=False)
    qy: float = Field(allow_inf_nan=False)
    qz: float = Field(allow_inf_nan=False)
    qw: float = Field(allow_inf_nan=False)
    stamp_ns: int | None = Field(default=None, ge=0)
    clock_domain: str | None = None
    simulation_epoch: str | None = None
    stamp_fresh: bool | None = None

    @classmethod
    def from_yaw(
        cls,
        *,
        frame_id: str,
        x: float,
        y: float,
        yaw: float,
        stamp_ns: int | None = None,
        clock_domain: str | None = None,
        simulation_epoch: str | None = None,
        stamp_fresh: bool | None = None,
    ) -> CanonicalGoal:
        normalized_yaw = _normalized_angle(yaw)
        return cls(
            frame_id=_normalized_frame_id(frame_id),
            x=x,
            y=y,
            yaw=normalized_yaw,
            qx=0.0,
            qy=0.0,
            qz=math.sin(normalized_yaw / 2.0),
            qw=math.cos(normalized_yaw / 2.0),
            stamp_ns=stamp_ns,
            clock_domain=clock_domain,
            simulation_epoch=simulation_epoch,
            stamp_fresh=stamp_fresh,
        )

    @classmethod
    def from_quaternion(
        cls,
        *,
        frame_id: str,
        x: float,
        y: float,
        qx: float,
        qy: float,
        qz: float,
        qw: float,
        stamp_ns: int | None = None,
        clock_domain: str | None = None,
        simulation_epoch: str | None = None,
        stamp_fresh: bool | None = None,
    ) -> CanonicalGoal:
        components = (qx, qy, qz, qw)
        if not all(math.isfinite(item) for item in components):
            raise ValueError("quaternion must be finite")
        norm = math.sqrt(sum(item * item for item in components))
        if norm <= 1e-12:
            raise ValueError("quaternion must have non-zero norm")
        normalized = tuple(item / norm for item in components)
        nqx, nqy, nqz, nqw = normalized
        yaw = _normalized_angle(
            math.atan2(
                2.0 * (nqw * nqz + nqx * nqy),
                1.0 - 2.0 * (nqy * nqy + nqz * nqz),
            )
        )
        return cls(
            frame_id=_normalized_frame_id(frame_id),
            x=x,
            y=y,
            yaw=yaw,
            qx=nqx,
            qy=nqy,
            qz=nqz,
            qw=nqw,
            stamp_ns=stamp_ns,
            clock_domain=clock_domain,
            simulation_epoch=simulation_epoch,
            stamp_fresh=stamp_fresh,
        )


class GoalComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    equivalent: bool
    frame_equal: bool
    position_delta_m: float = Field(ge=0, allow_inf_nan=False)
    quaternion_angular_distance_rad: float = Field(ge=0, allow_inf_nan=False)
    timestamp_compatible: bool


def _timestamp_compatible(left: CanonicalGoal, right: CanonicalGoal) -> bool:
    if left.stamp_fresh is False or right.stamp_fresh is False:
        return False
    if (
        left.clock_domain is not None
        and right.clock_domain is not None
        and left.clock_domain != right.clock_domain
    ):
        return False
    return not (
        left.simulation_epoch is not None
        and right.simulation_epoch is not None
        and left.simulation_epoch != right.simulation_epoch
    )


def compare_goals(
    left: CanonicalGoal,
    right: CanonicalGoal,
    *,
    position_tolerance_m: float = 1e-9,
    orientation_tolerance_rad: float = 1e-9,
) -> GoalComparison:
    """Compare canonical values while treating ``q`` and ``-q`` as identical."""

    if position_tolerance_m < 0 or orientation_tolerance_rad < 0:
        raise ValueError("goal comparison tolerances must not be negative")
    dot = abs(left.qx * right.qx + left.qy * right.qy + left.qz * right.qz + left.qw * right.qw)
    angular_distance = 2.0 * math.acos(max(-1.0, min(1.0, dot)))
    position_delta = math.hypot(left.x - right.x, left.y - right.y)
    frame_equal = left.frame_id == right.frame_id
    timestamp_compatible = _timestamp_compatible(left, right)
    return GoalComparison(
        equivalent=(
            frame_equal
            and position_delta <= position_tolerance_m
            and angular_distance <= orientation_tolerance_rad
            and timestamp_compatible
        ),
        frame_equal=frame_equal,
        position_delta_m=position_delta,
        quaternion_angular_distance_rad=angular_distance,
        timestamp_compatible=timestamp_compatible,
    )


class GroundTruthCalibration(BaseModel):
    """A verified planar transform from Isaac world coordinates into Nav2 map."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["VERIFIED", "GROUND_TRUTH_UNAVAILABLE"]
    scene_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    map_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: str = Field(min_length=1)
    world_frame_id: str | None = None
    map_frame_id: str | None = None
    translation_x_m: float | None = Field(default=None, allow_inf_nan=False)
    translation_y_m: float | None = Field(default=None, allow_inf_nan=False)
    rotation_yaw_rad: float | None = Field(default=None, allow_inf_nan=False)
    calibration_method: str | None = None
    residual_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def verified_transform_is_complete(self) -> GroundTruthCalibration:
        transform = (
            self.world_frame_id,
            self.map_frame_id,
            self.translation_x_m,
            self.translation_y_m,
            self.rotation_yaw_rad,
            self.calibration_method,
            self.residual_m,
        )
        if self.status == "VERIFIED" and any(item is None for item in transform):
            raise ValueError("verified ground truth requires a complete calibration record")
        if self.status == "VERIFIED":
            frames = (self.world_frame_id, self.map_frame_id)
            if any(not frame or not frame.lstrip("/") for frame in frames):
                raise ValueError("verified ground truth requires non-empty frame identifiers")
        return self

    def world_to_map(self, pose: Pose2D) -> Pose2D | None:
        if self.status != "VERIFIED":
            return None
        translation_x = self.translation_x_m
        translation_y = self.translation_y_m
        rotation = self.rotation_yaw_rad
        if translation_x is None or translation_y is None or rotation is None:
            return None
        cosine = math.cos(rotation)
        sine = math.sin(rotation)
        return Pose2D(
            x=translation_x + cosine * pose.x - sine * pose.y,
            y=translation_y + sine * pose.x + cosine * pose.y,
            yaw=_normalized_angle(pose.yaw + rotation),
        )


class PairingGate(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class PairingGateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: PairingGate
    failures: tuple[str, ...] = ()
    start_position_delta_m: float = Field(ge=0, allow_inf_nan=False)
    start_yaw_delta_rad: float = Field(ge=0, allow_inf_nan=False)


def evaluate_pairing_gate(
    *,
    left_runtime_fingerprint: str,
    right_runtime_fingerprint: str,
    left_epoch: str,
    right_epoch: str,
    left_start: Pose2D,
    right_start: Pose2D,
    left_covariance_xy: float,
    right_covariance_xy: float,
    left_stationary: bool,
    right_stationary: bool,
    left_active_goal: bool,
    right_active_goal: bool,
    max_start_position_delta_m: float = 0.05,
    max_start_yaw_delta_rad: float = 0.05,
    max_covariance_xy: float = 0.1,
) -> PairingGateResult:
    failures: list[str] = []
    start_position_delta = math.hypot(left_start.x - right_start.x, left_start.y - right_start.y)
    start_yaw_delta = abs(_normalized_angle(left_start.yaw - right_start.yaw))
    if left_runtime_fingerprint != right_runtime_fingerprint:
        failures.append("runtime_fingerprint")
    if left_epoch != right_epoch:
        failures.append("simulation_epoch")
    if start_position_delta > max_start_position_delta_m:
        failures.append("start_position")
    if start_yaw_delta > max_start_yaw_delta_rad:
        failures.append("start_yaw")
    if max(left_covariance_xy, right_covariance_xy) > max_covariance_xy:
        failures.append("localization_covariance")
    if not left_stationary or not right_stationary:
        failures.append("stationary")
    if left_active_goal or right_active_goal:
        failures.append("active_goal")
    return PairingGateResult(
        status=PairingGate.FAILED if failures else PairingGate.PASSED,
        failures=tuple(failures),
        start_position_delta_m=start_position_delta,
        start_yaw_delta_rad=start_yaw_delta,
    )


class PairClassification(StrEnum):
    GOAL_PAYLOAD_DIFFERENCE = "GOAL_PAYLOAD_DIFFERENCE"
    MAP_POSE_DIFFERENCE = "MAP_POSE_DIFFERENCE"
    ACTUAL_ENDPOINT_DIFFERENCE = "ACTUAL_ENDPOINT_DIFFERENCE"
    LOCALIZATION_GROUND_TRUTH_DIVERGENCE = "LOCALIZATION_GROUND_TRUTH_DIVERGENCE"
    JENAI_VERDICT_ONLY_DIFFERENCE = "JENAI_VERDICT_ONLY_DIFFERENCE"
    PAIRING_GATE_FAILED = "PAIRING_GATE_FAILED"
    RUNTIME_STACK_IDENTITY_DIFFERENCE = "RUNTIME_STACK_IDENTITY_DIFFERENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


def _pose_distance(left: Pose2D, right: Pose2D) -> float:
    return math.hypot(left.x - right.x, left.y - right.y)


def _pose_yaw_distance(left: Pose2D, right: Pose2D) -> float:
    return abs(_normalized_angle(left.yaw - right.yaw))


def _pose_difference_exceeds(
    left: Pose2D,
    right: Pose2D,
    *,
    position_threshold_m: float,
    yaw_threshold_rad: float,
) -> bool:
    return (
        _pose_distance(left, right) > position_threshold_m
        or _pose_yaw_distance(left, right) > yaw_threshold_rad
    )


def _endpoint_classifications(
    *,
    left_final_map: Pose2D | None,
    right_final_map: Pose2D | None,
    left_final_ground_truth: Pose2D | None,
    right_final_ground_truth: Pose2D | None,
    endpoint_difference_threshold_m: float,
    endpoint_difference_threshold_rad: float,
    localization_divergence_threshold_m: float,
    localization_divergence_threshold_rad: float,
) -> list[PairClassification]:
    classifications: list[PairClassification] = []
    if (
        left_final_map is not None
        and right_final_map is not None
        and _pose_difference_exceeds(
            left_final_map,
            right_final_map,
            position_threshold_m=endpoint_difference_threshold_m,
            yaw_threshold_rad=endpoint_difference_threshold_rad,
        )
    ):
        classifications.append(PairClassification.MAP_POSE_DIFFERENCE)
    if left_final_ground_truth is None or right_final_ground_truth is None:
        return classifications
    if _pose_difference_exceeds(
        left_final_ground_truth,
        right_final_ground_truth,
        position_threshold_m=endpoint_difference_threshold_m,
        yaw_threshold_rad=endpoint_difference_threshold_rad,
    ):
        classifications.append(PairClassification.ACTUAL_ENDPOINT_DIFFERENCE)
    localization_diverged = (
        left_final_map is not None
        and _pose_difference_exceeds(
            left_final_map,
            left_final_ground_truth,
            position_threshold_m=localization_divergence_threshold_m,
            yaw_threshold_rad=localization_divergence_threshold_rad,
        )
    ) or (
        right_final_map is not None
        and _pose_difference_exceeds(
            right_final_map,
            right_final_ground_truth,
            position_threshold_m=localization_divergence_threshold_m,
            yaw_threshold_rad=localization_divergence_threshold_rad,
        )
    )
    if localization_diverged:
        classifications.append(PairClassification.LOCALIZATION_GROUND_TRUTH_DIVERGENCE)
    return classifications


def _verdict_only_difference(
    *,
    classifications: list[PairClassification],
    left_final_map: Pose2D | None,
    right_final_map: Pose2D | None,
    left_execution_status: str | None,
    right_execution_status: str | None,
) -> bool:
    return (
        not classifications
        and left_final_map is not None
        and right_final_map is not None
        and left_execution_status is not None
        and right_execution_status is not None
        and left_execution_status != right_execution_status
    )


def classify_pair(
    *,
    left_goal: CanonicalGoal,
    right_goal: CanonicalGoal,
    pairing_gate: PairingGateResult,
    left_final_map: Pose2D | None,
    right_final_map: Pose2D | None,
    left_final_ground_truth: Pose2D | None,
    right_final_ground_truth: Pose2D | None,
    left_execution_status: str | None,
    right_execution_status: str | None,
    endpoint_difference_threshold_m: float = 0.05,
    endpoint_difference_threshold_rad: float = 0.15,
    localization_divergence_threshold_m: float = 0.05,
    localization_divergence_threshold_rad: float = 0.15,
) -> list[PairClassification]:
    """Classify supported differences without promoting missing evidence to fact."""

    thresholds = (
        endpoint_difference_threshold_m,
        endpoint_difference_threshold_rad,
        localization_divergence_threshold_m,
        localization_divergence_threshold_rad,
    )
    if any(value < 0 or not math.isfinite(value) for value in thresholds):
        raise ValueError("classification thresholds must be finite and non-negative")
    if pairing_gate.status is PairingGate.FAILED:
        if "runtime_fingerprint" in pairing_gate.failures:
            return [
                PairClassification.RUNTIME_STACK_IDENTITY_DIFFERENCE,
                PairClassification.PAIRING_GATE_FAILED,
            ]
        return [PairClassification.PAIRING_GATE_FAILED]
    if not compare_goals(left_goal, right_goal).equivalent:
        return [PairClassification.GOAL_PAYLOAD_DIFFERENCE]

    classifications = _endpoint_classifications(
        left_final_map=left_final_map,
        right_final_map=right_final_map,
        left_final_ground_truth=left_final_ground_truth,
        right_final_ground_truth=right_final_ground_truth,
        endpoint_difference_threshold_m=endpoint_difference_threshold_m,
        endpoint_difference_threshold_rad=endpoint_difference_threshold_rad,
        localization_divergence_threshold_m=localization_divergence_threshold_m,
        localization_divergence_threshold_rad=localization_divergence_threshold_rad,
    )
    if _verdict_only_difference(
        classifications=classifications,
        left_final_map=left_final_map,
        right_final_map=right_final_map,
        left_execution_status=left_execution_status,
        right_execution_status=right_execution_status,
    ):
        classifications.append(PairClassification.JENAI_VERDICT_ONLY_DIFFERENCE)
    evidence_complete = (
        left_final_map is not None
        and right_final_map is not None
        and left_execution_status is not None
        and right_execution_status is not None
    )
    if not classifications and not evidence_complete:
        classifications.append(PairClassification.INSUFFICIENT_EVIDENCE)
    return classifications
