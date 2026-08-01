"""Live capture and offline comparison for Isaac navigation differentials.

The runner is intentionally outside the product navigation path.  It either
dispatches the canonical goal directly through ``RosBridgeClient.nav_send``
(R1) or through the existing ``NavigationGateway`` with an in-memory
``nav_endpoint_retry_limit=0`` profile (R2).  It never mutates the stored
configuration or production navigation defaults.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

import jenai
from jenai.acceptance.nav_differential import (
    CanonicalGoal,
    GroundTruthCalibration,
    PairClassification,
    PairingGate,
    PairingGateResult,
    Pose2D,
    classify_pair,
    compare_goals,
    evaluate_pairing_gate,
)
from jenai.adapters.locations import find_location, load_locations_snapshot
from jenai.bridge import (
    BridgeError,
    BridgeRuntimeIdentity,
    MapIdentityInfo,
    PoseInfo,
    RosBridgeClient,
)
from jenai.config import default_config_path, load_config_snapshot
from jenai.config.models import AppConfig
from jenai.site_assets import bind_navigation_action
from jenai.tools.navigation_gateway import NavigationGateway

DIFFERENTIAL_EXECUTION_CONFIRMATION = "I UNDERSTAND THIS WILL MOVE THE ISAAC SIM ROBOT"
_ACTION_STATUS_TOPIC = "/navigate_to_pose/_action/status"
_ACTION_STATUS_TYPE = "action_msgs/msg/GoalStatusArray"
_CLOCK_TOPIC = "/clock"
_CLOCK_TYPE = "rosgraph_msgs/msg/Clock"
_AMCL_TOPIC = "/amcl_pose"
_AMCL_TYPE = "geometry_msgs/msg/PoseWithCovarianceStamped"
_ODOM_TYPE = "nav_msgs/msg/Odometry"
_NAV2_TERMINAL_STATUSES = frozenset({"succeeded", "canceled", "aborted", "failed", "rejected"})
_JENAI_NAVIGATION_EXECUTION_STATUSES = frozenset(
    {"succeeded", "failed", "endpoint_mismatch", "blocked", "referred", "unavailable"}
)
_ACTIVE_OUTPUT_RESERVATION: ContextVar[Any] = ContextVar(
    "jenai_nav_differential_output_reservation",
    default=None,
)


def _canonical_json_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_ros_message_type_name(value: str) -> bool:
    parts = value.split("/")
    return (
        len(parts) == 3
        and parts[1] == "msg"
        and all(part.isidentifier() for part in (parts[0], parts[2]))
    )


def _normalized_ground_truth_topic(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = "/" + value.lstrip("/")
    if (
        normalized == "/"
        or "//" in normalized
        or any(character.isspace() for character in normalized)
        or any(segment in {".", ".."} for segment in normalized.split("/"))
    ):
        raise ValueError("ground-truth topic must be a valid absolute ROS topic")
    return normalized


def _validated_ground_truth_type(value: str | None) -> str | None:
    if value is not None and not _valid_ros_message_type_name(value):
        raise ValueError("ground-truth message type must use package/msg/Type")
    return value


class DifferentialMode(StrEnum):
    R1_BRIDGE_NAV2 = "R1_bridge_nav2"
    R2_JENAI_NO_RETRY = "R2_jenai_no_retry"


class ResetPolicy(StrEnum):
    NONE = "none"
    NAV2_RESTART = "nav2_restart"
    ISAAC_REPLAY = "isaac_replay"
    FULL_CLEAN = "full_clean"


class DifferentialCaptureOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    output: Path
    location: str = Field(min_length=1)
    pair_id: str = Field(min_length=1)
    mode: DifferentialMode
    simulation_epoch: str = Field(min_length=1)
    reset_policy: ResetPolicy
    config_path: Path | None = None
    expected_source_root: Path | None = None
    expected_git_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    scene_path: Path | None = None
    live_scene_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    calibration_path: Path | None = None
    ground_truth_topic: str | None = None
    ground_truth_type: str = "geometry_msgs/msg/PoseStamped"
    execute: bool = False
    confirmation: str = ""
    preflight_sample_s: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    final_sample_s: float = Field(default=2.0, gt=0, allow_inf_nan=False)
    final_window_start_delay_s: float = Field(default=5.0, ge=0, allow_inf_nan=False)
    sample_interval_s: float = Field(default=0.2, gt=0, allow_inf_nan=False)
    max_start_speed_mps: float = Field(default=0.02, ge=0, allow_inf_nan=False)
    max_start_yaw_rate_rps: float = Field(default=0.03, ge=0, allow_inf_nan=False)
    max_topic_age_s: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    max_calibration_residual_m: float = Field(default=0.02, ge=0, allow_inf_nan=False)
    min_final_pose_samples: int = Field(default=10, ge=2)
    min_final_state_samples: int = Field(default=3, ge=2)
    min_final_ground_truth_samples: int = Field(default=3, ge=2)
    final_wall_timeout_s: float = Field(default=15.0, gt=0, allow_inf_nan=False)
    max_covariance_xy: float = Field(default=0.1, ge=0, allow_inf_nan=False)
    max_pair_start_position_delta_m: float = Field(default=0.05, ge=0, allow_inf_nan=False)
    max_pair_start_yaw_delta_rad: float = Field(default=0.05, ge=0, allow_inf_nan=False)

    @field_validator("ground_truth_topic")
    @classmethod
    def normalize_ground_truth_topic(cls, value: str | None) -> str | None:
        return _normalized_ground_truth_topic(value)

    @field_validator("ground_truth_type")
    @classmethod
    def validate_ground_truth_type(cls, value: str) -> str:
        return cast(str, _validated_ground_truth_type(value))

    @model_validator(mode="after")
    def execution_requires_confirmation(self) -> DifferentialCaptureOptions:
        if self.execute and self.confirmation != DIFFERENTIAL_EXECUTION_CONFIRMATION:
            raise ValueError(
                "Live differential capture requires the exact confirmation text: "
                f"{DIFFERENTIAL_EXECUTION_CONFIRMATION}"
            )
        if self.execute and (
            self.scene_path is None
            or not self.scene_path.is_absolute()
            or not self.scene_path.is_file()
        ):
            raise ValueError(
                "Live differential capture requires an existing absolute USD scene path."
            )
        if self.execute and (
            self.expected_source_root is None
            or not self.expected_source_root.is_absolute()
            or not self.expected_source_root.is_dir()
        ):
            raise ValueError(
                "Live differential capture requires the absolute reviewed source root."
            )
        if self.execute and self.expected_git_sha is None:
            raise ValueError("Live differential capture requires the reviewed commit SHA.")
        if self.execute and self.live_scene_sha256 is None:
            raise ValueError(
                "Live differential capture requires the active Isaac Stage root-layer SHA-256."
            )
        if self.output.exists():
            raise ValueError(f"Output already exists: {self.output}")
        if self.live_scene_sha256 is not None and self.scene_path is None:
            raise ValueError("live_scene_sha256 requires scene_path")
        if self.final_wall_timeout_s <= self.final_sample_s:
            raise ValueError("final_wall_timeout_s must exceed the required ROS-time window")
        if bool(self.ground_truth_topic) != bool(self.calibration_path):
            raise ValueError("ground_truth_topic and calibration_path must be configured together")
        return self


class DifferentialMeasurementContract(BaseModel):
    """Immutable evidence thresholds that must match across a paired experiment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    preflight_sample_s: float = Field(gt=0, allow_inf_nan=False)
    final_sample_s: float = Field(gt=0, allow_inf_nan=False)
    final_window_start_delay_s: float = Field(ge=0, allow_inf_nan=False)
    sample_interval_s: float = Field(gt=0, allow_inf_nan=False)
    max_topic_age_s: float = Field(gt=0, allow_inf_nan=False)
    max_calibration_residual_m: float = Field(ge=0, allow_inf_nan=False)
    min_final_pose_samples: int = Field(ge=2)
    min_final_state_samples: int = Field(ge=2)
    min_final_ground_truth_samples: int = Field(ge=2)
    final_wall_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    max_start_speed_mps: float = Field(ge=0, allow_inf_nan=False)
    max_start_yaw_rate_rps: float = Field(ge=0, allow_inf_nan=False)
    max_covariance_xy: float = Field(ge=0, allow_inf_nan=False)
    max_pair_start_position_delta_m: float = Field(default=0.05, ge=0, allow_inf_nan=False)
    max_pair_start_yaw_delta_rad: float = Field(default=0.05, ge=0, allow_inf_nan=False)
    ground_truth_topic: str | None = None
    ground_truth_type: str | None = None
    ground_truth_calibration_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator("ground_truth_topic")
    @classmethod
    def normalize_ground_truth_topic(cls, value: str | None) -> str | None:
        return _normalized_ground_truth_topic(value)

    @field_validator("ground_truth_type")
    @classmethod
    def validate_ground_truth_type(cls, value: str | None) -> str | None:
        return _validated_ground_truth_type(value)

    @model_validator(mode="after")
    def ground_truth_binding_is_all_or_none(self) -> DifferentialMeasurementContract:
        values = (
            self.ground_truth_topic,
            self.ground_truth_type,
            self.ground_truth_calibration_sha256,
        )
        if any(value is not None for value in values) and any(value is None for value in values):
            raise ValueError(
                "ground-truth topic, type, and calibration digest must be bound together"
            )
        return self


ArtifactOverall = Literal[
    "initializing",
    "preflight_only",
    "blocked",
    "captured",
    "insufficient_evidence",
    "failed",
    "cleanup_failed",
]


class TargetBinding(BaseModel):
    """Immutable saved-location identity bound to the dispatched goal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_query: str = Field(min_length=1)
    resolved_name: str = Field(min_length=1)
    resolved_id: str = Field(min_length=1)
    frame_id: str = Field(min_length=1)
    pose: Pose2D
    capability_id: Literal["navigate"]
    locations_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_goal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("requested_query", "resolved_name", "resolved_id")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("target binding text must not be blank")
        return normalized

    @field_validator("frame_id")
    @classmethod
    def normalize_frame(cls, value: str) -> str:
        normalized = value.strip().lstrip("/")
        if not normalized:
            raise ValueError("target binding frame must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_digests(self) -> TargetBinding:
        record = {
            "capability_id": self.capability_id,
            "frame_id": self.frame_id,
            "locations_sha256": self.locations_sha256,
            "pose": self.pose.model_dump(mode="json"),
            "resolved_id": self.resolved_id,
            "resolved_name": self.resolved_name,
        }
        if self.canonical_record_sha256 != _canonical_json_sha256(record):
            raise ValueError("target binding record digest does not match its fields")
        stable_binding = {
            "canonical_goal_sha256": self.canonical_goal_sha256,
            "canonical_record_sha256": self.canonical_record_sha256,
            "capability_id": self.capability_id,
            "locations_sha256": self.locations_sha256,
        }
        if self.binding_sha256 != _canonical_json_sha256(stable_binding):
            raise ValueError("target binding digest does not match its stable fields")
        return self


class DifferentialArtifact(BaseModel):
    """Reloadable envelope for every success, block, and failure artifact."""

    model_config = ConfigDict(extra="allow")

    schema_version: Literal[1] = 1
    evidence_derivation_version: Literal[3] | None = None
    run_id: str = Field(min_length=1)
    pair_id: str = Field(min_length=1)
    mode: DifferentialMode
    reset_policy: ResetPolicy
    execution_requested: bool
    measurement_contract: DifferentialMeasurementContract
    started_at: str = Field(min_length=1)
    finished_at: str | None = None
    runtime_identity: dict[str, Any] = Field(default_factory=dict)
    canonical_goal: CanonicalGoal | None = None
    target_binding: TargetBinding | None = None
    ground_truth_calibration: GroundTruthCalibration | None = None
    pose_observations: list[PoseLookupObservation] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)
    overall: ArtifactOverall = "initializing"
    failure: dict[str, Any] | None = None


class DifferentialComparisonReport(BaseModel):
    """Typed offline comparison; malformed captures never enter statistics."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    pair_id: str | None = None
    included: bool
    classifications: list[PairClassification]
    pairing_gate: dict[str, Any] | None = None
    detail: str | None = None


class PoseLookupPurpose(StrEnum):
    T0_START = "t0_start"
    T1_PRE_DISPATCH = "t1_pre_dispatch"
    R2_COMPLETION_VERDICT = "r2_completion_verdict"
    FINAL_WINDOW = "final_window"


class PoseLookupResult(BaseModel):
    """Exact typed result returned by one fresh tf2 bridge lookup."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    yaw: float = Field(allow_inf_nan=False)
    frame_id: str = Field(min_length=1)
    base_frame: str = Field(min_length=1)
    source: str = Field(min_length=1)
    initial_stamp_ns: int = Field(gt=0)
    stamp_ns: int = Field(gt=0)
    fresh_after_request: Literal[True]

    @model_validator(mode="after")
    def require_newer_transform(self) -> PoseLookupResult:
        if self.stamp_ns <= self.initial_stamp_ns:
            raise ValueError("fresh pose stamp must be newer than the initial transform")
        return self


class PoseLookupObservation(BaseModel):
    """Append-only evidence for one acceptance-owned fresh pose request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    purpose: PoseLookupPurpose
    attempt_tag: str | None = None
    request_host_monotonic_ns: int = Field(ge=0)
    completed_host_monotonic_ns: int = Field(ge=0)
    request_clock_ns: int | None = Field(default=None, ge=0)
    completed_clock_ns: int | None = Field(default=None, ge=0)
    fresh_requested: Literal[True]
    frame_id: str = Field(min_length=1)
    base_frame: str = Field(min_length=1)
    timeout_s: float = Field(gt=0, allow_inf_nan=False)
    status: Literal["SUCCESS", "ERROR"]
    result: PoseLookupResult | None = None
    raw_result: dict[str, Any] | None = None
    error_type: str | None = None
    error_detail: str | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> PoseLookupObservation:
        if self.completed_host_monotonic_ns < self.request_host_monotonic_ns:
            raise ValueError("pose lookup completion must follow its request")
        if self.purpose is PoseLookupPurpose.R2_COMPLETION_VERDICT:
            if not isinstance(self.attempt_tag, str) or not self.attempt_tag.strip():
                raise ValueError("R2 completion pose evidence must name its navigation attempt")
        elif self.attempt_tag is not None:
            raise ValueError("only R2 completion pose evidence may name a navigation attempt")
        if self.status == "SUCCESS":
            if (
                self.result is None
                or self.raw_result is not None
                or self.error_type is not None
                or self.error_detail is not None
            ):
                raise ValueError("successful pose lookup must contain only a typed result")
            if self.result.frame_id.lstrip("/") != self.frame_id.lstrip(
                "/"
            ) or self.result.base_frame.lstrip("/") != self.base_frame.lstrip("/"):
                raise ValueError("pose lookup result frames must match the exact request")
        elif self.result is not None or not self.error_type or not self.error_detail:
            raise ValueError("failed pose lookup must contain typed error evidence")
        return self


DifferentialArtifact.model_rebuild()


class _PoseObservationRecorder:
    def __init__(self) -> None:
        self._observations: list[dict[str, Any]] = []

    def snapshot(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._observations)

    def record_external(
        self,
        *,
        purpose: PoseLookupPurpose,
        attempt_tag: str,
        requested_ns: int,
        completed_ns: int,
        request_clock_ns: int | None = None,
        completed_clock_ns: int | None = None,
        frame_id: str,
        base_frame: str,
        timeout_s: float,
        pose: PoseInfo | None = None,
        error: Exception | None = None,
    ) -> dict[str, Any]:
        """Append an exact pose call already made by an acceptance proxy."""

        sequence = len(self._observations)
        common = {
            "observation_id": f"pose-{sequence:04d}-{uuid4().hex}",
            "sequence": sequence,
            "purpose": purpose,
            "attempt_tag": attempt_tag,
            "request_host_monotonic_ns": requested_ns,
            "completed_host_monotonic_ns": completed_ns,
            "request_clock_ns": request_clock_ns,
            "completed_clock_ns": completed_clock_ns,
            "fresh_requested": True,
            "frame_id": frame_id,
            "base_frame": base_frame,
            "timeout_s": timeout_s,
        }
        if error is not None:
            observation = PoseLookupObservation.model_validate(
                {
                    **common,
                    "status": "ERROR",
                    "error_type": type(error).__name__,
                    "error_detail": str(error),
                }
            )
        else:
            if pose is None:
                raise ValueError(
                    "successful external pose observation requires typed pose evidence"
                )
            raw_result = {
                "x": pose.x,
                "y": pose.y,
                "yaw": pose.yaw,
                "frame_id": pose.frame_id,
                "base_frame": pose.base_frame,
                "source": pose.source,
                "initial_stamp_ns": pose.initial_stamp_ns,
                "stamp_ns": pose.stamp_ns,
                "fresh_after_request": pose.fresh_after_request,
            }
            try:
                result = PoseLookupResult.model_validate(raw_result)
            except ValueError as exc:
                observation = PoseLookupObservation.model_validate(
                    {
                        **common,
                        "status": "ERROR",
                        "raw_result": raw_result,
                        "error_type": "InvalidPoseLookupEvidence",
                        "error_detail": str(exc),
                    }
                )
            else:
                observation = PoseLookupObservation.model_validate(
                    {
                        **common,
                        "status": "SUCCESS",
                        "result": result,
                    }
                )
        payload = observation.model_dump(mode="json")
        self._observations.append(payload)
        return copy.deepcopy(payload)

    async def capture(
        self,
        bridge: RosBridgeClient,
        clock: _TopicRecorder,
        *,
        purpose: PoseLookupPurpose,
        frame_id: str,
        base_frame: str,
        timeout_s: float,
    ) -> tuple[Pose2D | None, str, dict[str, Any]]:
        sequence = len(self._observations)
        observation_id = f"pose-{sequence:04d}-{uuid4().hex}"
        requested_ns = time.monotonic_ns()
        request_clock_ns = _clock_at_host(clock, requested_ns)
        try:
            pose = await bridge.get_pose(
                timeout=timeout_s,
                fresh=True,
                frame_id=frame_id,
                base_frame=base_frame,
            )
        except BridgeError as exc:
            completed_ns = time.monotonic_ns()
            observation = PoseLookupObservation(
                observation_id=observation_id,
                sequence=sequence,
                purpose=purpose,
                request_host_monotonic_ns=requested_ns,
                completed_host_monotonic_ns=completed_ns,
                request_clock_ns=request_clock_ns,
                completed_clock_ns=_clock_at_host(clock, completed_ns),
                fresh_requested=True,
                frame_id=frame_id,
                base_frame=base_frame,
                timeout_s=timeout_s,
                status="ERROR",
                error_type=type(exc).__name__,
                error_detail=str(exc),
            )
            payload = observation.model_dump(mode="json")
            self._observations.append(payload)
            return None, observation_id, copy.deepcopy(payload)

        completed_ns = time.monotonic_ns()
        raw_result = {
            "x": pose.x,
            "y": pose.y,
            "yaw": pose.yaw,
            "frame_id": pose.frame_id,
            "base_frame": getattr(pose, "base_frame", None),
            "source": pose.source,
            "initial_stamp_ns": getattr(pose, "initial_stamp_ns", None),
            "stamp_ns": getattr(pose, "stamp_ns", None),
            "fresh_after_request": getattr(pose, "fresh_after_request", None),
        }
        try:
            result = PoseLookupResult.model_validate(raw_result)
        except ValueError as exc:
            observation = PoseLookupObservation(
                observation_id=observation_id,
                sequence=sequence,
                purpose=purpose,
                request_host_monotonic_ns=requested_ns,
                completed_host_monotonic_ns=completed_ns,
                request_clock_ns=request_clock_ns,
                completed_clock_ns=_clock_at_host(clock, completed_ns),
                fresh_requested=True,
                frame_id=frame_id,
                base_frame=base_frame,
                timeout_s=timeout_s,
                status="ERROR",
                raw_result=raw_result,
                error_type="InvalidPoseLookupEvidence",
                error_detail=str(exc),
            )
            payload = observation.model_dump(mode="json")
            self._observations.append(payload)
            return None, observation_id, copy.deepcopy(payload)

        observation = PoseLookupObservation(
            observation_id=observation_id,
            sequence=sequence,
            purpose=purpose,
            request_host_monotonic_ns=requested_ns,
            completed_host_monotonic_ns=completed_ns,
            request_clock_ns=request_clock_ns,
            completed_clock_ns=_clock_at_host(clock, completed_ns),
            fresh_requested=True,
            frame_id=frame_id,
            base_frame=base_frame,
            timeout_s=timeout_s,
            status="SUCCESS",
            result=result,
        )
        payload = observation.model_dump(mode="json")
        self._observations.append(payload)
        return (
            Pose2D(x=result.x, y=result.y, yaw=result.yaw),
            observation_id,
            copy.deepcopy(payload),
        )


class _TopicRecorder:
    def __init__(self) -> None:
        self.samples: list[dict[str, Any]] = []

    def record(self, message: dict[str, Any]) -> None:
        self.samples.append(
            {
                "host_monotonic_ns": time.monotonic_ns(),
                "message": copy.deepcopy(message),
            }
        )


def _snapshot_topic_samples(
    recorders: dict[str, _TopicRecorder],
) -> dict[str, list[dict[str, Any]]]:
    return {
        key: copy.deepcopy(recorder.samples)
        for key, recorder in recorders.items()
        if recorder.samples
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        if not path.is_file():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _calibration_payload_sha256(calibration: GroundTruthCalibration) -> str:
    payload = json.dumps(
        calibration.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _calibration_file_payload_sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    try:
        calibration = GroundTruthCalibration.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return _calibration_payload_sha256(calibration)


def _command_output(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str | None:
    environment = os.environ.copy()
    if env:
        environment.update(env)
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
            cwd=cwd,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _text_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _effective_ros_domain(config: AppConfig) -> str:
    if config.deployment_mode == "simulation":
        return os.environ.get("ROS_DOMAIN_ID", "0")
    if config.vehicle.domain_id is not None:
        return str(config.vehicle.domain_id)
    return os.environ.get("ROS_DOMAIN_ID", "0")


def _nav2_state_dir() -> Path:
    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    return Path(os.environ.get("JENAI_NAV2_STATE_DIR", runtime_dir / f"jenai-nav2-{os.getuid()}"))


def _nav_params_path(session: str) -> Path | None:
    state_dir = _nav2_state_dir()
    state_file = state_dir / f"{session}-override-path"
    try:
        if state_file.is_file() and state_file.stat().st_size <= 4096:
            lines = state_file.read_text(encoding="utf-8").splitlines()
            if len(lines) == 1:
                recorded = Path(lines[0].strip()).expanduser()
                if recorded.is_absolute():
                    return recorded.resolve()
    except (OSError, UnicodeError):
        pass
    return None


def _normalized_ros_topic(value: str | None) -> str | None:
    if value is None:
        return None
    topic = "/" + value.strip().strip("\"'").lstrip("/")
    if (
        topic == "/"
        or "//" in topic
        or any(character.isspace() for character in topic)
        or any(segment in {".", ".."} for segment in topic.split("/"))
    ):
        return None
    return topic


def _controller_odom_topic(*, ros_env: dict[str, str]) -> str | None:
    value = _command_output(
        [
            "ros2",
            "param",
            "get",
            "/controller_server",
            "odom_topic",
            "--no-daemon",
            "--spin-time",
            "3.0",
            "--hide-type",
        ],
        env=ros_env,
    )
    return _normalized_ros_topic(value)


def _process_identity(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
) -> dict[str, Any] | None:
    if pid <= 0:
        return None
    try:
        process_root = proc_root / str(pid)
        value = (process_root / "stat").read_text(encoding="utf-8")
        cmdline = (process_root / "cmdline").read_bytes()
    except OSError:
        return None
    if not cmdline:
        return None
    closing = value.rfind(")")
    if closing < 0:
        return None
    fields = value[closing + 1 :].split()
    if len(fields) <= 19:
        return None
    try:
        ppid = int(fields[1])
        start_ticks = int(fields[19])
    except ValueError:
        return None
    if ppid < 0 or start_ticks <= 0:
        return None
    return {
        "pid": pid,
        "ppid": ppid,
        "start_ticks": start_ticks,
        "cmdline_sha256": hashlib.sha256(cmdline).hexdigest(),
    }


def _child_pids(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
) -> set[int] | None:
    try:
        task_roots = sorted(
            (entry for entry in (proc_root / str(pid) / "task").iterdir() if entry.is_dir()),
            key=lambda entry: int(entry.name),
        )
    except (OSError, ValueError):
        return None
    if not task_roots:
        return None
    children: set[int] = set()
    for task_root in task_roots:
        try:
            raw = (task_root / "children").read_text(encoding="utf-8").strip()
            child_pids = [int(value) for value in raw.split()] if raw else []
        except (OSError, ValueError):
            return None
        if any(child <= 0 for child in child_pids):
            return None
        children.update(child_pids)
    return children


def _process_tree_snapshot_once(
    root_pid: int,
    *,
    proc_root: Path = Path("/proc"),
    max_processes: int = 256,
) -> list[dict[str, Any]] | None:
    pending = [root_pid]
    seen: set[int] = set()
    identities: list[dict[str, Any]] = []
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        if len(seen) >= max_processes:
            return None
        identity = _process_identity(pid, proc_root=proc_root)
        children = _child_pids(pid, proc_root=proc_root)
        if identity is None or children is None:
            return None
        seen.add(pid)
        identities.append(identity)
        pending.extend(sorted(children - seen, reverse=True))
    return sorted(identities, key=lambda item: int(item["pid"]))


def _stable_process_tree_snapshot(
    root_pid: int,
    *,
    proc_root: Path = Path("/proc"),
) -> list[dict[str, Any]] | None:
    first = _process_tree_snapshot_once(root_pid, proc_root=proc_root)
    second = _process_tree_snapshot_once(root_pid, proc_root=proc_root)
    if first is None or second is None or first != second:
        return None
    return first


def _tmux_navigation_pane(session: str) -> tuple[str, int, str, int] | None:
    value = _command_output(
        [
            "tmux",
            "display-message",
            "-p",
            "-t",
            f"{session}:navigation",
            "#{session_id}\t#{session_created}\t#{pane_id}\t#{pane_pid}",
        ]
    )
    if value is None:
        return None
    fields = value.split("\t")
    if len(fields) != 4:
        return None
    session_id, raw_created, pane_id, raw_pid = fields
    try:
        created = int(raw_created)
        pid = int(raw_pid)
    except ValueError:
        return None
    if not session_id or not pane_id or created < 0 or pid <= 0:
        return None
    return session_id, created, pane_id, pid


def _nav2_process_generation(
    session: str,
    *,
    proc_root: Path = Path("/proc"),
) -> dict[str, Any] | None:
    first = _tmux_navigation_pane(session)
    if first is None:
        return None
    session_id, session_created, pane_id, pane_pid = first
    try:
        boot_id = (proc_root / "sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    processes = _stable_process_tree_snapshot(pane_pid, proc_root=proc_root)
    second = _tmux_navigation_pane(session)
    pane_identity = (
        next((process for process in processes if process.get("pid") == pane_pid), None)
        if processes is not None
        else None
    )
    if (
        second != first
        or processes is None
        or len(processes) < 2
        or pane_identity is None
        or not boot_id
    ):
        return None
    start_ticks = int(pane_identity["start_ticks"])
    return {
        "boot_id": boot_id,
        "session": session,
        "session_id": session_id,
        "session_created": session_created,
        "pane_id": pane_id,
        "pane_pid": pane_pid,
        "pane_start_ticks": start_ticks,
        "processes": processes,
    }


def _navigate_to_pose_server_providers(output: str | None) -> list[dict[str, str]] | None:
    """Parse ``ros2 action info -t`` server identities, failing closed."""

    if not isinstance(output, str):
        return None
    lines = output.splitlines()
    try:
        heading_index = next(
            index for index, line in enumerate(lines) if line.startswith("Action servers:")
        )
        declared_count = int(lines[heading_index].split(":", 1)[1].strip())
    except (StopIteration, ValueError, IndexError):
        return None
    providers: list[dict[str, str]] = []
    for raw_line in lines[heading_index + 1 :]:
        line = raw_line.strip()
        if not line:
            continue
        if " [" not in line or not line.endswith("]"):
            return None
        node, raw_type = line.rsplit(" [", 1)
        action_type = raw_type[:-1]
        if not node.startswith("/") or not node.strip() or not action_type:
            return None
        providers.append({"node": node, "action_type": action_type})
    if declared_count != len(providers):
        return None
    return providers


def _pairable_ros_middleware_identity(value: object) -> object:
    """Project a sidecar descriptor onto fields stable across captures.

    The full PID-bound descriptor remains in each artifact for validation and
    within-run respawn pinning.  PID and its descriptor digest are deliberately
    excluded from cross-run compatibility because every CLI capture owns a new
    sidecar process.
    """

    try:
        descriptor = BridgeRuntimeIdentity.from_payload(value)
    except BridgeError:
        return value
    return {
        "schema_version": descriptor.schema_version,
        "boot_id": descriptor.boot_id,
        "python_executable": descriptor.python_executable,
        "python_version": descriptor.python_version,
        "rmw_implementation_requested": descriptor.rmw_implementation_requested,
        "rmw_implementation_effective": descriptor.rmw_implementation_effective,
        "ros_domain_id": descriptor.ros_domain_id,
        "dds_config_mode": descriptor.dds_config_mode,
        "dds_bindings": [
            {"name": name, "kind": kind, "sha256": digest}
            for name, kind, digest in descriptor.dds_bindings
        ],
        "dds_config_sha256": descriptor.dds_config_sha256,
        "ros_environment_bindings": [
            {"name": name, "kind": kind, "sha256": digest}
            for name, kind, digest in descriptor.ros_environment_bindings
        ],
        "ros_environment_sha256": descriptor.ros_environment_sha256,
    }


def _runtime_fingerprint(identity: dict[str, Any]) -> str:
    fields = {
        key: identity.get(key)
        for key in (
            "git_sha",
            "git_dirty",
            "source_root",
            "reviewed_git_sha",
            "expected_source_root",
            "expected_git_sha",
            "expected_git_dirty",
            "jenai_import_path",
            "python_executable",
            "python_version",
            "bridge_script_sha256",
            "config_sha256",
            "site_id",
            "site_version",
            "site_map_sha256",
            "site_locations_sha256",
            "locations_sha256",
            "nav_params_sha256",
            "bridge_domain_id",
            "rmw_implementation",
            "dds_profile_sha256",
            "ros_middleware",
            "scene_path",
            "scene_sha256",
            "live_scene_sha256",
            "live_map_sha256",
            "site_map_frame",
            "robot_base_frame",
            "live_map_frame",
            "live_map_identity_initial",
            "controller_lifecycle",
            "planner_lifecycle",
            "bt_navigator_lifecycle",
            "runtime_parameter_sha256",
            "node_name_counts",
            "navigate_to_pose_action_count",
            "navigate_to_pose_server_providers",
            "controller_odom_topic",
            "nav2_process_generation",
            "ground_truth_calibration_effective_sha256",
        )
    }
    fields["ros_middleware"] = _pairable_ros_middleware_identity(identity.get("ros_middleware"))
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode("utf-8")).hexdigest()


def _apply_runtime_fingerprint(identity: dict[str, Any]) -> None:
    identity["fingerprint"] = _runtime_fingerprint(identity)


def _record_ground_truth_calibration(
    artifact: dict[str, Any],
    identity: dict[str, Any],
    calibration: GroundTruthCalibration,
) -> None:
    """Bind the effective capture-time calibration to the runtime identity."""

    artifact["ground_truth_calibration"] = calibration.model_dump(mode="json")
    identity["ground_truth_calibration_effective_sha256"] = _calibration_payload_sha256(calibration)
    _apply_runtime_fingerprint(identity)


def _source_revision_identity(
    source_root: Path,
    reviewed_root: Path | None,
    *,
    reviewed_git_sha: str | None,
) -> dict[str, Any]:
    revision = _command_output(["git", "rev-parse", "HEAD"], cwd=source_root)
    dirty_output = _command_output(["git", "status", "--porcelain"], cwd=source_root)
    reviewed_revision = (
        _command_output(["git", "rev-parse", "HEAD"], cwd=reviewed_root)
        if reviewed_root is not None
        else None
    )
    reviewed_dirty_output = (
        _command_output(["git", "status", "--porcelain"], cwd=reviewed_root)
        if reviewed_root is not None
        else None
    )
    return {
        "git_sha": revision,
        "git_dirty": None if dirty_output is None else bool(dirty_output),
        "reviewed_git_sha": reviewed_git_sha,
        "expected_git_sha": reviewed_revision,
        "expected_git_dirty": (
            None if reviewed_dirty_output is None else bool(reviewed_dirty_output)
        ),
    }


def _nav2_runtime_identity(session: str, *, ros_env: dict[str, str]) -> dict[str, Any]:
    ros_nodes = _command_output(
        [
            "ros2",
            "node",
            "list",
            "--no-daemon",
            "--spin-time",
            "3.0",
        ],
        env=ros_env,
    )
    node_lines = [line.strip() for line in (ros_nodes or "").splitlines() if line.strip()]
    required_nodes = ("/amcl", "/controller_server", "/planner_server", "/bt_navigator")
    node_counts = {name: node_lines.count(name) for name in required_nodes}
    action_list = _command_output(["ros2", "action", "list", "-t"], env=ros_env)
    action_lines = [line.strip() for line in (action_list or "").splitlines() if line.strip()]
    action_info = _command_output(
        ["ros2", "action", "info", "/navigate_to_pose", "-t"], env=ros_env
    )
    parameter_snapshots = {
        node: _command_output(["ros2", "param", "dump", node], env=ros_env)
        for node in required_nodes
    }
    return {
        "ros_nodes": ros_nodes,
        "node_name_counts": node_counts,
        "navigate_to_pose_actions": action_lines,
        "navigate_to_pose_action_count": sum(
            line.split(maxsplit=1)[0] == "/navigate_to_pose" for line in action_lines
        ),
        "navigate_to_pose_server_providers": _navigate_to_pose_server_providers(action_info),
        "controller_odom_topic": _controller_odom_topic(ros_env=ros_env),
        "nav2_tmux_session": session,
        "nav2_process_generation": _nav2_process_generation(session),
        "controller_lifecycle": _command_output(
            ["ros2", "lifecycle", "get", "/controller_server"], env=ros_env
        ),
        "planner_lifecycle": _command_output(
            ["ros2", "lifecycle", "get", "/planner_server"], env=ros_env
        ),
        "bt_navigator_lifecycle": _command_output(
            ["ros2", "lifecycle", "get", "/bt_navigator"], env=ros_env
        ),
        "runtime_parameter_sha256": {
            node: _text_sha256(snapshot) for node, snapshot in parameter_snapshots.items()
        },
        "process_inventory": _safe_matching_process_inventory(),
    }


def _safe_matching_process_inventory() -> list[dict[str, Any]] | None:
    raw_pids = _command_output(
        ["pgrep", "-f", "nav2|amcl|controller_server|planner_server|bt_navigator|ros_bridge"]
    )
    if raw_pids is None:
        return None
    identities: list[dict[str, Any]] = []
    for value in raw_pids.splitlines():
        try:
            pid = int(value.strip())
        except ValueError:
            continue
        identity = _process_identity(pid)
        if identity is not None:
            identities.append(identity)
    return sorted(identities, key=lambda item: int(item["pid"]))


_RUNTIME_STACK_CONTINUITY_FIELDS = (
    "node_name_counts",
    "navigate_to_pose_action_count",
    "navigate_to_pose_server_providers",
    "controller_odom_topic",
    "nav2_tmux_session",
    "nav2_process_generation",
    "controller_lifecycle",
    "planner_lifecycle",
    "bt_navigator_lifecycle",
    "runtime_parameter_sha256",
)


def _runtime_stack_projection(value: dict[str, Any]) -> dict[str, Any]:
    return {field: copy.deepcopy(value.get(field)) for field in _RUNTIME_STACK_CONTINUITY_FIELDS}


def _capture_runtime_stack_checkpoint(
    identity: dict[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    expected = _runtime_stack_projection(identity)
    session = identity.get("nav2_tmux_session")
    domain = identity.get("bridge_domain_id")
    if not isinstance(session, str) or not session or not _valid_domain_id(domain):
        return {
            "label": label,
            "status": "FAIL",
            "observed_host_monotonic_ns": time.monotonic_ns(),
            "expected": expected,
            "observed": None,
            "failures": ["runtime_stack_identity_unavailable"],
        }
    observed = _runtime_stack_projection(
        _nav2_runtime_identity(session, ros_env={"ROS_DOMAIN_ID": str(domain)})
    )
    drift = [
        f"runtime_stack_{field}_changed"
        for field in _RUNTIME_STACK_CONTINUITY_FIELDS
        if observed.get(field) != expected.get(field)
    ]
    return {
        "label": label,
        "status": "FAIL" if drift else "PASS",
        "observed_host_monotonic_ns": time.monotonic_ns(),
        "expected": expected,
        "observed": observed,
        "failures": drift,
    }


def _runtime_identity(
    config: AppConfig,
    config_path: Path,
    *,
    reviewed_git_sha: str | None,
    expected_source_root: Path | None,
    scene_path: Path | None,
    live_scene_sha256: str | None,
    simulation_epoch: str,
) -> dict[str, Any]:
    locations_path = config.resolved_locations_path(config_path)
    session = os.environ.get("JENAI_NAV2_TMUX_SESSION", "nav2")
    nav_params_path = _nav_params_path(session)

    source_root = Path(jenai.__file__).resolve().parents[2]
    bridge_script_path = source_root / "src" / "jenai" / "bridge" / "ros_bridge.py"
    reviewed_root = expected_source_root.resolve() if expected_source_root is not None else None
    bridge_domain_id = _effective_ros_domain(config)
    ros_env = {"ROS_DOMAIN_ID": bridge_domain_id}
    identity: dict[str, Any] = {
        "source_root": str(source_root),
        "expected_source_root": str(reviewed_root) if reviewed_root is not None else None,
        "jenai_import_path": str(Path(jenai.__file__).resolve()),
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "bridge_script_path": str(bridge_script_path),
        "bridge_script_sha256": _sha256(bridge_script_path),
        "deployment_mode": config.deployment_mode,
        "config_path": str(config_path.resolve()),
        "config_sha256": _sha256(config_path),
        "site_id": config.site.site_id,
        "site_version": config.site.version,
        "site_map_sha256": config.site.map_sha256,
        "site_locations_sha256": config.site.locations_sha256,
        "locations_path": str(locations_path.resolve()) if locations_path else None,
        "locations_sha256": _sha256(locations_path),
        "nav_params_path": str(nav_params_path) if nav_params_path is not None else None,
        "nav_params_sha256": _sha256(nav_params_path),
        "ambient_ros_domain_id": os.environ.get("ROS_DOMAIN_ID", "0"),
        "bridge_domain_id": bridge_domain_id,
        "rmw_implementation": os.environ.get("RMW_IMPLEMENTATION"),
        "ros_middleware": None,
        "dds_profile_sha256": _sha256(
            Path(os.environ["FASTRTPS_DEFAULT_PROFILES_FILE"])
            if os.environ.get("FASTRTPS_DEFAULT_PROFILES_FILE")
            else None
        ),
        "scene_path": str(scene_path.resolve()) if scene_path else config.site.reference_scene,
        "scene_sha256": _sha256(scene_path),
        "live_scene_sha256": live_scene_sha256,
        "live_map_sha256": None,
        "live_map_frame": None,
        "simulation_epoch": simulation_epoch,
    }
    identity.update(
        _source_revision_identity(
            source_root,
            reviewed_root,
            reviewed_git_sha=reviewed_git_sha,
        )
    )
    identity.update(_nav2_runtime_identity(session, ros_env=ros_env))
    _apply_runtime_fingerprint(identity)
    return identity


def _stamp_ns(stamp: object) -> int | None:
    if not isinstance(stamp, dict):
        return None
    sec = stamp.get("sec")
    nanosec = stamp.get("nanosec")
    if (
        type(sec) is not int
        or type(nanosec) is not int
        or sec < 0
        or nanosec < 0
        or nanosec >= 1_000_000_000
    ):
        return None
    return sec * 1_000_000_000 + nanosec


def _clock_ns(message: dict[str, Any]) -> int | None:
    return _stamp_ns(message.get("clock"))


def _header_stamp_ns(message: dict[str, Any]) -> int | None:
    header = message.get("header")
    return _stamp_ns(header.get("stamp")) if isinstance(header, dict) else None


def _clock_at_host(clock: _TopicRecorder, host_monotonic_ns: int) -> int | None:
    observed: int | None = None
    for sample in clock.samples:
        sample_host = sample.get("host_monotonic_ns")
        message = sample.get("message")
        if type(sample_host) is not int or sample_host > host_monotonic_ns:
            continue
        if isinstance(message, dict) and (value := _clock_ns(message)) is not None:
            observed = value
    return observed


def _topic_sample_evidence(
    sample: dict[str, Any],
    clock: _TopicRecorder,
    *,
    max_age_s: float,
    max_future_s: float = 0.0,
    current_host_monotonic_ns: int | None = None,
) -> dict[str, Any]:
    host_ns = sample.get("host_monotonic_ns")
    message = sample.get("message")
    if type(host_ns) is not int or not isinstance(message, dict):
        return {"fresh": False, "failure": "malformed_sample"}
    evaluated_host_ns = current_host_monotonic_ns or host_ns
    source_stamp_ns = _header_stamp_ns(message)
    sample_clock_ns = _clock_at_host(clock, host_ns)
    evaluation_clock_ns = _clock_at_host(clock, evaluated_host_ns)
    source_age_ns = (
        evaluation_clock_ns - source_stamp_ns
        if evaluation_clock_ns is not None and source_stamp_ns is not None
        else None
    )
    host_age_ns = evaluated_host_ns - host_ns
    max_age_ns = int(max_age_s * 1_000_000_000)
    max_future_ns = int(max_future_s * 1_000_000_000)
    fresh = (
        source_age_ns is not None
        and -max_future_ns <= source_age_ns <= max_age_ns
        and 0 <= host_age_ns <= max_age_ns
    )
    return {
        "host_monotonic_ns": host_ns,
        "host_age_ns": host_age_ns,
        "source_stamp_ns": source_stamp_ns,
        "sample_clock_ns": sample_clock_ns,
        "capture_clock_ns": evaluation_clock_ns,
        "source_age_ns": source_age_ns,
        "fresh": fresh,
        "message": message,
    }


def _latest_topic_evidence(
    recorder: _TopicRecorder,
    clock: _TopicRecorder,
    *,
    max_age_s: float,
    max_future_s: float = 0.0,
    cutoff_host_monotonic_ns: int,
    current_host_monotonic_ns: int,
) -> dict[str, Any] | None:
    candidates = [
        sample
        for sample in recorder.samples
        if type(sample.get("host_monotonic_ns")) is int
        and cutoff_host_monotonic_ns
        <= int(sample["host_monotonic_ns"])
        <= current_host_monotonic_ns
    ]
    if not candidates:
        return None
    return _topic_sample_evidence(
        candidates[-1],
        clock,
        max_age_s=max_age_s,
        max_future_s=max_future_s,
        current_host_monotonic_ns=current_host_monotonic_ns,
    )


def _window_topic_evidence(
    recorder: _TopicRecorder,
    clock: _TopicRecorder,
    *,
    start_host_ns: int,
    end_host_ns: int,
    max_age_s: float,
    max_future_s: float = 0.0,
) -> list[dict[str, Any]]:
    return [
        _topic_sample_evidence(
            sample,
            clock,
            max_age_s=max_age_s,
            max_future_s=max_future_s,
        )
        for sample in recorder.samples
        if type(sample.get("host_monotonic_ns")) is int
        and start_host_ns <= int(sample["host_monotonic_ns"]) <= end_host_ns
    ]


def _nested_dict(payload: dict[str, Any], *path: str) -> dict[str, Any] | None:
    current: object = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, dict) else None


def _quaternion_yaw(orientation: dict[str, Any]) -> float | None:
    try:
        x = float(orientation["x"])
        y = float(orientation["y"])
        z = float(orientation["z"])
        w = float(orientation["w"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (x, y, z, w)):
        return None
    scale = max(abs(value) for value in (x, y, z, w))
    if scale == 0.0:
        return None
    scaled = (x / scale, y / scale, z / scale, w / scale)
    norm = math.hypot(*scaled)
    x, y, z, w = (value / norm for value in scaled)
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _pose_from_message(message: dict[str, Any], *, odometry: bool = False) -> Pose2D | None:
    path = ("pose", "pose") if odometry else ("pose",)
    pose = _nested_dict(message, *path)
    if not odometry and pose is not None and "position" not in pose:
        pose = _nested_dict(message, "pose", "pose")
    if pose is None:
        return None
    position = pose.get("position")
    orientation = pose.get("orientation")
    if not isinstance(position, dict) or not isinstance(orientation, dict):
        return None
    yaw = _quaternion_yaw(orientation)
    try:
        x = float(position["x"])
        y = float(position["y"])
    except (KeyError, TypeError, ValueError):
        return None
    if yaw is None or not math.isfinite(x) or not math.isfinite(y):
        return None
    return Pose2D(x=x, y=y, yaw=yaw)


def _covariance_xy(message: dict[str, Any]) -> float | None:
    pose_with_covariance = _nested_dict(message, "pose")
    if pose_with_covariance is None:
        return None
    covariance = pose_with_covariance.get("covariance")
    if not isinstance(covariance, list) or len(covariance) < 8:
        return None
    try:
        values = (float(covariance[0]), float(covariance[7]))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) and value >= 0 for value in values):
        return None
    return max(values)


def _velocity(message: dict[str, Any]) -> tuple[float, float] | None:
    twist = _nested_dict(message, "twist", "twist")
    if twist is None:
        return None
    linear = twist.get("linear")
    angular = twist.get("angular")
    if not isinstance(linear, dict) or not isinstance(angular, dict):
        return None
    try:
        x = float(linear["x"])
        y = float(linear.get("y", 0.0))
        yaw_rate = float(angular["z"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (x, y, yaw_rate)):
        return None
    return math.hypot(x, y), yaw_rate


def _goal_status_records(message: dict[str, Any]) -> list[dict[str, Any]]:
    statuses = message.get("status_list")
    if not isinstance(statuses, list):
        return []
    records: list[dict[str, Any]] = []
    for entry in statuses:
        if not isinstance(entry, dict):
            continue
        goal_info = entry.get("goal_info")
        goal_id = goal_info.get("goal_id") if isinstance(goal_info, dict) else None
        raw_uuid = goal_id.get("uuid") if isinstance(goal_id, dict) else None
        raw_status = entry.get("status")
        if (
            not isinstance(raw_uuid, list)
            or len(raw_uuid) != 16
            or any(type(item) is not int or item < 0 or item > 255 for item in raw_uuid)
            or type(raw_status) is not int
            or not 0 <= raw_status <= 6
        ):
            continue
        records.append(
            {
                "goal_uuid": bytes(raw_uuid).hex(),
                "status": raw_status,
                "goal_stamp_ns": (
                    _stamp_ns(goal_info.get("stamp")) if isinstance(goal_info, dict) else None
                ),
            }
        )
    return records


def _goal_ids(message: dict[str, Any], *, active_only: bool) -> set[str]:
    return {
        str(record["goal_uuid"])
        for record in _goal_status_records(message)
        if not active_only or record.get("status") in {1, 2, 3}
    }


def _latest_message(recorder: _TopicRecorder) -> dict[str, Any] | None:
    if not recorder.samples:
        return None
    message = recorder.samples[-1].get("message")
    return message if isinstance(message, dict) else None


def _latest_action_status_evidence(
    recorder: _TopicRecorder,
    *,
    max_age_s: float,
    cutoff_host_monotonic_ns: int | None = None,
    current_host_monotonic_ns: int | None = None,
) -> dict[str, Any] | None:
    current_ns = (
        current_host_monotonic_ns if current_host_monotonic_ns is not None else time.monotonic_ns()
    )
    cutoff_ns = (
        cutoff_host_monotonic_ns
        if cutoff_host_monotonic_ns is not None
        else current_ns - int(max_age_s * 1_000_000_000)
    )
    candidates = [
        sample
        for sample in recorder.samples
        if type(sample.get("host_monotonic_ns")) is int
        and cutoff_ns <= int(sample["host_monotonic_ns"]) <= current_ns
    ]
    if not candidates:
        return None
    sample = candidates[-1]
    host_ns = sample.get("host_monotonic_ns")
    message = sample.get("message")
    if type(host_ns) is not int or not isinstance(message, dict):
        return None
    statuses = message.get("status_list")
    age_ns = current_ns - host_ns
    schema_valid = isinstance(statuses, list) and len(_goal_status_records(message)) == len(
        statuses
    )
    fresh = schema_valid and 0 <= age_ns <= int(max_age_s * 1_000_000_000)
    return {
        "host_monotonic_ns": host_ns,
        "host_age_ns": age_ns,
        "schema_valid": schema_valid,
        "fresh": fresh,
        "message": message,
    }


def _start_state_failures(
    *,
    pose: Pose2D | None,
    clock_advancing: bool,
    clock_backwards: bool,
    amcl_fresh: bool,
    amcl_pose: Pose2D | None,
    covariance: float | None,
    odom_fresh: bool,
    odom_valid: bool,
    action_status_fresh: bool,
    stationary: bool,
    active_goals: set[str],
    max_covariance_xy: float,
) -> list[str]:
    checks = (
        (pose is None, "map_pose_unavailable"),
        (not clock_advancing or clock_backwards, "clock_not_advancing"),
        (not amcl_fresh, "amcl_stale_or_headerless"),
        (amcl_pose is None, "amcl_pose_invalid"),
        (covariance is None or covariance > max_covariance_xy, "amcl_covariance"),
        (not odom_fresh, "odom_stale_or_headerless"),
        (not odom_valid, "odom_invalid"),
        (not action_status_fresh, "action_status_stale_or_malformed"),
        (not stationary, "robot_not_stationary"),
        (bool(active_goals), "active_nav2_goal"),
    )
    return [failure for failed, failure in checks if failed]


def _source_metadata(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    if evidence is None:
        return None
    return {key: value for key, value in evidence.items() if key != "message"}


def _ground_truth_state_evidence(
    ground_truth: _TopicRecorder | None,
    clock: _TopicRecorder,
    calibration: GroundTruthCalibration | None,
    *,
    max_age_s: float,
    max_future_s: float,
    cutoff_host_monotonic_ns: int,
    current_host_monotonic_ns: int,
) -> tuple[dict[str, Any], bool]:
    required = calibration is not None and calibration.status == "VERIFIED"
    unavailable = {
        "ground_truth_required": required,
        "ground_truth_source": None,
        "ground_truth_source_frame_id": None,
        "ground_truth_world_pose": None,
        "ground_truth_map_pose": None,
    }
    if not required or ground_truth is None or calibration is None:
        return unavailable, not required
    evidence = _latest_topic_evidence(
        ground_truth,
        clock,
        max_age_s=max_age_s,
        max_future_s=max_future_s,
        cutoff_host_monotonic_ns=cutoff_host_monotonic_ns,
        current_host_monotonic_ns=current_host_monotonic_ns,
    )
    message = evidence.get("message") if isinstance(evidence, dict) else None
    header = message.get("header") if isinstance(message, dict) else None
    source_frame = header.get("frame_id") if isinstance(header, dict) else None
    world_pose = _pose_from_message(message) if isinstance(message, dict) else None
    map_pose = calibration.world_to_map(world_pose) if world_pose is not None else None
    valid = (
        evidence is not None
        and evidence.get("fresh") is True
        and isinstance(source_frame, str)
        and source_frame.lstrip("/") == str(calibration.world_frame_id).lstrip("/")
        and world_pose is not None
        and map_pose is not None
    )
    if not valid or world_pose is None or map_pose is None:
        return unavailable, False
    return (
        {
            "ground_truth_required": True,
            "ground_truth_source": _source_metadata(evidence),
            "ground_truth_source_frame_id": source_frame,
            "ground_truth_world_pose": world_pose.model_dump(mode="json"),
            "ground_truth_map_pose": map_pose.model_dump(mode="json"),
        },
        True,
    )


def _state_clock_evidence(
    clock: _TopicRecorder,
    *,
    window_start_host_ns: int,
    evaluated_host_ns: int,
) -> tuple[list[dict[str, int]], list[int]]:
    evidence = [
        {
            "host_monotonic_ns": int(sample["host_monotonic_ns"]),
            "clock_ns": value,
        }
        for sample in clock.samples
        if type(sample.get("host_monotonic_ns")) is int
        and window_start_host_ns <= int(sample["host_monotonic_ns"]) <= evaluated_host_ns
        and isinstance(sample.get("message"), dict)
        and (value := _clock_ns(sample["message"])) is not None
    ]
    return evidence, [item["clock_ns"] for item in evidence]


def _fresh_message(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    if evidence is None or evidence.get("fresh") is not True:
        return None
    return cast(dict[str, Any], evidence["message"])


def _action_status_window_evidence(
    action_status: _TopicRecorder,
    *,
    max_age_s: float,
    cutoff_host_ns: int,
    evaluated_host_ns: int,
    observation_ready: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool]:
    evidence = _latest_action_status_evidence(
        action_status,
        max_age_s=max_age_s,
        cutoff_host_monotonic_ns=cutoff_host_ns,
        current_host_monotonic_ns=evaluated_host_ns,
    )
    samples_observed_before_evaluation = [
        sample
        for sample in action_status.samples
        if type(sample.get("host_monotonic_ns")) is int
        and int(sample["host_monotonic_ns"]) <= evaluated_host_ns
    ]
    no_status_observed = observation_ready and not samples_observed_before_evaluation
    source = (
        {
            "fresh": True,
            "observation": "no_status_observed",
            "cutoff_host_monotonic_ns": cutoff_host_ns,
            "evaluated_host_monotonic_ns": evaluated_host_ns,
        }
        if no_status_observed
        else _source_metadata(evidence)
    )
    return evidence, source, no_status_observed


def _initial_state(
    *,
    pose: Pose2D | None,
    map_pose_observation_id: str | None = None,
    clock: _TopicRecorder,
    amcl: _TopicRecorder,
    odom: _TopicRecorder,
    action_status: _TopicRecorder,
    options: DifferentialCaptureOptions,
    ground_truth: _TopicRecorder | None = None,
    calibration: GroundTruthCalibration | None = None,
    cutoff_host_monotonic_ns: int | None = None,
    current_host_monotonic_ns: int | None = None,
    action_status_observation_ready: bool = False,
    nomotion_update_acknowledged: bool = False,
) -> dict[str, Any]:
    evaluated_host_ns = current_host_monotonic_ns or time.monotonic_ns()
    max_age_ns = int(options.max_topic_age_s * 1_000_000_000)
    cutoff_host_ns = cutoff_host_monotonic_ns or evaluated_host_ns - max_age_ns
    recent_window_start_ns = max(cutoff_host_ns, evaluated_host_ns - max_age_ns)
    clock_evidence, clock_values = _state_clock_evidence(
        clock,
        window_start_host_ns=recent_window_start_ns,
        evaluated_host_ns=evaluated_host_ns,
    )
    amcl_evidence = _latest_topic_evidence(
        amcl,
        clock,
        max_age_s=options.max_topic_age_s,
        max_future_s=options.sample_interval_s,
        cutoff_host_monotonic_ns=cutoff_host_ns,
        current_host_monotonic_ns=evaluated_host_ns,
    )
    odom_evidence = _latest_topic_evidence(
        odom,
        clock,
        max_age_s=options.max_topic_age_s,
        max_future_s=options.sample_interval_s,
        cutoff_host_monotonic_ns=cutoff_host_ns,
        current_host_monotonic_ns=evaluated_host_ns,
    )
    action_evidence, action_source, no_status_observed = _action_status_window_evidence(
        action_status,
        max_age_s=options.max_topic_age_s,
        cutoff_host_ns=cutoff_host_ns,
        evaluated_host_ns=evaluated_host_ns,
        observation_ready=action_status_observation_ready,
    )
    amcl_message = _fresh_message(amcl_evidence)
    odom_message = _fresh_message(odom_evidence)
    action_message = _fresh_message(action_evidence)
    covariance = _covariance_xy(amcl_message) if amcl_message else None
    velocity = _velocity(odom_message) if odom_message else None
    active_goals = _goal_ids(action_message or {}, active_only=True)
    known_goals = _goal_ids(action_message or {}, active_only=False)
    amcl_pose = _pose_from_message(amcl_message) if amcl_message else None
    odom_pose = _pose_from_message(odom_message, odometry=True) if odom_message else None
    clock_advancing = len(clock_values) >= 2 and clock_values[-1] > clock_values[0]
    clock_pairs = zip(clock_values, clock_values[1:], strict=False)
    clock_backwards = any(right < left for left, right in clock_pairs)
    stationary = (
        velocity is not None
        and velocity[0] <= options.max_start_speed_mps
        and abs(velocity[1]) <= options.max_start_yaw_rate_rps
    )
    ground_truth_fields, ground_truth_valid = _ground_truth_state_evidence(
        ground_truth,
        clock,
        calibration,
        max_age_s=options.max_topic_age_s,
        max_future_s=options.sample_interval_s,
        cutoff_host_monotonic_ns=cutoff_host_ns,
        current_host_monotonic_ns=evaluated_host_ns,
    )
    failures = _start_state_failures(
        pose=pose,
        clock_advancing=clock_advancing,
        clock_backwards=clock_backwards,
        amcl_fresh=amcl_evidence is not None and amcl_evidence.get("fresh") is True,
        amcl_pose=amcl_pose,
        covariance=covariance,
        odom_fresh=odom_evidence is not None and odom_evidence.get("fresh") is True,
        odom_valid=odom_pose is not None and velocity is not None,
        action_status_fresh=(action_evidence is not None and action_evidence.get("fresh") is True)
        or no_status_observed,
        stationary=stationary,
        active_goals=active_goals,
        max_covariance_xy=options.max_covariance_xy,
    )
    if ground_truth_fields["ground_truth_required"] is True and not ground_truth_valid:
        failures.append("ground_truth_start_evidence")
    if not nomotion_update_acknowledged:
        failures.append("amcl_nomotion_update_unacknowledged")
    failures = list(dict.fromkeys(failures))
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "simulation_epoch": options.simulation_epoch,
        "cutoff_host_monotonic_ns": cutoff_host_ns,
        "evaluated_host_monotonic_ns": evaluated_host_ns,
        "map_pose_observation_id": map_pose_observation_id,
        "map_to_base": pose.model_dump(mode="json") if pose else None,
        "amcl_pose": amcl_pose.model_dump(mode="json") if amcl_pose else None,
        "amcl_covariance_xy": covariance,
        "amcl_source": _source_metadata(amcl_evidence),
        "odom_pose": odom_pose.model_dump(mode="json") if odom_pose else None,
        "odom_source": _source_metadata(odom_evidence),
        "action_status_source": action_source,
        "amcl_nomotion_update_acknowledged": nomotion_update_acknowledged,
        "linear_velocity_mps": velocity[0] if velocity else None,
        "angular_velocity_rps": velocity[1] if velocity else None,
        "stationary": stationary,
        "active_goal_ids": sorted(active_goals),
        "known_goal_ids": sorted(known_goals),
        "clock_evidence": clock_evidence,
        "clock_samples_ns": clock_values,
        "clock_advancing": clock_advancing,
        "clock_backwards": clock_backwards,
        **ground_truth_fields,
    }


def _topic_stream_specs(
    options: DifferentialCaptureOptions,
    *,
    odom_topic: str,
) -> list[tuple[str, str, str, str]]:
    specs = [
        ("clock", _CLOCK_TOPIC, _CLOCK_TYPE, "sensor_data"),
        ("amcl", _AMCL_TOPIC, _AMCL_TYPE, "transient_local"),
        ("odom", odom_topic, _ODOM_TYPE, "sensor_data"),
        ("action_status", _ACTION_STATUS_TOPIC, _ACTION_STATUS_TYPE, "transient_local"),
    ]
    if options.ground_truth_topic:
        specs.append(
            ("ground_truth", options.ground_truth_topic, options.ground_truth_type, "sensor_data")
        )
    return specs


async def _watch_topics(
    bridge: RosBridgeClient,
    recorders: dict[str, _TopicRecorder],
    options: DifferentialCaptureOptions,
    *,
    odom_topic: str,
) -> list[int]:
    watch_ids: list[int] = []
    for key, topic, message_type, qos_profile in _topic_stream_specs(
        options, odom_topic=odom_topic
    ):
        watch_ids.append(
            await bridge.watch(
                topic,
                message_type,
                recorders[key].record,
                throttle=options.sample_interval_s,
                qos_profile=qos_profile,
            )
        )
    return watch_ids


async def _heartbeat(bridge: RosBridgeClient) -> None:
    while True:
        await asyncio.sleep(2.0)
        await bridge.ping()


async def _await_tagged_result(
    bridge: RosBridgeClient,
    *,
    tag: str,
    timeout_s: float,
    timeout_start: asyncio.Event,
) -> tuple[dict[str, Any], int]:
    future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()

    def record(event: dict[str, Any]) -> None:
        if event.get("tag") == tag and not future.done():
            future.set_result(dict(event))

    bridge.on_event("nav_result", record)
    try:
        await timeout_start.wait()
        result = await asyncio.wait_for(future, timeout_s)
        return result, time.monotonic_ns()
    finally:
        bridge.off_event("nav_result", record)


async def _run_r1(
    bridge: RosBridgeClient,
    goal: CanonicalGoal,
    *,
    tag: str,
    timeout_s: float,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    timeout_start = asyncio.Event()
    result_task = asyncio.create_task(
        _await_tagged_result(
            bridge,
            tag=tag,
            timeout_s=timeout_s,
            timeout_start=timeout_start,
        )
    )
    await asyncio.sleep(0)
    try:
        await bridge.nav_send(
            goal.x,
            goal.y,
            goal.yaw,
            frame_id=goal.frame_id,
            tag=tag,
        )
        timeout_start.set()
        terminal, terminal_ns = await result_task
    except BaseException:
        result_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await result_task
        raise
    terminal["observed_host_monotonic_ns"] = terminal_ns
    return terminal, None


def _r2_execution_config(config: AppConfig) -> tuple[AppConfig, dict[str, Any]]:
    no_retry_vehicle = config.vehicle.model_copy(update={"nav_endpoint_retry_limit": 0})
    execution_config = config.model_copy(update={"vehicle": no_retry_vehicle})
    effective_domain = _effective_ros_domain(execution_config)
    twin_was_enabled = execution_config.twin.enabled
    twin_disabled_for_same_domain = (
        execution_config.deployment_mode == "simulation"
        and twin_was_enabled
        and str(execution_config.twin.domain_id) == effective_domain
    )
    if twin_disabled_for_same_domain:
        execution_config = execution_config.model_copy(
            update={"twin": execution_config.twin.model_copy(update={"enabled": False})}
        )
    return execution_config, {
        "nav_endpoint_retry_limit": 0,
        "twin_enabled_in_base_config": twin_was_enabled,
        "twin_disabled_for_same_domain": twin_disabled_for_same_domain,
        "effective_twin_enabled": execution_config.twin.enabled,
        "effective_bridge_domain_id": effective_domain,
    }


async def _run_r2(
    bridge: RosBridgeClient,
    config: AppConfig,
    config_path: Path,
    outgoing_action: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    execution_config, effective_override = _r2_execution_config(config)
    gateway = NavigationGateway(
        execution_config,
        config_path=config_path,
        get_bridge=lambda: _return_bridge(bridge),
    )
    terminal_events: list[dict[str, Any]] = []

    def record_terminal(event: dict[str, Any]) -> None:
        observed = dict(event)
        observed["observed_host_monotonic_ns"] = time.monotonic_ns()
        terminal_events.append(observed)

    bridge.on_event("nav_result", record_terminal)
    try:
        result = await gateway.execute(outgoing_action)
    finally:
        bridge.off_event("nav_result", record_terminal)
        await gateway.close()
    attempts = [item.model_dump(mode="json") for item in result.navigation_attempts]
    attempt_tags = {str(item["tag"]) for item in attempts}
    terminal = next(
        (event for event in reversed(terminal_events) if str(event.get("tag")) in attempt_tags),
        None,
    )
    return terminal, {
        "execution_status": result.execution_status,
        "route_preview": result.route_preview,
        "outgoing_action": result.outgoing_action,
        "navigation_attempts": attempts,
        "observed_nav_results": terminal_events,
        "effective_experimental_config": effective_override,
    }


async def _return_bridge(bridge: RosBridgeClient) -> RosBridgeClient:
    return bridge


class _ObservedNavBridge:
    """Harness-only proxy that records and gates the exact nav_send boundary."""

    def __init__(
        self,
        delegate: RosBridgeClient,
        *,
        simulation_epoch: str,
        expected_goal: CanonicalGoal,
        on_nav_send: Callable[[CanonicalGoal, str, int], Awaitable[dict[str, Any]]],
        pose_observations: _PoseObservationRecorder | None = None,
        clock: _TopicRecorder | None = None,
    ) -> None:
        self._delegate = delegate
        self._simulation_epoch = simulation_epoch
        self._expected_goal = expected_goal
        self._on_nav_send = on_nav_send
        self._pose_observations = pose_observations
        self._clock = clock
        self._active_attempt_tag: str | None = None
        self.verdict_pose_observation_ids: list[str] = []
        self.observations: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    async def nav_send(
        self,
        x: float,
        y: float,
        yaw: float = 0.0,
        frame_id: str = "map",
        tag: str = "",
    ) -> None:
        invoked_ns = time.monotonic_ns()
        goal = CanonicalGoal.from_yaw(
            frame_id=frame_id,
            x=x,
            y=y,
            yaw=yaw,
            clock_domain="ros",
            simulation_epoch=self._simulation_epoch,
        )
        goal_comparison = compare_goals(self._expected_goal, goal)
        observation: dict[str, Any] = {
            "tag": tag,
            "nav_send_invoked_host_monotonic_ns": invoked_ns,
            "nav_send_forwarded_host_monotonic_ns": None,
            "forward_completed_host_monotonic_ns": None,
            "actual_goal": goal.model_dump(mode="json"),
            "goal_comparison": goal_comparison.model_dump(mode="json"),
            "state_before_forward": None,
        }
        self.observations.append(observation)
        if not goal_comparison.equivalent:
            observation["blocked_reason"] = "actual_goal_differs_from_canonical_goal"
            raise BridgeError(
                "Differential actual nav_send goal differs from the canonical goal; "
                "motion was not forwarded."
            )

        t1_state = await self._on_nav_send(goal, tag, invoked_ns)
        observation["state_before_forward"] = t1_state
        if t1_state.get("status") != "PASS":
            raise BridgeError(
                "Differential dispatch state gate failed before nav_send: "
                + ", ".join(str(item) for item in t1_state.get("failures", []))
            )
        observation["nav_send_forwarded_host_monotonic_ns"] = time.monotonic_ns()
        self._active_attempt_tag = tag
        try:
            await self._delegate.nav_send(x, y, yaw, frame_id=frame_id, tag=tag)
        except BaseException:
            self._active_attempt_tag = None
            raise
        observation["forward_completed_host_monotonic_ns"] = time.monotonic_ns()

    async def get_pose(
        self,
        timeout: float = 3.0,
        *,
        fresh: bool = False,
        frame_id: str = "map",
        base_frame: str = "base_link",
    ) -> PoseInfo:
        if (
            not fresh
            or self._pose_observations is None
            or self._clock is None
            or self._active_attempt_tag is None
        ):
            return await self._delegate.get_pose(
                timeout=timeout,
                fresh=fresh,
                frame_id=frame_id,
                base_frame=base_frame,
            )
        requested_ns = time.monotonic_ns()
        request_clock_ns = _clock_at_host(self._clock, requested_ns)
        try:
            pose = await self._delegate.get_pose(
                timeout=timeout,
                fresh=True,
                frame_id=frame_id,
                base_frame=base_frame,
            )
        except BridgeError as exc:
            completed_ns = time.monotonic_ns()
            recorded = self._pose_observations.record_external(
                purpose=PoseLookupPurpose.R2_COMPLETION_VERDICT,
                attempt_tag=self._active_attempt_tag,
                requested_ns=requested_ns,
                completed_ns=completed_ns,
                request_clock_ns=request_clock_ns,
                completed_clock_ns=_clock_at_host(self._clock, completed_ns),
                frame_id=frame_id,
                base_frame=base_frame,
                timeout_s=timeout,
                error=exc,
            )
            self.verdict_pose_observation_ids.append(str(recorded["observation_id"]))
            raise
        completed_ns = time.monotonic_ns()
        recorded = self._pose_observations.record_external(
            purpose=PoseLookupPurpose.R2_COMPLETION_VERDICT,
            attempt_tag=self._active_attempt_tag,
            requested_ns=requested_ns,
            completed_ns=completed_ns,
            request_clock_ns=request_clock_ns,
            completed_clock_ns=_clock_at_host(self._clock, completed_ns),
            frame_id=frame_id,
            base_frame=base_frame,
            timeout_s=timeout,
            pose=pose,
        )
        self.verdict_pose_observation_ids.append(str(recorded["observation_id"]))
        return pose


async def _dispatch_mode(
    options: DifferentialCaptureOptions,
    bridge: RosBridgeClient,
    config: AppConfig,
    config_path: Path,
    goal: CanonicalGoal,
    bound_action: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if options.mode is DifferentialMode.R1_BRIDGE_NAV2:
        return await _run_r1(
            bridge,
            goal,
            tag=f"navdiff-{uuid4().hex[:8]}",
            timeout_s=config.vehicle.nav_timeout_s,
        )
    return await _run_r2(bridge, config, config_path, bound_action)


def _new_goal_ids(
    recorder: _TopicRecorder,
    clock: _TopicRecorder,
    *,
    before: set[str],
    dispatched_at_ns: int,
    max_age_s: float,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    seen = set(before)
    for sample in recorder.samples:
        host_ns = sample.get("host_monotonic_ns")
        message = sample.get("message")
        if type(host_ns) is not int or host_ns < dispatched_at_ns or not isinstance(message, dict):
            continue
        capture_clock_ns = _clock_at_host(clock, host_ns)
        for record in _goal_status_records(message):
            goal_id = str(record["goal_uuid"])
            if goal_id in seen or record.get("status") == 0:
                continue
            goal_stamp_ns = record.get("goal_stamp_ns")
            age_ns = (
                capture_clock_ns - int(goal_stamp_ns)
                if capture_clock_ns is not None and type(goal_stamp_ns) is int
                else None
            )
            observations.append(
                {
                    **record,
                    "observed_host_monotonic_ns": host_ns,
                    "capture_clock_ns": capture_clock_ns,
                    "goal_stamp_age_ns": age_ns,
                    "goal_stamp_fresh": (
                        age_ns is not None and 0 <= age_ns <= int(max_age_s * 1_000_000_000)
                    ),
                }
            )
            seen.add(goal_id)
    return observations


def _pose_attempt_from_observation(observation: dict[str, Any]) -> dict[str, Any]:
    observation_id = observation.get("observation_id")
    requested_ns = observation.get("request_host_monotonic_ns")
    completed_ns = observation.get("completed_host_monotonic_ns")
    completed_clock_ns = observation.get("completed_clock_ns")
    result = observation.get("result")
    if observation.get("status") == "SUCCESS" and isinstance(result, dict):
        return {
            "pose_observation_id": observation_id,
            "requested_host_monotonic_ns": requested_ns,
            "observed_host_monotonic_ns": completed_ns,
            "capture_clock_ns": completed_clock_ns,
            "fresh": completed_clock_ns is not None,
            "pose": {
                "x": result.get("x"),
                "y": result.get("y"),
                "yaw": result.get("yaw"),
                "frame_id": result.get("frame_id"),
                "source": result.get("source"),
            },
        }
    return {
        "pose_observation_id": observation_id,
        "requested_host_monotonic_ns": requested_ns,
        "capture_clock_ns": completed_clock_ns,
        "fresh": False,
        "error": str(observation.get("error_detail") or "Fresh pose lookup failed."),
    }


async def _capture_map_pose_sample(
    bridge: RosBridgeClient,
    config: AppConfig,
    options: DifferentialCaptureOptions,
    clock: _TopicRecorder,
    pose_observations: _PoseObservationRecorder,
) -> dict[str, Any]:
    _, _, observation = await pose_observations.capture(
        bridge,
        clock,
        purpose=PoseLookupPurpose.FINAL_WINDOW,
        frame_id=config.site.map_frame,
        base_frame=config.vehicle.robot_base_frame,
        timeout_s=max(0.5, options.sample_interval_s * 2.0),
    )
    return _pose_attempt_from_observation(observation)


async def _collect_final_map_window(
    bridge: RosBridgeClient,
    config: AppConfig,
    options: DifferentialCaptureOptions,
    clock: _TopicRecorder,
    pose_observations: _PoseObservationRecorder | None = None,
) -> tuple[int, int, int | None, int | None, bool, list[dict[str, Any]], list[str]]:
    recorder = pose_observations or _PoseObservationRecorder()
    start_host_ns = time.monotonic_ns()
    start_clock_ns = _clock_at_host(clock, start_host_ns)
    required_duration_ns = int(options.final_sample_s * 1_000_000_000)
    failures: list[str] = []
    samples: list[dict[str, Any]] = []
    clock_backwards = False
    previous_clock_ns = start_clock_ns
    if start_clock_ns is None:
        failures.append("final_clock_unavailable")
    else:
        wall_deadline = time.monotonic() + options.final_wall_timeout_s
        while time.monotonic() < wall_deadline:
            requested_ns = time.monotonic_ns()
            observed_clock_ns = _clock_at_host(clock, requested_ns)
            if (
                previous_clock_ns is not None
                and observed_clock_ns is not None
                and observed_clock_ns < previous_clock_ns
            ):
                clock_backwards = True
                failures.append("final_clock_moved_backwards")
                break
            if observed_clock_ns is not None:
                previous_clock_ns = observed_clock_ns
            samples.append(
                await _capture_map_pose_sample(
                    bridge,
                    config,
                    options,
                    clock,
                    recorder,
                )
            )
            if (
                observed_clock_ns is not None
                and observed_clock_ns - start_clock_ns >= required_duration_ns
            ):
                break
            await asyncio.sleep(options.sample_interval_s)
        else:
            failures.append("final_clock_window_wall_timeout")
    end_host_ns = time.monotonic_ns()
    return (
        start_host_ns,
        end_host_ns,
        start_clock_ns,
        _clock_at_host(clock, end_host_ns),
        clock_backwards,
        samples,
        failures,
    )


def _clock_window_evidence(
    clock: _TopicRecorder,
    *,
    start_host_ns: int,
    end_host_ns: int,
) -> tuple[list[dict[str, int | None]], list[int]]:
    samples = [
        {
            "host_monotonic_ns": int(sample["host_monotonic_ns"]),
            "clock_ns": _clock_ns(cast(dict[str, Any], sample["message"])),
        }
        for sample in clock.samples
        if type(sample.get("host_monotonic_ns")) is int
        and start_host_ns <= int(sample["host_monotonic_ns"]) <= end_host_ns
        and isinstance(sample.get("message"), dict)
    ]
    values: list[int] = []
    for sample in samples:
        value = sample.get("clock_ns")
        if type(value) is int:
            values.append(value)
    return samples, values


def _annotate_final_localization_samples(
    amcl_samples: list[dict[str, Any]],
    odom_samples: list[dict[str, Any]],
) -> None:
    for evidence in amcl_samples:
        message = evidence.get("message")
        if isinstance(message, dict):
            parsed_pose = _pose_from_message(message)
            evidence["pose"] = parsed_pose.model_dump(mode="json") if parsed_pose else None
            evidence["covariance_xy"] = _covariance_xy(message)
    for evidence in odom_samples:
        message = evidence.get("message")
        if isinstance(message, dict):
            parsed_pose = _pose_from_message(message, odometry=True)
            evidence["pose"] = parsed_pose.model_dump(mode="json") if parsed_pose else None
            velocity = _velocity(message)
            evidence["linear_velocity_mps"] = velocity[0] if velocity else None
            evidence["angular_velocity_rps"] = velocity[1] if velocity else None


def _valid_final_amcl(
    samples: list[dict[str, Any]],
    *,
    max_covariance_xy: float,
) -> list[dict[str, Any]]:
    return [
        sample
        for sample in samples
        if sample.get("fresh") is True
        and _valid_pose_payload(sample.get("pose"))
        and _finite_number(sample.get("covariance_xy"))
        and math.isfinite(float(sample["covariance_xy"]))
        and 0 <= float(sample["covariance_xy"]) <= max_covariance_xy
    ]


def _valid_final_odom(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        sample
        for sample in samples
        if sample.get("fresh") is True
        and _valid_pose_payload(sample.get("pose"))
        and _finite_number(sample.get("linear_velocity_mps"))
        and _finite_number(sample.get("angular_velocity_rps"))
    ]


def _bounded_stream_samples(
    samples: list[dict[str, Any]],
    *,
    start_clock_ns: int | None,
    end_clock_ns: int | None,
) -> list[dict[str, Any]]:
    if start_clock_ns is None or end_clock_ns is None:
        return []
    return [
        sample
        for sample in samples
        if type(sample.get("source_stamp_ns")) is int
        and start_clock_ns <= int(sample["source_stamp_ns"]) <= end_clock_ns
    ]


def _stream_window_failures(
    name: str,
    samples: list[dict[str, Any]],
    *,
    min_samples: int,
    start_clock_ns: int | None,
    end_clock_ns: int | None,
    required_duration_ns: int,
    coverage_slack_ns: int,
) -> list[str]:
    if start_clock_ns is None or end_clock_ns is None:
        return [f"final_{name}_coverage_unavailable"]
    stamps = [
        int(sample["source_stamp_ns"])
        for sample in samples
        if type(sample.get("source_stamp_ns")) is int
    ]
    distinct_stamps = set(stamps)
    target_end_ns = start_clock_ns + required_duration_ns
    failures: list[str] = []
    if any(stamp < start_clock_ns or stamp > end_clock_ns for stamp in stamps):
        failures.append(f"final_{name}_sample_outside_window")
    if len(distinct_stamps) < min_samples:
        failures.append(f"insufficient_fresh_final_{name}_samples")
    if any(right < left for left, right in zip(stamps, stamps[1:], strict=False)):
        failures.append(f"final_{name}_source_stamp_non_monotonic")
    if not stamps or min(stamps) > start_clock_ns + coverage_slack_ns:
        failures.append(f"final_{name}_begin_coverage_missing")
    if not stamps or max(stamps) < target_end_ns - coverage_slack_ns:
        failures.append(f"final_{name}_tail_coverage_missing")
    return failures


def _final_window_failures(
    *,
    failures: list[str],
    terminal_bound: bool,
    start_clock_ns: int | None,
    end_clock_ns: int | None,
    required_duration_ns: int,
    coverage_slack_ns: int,
    clock_values: list[int],
    valid_map_count: int,
    min_map_count: int,
    fresh_amcl: list[dict[str, Any]],
    fresh_odom: list[dict[str, Any]],
    min_state_samples: int,
    verified_ground_truth: list[dict[str, Any]],
    ground_truth_required: bool,
    min_ground_truth_samples: int,
    stationary: bool,
) -> list[str]:
    checks = (
        (not terminal_bound, "final_window_not_bound_to_terminal"),
        (
            start_clock_ns is None
            or end_clock_ns is None
            or end_clock_ns - start_clock_ns < required_duration_ns,
            "final_clock_did_not_advance_required_window",
        ),
        (len(clock_values) < 2, "insufficient_final_clock_samples"),
        (
            any(right < left for left, right in zip(clock_values, clock_values[1:], strict=False)),
            "final_clock_moved_backwards",
        ),
        (valid_map_count < min_map_count, "insufficient_fresh_map_pose_samples"),
        (not stationary, "robot_not_stationary_in_final_window"),
    )
    combined = [*failures, *(failure for failed, failure in checks if failed)]
    combined.extend(
        _stream_window_failures(
            "amcl",
            fresh_amcl,
            min_samples=min_state_samples,
            start_clock_ns=start_clock_ns,
            end_clock_ns=end_clock_ns,
            required_duration_ns=required_duration_ns,
            coverage_slack_ns=coverage_slack_ns,
        )
    )
    combined.extend(
        _stream_window_failures(
            "odom",
            fresh_odom,
            min_samples=min_state_samples,
            start_clock_ns=start_clock_ns,
            end_clock_ns=end_clock_ns,
            required_duration_ns=required_duration_ns,
            coverage_slack_ns=coverage_slack_ns,
        )
    )
    if ground_truth_required:
        combined.extend(
            _stream_window_failures(
                "ground_truth",
                verified_ground_truth,
                min_samples=min_ground_truth_samples,
                start_clock_ns=start_clock_ns,
                end_clock_ns=end_clock_ns,
                required_duration_ns=required_duration_ns,
                coverage_slack_ns=coverage_slack_ns,
            )
        )
    return list(dict.fromkeys(combined))


@dataclass(frozen=True, slots=True)
class _FinalStreamProjection:
    clock_samples: list[dict[str, int | None]]
    clock_values: list[int]
    amcl_samples: list[dict[str, Any]]
    valid_amcl: list[dict[str, Any]]
    odom_samples: list[dict[str, Any]]
    valid_odom: list[dict[str, Any]]
    raw_ground_truth: list[dict[str, Any]]
    verified_ground_truth: list[dict[str, Any]]
    ground_truth_required: bool
    stationary: bool


def _project_final_streams(
    recorders: dict[str, _TopicRecorder],
    *,
    start_host_ns: int,
    end_host_ns: int,
    start_clock_ns: int | None,
    end_clock_ns: int | None,
    max_topic_age_s: float,
    max_future_source_lead_s: float,
    max_covariance_xy: float,
    max_speed_mps: float,
    max_yaw_rate_rps: float,
    calibration: GroundTruthCalibration | None,
) -> _FinalStreamProjection:
    clock_samples, clock_values = _clock_window_evidence(
        recorders["clock"],
        start_host_ns=start_host_ns,
        end_host_ns=end_host_ns,
    )
    amcl_samples = _window_topic_evidence(
        recorders["amcl"],
        recorders["clock"],
        start_host_ns=start_host_ns,
        end_host_ns=end_host_ns,
        max_age_s=max_topic_age_s,
        max_future_s=max_future_source_lead_s,
    )
    odom_samples = _window_topic_evidence(
        recorders["odom"],
        recorders["clock"],
        start_host_ns=start_host_ns,
        end_host_ns=end_host_ns,
        max_age_s=max_topic_age_s,
        max_future_s=max_future_source_lead_s,
    )
    ground_truth_recorder = recorders.get("ground_truth")
    raw_ground_truth = (
        _window_topic_evidence(
            ground_truth_recorder,
            recorders["clock"],
            start_host_ns=start_host_ns,
            end_host_ns=end_host_ns,
            max_age_s=max_topic_age_s,
            max_future_s=max_future_source_lead_s,
        )
        if ground_truth_recorder is not None
        else []
    )
    _annotate_final_localization_samples(amcl_samples, odom_samples)
    valid_amcl = _bounded_stream_samples(
        _valid_final_amcl(amcl_samples, max_covariance_xy=max_covariance_xy),
        start_clock_ns=start_clock_ns,
        end_clock_ns=end_clock_ns,
    )
    valid_odom = _bounded_stream_samples(
        _valid_final_odom(odom_samples),
        start_clock_ns=start_clock_ns,
        end_clock_ns=end_clock_ns,
    )
    ground_truth_required = calibration is not None and calibration.status == "VERIFIED"
    verified_ground_truth = (
        _bounded_stream_samples(
            _ground_truth_samples(raw_ground_truth, calibration),
            start_clock_ns=start_clock_ns,
            end_clock_ns=end_clock_ns,
        )
        if ground_truth_required and calibration is not None
        else []
    )
    stationary = bool(valid_odom) and all(
        float(sample["linear_velocity_mps"]) <= max_speed_mps
        and abs(float(sample["angular_velocity_rps"])) <= max_yaw_rate_rps
        for sample in valid_odom
    )
    return _FinalStreamProjection(
        clock_samples=clock_samples,
        clock_values=clock_values,
        amcl_samples=amcl_samples,
        valid_amcl=valid_amcl,
        odom_samples=odom_samples,
        valid_odom=valid_odom,
        raw_ground_truth=raw_ground_truth,
        verified_ground_truth=verified_ground_truth,
        ground_truth_required=ground_truth_required,
        stationary=stationary,
    )


async def _sample_final_observation_window(
    bridge: RosBridgeClient,
    config: AppConfig,
    options: DifferentialCaptureOptions,
    recorders: dict[str, _TopicRecorder],
    *,
    terminal_host_ns: int | None,
    calibration: GroundTruthCalibration | None = None,
    pose_observations: _PoseObservationRecorder | None = None,
) -> dict[str, Any]:
    if pose_observations is None:
        collected = await _collect_final_map_window(
            bridge,
            config,
            options,
            recorders["clock"],
        )
    else:
        collected = await _collect_final_map_window(
            bridge,
            config,
            options,
            recorders["clock"],
            pose_observations,
        )
    (
        start_host_ns,
        end_host_ns,
        start_clock_ns,
        end_clock_ns,
        clock_backwards,
        map_attempts,
        failures,
    ) = collected
    required_duration_ns = int(options.final_sample_s * 1_000_000_000)
    coverage_slack_ns = int(
        min(options.max_topic_age_s, options.sample_interval_s * 2.0) * 1_000_000_000
    )
    map_samples = [
        sample
        for sample in map_attempts
        if sample.get("fresh") is True and isinstance(sample.get("pose"), dict)
    ]
    streams = _project_final_streams(
        recorders,
        start_host_ns=start_host_ns,
        end_host_ns=end_host_ns,
        start_clock_ns=start_clock_ns,
        end_clock_ns=end_clock_ns,
        max_topic_age_s=options.max_topic_age_s,
        max_future_source_lead_s=options.sample_interval_s,
        max_covariance_xy=options.max_covariance_xy,
        max_speed_mps=options.max_start_speed_mps,
        max_yaw_rate_rps=options.max_start_yaw_rate_rps,
        calibration=calibration,
    )
    failures = _final_window_failures(
        failures=failures,
        terminal_bound=terminal_host_ns is not None and start_host_ns >= terminal_host_ns,
        start_clock_ns=start_clock_ns,
        end_clock_ns=end_clock_ns,
        required_duration_ns=required_duration_ns,
        coverage_slack_ns=coverage_slack_ns,
        clock_values=streams.clock_values,
        valid_map_count=len(map_samples),
        min_map_count=options.min_final_pose_samples,
        fresh_amcl=streams.valid_amcl,
        fresh_odom=streams.valid_odom,
        min_state_samples=options.min_final_state_samples,
        verified_ground_truth=streams.verified_ground_truth,
        ground_truth_required=streams.ground_truth_required,
        min_ground_truth_samples=options.min_final_ground_truth_samples,
        stationary=streams.stationary,
    )
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "terminal_host_monotonic_ns": terminal_host_ns,
        "start_host_monotonic_ns": start_host_ns,
        "end_host_monotonic_ns": end_host_ns,
        "start_clock_ns": start_clock_ns,
        "end_clock_ns": end_clock_ns,
        "required_duration_ns": required_duration_ns,
        "coverage_slack_ns": coverage_slack_ns,
        "clock_backwards": clock_backwards,
        "clock_samples": streams.clock_samples,
        "map_pose_samples": map_samples,
        "map_pose_attempts": map_attempts,
        "amcl_samples": streams.amcl_samples,
        "valid_amcl_samples": streams.valid_amcl,
        "odom_samples": streams.odom_samples,
        "valid_odom_samples": streams.valid_odom,
        "ground_truth_samples": streams.raw_ground_truth,
        "verified_ground_truth_samples": streams.verified_ground_truth,
        "ground_truth_required": streams.ground_truth_required,
        "stationary": streams.stationary,
    }


def _median_pose(samples: list[dict[str, Any]]) -> Pose2D | None:
    poses = [
        cast(dict[str, Any], sample["pose"])
        for sample in samples
        if isinstance(sample.get("pose"), dict)
    ]
    if not poses:
        return None
    x_values = sorted(float(pose["x"]) for pose in poses)
    y_values = sorted(float(pose["y"]) for pose in poses)
    sin_values = [math.sin(float(pose["yaw"])) for pose in poses]
    cos_values = [math.cos(float(pose["yaw"])) for pose in poses]
    midpoint = len(poses) // 2

    def median(values: list[float]) -> float:
        ordered = sorted(values)
        if len(ordered) % 2:
            return ordered[midpoint]
        return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0

    return Pose2D(
        x=median(x_values),
        y=median(y_values),
        yaw=math.atan2(sum(sin_values), sum(cos_values)),
    )


def _unavailable_calibration(
    runtime_identity: dict[str, Any],
    reason: str,
) -> GroundTruthCalibration:
    return GroundTruthCalibration(
        status="GROUND_TRUTH_UNAVAILABLE",
        scene_sha256=str(runtime_identity.get("scene_sha256") or "0" * 64),
        map_sha256=str(runtime_identity.get("site_map_sha256") or "0" * 64),
        source=reason,
    )


def _load_calibration(
    options: DifferentialCaptureOptions,
    runtime_identity: dict[str, Any],
) -> GroundTruthCalibration:
    if options.calibration_path is None:
        return _unavailable_calibration(runtime_identity, "no map/world calibration configured")
    calibration = GroundTruthCalibration.model_validate_json(
        options.calibration_path.read_text(encoding="utf-8")
    )
    if calibration.status != "VERIFIED":
        return calibration
    if (
        calibration.residual_m is None
        or calibration.residual_m > options.max_calibration_residual_m
    ):
        return _unavailable_calibration(
            runtime_identity,
            "calibration residual exceeds the declared trust threshold",
        )
    calibration_map_frame = (calibration.map_frame_id or "").lstrip("/")
    configured_map_frame = str(runtime_identity.get("site_map_frame") or "").lstrip("/")
    live_map_frame = str(runtime_identity.get("live_map_frame") or "").lstrip("/")
    expected = (
        calibration.scene_sha256 == runtime_identity.get("scene_sha256")
        and calibration.scene_sha256 == runtime_identity.get("live_scene_sha256")
        and calibration.map_sha256 == runtime_identity.get("site_map_sha256")
        and calibration.map_sha256 == runtime_identity.get("live_map_sha256")
        and calibration_map_frame == configured_map_frame == live_map_frame
    )
    if not expected:
        return _unavailable_calibration(
            runtime_identity,
            "calibration scene/live-stage or configured/live-map identity/frame does not match",
        )
    return calibration


def _ground_truth_samples(
    samples: list[dict[str, Any]],
    calibration: GroundTruthCalibration,
) -> list[dict[str, Any]]:
    if calibration.status != "VERIFIED" or calibration.world_frame_id is None:
        return []
    expected_frame = calibration.world_frame_id.lstrip("/")
    results: list[dict[str, Any]] = []
    for sample in samples:
        message = sample.get("message")
        header = message.get("header") if isinstance(message, dict) else None
        observed_frame = header.get("frame_id") if isinstance(header, dict) else None
        if (
            sample.get("fresh") is not True
            or not isinstance(message, dict)
            or not isinstance(observed_frame, str)
            or observed_frame.lstrip("/") != expected_frame
        ):
            continue
        world_pose = _pose_from_message(message)
        if world_pose is None:
            continue
        map_pose = calibration.world_to_map(world_pose)
        results.append(
            {
                "host_monotonic_ns": sample.get("host_monotonic_ns"),
                "source_stamp_ns": sample.get("source_stamp_ns"),
                "capture_clock_ns": sample.get("capture_clock_ns"),
                "source_age_ns": sample.get("source_age_ns"),
                "source_frame_id": observed_frame,
                "fresh": True,
                "world_pose": world_pose.model_dump(mode="json"),
                "map_pose": map_pose.model_dump(mode="json") if map_pose else None,
            }
        )
    return results


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        reservation = _ACTIVE_OUTPUT_RESERVATION.get()
        if isinstance(reservation, _OutputReservation) and reservation.output == path:
            reservation.commit_temporary(temporary)
        else:
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class _OutputReservation:
    """Atomically reserve one evidence path before any motion can begin.

    A reservation that survives a motion-attempted persistence failure is an
    intentional contamination marker: another run must not silently reuse the
    path after evidence may have been lost.
    """

    def __init__(
        self,
        output: Path,
        lock_path: Path,
        reservation_id: str,
    ) -> None:
        self.output = output
        self.path = lock_path
        self.reservation_id = reservation_id
        self._context_token: Token[Any] | None = None
        self._committed = False
        self._released = False

    @classmethod
    def acquire(cls, output: Path) -> _OutputReservation:
        output.parent.mkdir(parents=True, exist_ok=True)
        lock_path = output.with_name(f".{output.name}.capture.lock")
        reservation_id = uuid4().hex
        marker = {
            "kind": "jenai-isaac-nav-differential-output-reservation-v1",
            "output_name": output.name,
            "pid": os.getpid(),
            "reservation_id": reservation_id,
            "reserved_at": _utc_now(),
            "state": "reserved-before-motion",
        }
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        try:
            output_descriptor = os.open(output, flags, 0o600)
        except FileExistsError as exc:
            raise FileExistsError(f"Capture output is already reserved: {output}") from exc
        try:
            with os.fdopen(output_descriptor, "w", encoding="utf-8") as stream:
                json.dump(marker, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            lock_descriptor = os.open(lock_path, flags, 0o600)
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(output_descriptor)
            output.unlink(missing_ok=True)
            raise
        try:
            with os.fdopen(lock_descriptor, "w", encoding="utf-8") as stream:
                json.dump(marker, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            lock_path.unlink(missing_ok=True)
            output.unlink(missing_ok=True)
            raise
        reservation = cls(output, lock_path, reservation_id)
        reservation._context_token = _ACTIVE_OUTPUT_RESERVATION.set(reservation)
        return reservation

    def _owns_current_marker(self) -> bool:
        try:
            payload = json.loads(self.output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return (
            isinstance(payload, dict)
            and payload.get("kind") == "jenai-isaac-nav-differential-output-reservation-v1"
            and payload.get("reservation_id") == self.reservation_id
        )

    def commit_temporary(self, temporary: Path) -> None:
        if self._released or self._committed or not self._owns_current_marker():
            raise FileExistsError(f"Capture output reservation was lost: {self.output}")
        os.replace(temporary, self.output)
        self._committed = True

    def release(self, *, discard_marker: bool = False) -> None:
        if self._released:
            return
        if discard_marker and not self._committed and self._owns_current_marker():
            self.output.unlink(missing_ok=True)
        self.path.unlink(missing_ok=True)
        if self._context_token is not None and _ACTIVE_OUTPUT_RESERVATION.get() is self:
            _ACTIVE_OUTPUT_RESERVATION.reset(self._context_token)
        self._released = True


def _write_capture_artifact(
    path: Path,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    validated = DifferentialArtifact.model_validate(artifact).model_dump(mode="json")
    _atomic_write_json(path, validated)
    return validated


def _write_comparison_report(path: Path, report: dict[str, Any]) -> dict[str, Any]:
    reservation = _OutputReservation.acquire(path)
    try:
        validated = DifferentialComparisonReport.model_validate(report).model_dump(mode="json")
        _atomic_write_json(path, validated)
    except BaseException:
        reservation.release(discard_marker=True)
        raise
    reservation.release()
    return validated


def _valid_domain_id(value: object) -> bool:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return False
    return 0 <= parsed <= 232


def _valid_nav2_process_generation(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        UUID(str(value.get("boot_id")))
    except (ValueError, TypeError):
        return False
    integer_fields = ("session_created", "pane_pid", "pane_start_ticks")
    if not (
        all(type(value.get(field)) is int and int(value[field]) > 0 for field in integer_fields)
        and isinstance(value.get("session"), str)
        and bool(value["session"])
        and isinstance(value.get("session_id"), str)
        and bool(value["session_id"])
        and isinstance(value.get("pane_id"), str)
        and bool(value["pane_id"])
    ):
        return False
    processes = value.get("processes")
    if not isinstance(processes, list) or len(processes) < 2:
        return False
    expected_fields = {"pid", "ppid", "start_ticks", "cmdline_sha256"}
    pids: list[int] = []
    for process in processes:
        if not isinstance(process, dict) or set(process) != expected_fields:
            return False
        pid = process.get("pid")
        ppid = process.get("ppid")
        start_ticks = process.get("start_ticks")
        digest = process.get("cmdline_sha256")
        if (
            type(pid) is not int
            or pid <= 0
            or type(ppid) is not int
            or ppid < 0
            or type(start_ticks) is not int
            or start_ticks <= 0
            or not _valid_sha256(digest)
        ):
            return False
        pids.append(pid)
    if pids != sorted(pids) or len(set(pids)) != len(pids):
        return False
    pane_pid = int(value["pane_pid"])
    root = next((process for process in processes if process["pid"] == pane_pid), None)
    pid_set = set(pids)
    return (
        root is not None
        and root["start_ticks"] == value["pane_start_ticks"]
        and all(process["pid"] == pane_pid or process["ppid"] in pid_set for process in processes)
    )


def _runtime_stack_failures(identity: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for field in ("controller_lifecycle", "planner_lifecycle", "bt_navigator_lifecycle"):
        value = identity.get(field)
        if not isinstance(value, str) or not value.startswith("active"):
            failures.append(f"inactive_{field}")

    required_nodes = {"/amcl", "/controller_server", "/planner_server", "/bt_navigator"}
    node_counts = identity.get("node_name_counts")
    unique_nodes = (
        isinstance(node_counts, dict)
        and set(node_counts) == required_nodes
        and all(node_counts[node] == 1 for node in required_nodes)
    )
    parameter_hashes = identity.get("runtime_parameter_sha256")
    complete_parameters = (
        isinstance(parameter_hashes, dict)
        and set(parameter_hashes) == required_nodes
        and all(_valid_sha256(parameter_hashes[node]) for node in required_nodes)
    )
    providers = identity.get("navigate_to_pose_server_providers")
    unique_provider = (
        isinstance(providers, list)
        and len(providers) == 1
        and isinstance(providers[0], dict)
        and set(providers[0]) == {"node", "action_type"}
        and isinstance(providers[0].get("node"), str)
        and cast(str, providers[0]["node"]).startswith("/")
        and providers[0].get("action_type") == "nav2_msgs/action/NavigateToPose"
    )
    checks = (
        (not unique_nodes, "nav2_node_uniqueness"),
        (
            not unique_provider,
            "navigate_to_pose_server_uniqueness",
        ),
        (not complete_parameters, "runtime_parameter_snapshot"),
        (
            not isinstance(identity.get("controller_odom_topic"), str)
            or _normalized_ros_topic(cast(str, identity["controller_odom_topic"]))
            != identity.get("controller_odom_topic"),
            "controller_odom_topic",
        ),
        (
            not _valid_nav2_process_generation(identity.get("nav2_process_generation")),
            "nav2_process_generation",
        ),
    )
    failures.extend(failure for failed, failure in checks if failed)
    return failures


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_git_revision(value: object) -> bool:
    return (
        isinstance(value, str)
        and 40 <= len(value) <= 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _normalized_absolute_path(value: object) -> bool:
    return isinstance(value, str) and os.path.isabs(value) and os.path.normpath(value) == value


def _valid_python_version(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parts = value.split(".")
    return len(parts) >= 3 and all(part.isdigit() for part in parts[:3])


def _ros_middleware_failures(identity: dict[str, Any]) -> list[str]:
    value = identity.get("ros_middleware")
    try:
        descriptor = BridgeRuntimeIdentity.from_payload(value)
    except BridgeError:
        return ["ros_middleware_identity_missing"]
    requested = descriptor.rmw_implementation_requested
    effective = descriptor.rmw_implementation_effective
    expected_domain = identity.get("bridge_domain_id")
    checks = (
        (
            isinstance(requested, str) and isinstance(effective, str) and requested != effective,
            "rmw_implementation_mismatch",
        ),
        (
            not _valid_domain_id(expected_domain)
            or descriptor.ros_domain_id != int(cast(str, expected_domain)),
            "bridge_runtime_domain_mismatch",
        ),
    )
    return [failure for failed, failure in checks if failed]


def _source_identity_failures(
    identity: dict[str, Any],
    *,
    require_ros_middleware: bool = True,
) -> list[str]:
    required_hashes = (
        "config_sha256",
        "bridge_script_sha256",
        "site_map_sha256",
        "site_locations_sha256",
        "locations_sha256",
        "nav_params_sha256",
        "scene_sha256",
        "live_scene_sha256",
    )
    source_root = identity.get("source_root")
    expected_source_root = identity.get("expected_source_root")
    fingerprint = identity.get("fingerprint")
    reviewed_git_sha = identity.get("reviewed_git_sha")
    import_path = identity.get("jenai_import_path")
    bridge_script_path = identity.get("bridge_script_path")
    checks = (
        (not _valid_git_revision(identity.get("git_sha")), "git_revision_unavailable"),
        (identity.get("git_dirty") is not False, "clean_git_revision_required"),
        (
            not _normalized_absolute_path(source_root),
            "source_root_unavailable",
        ),
        (
            not _normalized_absolute_path(expected_source_root),
            "expected_source_root_unavailable",
        ),
        (
            isinstance(source_root, str)
            and isinstance(expected_source_root, str)
            and source_root != expected_source_root,
            "source_root_mismatch",
        ),
        (
            isinstance(expected_source_root, str)
            and (
                not _normalized_absolute_path(import_path)
                or not Path(str(import_path)).is_relative_to(
                    Path(expected_source_root) / "src" / "jenai"
                )
            ),
            "jenai_import_path_mismatch",
        ),
        (
            isinstance(expected_source_root, str)
            and (
                not _normalized_absolute_path(bridge_script_path)
                or Path(str(bridge_script_path))
                != Path(expected_source_root) / "src" / "jenai" / "bridge" / "ros_bridge.py"
            ),
            "bridge_script_path_mismatch",
        ),
        (
            not _normalized_absolute_path(identity.get("python_executable")),
            "python_executable_unavailable",
        ),
        (not _valid_python_version(identity.get("python_version")), "python_version_invalid"),
        (
            not _valid_git_revision(identity.get("expected_git_sha")),
            "expected_git_revision_unavailable",
        ),
        (
            identity.get("expected_git_dirty") is not False,
            "clean_expected_git_revision_required",
        ),
        (
            not _valid_git_revision(reviewed_git_sha),
            "reviewed_git_revision_unavailable",
        ),
        (
            isinstance(identity.get("git_sha"), str)
            and isinstance(identity.get("expected_git_sha"), str)
            and identity.get("git_sha") != identity.get("expected_git_sha"),
            "source_revision_mismatch",
        ),
        (
            isinstance(identity.get("git_sha"), str)
            and isinstance(reviewed_git_sha, str)
            and identity.get("git_sha") != reviewed_git_sha,
            "reviewed_source_revision_mismatch",
        ),
        (
            isinstance(identity.get("expected_git_sha"), str)
            and isinstance(reviewed_git_sha, str)
            and identity.get("expected_git_sha") != reviewed_git_sha,
            "reviewed_expected_revision_mismatch",
        ),
        (
            identity.get("deployment_mode") != "simulation",
            "simulation_deployment_mode_required",
        ),
        (
            identity.get("scene_sha256") != identity.get("live_scene_sha256"),
            "live_scene_identity_mismatch",
        ),
        (not _valid_domain_id(identity.get("bridge_domain_id")), "invalid_bridge_domain_id"),
        (not _valid_sha256(fingerprint), "runtime_fingerprint_invalid"),
        (
            _valid_sha256(fingerprint) and fingerprint != _runtime_fingerprint(identity),
            "runtime_fingerprint_mismatch",
        ),
    )
    failures = [failure for failed, failure in checks if failed]
    if require_ros_middleware:
        failures.extend(_ros_middleware_failures(identity))
    failures.extend(
        f"missing_{field}" for field in required_hashes if not _valid_sha256(identity.get(field))
    )
    return list(dict.fromkeys(failures))


def _runtime_identity_failures(
    identity: dict[str, Any],
    *,
    require_end_generation: bool = False,
) -> list[str]:
    checks: tuple[tuple[bool, str], ...] = (
        (
            identity.get("live_map_sha256") != identity.get("site_map_sha256"),
            "live_map_identity_mismatch",
        ),
        (
            identity.get("live_map_frame") != identity.get("site_map_frame"),
            "live_map_frame_mismatch",
        ),
        (not _valid_sha256(identity.get("live_map_sha256")), "missing_live_map_sha256"),
    )
    failures = [*_source_identity_failures(identity)]
    failures.extend(failure for failed, failure in checks if failed)
    failures.extend(_runtime_stack_failures(identity))
    if require_end_generation and (
        not _valid_nav2_process_generation(identity.get("nav2_process_generation_end"))
        or identity.get("nav2_process_generation_end") != identity.get("nav2_process_generation")
    ):
        failures.append("nav2_process_generation_changed")
    return list(dict.fromkeys(failures))


def _measurement_contract(
    options: DifferentialCaptureOptions,
) -> DifferentialMeasurementContract:
    return DifferentialMeasurementContract(
        preflight_sample_s=options.preflight_sample_s,
        final_sample_s=options.final_sample_s,
        final_window_start_delay_s=options.final_window_start_delay_s,
        sample_interval_s=options.sample_interval_s,
        max_topic_age_s=options.max_topic_age_s,
        max_calibration_residual_m=options.max_calibration_residual_m,
        min_final_pose_samples=options.min_final_pose_samples,
        min_final_state_samples=options.min_final_state_samples,
        min_final_ground_truth_samples=options.min_final_ground_truth_samples,
        final_wall_timeout_s=options.final_wall_timeout_s,
        max_start_speed_mps=options.max_start_speed_mps,
        max_start_yaw_rate_rps=options.max_start_yaw_rate_rps,
        max_covariance_xy=options.max_covariance_xy,
        max_pair_start_position_delta_m=options.max_pair_start_position_delta_m,
        max_pair_start_yaw_delta_rad=options.max_pair_start_yaw_delta_rad,
        ground_truth_topic=options.ground_truth_topic,
        ground_truth_type=(options.ground_truth_type if options.ground_truth_topic else None),
        ground_truth_calibration_sha256=_calibration_file_payload_sha256(options.calibration_path),
    )


def _base_artifact(options: DifferentialCaptureOptions) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "evidence_derivation_version": 3,
        "run_id": f"nav-diff-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}",
        "pair_id": options.pair_id,
        "mode": options.mode,
        "reset_policy": options.reset_policy,
        "execution_requested": options.execute,
        "measurement_contract": _measurement_contract(options).model_dump(mode="json"),
        "started_at": _utc_now(),
        "runtime_identity": {},
        "canonical_goal": None,
        "target_binding": None,
        "ground_truth_calibration": None,
        "pose_observations": [],
        "checks": [],
        "overall": "initializing",
    }


def _target_binding(
    *,
    requested_query: str,
    bound_action: dict[str, Any],
    goal: CanonicalGoal,
    locations_sha256: object,
) -> TargetBinding:
    capability_id = bound_action.get("capability_id")
    if capability_id != "navigate":
        raise ValueError(
            "Isaac navigation differential capture supports only the navigate capability."
        )
    raw_location = bound_action.get("goal")
    if not isinstance(raw_location, dict) or not _valid_sha256(locations_sha256):
        raise ValueError("Bound target identity is incomplete.")
    resolved_name = raw_location.get("name")
    resolved_id = raw_location.get("id")
    if not isinstance(resolved_name, str) or not isinstance(resolved_id, str):
        raise ValueError("Bound target has no stable saved-location identity.")
    pose = Pose2D(x=goal.x, y=goal.y, yaw=goal.yaw)
    record = {
        "capability_id": capability_id,
        "frame_id": goal.frame_id.lstrip("/"),
        "locations_sha256": locations_sha256,
        "pose": pose.model_dump(mode="json"),
        "resolved_id": resolved_id,
        "resolved_name": resolved_name,
    }
    canonical_goal_sha256 = _canonical_json_sha256(goal.model_dump(mode="json"))
    canonical_record_sha256 = _canonical_json_sha256(record)
    stable_binding = {
        "canonical_goal_sha256": canonical_goal_sha256,
        "canonical_record_sha256": canonical_record_sha256,
        "capability_id": capability_id,
        "locations_sha256": locations_sha256,
    }
    return TargetBinding(
        requested_query=requested_query,
        resolved_name=resolved_name,
        resolved_id=resolved_id,
        frame_id=goal.frame_id,
        pose=pose,
        capability_id=capability_id,
        locations_sha256=cast(str, locations_sha256),
        canonical_record_sha256=canonical_record_sha256,
        canonical_goal_sha256=canonical_goal_sha256,
        binding_sha256=_canonical_json_sha256(stable_binding),
    )


def _prepare_capture(
    options: DifferentialCaptureOptions,
) -> tuple[
    Path,
    AppConfig,
    dict[str, Any],
    CanonicalGoal,
    dict[str, Any],
    TargetBinding,
]:
    config_path = (options.config_path or default_config_path()).expanduser().resolve()
    config_snapshot = load_config_snapshot(config_path)
    config = config_snapshot.config
    locations_path = config.resolved_locations_path(config_path)
    if locations_path is None:
        raise ValueError("No locations_path is configured.")
    locations_snapshot = load_locations_snapshot(locations_path)
    location = find_location(list(locations_snapshot.locations), options.location)
    raw_action: dict[str, Any] = {
        "capability_id": "navigate",
        "goal": location.model_dump(mode="json"),
    }
    bound_action = bind_navigation_action(
        config,
        config_path,
        raw_action,
        locations_snapshot=locations_snapshot,
    )
    raw_goal = bound_action["goal"]
    if not isinstance(raw_goal, dict):
        raise ValueError("Bound navigation goal is not an object.")
    raw_pose = raw_goal.get("pose")
    if not isinstance(raw_pose, dict):
        raise ValueError("Bound navigation goal has no pose.")
    goal = CanonicalGoal.from_yaw(
        frame_id=str(raw_goal.get("frame_id", "map")),
        x=float(raw_pose["x"]),
        y=float(raw_pose["y"]),
        yaw=float(raw_pose.get("yaw", 0.0)),
        clock_domain="ros",
        simulation_epoch=options.simulation_epoch,
    )
    identity = _runtime_identity(
        config,
        config_path,
        expected_source_root=options.expected_source_root,
        scene_path=options.scene_path,
        live_scene_sha256=options.live_scene_sha256,
        simulation_epoch=options.simulation_epoch,
        reviewed_git_sha=options.expected_git_sha,
    )
    identity["site_map_frame"] = config.site.map_frame
    identity["robot_base_frame"] = config.vehicle.robot_base_frame
    identity["config_sha256"] = config_snapshot.sha256
    identity["locations_sha256"] = locations_snapshot.sha256
    _apply_runtime_fingerprint(identity)
    target_binding = _target_binding(
        requested_query=options.location,
        bound_action=bound_action,
        goal=goal,
        locations_sha256=identity.get("locations_sha256"),
    )
    return config_path, config, bound_action, goal, identity, target_binding


async def _collect_start_state(
    bridge: RosBridgeClient,
    config: AppConfig,
    recorders: dict[str, _TopicRecorder],
    options: DifferentialCaptureOptions,
    calibration: GroundTruthCalibration,
    pose_observations: _PoseObservationRecorder,
) -> dict[str, Any]:
    cutoff_host_ns = time.monotonic_ns()
    return await _collect_dispatch_state(
        bridge,
        config,
        recorders,
        options,
        calibration,
        pose_observations,
        purpose=PoseLookupPurpose.T0_START,
        cutoff_host_monotonic_ns=cutoff_host_ns,
    )


async def _collect_dispatch_state(
    bridge: RosBridgeClient,
    config: AppConfig,
    recorders: dict[str, _TopicRecorder],
    options: DifferentialCaptureOptions,
    calibration: GroundTruthCalibration,
    pose_observations: _PoseObservationRecorder,
    *,
    purpose: PoseLookupPurpose,
    cutoff_host_monotonic_ns: int,
) -> dict[str, Any]:
    async with asyncio.TaskGroup() as tasks:
        nomotion_task = tasks.create_task(bridge.request_nomotion_update())
        pose_task = tasks.create_task(
            pose_observations.capture(
                bridge,
                recorders["clock"],
                purpose=purpose,
                frame_id=config.site.map_frame,
                base_frame=config.vehicle.robot_base_frame,
                timeout_s=3.0,
            )
        )
        tasks.create_task(asyncio.sleep(options.preflight_sample_s))
    start_pose, observation_id, _ = pose_task.result()
    return _initial_state(
        pose=start_pose,
        map_pose_observation_id=observation_id,
        clock=recorders["clock"],
        amcl=recorders["amcl"],
        odom=recorders["odom"],
        action_status=recorders["action_status"],
        options=options,
        ground_truth=recorders["ground_truth"],
        calibration=calibration,
        cutoff_host_monotonic_ns=cutoff_host_monotonic_ns,
        action_status_observation_ready=True,
        nomotion_update_acknowledged=nomotion_task.result(),
    )


def _dispatch_timeline(
    dispatch_observations: list[dict[str, Any]],
    goal_observations: list[dict[str, Any]],
    terminal: dict[str, Any] | None,
    *,
    request_ns: int,
    returned_ns: int,
) -> dict[str, Any]:
    failures: list[str] = []
    dispatch = dispatch_observations[0] if len(dispatch_observations) == 1 else None
    if dispatch is None:
        failures.append(
            "no_actual_nav_send" if not dispatch_observations else "multiple_actual_nav_sends"
        )
    elif dispatch.get("forward_completed_host_monotonic_ns") is None:
        failures.append("nav_send_not_completed")
    if dispatch is not None:
        state = dispatch.get("state_before_forward")
        if not isinstance(state, dict) or state.get("status") != "PASS":
            failures.append("dispatch_state_gate_failed")

    terminal_ns = terminal.get("observed_host_monotonic_ns") if terminal else None
    unique_candidates = {str(item.get("goal_uuid")) for item in goal_observations}
    fresh_candidates = [
        item
        for item in goal_observations
        if item.get("goal_stamp_fresh") is True
        and type(item.get("observed_host_monotonic_ns")) is int
        and type(terminal_ns) is int
        and int(item["observed_host_monotonic_ns"]) <= terminal_ns
    ]
    accepted: dict[str, Any] | None = None
    if len(unique_candidates) == 1 and len(fresh_candidates) == 1:
        accepted = fresh_candidates[0]
    elif not goal_observations:
        failures.append("goal_uuid_unavailable")
    else:
        failures.append("goal_uuid_ambiguous_or_stale")
    if terminal is None:
        failures.append("nav2_terminal_unavailable")

    dispatch_ns = (
        dispatch.get("nav_send_forwarded_host_monotonic_ns") if isinstance(dispatch, dict) else None
    )
    accepted_ns = accepted.get("observed_host_monotonic_ns") if accepted else None
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "request_host_monotonic_ns": request_ns,
        "return_host_monotonic_ns": returned_ns,
        "dispatch_count": len(dispatch_observations),
        "dispatch_observations": dispatch_observations,
        "actual_goal": dispatch.get("actual_goal") if isinstance(dispatch, dict) else None,
        "state_before_forward": (
            dispatch.get("state_before_forward") if isinstance(dispatch, dict) else None
        ),
        "nav_send_forwarded_host_monotonic_ns": dispatch_ns,
        "accepted_goal_observations": goal_observations,
        "accepted_goal_uuid": accepted.get("goal_uuid") if accepted else None,
        "goal_uuid_evidence": (
            "INFERRED_UNIQUE_ACTION_STATUS"
            if accepted is not None
            else (
                "UNAVAILABLE_NO_NEW_STATUS_UUID_OBSERVED"
                if not goal_observations
                else "AMBIGUOUS_OR_STALE_ACTION_STATUS"
            )
        ),
        "latency_ms": {
            "request_to_dispatch": (
                round((int(dispatch_ns) - request_ns) / 1_000_000.0, 3)
                if type(dispatch_ns) is int
                else None
            ),
            "dispatch_to_accept": (
                round((int(accepted_ns) - int(dispatch_ns)) / 1_000_000.0, 3)
                if type(accepted_ns) is int and type(dispatch_ns) is int
                else None
            ),
            "accept_to_terminal": (
                round((int(terminal_ns) - int(accepted_ns)) / 1_000_000.0, 3)
                if type(terminal_ns) is int and type(accepted_ns) is int
                else None
            ),
            "terminal_to_verification_return": (
                round((returned_ns - int(terminal_ns)) / 1_000_000.0, 3)
                if type(terminal_ns) is int
                else None
            ),
        },
    }


async def _wait_for_terminal_relative_window_start(
    terminal_host_ns: int,
    *,
    delay_s: float,
) -> int:
    target_ns = terminal_host_ns + int(delay_s * 1_000_000_000)
    remaining_ns = target_ns - time.monotonic_ns()
    if remaining_ns > 0:
        await asyncio.sleep(remaining_ns / 1_000_000_000.0)
    return time.monotonic_ns()


async def _record_final_live_evidence(
    artifact: dict[str, Any],
    options: DifferentialCaptureOptions,
    bridge: RosBridgeClient,
    config: AppConfig,
    calibration: GroundTruthCalibration,
    recorders: dict[str, _TopicRecorder],
    pose_observations: _PoseObservationRecorder,
    terminal: dict[str, Any] | None,
    jenai_result: dict[str, Any] | None,
    timeline: dict[str, Any],
    initial_map_identity: object,
) -> None:
    terminal_ns = terminal.get("observed_host_monotonic_ns") if terminal else None
    schedule_release_ns = (
        await _wait_for_terminal_relative_window_start(
            terminal_ns,
            delay_s=options.final_window_start_delay_s,
        )
        if type(terminal_ns) is int
        else None
    )
    final_window = await _sample_final_observation_window(
        bridge,
        config,
        options,
        recorders,
        terminal_host_ns=terminal_ns if type(terminal_ns) is int else None,
        calibration=calibration,
        pose_observations=pose_observations,
    )
    final_window["scheduled_start_delay_ns"] = int(
        options.final_window_start_delay_s * 1_000_000_000
    )
    final_window["schedule_release_host_monotonic_ns"] = schedule_release_ns
    artifact["final_observation_window"] = final_window
    post_final_map = await _capture_map_identity_checkpoint(
        bridge,
        label="post_final_window",
        expected=initial_map_identity,
    )
    artifact["post_final_window_map_identity_checkpoint"] = post_final_map
    post_final_runtime = _capture_runtime_stack_checkpoint(
        cast(dict[str, Any], artifact["runtime_identity"]),
        label="post_final_window",
    )
    artifact["post_final_window_runtime_stack_checkpoint"] = post_final_runtime
    map_samples = cast(list[dict[str, Any]], final_window["map_pose_samples"])
    median = _median_pose(map_samples)
    artifact["final_map_pose_samples"] = map_samples
    artifact["final_map_pose_median"] = median.model_dump(mode="json") if median else None

    gt_samples = cast(list[dict[str, Any]], final_window["verified_ground_truth_samples"])
    artifact["ground_truth_samples"] = gt_samples
    gt_map_poses = [
        {"pose": sample["map_pose"]}
        for sample in gt_samples
        if isinstance(sample.get("map_pose"), dict)
    ]
    gt_median = _median_pose(gt_map_poses)
    artifact["final_ground_truth_map_median"] = (
        gt_median.model_dump(mode="json") if gt_median else None
    )
    artifact["topic_samples"] = _snapshot_topic_samples(recorders)
    artifact["execution_status"] = (
        str(jenai_result.get("execution_status"))
        if jenai_result is not None
        else str((terminal or {}).get("status") or "unknown")
    )
    evidence_complete = (
        timeline["status"] == "PASS"
        and final_window["status"] == "PASS"
        and median is not None
        and terminal is not None
        and artifact["execution_status"] != "unknown"
        and artifact.get("terminal_map_identity_checkpoint", {}).get("status") == "PASS"
        and post_final_map["status"] == "PASS"
        and post_final_runtime["status"] == "PASS"
    )
    artifact["overall"] = "captured" if evidence_complete else "insufficient_evidence"


def _pre_dispatch_observer(
    options: DifferentialCaptureOptions,
    bridge: RosBridgeClient,
    config: AppConfig,
    calibration: GroundTruthCalibration,
    recorders: dict[str, _TopicRecorder],
    pose_observations: _PoseObservationRecorder,
    identity: dict[str, Any],
    initial_map_identity: object,
) -> Callable[[CanonicalGoal, str, int], Awaitable[dict[str, Any]]]:
    async def observe_nav_send(
        actual_goal: CanonicalGoal,
        tag: str,
        invoked_ns: int,
    ) -> dict[str, Any]:
        del actual_goal, tag
        state = await _collect_dispatch_state(
            bridge,
            config,
            recorders,
            options,
            calibration,
            pose_observations,
            purpose=PoseLookupPurpose.T1_PRE_DISPATCH,
            cutoff_host_monotonic_ns=invoked_ns,
        )
        input_continuity = _capture_input_continuity(identity)
        map_checkpoint = await _capture_map_identity_checkpoint(
            bridge,
            label="pre_dispatch",
            expected=initial_map_identity,
        )
        runtime_checkpoint = _capture_runtime_stack_checkpoint(
            identity,
            label="pre_dispatch",
        )
        state["input_continuity"] = input_continuity
        state["map_identity_checkpoint"] = map_checkpoint
        state["runtime_stack_checkpoint"] = runtime_checkpoint
        continuity_failures = [
            *cast(list[str], input_continuity["failures"]),
            *cast(list[str], map_checkpoint["failures"]),
            *cast(list[str], runtime_checkpoint["failures"]),
        ]
        if continuity_failures:
            state["status"] = "FAIL"
            state["failures"] = list(
                dict.fromkeys([*cast(list[str], state.get("failures", [])), *continuity_failures])
            )
        return state

    return observe_nav_send


def _observed_dispatch_timeline(
    observed_bridge: _ObservedNavBridge,
    recorders: dict[str, _TopicRecorder],
    options: DifferentialCaptureOptions,
    terminal: dict[str, Any] | None,
    *,
    request_ns: int,
    returned_ns: int,
) -> dict[str, Any]:
    dispatch = observed_bridge.observations[0] if len(observed_bridge.observations) == 1 else None
    dispatch_ns = (
        dispatch.get("nav_send_forwarded_host_monotonic_ns")
        if isinstance(dispatch, dict)
        else request_ns
    )
    dispatch_state = dispatch.get("state_before_forward") if isinstance(dispatch, dict) else None
    status_before = set(
        cast(list[str], dispatch_state.get("known_goal_ids", []))
        if isinstance(dispatch_state, dict)
        else []
    )
    goal_observations = _new_goal_ids(
        recorders["action_status"],
        recorders["clock"],
        before=status_before,
        dispatched_at_ns=int(dispatch_ns) if type(dispatch_ns) is int else request_ns,
        max_age_s=options.max_topic_age_s,
    )
    return _dispatch_timeline(
        observed_bridge.observations,
        goal_observations,
        terminal,
        request_ns=request_ns,
        returned_ns=returned_ns,
    )


async def _record_live_evidence(
    artifact: dict[str, Any],
    options: DifferentialCaptureOptions,
    bridge: RosBridgeClient,
    config: AppConfig,
    config_path: Path,
    goal: CanonicalGoal,
    bound_action: dict[str, Any],
    calibration: GroundTruthCalibration,
    recorders: dict[str, _TopicRecorder],
    pose_observations: _PoseObservationRecorder,
) -> None:
    identity = cast(dict[str, Any], artifact["runtime_identity"])
    initial_map_identity = identity.get("live_map_identity_initial")
    observe_nav_send = _pre_dispatch_observer(
        options,
        bridge,
        config,
        calibration,
        recorders,
        pose_observations,
        identity,
        initial_map_identity,
    )

    observed_bridge = _ObservedNavBridge(
        bridge,
        simulation_epoch=options.simulation_epoch,
        expected_goal=goal,
        on_nav_send=observe_nav_send,
        pose_observations=(
            pose_observations if options.mode is DifferentialMode.R2_JENAI_NO_RETRY else None
        ),
        clock=recorders["clock"],
    )
    terminal_map_task: asyncio.Task[dict[str, Any]] | None = None

    def schedule_terminal_map_checkpoint(event: dict[str, Any]) -> None:
        nonlocal terminal_map_task
        if terminal_map_task is not None:
            return
        event_tag = event.get("tag")
        if not isinstance(event_tag, str) or not any(
            observation.get("tag") == event_tag for observation in observed_bridge.observations
        ):
            return
        terminal_map_task = asyncio.create_task(
            _capture_map_identity_checkpoint(
                bridge,
                label="terminal",
                expected=initial_map_identity,
            )
        )

    observed_bridge.on_event("nav_result", schedule_terminal_map_checkpoint)
    request_ns = time.monotonic_ns()
    try:
        terminal, jenai_result = await _dispatch_mode(
            options,
            cast(RosBridgeClient, observed_bridge),
            config,
            config_path,
            goal,
            bound_action,
        )
        if jenai_result is not None:
            jenai_result["endpoint_pose_observation_ids"] = list(
                observed_bridge.verdict_pose_observation_ids
            )
    finally:
        observed_bridge.off_event("nav_result", schedule_terminal_map_checkpoint)
        artifact["dispatch_observations"] = observed_bridge.observations
        artifact["topic_samples_at_dispatch_end"] = _snapshot_topic_samples(recorders)
    returned_ns = time.monotonic_ns()
    timeline = _observed_dispatch_timeline(
        observed_bridge,
        recorders,
        options,
        terminal,
        request_ns=request_ns,
        returned_ns=returned_ns,
    )
    artifact["t1_goal_dispatch"] = timeline
    artifact["nav2_terminal"] = terminal
    artifact["jenai_result"] = jenai_result
    artifact["terminal_map_identity_checkpoint"] = (
        await terminal_map_task
        if terminal_map_task is not None
        else {
            "label": "terminal",
            "status": "FAIL",
            "observed_host_monotonic_ns": time.monotonic_ns(),
            "identity": None,
            "failures": ["nav2_terminal_map_identity_unavailable"],
        }
    )
    await _record_final_live_evidence(
        artifact,
        options,
        bridge,
        config,
        calibration,
        recorders,
        pose_observations,
        terminal,
        jenai_result,
        timeline,
        initial_map_identity,
    )


async def _cleanup_halt(
    bridge: RosBridgeClient,
    config: AppConfig,
    *,
    motion_attempted: bool,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if not motion_attempted:
        return {"status": "SKIP", "detail": "No motion was attempted."}, []
    if not bridge.running:
        return (
            {
                "status": "FAIL",
                "detail": "Bridge was not running; final halt could not be confirmed.",
            },
            [{"step": "final_halt", "detail": "bridge_not_running"}],
        )
    try:
        evidence = await bridge.halt_with_evidence(
            config.vehicle.cmd_vel_topic,
            config.vehicle.cmd_vel_stamped,
        )
    except Exception as exc:
        return (
            {"status": "FAIL", "type": type(exc).__name__, "detail": str(exc)},
            [{"step": "final_halt", "detail": str(exc)}],
        )
    confirmed = evidence.zero_velocity_command_published and (
        not evidence.navigation_cancel_requested or evidence.navigation_cancel_acknowledged
    )
    result = {
        "status": "PASS" if confirmed else "FAIL",
        "zero_velocity_command_published": evidence.zero_velocity_command_published,
        "navigation_cancel_requested": evidence.navigation_cancel_requested,
        "navigation_cancel_acknowledged": evidence.navigation_cancel_acknowledged,
        "motion_stop_observed": False,
    }
    failures = [] if confirmed else [{"step": "final_halt", "detail": "unconfirmed"}]
    return result, failures


async def _cleanup_unwatch(
    bridge: RosBridgeClient,
    watch_ids: list[int],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    details: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for watch_id in watch_ids:
        try:
            await bridge.unwatch(watch_id)
        except Exception as exc:
            details.append({"watch_id": watch_id, "type": type(exc).__name__, "detail": str(exc)})
            failures.append({"step": "unwatch", "detail": f"{watch_id}: {exc}"})
    return {"status": "PASS" if not failures else "FAIL", "failures": details}, failures


async def _cleanup_bridge_stop(
    bridge: RosBridgeClient,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    try:
        await bridge.stop()
    except Exception as exc:
        return (
            {"status": "FAIL", "type": type(exc).__name__, "detail": str(exc)},
            [{"step": "bridge_shutdown", "detail": str(exc)}],
        )
    return {"status": "PASS"}, []


async def _cancel_cleanup_heartbeat(
    heartbeat: asyncio.Task[None] | None,
) -> list[dict[str, str]]:
    if heartbeat is None:
        return []
    heartbeat.cancel()
    try:
        await heartbeat
    except asyncio.CancelledError:
        return []
    except Exception as exc:
        return [{"step": "heartbeat", "detail": str(exc)}]
    return []


async def _cleanup_with_replacement_bridge(
    config: AppConfig,
    primary_runtime_identity: object,
) -> dict[str, Any]:
    """Use one private bridge solely to publish zero and cancel active goals."""

    try:
        primary = BridgeRuntimeIdentity.from_payload(primary_runtime_identity)
    except BridgeError as exc:
        return {
            "status": "FAIL",
            "failures": [{"step": "rescue_identity", "detail": str(exc)}],
            "runtime_identity": None,
            "identity_compatible": False,
            "final_halt": {"status": "FAIL", "detail": "Primary bridge identity unavailable."},
            "bridge_shutdown": {"status": "SKIP"},
        }

    replacement = RosBridgeClient(domain_id=primary.ros_domain_id)
    result: dict[str, Any] = {
        "status": "FAIL",
        "failures": [],
        "runtime_identity": None,
        "identity_compatible": False,
        "final_halt": {"status": "FAIL", "detail": "Replacement halt was not attempted."},
        "bridge_shutdown": {"status": "UNCONFIRMED"},
    }
    failures = cast(list[dict[str, str]], result["failures"])
    try:
        await replacement.configure_safety(
            watchdog_s=6.0,
            cmd_vel_topic=config.vehicle.cmd_vel_topic,
            stamped=config.vehicle.cmd_vel_stamped,
            pose_jump_threshold_m=config.vehicle.pose_jump_threshold_m,
            pose_jump_window_s=config.vehicle.pose_jump_window_s,
        )
        await replacement.start(timeout=10.0)
        replacement_identity = await replacement.runtime_identity(pin=True)
        replacement_payload = replacement_identity.to_payload()
        result["runtime_identity"] = replacement_payload
        compatible = (
            replacement_identity.pid != primary.pid
            and (
                replacement_identity.boot_id != primary.boot_id
                or replacement_identity.process_start_ticks != primary.process_start_ticks
            )
            and replacement_identity.launch_nonce != primary.launch_nonce
            and _pairable_ros_middleware_identity(replacement_payload)
            == _pairable_ros_middleware_identity(primary_runtime_identity)
        )
        result["identity_compatible"] = compatible
        if not compatible:
            failures.append(
                {"step": "rescue_identity", "detail": "replacement_runtime_identity_mismatch"}
            )
        else:
            halt, halt_failures = await _cleanup_halt(
                replacement,
                config,
                motion_attempted=True,
            )
            result["final_halt"] = halt
            failures.extend(halt_failures)
    except Exception as exc:
        failures.append({"step": "rescue_bridge", "detail": str(exc)})
    finally:
        shutdown, shutdown_failures = await _cleanup_bridge_stop(replacement)
        result["bridge_shutdown"] = shutdown
        failures.extend(shutdown_failures)
    result["status"] = "PASS" if not failures else "FAIL"
    return result


async def _cleanup_live_capture(
    bridge: RosBridgeClient,
    config: AppConfig,
    watch_ids: list[int],
    heartbeat: asyncio.Task[None] | None,
    *,
    motion_attempted: bool,
    primary_runtime_identity: object = None,
) -> dict[str, Any]:
    primary_halt, primary_halt_failures = await _cleanup_halt(
        bridge,
        config,
        motion_attempted=motion_attempted,
    )
    failures = await _cancel_cleanup_heartbeat(heartbeat)
    rescue: dict[str, Any] | None = None
    final_halt = primary_halt
    if motion_attempted and primary_halt.get("status") != "PASS":
        rescue = await _cleanup_with_replacement_bridge(config, primary_runtime_identity)
        final_halt = cast(dict[str, Any], rescue["final_halt"])
        if rescue["status"] != "PASS":
            failures.extend(primary_halt_failures)
            failures.extend(cast(list[dict[str, str]], rescue["failures"]))
    elif primary_halt.get("status") == "FAIL":
        failures.extend(primary_halt_failures)

    if bridge.running:
        unwatch, unwatch_failures = await _cleanup_unwatch(bridge, watch_ids)
    else:
        unwatch = {
            "status": "PASS",
            "failures": [],
            "detail": "Primary bridge exited; its process-owned watches are gone.",
        }
        unwatch_failures = []
    shutdown, shutdown_failures = await _cleanup_bridge_stop(bridge)
    failures.extend(unwatch_failures)
    failures.extend(shutdown_failures)
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "final_halt": final_halt,
        "primary_halt": primary_halt,
        "rescue_bridge": rescue,
        "unwatch": unwatch,
        "bridge_shutdown": shutdown,
    }


def _map_identity_payload(value: MapIdentityInfo) -> dict[str, Any]:
    return {
        "algorithm": value.algorithm,
        "digest": value.digest,
        "frame_id": value.frame_id.lstrip("/"),
        "source": value.source,
        "geometry": {
            "width": value.width,
            "height": value.height,
            "resolution": value.resolution,
            "origin_x": value.origin_x,
            "origin_y": value.origin_y,
            "origin_yaw": value.origin_yaw,
        },
    }


def _map_identity_mismatch(expected: object, observed: object) -> bool:
    return not isinstance(expected, dict) or not isinstance(observed, dict) or expected != observed


async def _capture_map_identity_checkpoint(
    bridge: RosBridgeClient,
    *,
    label: str,
    expected: object,
) -> dict[str, Any]:
    observed_ns = time.monotonic_ns()
    try:
        observed = _map_identity_payload(await bridge.map_identity(timeout=3.0))
    except Exception as exc:
        return {
            "label": label,
            "status": "FAIL",
            "observed_host_monotonic_ns": observed_ns,
            "identity": None,
            "failures": ["live_map_identity_unavailable"],
            "error_type": type(exc).__name__,
            "error_detail": str(exc),
        }
    failures = ["live_map_identity_changed"] if _map_identity_mismatch(expected, observed) else []
    return {
        "label": label,
        "status": "FAIL" if failures else "PASS",
        "observed_host_monotonic_ns": time.monotonic_ns(),
        "identity": observed,
        "failures": failures,
    }


def _capture_input_continuity(identity: dict[str, Any]) -> dict[str, Any]:
    """Re-observe the immutable inputs without exposing their raw contents."""

    path_bindings = (
        ("config_path", "config_sha256", "config_changed_since_prepare"),
        ("locations_path", "locations_sha256", "locations_changed_since_prepare"),
        ("bridge_script_path", "bridge_script_sha256", "bridge_source_changed_since_review"),
    )
    observed_hashes: dict[str, str | None] = {}
    failures: list[str] = []
    for path_field, digest_field, failure in path_bindings:
        raw_path = identity.get(path_field)
        try:
            observed = (
                _sha256(Path(cast(str, raw_path))) if _normalized_absolute_path(raw_path) else None
            )
        except OSError:
            observed = None
        observed_hashes[digest_field] = observed
        if not _valid_sha256(observed) or observed != identity.get(digest_field):
            failures.append(failure)

    source_root = identity.get("source_root")
    root = Path(cast(str, source_root)) if _normalized_absolute_path(source_root) else None
    revision = _command_output(["git", "rev-parse", "HEAD"], cwd=root) if root else None
    dirty_output = _command_output(["git", "status", "--porcelain"], cwd=root) if root else None
    dirty = None if dirty_output is None else bool(dirty_output)
    if revision != identity.get("git_sha"):
        failures.append("source_revision_changed_since_prepare")
    if dirty != identity.get("git_dirty"):
        failures.append("source_dirty_state_changed_since_prepare")
    return {
        "status": "FAIL" if failures else "PASS",
        "observed_host_monotonic_ns": time.monotonic_ns(),
        "observed_hashes": observed_hashes,
        "observed_git_sha": revision,
        "observed_git_dirty": dirty,
        "failures": list(dict.fromkeys(failures)),
    }


async def _enrich_live_identity(
    bridge: RosBridgeClient,
    identity: dict[str, Any],
) -> None:
    live_map = await bridge.map_identity(timeout=3.0)
    initial_map_identity = _map_identity_payload(live_map)
    identity.update(
        {
            "live_map_algorithm": live_map.algorithm,
            "live_map_sha256": live_map.digest,
            "live_map_frame": live_map.frame_id,
            "live_map_source": live_map.source,
            "live_map_geometry": {
                "width": live_map.width,
                "height": live_map.height,
                "resolution": live_map.resolution,
                "origin_x": live_map.origin_x,
                "origin_y": live_map.origin_y,
                "origin_yaw": live_map.origin_yaw,
            },
            "live_map_identity_initial": initial_map_identity,
        }
    )
    _apply_runtime_fingerprint(identity)


def _complete_without_live_bridge(
    options: DifferentialCaptureOptions,
    config: AppConfig,
    identity: dict[str, Any],
    artifact: dict[str, Any],
) -> bool:
    if not options.execute:
        calibration = _load_calibration(options, identity)
        _record_ground_truth_calibration(artifact, identity, calibration)
        artifact["overall"] = "preflight_only"
        artifact["checks"].append(
            {
                "id": "execution",
                "status": "SKIP",
                "detail": "Live motion was not requested.",
            }
        )
        return True
    if config.deployment_mode != "simulation":
        artifact["overall"] = "blocked"
        artifact["checks"].append(
            {
                "id": "deployment_mode_gate",
                "status": "FAIL",
                "detail": "Differential live capture is simulation-only.",
                "failures": ["simulation_deployment_mode_required"],
            }
        )
        return True
    source_failures = _source_identity_failures(identity, require_ros_middleware=False)
    if not source_failures:
        return False
    artifact["overall"] = "blocked"
    artifact["checks"].append(
        {
            "id": "source_identity_gate",
            "status": "FAIL",
            "detail": "The executing source is not the reviewed clean revision.",
            "failures": source_failures,
        }
    )
    return True


def _record_capture_gate_failure(
    artifact: dict[str, Any],
    *,
    check_id: str,
    detail: str,
    failures: list[str],
) -> None:
    artifact["overall"] = "blocked"
    artifact["checks"].append(
        {
            "id": check_id,
            "status": "FAIL",
            "detail": detail,
            "failures": failures,
        }
    )


async def _safe_cleanup_live_capture(
    bridge: RosBridgeClient,
    config: AppConfig,
    watch_ids: list[int],
    heartbeat: asyncio.Task[None] | None,
    *,
    motion_attempted: bool,
    primary_runtime_identity: object = None,
) -> dict[str, Any]:
    try:
        return await _cleanup_live_capture(
            bridge,
            config,
            watch_ids,
            heartbeat,
            motion_attempted=motion_attempted,
            primary_runtime_identity=primary_runtime_identity,
        )
    except Exception as exc:
        return {
            "status": "FAIL",
            "failures": [
                {
                    "step": "cleanup_orchestrator",
                    "type": type(exc).__name__,
                    "detail": str(exc),
                }
            ],
            "final_halt": {
                "status": "FAIL" if motion_attempted else "SKIP",
                "detail": "Cleanup orchestration failed before complete evidence.",
            },
            "bridge_shutdown": {"status": "UNCONFIRMED"},
        }


def _record_end_generation(artifact: dict[str, Any]) -> None:
    runtime_identity = artifact.get("runtime_identity")
    if not isinstance(runtime_identity, dict):
        return
    session = runtime_identity.get("nav2_tmux_session")
    end_generation = _nav2_process_generation(session) if isinstance(session, str) else None
    runtime_identity["nav2_process_generation_end"] = end_generation
    if end_generation == runtime_identity.get("nav2_process_generation"):
        return
    artifact["checks"].append(
        {
            "id": "nav2_process_generation_end",
            "status": "FAIL",
            "detail": "Nav2 generation changed during the capture.",
        }
    )
    if artifact.get("overall") == "captured":
        artifact["overall"] = "insufficient_evidence"


def _cancelled_cleanup_result(
    *,
    motion_attempted: bool,
    detail: str,
) -> dict[str, Any]:
    return {
        "status": "FAIL",
        "failures": [
            {
                "step": "cleanup_orchestrator",
                "type": "CancelledError",
                "detail": detail,
            }
        ],
        "final_halt": {
            "status": "FAIL" if motion_attempted else "SKIP",
            "detail": "Cleanup was cancelled before complete stop evidence.",
        },
        "bridge_shutdown": {"status": "UNCONFIRMED"},
    }


async def _await_cleanup_despite_cancellation(
    cleanup_task: asyncio.Task[dict[str, Any]],
    *,
    motion_attempted: bool,
) -> tuple[dict[str, Any], asyncio.CancelledError | None]:
    interrupted: asyncio.CancelledError | None = None
    while True:
        try:
            return await asyncio.shield(cleanup_task), interrupted
        except asyncio.CancelledError as exc:
            if cleanup_task.done():
                if cleanup_task.cancelled():
                    detail = str(exc) or "cleanup task cancelled internally"
                    return (
                        _cancelled_cleanup_result(
                            motion_attempted=motion_attempted,
                            detail=detail,
                        ),
                        interrupted,
                    )
                return cleanup_task.result(), interrupted
            if interrupted is None:
                interrupted = exc


async def _finalize_capture(
    artifact: dict[str, Any],
    options: DifferentialCaptureOptions,
    *,
    bridge: RosBridgeClient | None,
    config: AppConfig | None,
    watch_ids: list[int],
    heartbeat: asyncio.Task[None] | None,
    motion_attempted: bool,
    output_reservation: _OutputReservation | None = None,
) -> dict[str, Any]:
    interrupted: asyncio.CancelledError | None = None
    if bridge is not None and config is not None:
        runtime_identity = artifact.get("runtime_identity")
        primary_runtime_identity = (
            runtime_identity.get("ros_middleware") if isinstance(runtime_identity, dict) else None
        )
        cleanup_task = asyncio.create_task(
            _safe_cleanup_live_capture(
                bridge,
                config,
                watch_ids,
                heartbeat,
                motion_attempted=motion_attempted,
                primary_runtime_identity=primary_runtime_identity,
            )
        )
        cleanup, interrupted = await _await_cleanup_despite_cancellation(
            cleanup_task,
            motion_attempted=motion_attempted,
        )
        artifact["cleanup"] = cleanup
        artifact["final_halt"] = cleanup.get("final_halt")
        if cleanup["status"] != "PASS":
            artifact["overall_before_cleanup"] = artifact.get("overall")
            artifact["overall"] = "cleanup_failed"
        _record_end_generation(artifact)
    identity = artifact.get("runtime_identity")
    if isinstance(identity, dict) and identity.get("source_root"):
        post_cleanup_continuity = _capture_input_continuity(identity)
        artifact["post_cleanup_input_continuity"] = post_cleanup_continuity
        if post_cleanup_continuity["status"] != "PASS" and artifact.get("overall") == "captured":
            artifact["overall"] = "insufficient_evidence"
    artifact["finished_at"] = _utc_now()
    try:
        persisted = _write_capture_artifact(
            options.output,
            artifact,
        )
    except BaseException:
        if not motion_attempted and output_reservation is not None:
            output_reservation.release(discard_marker=True)
        raise
    if output_reservation is not None:
        output_reservation.release()
    if interrupted is not None:
        raise interrupted
    return persisted


@dataclass(slots=True)
class _CaptureResources:
    stage: str = "prepare"
    config: AppConfig | None = None
    bridge: RosBridgeClient | None = None
    watch_ids: list[int] = dataclass_field(default_factory=list)
    heartbeat: asyncio.Task[None] | None = None
    motion_attempted: bool = False


async def _capture_live_path(
    artifact: dict[str, Any],
    options: DifferentialCaptureOptions,
    *,
    config_path: Path,
    config: AppConfig,
    bound_action: dict[str, Any],
    goal: CanonicalGoal,
    identity: dict[str, Any],
    pose_observations: _PoseObservationRecorder,
    resources: _CaptureResources,
) -> None:
    resources.stage = "bridge_start"
    bridge = RosBridgeClient(domain_id=int(_effective_ros_domain(config)))
    resources.bridge = bridge
    await bridge.configure_safety(
        watchdog_s=6.0,
        cmd_vel_topic=config.vehicle.cmd_vel_topic,
        stamped=config.vehicle.cmd_vel_stamped,
        pose_jump_threshold_m=config.vehicle.pose_jump_threshold_m,
        pose_jump_window_s=config.vehicle.pose_jump_window_s,
    )
    await bridge.start()
    resources.stage = "live_identity"
    bridge_identity = await bridge.runtime_identity(pin=True)
    identity["ros_middleware"] = bridge_identity.to_payload()
    _apply_runtime_fingerprint(identity)
    await _enrich_live_identity(bridge, identity)
    calibration = _load_calibration(options, identity)
    _record_ground_truth_calibration(artifact, identity, calibration)
    identity_failures = _runtime_identity_failures(identity)
    if identity_failures:
        _record_capture_gate_failure(
            artifact,
            check_id="runtime_identity_gate",
            detail="Live runtime identity is incomplete or not release-clean.",
            failures=identity_failures,
        )
        return
    resources.stage = "topic_watch"
    odom_topic = str(identity["controller_odom_topic"])
    artifact["topic_stream_contract"] = {
        key: {"topic": topic, "message_type": message_type, "qos_profile": qos_profile}
        for key, topic, message_type, qos_profile in _topic_stream_specs(
            options, odom_topic=odom_topic
        )
    }
    recorders = {
        key: _TopicRecorder() for key in ("clock", "amcl", "odom", "action_status", "ground_truth")
    }
    resources.watch_ids = await _watch_topics(
        bridge,
        recorders,
        options,
        odom_topic=odom_topic,
    )
    resources.heartbeat = asyncio.create_task(_heartbeat(bridge))
    resources.stage = "start_gate"
    t0 = await _collect_start_state(
        bridge,
        config,
        recorders,
        options,
        calibration,
        pose_observations,
    )
    artifact["t0_scenario_start"] = t0
    if t0["status"] != "PASS":
        _record_capture_gate_failure(
            artifact,
            check_id="pairing_start_gate",
            detail="Start state was not eligible for paired execution.",
            failures=t0["failures"],
        )
        return
    resources.stage = "motion_dispatch"
    resources.motion_attempted = True
    await _record_live_evidence(
        artifact,
        options,
        bridge,
        config,
        config_path,
        goal,
        bound_action,
        calibration,
        recorders,
        pose_observations,
    )


async def capture_navigation_differential(
    options: DifferentialCaptureOptions,
) -> dict[str, Any]:
    """Capture one R1 or R2 run and persist every valid request outcome."""
    options = DifferentialCaptureOptions.model_validate(options)
    artifact = _base_artifact(options)
    output_reservation = _OutputReservation.acquire(options.output)
    resources = _CaptureResources()
    pose_observations = _PoseObservationRecorder()
    cancelled: asyncio.CancelledError | None = None
    try:
        config_path, config, bound_action, goal, identity, target_binding = _prepare_capture(
            options
        )
        resources.config = config
        artifact["runtime_identity"] = identity
        artifact["canonical_goal"] = goal.model_dump(mode="json")
        artifact["target_binding"] = (
            target_binding.model_dump(mode="json")
            if isinstance(target_binding, TargetBinding)
            else None
        )
        if not _complete_without_live_bridge(options, config, identity, artifact):
            await _capture_live_path(
                artifact,
                options,
                config_path=config_path,
                config=config,
                bound_action=bound_action,
                goal=goal,
                identity=identity,
                pose_observations=pose_observations,
                resources=resources,
            )
    except asyncio.CancelledError as exc:
        cancelled = exc
        artifact["overall"] = "failed"
        artifact["failure"] = {
            "type": type(exc).__name__,
            "stage": resources.stage,
            "detail": "Capture task was cancelled.",
        }
    except Exception as exc:
        artifact["overall"] = "failed"
        artifact["failure"] = {
            "type": type(exc).__name__,
            "stage": resources.stage,
            "detail": str(exc),
        }
    finally:
        artifact["pose_observations"] = pose_observations.snapshot()
        artifact = await _finalize_capture(
            artifact,
            options,
            bridge=resources.bridge,
            config=resources.config,
            watch_ids=resources.watch_ids,
            heartbeat=resources.heartbeat,
            motion_attempted=resources.motion_attempted,
            output_reservation=output_reservation,
        )
    if cancelled is not None:
        raise cancelled
    return artifact


def _artifact_pose(artifact: dict[str, Any], key: str) -> Pose2D | None:
    value = artifact.get(key)
    if not isinstance(value, dict):
        return None
    try:
        return Pose2D.model_validate(value)
    except ValueError:
        return None


def _insufficient_report(pair_id: object, detail: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "pair_id": str(pair_id) if pair_id else None,
        "included": False,
        "classifications": [PairClassification.INSUFFICIENT_EVIDENCE],
        "pairing_gate": None,
        "detail": detail,
    }


def _validated_artifact(payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return DifferentialArtifact.model_validate(payload).model_dump(mode="json")
    except ValueError:
        return None


def _actual_dispatch_goal(artifact: dict[str, Any]) -> CanonicalGoal | None:
    timeline = artifact.get("t1_goal_dispatch")
    if not isinstance(timeline, dict) or timeline.get("status") != "PASS":
        return None
    value = timeline.get("actual_goal")
    try:
        return CanonicalGoal.model_validate(value)
    except ValueError:
        return None


def _target_binding_failures(
    artifact: dict[str, Any],
    goal: CanonicalGoal,
) -> list[str]:
    try:
        binding = TargetBinding.model_validate(artifact.get("target_binding"))
    except ValueError:
        return ["target_binding_invalid"]
    identity = artifact.get("runtime_identity")
    canonical_goal_sha256 = _canonical_json_sha256(goal.model_dump(mode="json"))
    failures: list[str] = []
    if binding.canonical_goal_sha256 != canonical_goal_sha256:
        failures.append("target_binding_goal_digest_mismatch")
    if binding.locations_sha256 != (
        identity.get("locations_sha256") if isinstance(identity, dict) else None
    ):
        failures.append("target_binding_locations_mismatch")
    if binding.frame_id != goal.frame_id.lstrip("/") or binding.pose != Pose2D(
        x=goal.x,
        y=goal.y,
        yaw=goal.yaw,
    ):
        failures.append("target_binding_goal_mismatch")
    return failures


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _within_limit(value: object, maximum: float, *, absolute: bool = False) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    numeric = float(value)
    if not math.isfinite(numeric):
        return False
    return (abs(numeric) if absolute else numeric) <= maximum


def _pose_payload(value: object) -> Pose2D | None:
    if not isinstance(value, dict):
        return None
    try:
        return Pose2D(x=value["x"], y=value["y"], yaw=value["yaw"])
    except (KeyError, TypeError, ValueError):
        return None


def _valid_pose_payload(value: object) -> bool:
    return _pose_payload(value) is not None


def _valid_state_clock(state: dict[str, Any]) -> bool:
    values = state.get("clock_samples_ns")
    evidence = state.get("clock_evidence")
    cutoff_ns = state.get("cutoff_host_monotonic_ns")
    evaluated_ns = state.get("evaluated_host_monotonic_ns")
    if (
        not isinstance(values, list)
        or len(values) < 2
        or any(type(value) is not int for value in values)
        or not isinstance(evidence, list)
        or len(evidence) != len(values)
        or type(cutoff_ns) is not int
        or type(evaluated_ns) is not int
        or cutoff_ns > evaluated_ns
    ):
        return False
    evidence_hosts: list[int] = []
    evidence_clocks: list[int] = []
    for item in evidence:
        if (
            not isinstance(item, dict)
            or type(item.get("host_monotonic_ns")) is not int
            or type(item.get("clock_ns")) is not int
        ):
            return False
        evidence_hosts.append(int(item["host_monotonic_ns"]))
        evidence_clocks.append(int(item["clock_ns"]))
    return (
        evidence_clocks == values
        and all(cutoff_ns <= host <= evaluated_ns for host in evidence_hosts)
        and values[-1] > values[0]
        and all(right >= left for left, right in zip(values, values[1:], strict=False))
        and all(
            right >= left for left, right in zip(evidence_hosts, evidence_hosts[1:], strict=False)
        )
        and state.get("clock_advancing") is True
        and state.get("clock_backwards") is False
    )


def _valid_covariance(value: object, maximum: float) -> bool:
    return _within_limit(value, maximum) and isinstance(value, (int, float)) and value >= 0


def _valid_topic_source(
    value: object,
    *,
    max_age_ns: int,
    max_future_ns: int,
    cutoff_ns: int | None,
    evaluated_ns: int | None,
) -> bool:
    if not isinstance(value, dict) or value.get("fresh") is not True:
        return False
    integer_fields = (
        "host_monotonic_ns",
        "host_age_ns",
        "source_stamp_ns",
        "sample_clock_ns",
        "capture_clock_ns",
        "source_age_ns",
    )
    if any(type(value.get(field)) is not int for field in integer_fields):
        return False
    host_age_ns = int(value["host_age_ns"])
    source_age_ns = int(value["source_age_ns"])
    host_ns = int(value["host_monotonic_ns"])
    return (
        cutoff_ns is not None
        and evaluated_ns is not None
        and cutoff_ns <= host_ns <= evaluated_ns
        and host_age_ns == evaluated_ns - host_ns
        and 0 <= host_age_ns <= max_age_ns
        and -max_future_ns <= source_age_ns <= max_age_ns
        and int(value["capture_clock_ns"]) - int(value["source_stamp_ns"]) == source_age_ns
    )


def _valid_action_source(
    value: object,
    *,
    max_age_ns: int,
    min_observation_ns: int,
    cutoff_ns: int | None,
    evaluated_ns: int | None,
) -> bool:
    if isinstance(value, dict) and value.get("observation") == "no_status_observed":
        return (
            set(value)
            == {
                "fresh",
                "observation",
                "cutoff_host_monotonic_ns",
                "evaluated_host_monotonic_ns",
            }
            and value.get("fresh") is True
            and cutoff_ns is not None
            and evaluated_ns is not None
            and value.get("cutoff_host_monotonic_ns") == cutoff_ns
            and cutoff_ns <= evaluated_ns
            and evaluated_ns - cutoff_ns >= min_observation_ns
        )
    host_ns = value.get("host_monotonic_ns") if isinstance(value, dict) else None
    host_age_ns = value.get("host_age_ns") if isinstance(value, dict) else None
    return (
        isinstance(value, dict)
        and value.get("fresh") is True
        and value.get("schema_valid") is True
        and type(host_ns) is int
        and type(host_age_ns) is int
        and cutoff_ns is not None
        and evaluated_ns is not None
        and cutoff_ns <= host_ns <= evaluated_ns
        and host_age_ns == evaluated_ns - host_ns
        and 0 <= host_age_ns <= max_age_ns
    )


def _ground_truth_state_checks(
    state: dict[str, Any],
    calibration: GroundTruthCalibration | None,
    *,
    max_age_ns: int,
    max_future_ns: int,
    cutoff_ns: int | None,
    evaluated_ns: int | None,
    label: str,
) -> list[tuple[bool, str]]:
    if calibration is None:
        return [
            (
                state.get("ground_truth_required") is not False
                or state.get("ground_truth_source") is not None
                or state.get("ground_truth_source_frame_id") is not None
                or state.get("ground_truth_world_pose") is not None
                or state.get("ground_truth_map_pose") is not None,
                f"{label}_unverified_ground_truth_present",
            )
        ]
    source_frame = state.get("ground_truth_source_frame_id")
    world_pose = _pose_payload(state.get("ground_truth_world_pose"))
    map_pose = _pose_payload(state.get("ground_truth_map_pose"))
    expected_map_pose = calibration.world_to_map(world_pose) if world_pose is not None else None
    return [
        (
            state.get("ground_truth_required") is not True,
            f"{label}_ground_truth_requirement",
        ),
        (
            not _valid_topic_source(
                state.get("ground_truth_source"),
                max_age_ns=max_age_ns,
                max_future_ns=max_future_ns,
                cutoff_ns=cutoff_ns,
                evaluated_ns=evaluated_ns,
            ),
            f"{label}_ground_truth_source",
        ),
        (
            not isinstance(source_frame, str)
            or source_frame.lstrip("/") != str(calibration.world_frame_id).lstrip("/"),
            f"{label}_ground_truth_frame",
        ),
        (
            world_pose is None
            or map_pose is None
            or not _pose_matches(expected_map_pose, map_pose),
            f"{label}_ground_truth_pose",
        ),
    ]


def _state_evidence_failures(
    state: object,
    *,
    contract: DifferentialMeasurementContract,
    ground_truth_calibration: GroundTruthCalibration | None,
    expected_epoch: str | None,
    label: str,
) -> list[str]:
    if not isinstance(state, dict):
        return [f"{label}_missing"]
    max_age_ns = int(contract.max_topic_age_s * 1_000_000_000)
    max_future_ns = int(contract.sample_interval_s * 1_000_000_000)
    min_observation_ns = int(contract.preflight_sample_s * 1_000_000_000)
    linear_velocity = state.get("linear_velocity_mps")
    angular_velocity = state.get("angular_velocity_rps")
    evaluated_ns = state.get("evaluated_host_monotonic_ns")
    cutoff_ns = state.get("cutoff_host_monotonic_ns")
    typed_cutoff_ns = cutoff_ns if type(cutoff_ns) is int else None
    typed_evaluated_ns = evaluated_ns if type(evaluated_ns) is int else None
    checks: list[tuple[bool, str]] = [
        (
            state.get("status") != "PASS" or state.get("failures") not in ([], ()),
            f"{label}_status",
        ),
        (not _valid_pose_payload(state.get("map_to_base")), f"{label}_map_pose"),
        (not _valid_pose_payload(state.get("amcl_pose")), f"{label}_amcl_pose"),
        (not _valid_pose_payload(state.get("odom_pose")), f"{label}_odom_pose"),
        (
            not _valid_covariance(state.get("amcl_covariance_xy"), contract.max_covariance_xy),
            f"{label}_amcl_covariance",
        ),
        (state.get("simulation_epoch") != expected_epoch, f"{label}_simulation_epoch"),
        (
            not _within_limit(linear_velocity, contract.max_start_speed_mps),
            f"{label}_linear_velocity",
        ),
        (
            not _within_limit(
                angular_velocity,
                contract.max_start_yaw_rate_rps,
                absolute=True,
            ),
            f"{label}_angular_velocity",
        ),
        (state.get("stationary") is not True, f"{label}_stationary"),
        (state.get("active_goal_ids") not in ([], ()), f"{label}_active_goal"),
        (not _valid_state_clock(state), f"{label}_clock"),
        (
            not _valid_topic_source(
                state.get("amcl_source"),
                max_age_ns=max_age_ns,
                max_future_ns=max_future_ns,
                cutoff_ns=typed_cutoff_ns,
                evaluated_ns=typed_evaluated_ns,
            ),
            f"{label}_amcl_source",
        ),
        (
            not _valid_topic_source(
                state.get("odom_source"),
                max_age_ns=max_age_ns,
                max_future_ns=max_future_ns,
                cutoff_ns=typed_cutoff_ns,
                evaluated_ns=typed_evaluated_ns,
            ),
            f"{label}_odom_source",
        ),
        (
            not _valid_action_source(
                state.get("action_status_source"),
                max_age_ns=max_age_ns,
                min_observation_ns=min_observation_ns,
                cutoff_ns=typed_cutoff_ns,
                evaluated_ns=typed_evaluated_ns,
            ),
            f"{label}_action_status_source",
        ),
        (
            type(evaluated_ns) is not int or type(cutoff_ns) is not int or cutoff_ns > evaluated_ns,
            f"{label}_evaluation_window",
        ),
    ]
    checks.extend(
        _ground_truth_state_checks(
            state,
            ground_truth_calibration,
            max_age_ns=max_age_ns,
            max_future_ns=max_future_ns,
            cutoff_ns=typed_cutoff_ns,
            evaluated_ns=typed_evaluated_ns,
            label=label,
        )
    )
    return [failure for failed, failure in checks if failed]


def _evidence_items(value: object) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        return [], False
    return cast(list[dict[str, Any]], value), True


def _valid_map_window_sample(sample: dict[str, Any]) -> bool:
    return (
        sample.get("fresh") is True
        and _valid_pose_payload(sample.get("pose"))
        and type(sample.get("capture_clock_ns")) is int
    )


def _valid_ground_truth_window_sample(
    sample: dict[str, Any],
    calibration: GroundTruthCalibration,
) -> bool:
    source_frame = sample.get("source_frame_id")
    world_pose = _pose_payload(sample.get("world_pose"))
    map_pose = _pose_payload(sample.get("map_pose"))
    expected_map_pose = calibration.world_to_map(world_pose) if world_pose is not None else None
    return (
        sample.get("fresh") is True
        and type(sample.get("source_stamp_ns")) is int
        and isinstance(source_frame, str)
        and source_frame.lstrip("/") == str(calibration.world_frame_id).lstrip("/")
        and _pose_matches(expected_map_pose, map_pose)
    )


def _clock_window_failures(
    samples: object,
    *,
    start_host_ns: int | None,
    end_host_ns: int | None,
    start_clock_ns: int | None,
    end_clock_ns: int | None,
    coverage_slack_ns: int,
) -> list[str]:
    items, schema_valid = _evidence_items(samples)
    if not schema_valid or len(items) < 2:
        return ["final_clock_samples"]
    hosts: list[int] = []
    clocks: list[int] = []
    for item in items:
        host_ns = item.get("host_monotonic_ns")
        clock_ns = item.get("clock_ns")
        if type(host_ns) is not int or type(clock_ns) is not int:
            return ["final_clock_sample_schema"]
        hosts.append(host_ns)
        clocks.append(clock_ns)
    failures: list[str] = []
    if (
        start_host_ns is None
        or end_host_ns is None
        or any(host < start_host_ns or host > end_host_ns for host in hosts)
        or any(right < left for left, right in zip(hosts, hosts[1:], strict=False))
    ):
        failures.append("final_clock_host_order")
    if any(right < left for left, right in zip(clocks, clocks[1:], strict=False)):
        failures.append("final_clock_source_order")
    if (
        start_clock_ns is None
        or end_clock_ns is None
        or clocks[0] > start_clock_ns + coverage_slack_ns
        or clocks[-1] < end_clock_ns - coverage_slack_ns
    ):
        failures.append("final_clock_coverage")
    return failures


def _pose_matches(left: Pose2D | None, right: Pose2D | None, *, tolerance: float = 1e-9) -> bool:
    if left is None or right is None:
        return False
    yaw_delta = math.atan2(math.sin(left.yaw - right.yaw), math.cos(left.yaw - right.yaw))
    return (
        math.hypot(left.x - right.x, left.y - right.y) <= tolerance and abs(yaw_delta) <= tolerance
    )


def _derived_median(samples: list[dict[str, Any]], *, pose_key: str) -> Pose2D | None:
    return _median_pose(
        [{"pose": sample[pose_key]} for sample in samples if isinstance(sample.get(pose_key), dict)]
    )


def _ground_truth_requirement(
    artifact: dict[str, Any],
    *,
    contract: DifferentialMeasurementContract,
    identity: dict[str, Any],
) -> tuple[GroundTruthCalibration | None, list[str]]:
    raw = artifact.get("ground_truth_calibration")
    if raw is None:
        binding_present = any(
            value is not None
            for value in (
                contract.ground_truth_topic,
                contract.ground_truth_type,
                contract.ground_truth_calibration_sha256,
            )
        )
        return None, (["ground_truth_calibration_missing"] if binding_present else [])
    try:
        calibration = GroundTruthCalibration.model_validate(raw)
    except ValueError:
        return None, ["ground_truth_calibration_invalid"]
    effective_digest = identity.get("ground_truth_calibration_effective_sha256")
    if not _valid_sha256(effective_digest) or effective_digest != _calibration_payload_sha256(
        calibration
    ):
        return None, ["ground_truth_calibration_identity_binding"]
    if calibration.status != "VERIFIED":
        return None, []
    calibration_frame = (calibration.map_frame_id or "").lstrip("/")
    configured_frame = str(identity.get("site_map_frame") or "").lstrip("/")
    live_frame = str(identity.get("live_map_frame") or "").lstrip("/")
    checks = (
        (
            contract.ground_truth_topic is None
            or contract.ground_truth_type is None
            or contract.ground_truth_calibration_sha256 != _calibration_payload_sha256(calibration),
            "ground_truth_contract_binding",
        ),
        (
            calibration.residual_m is None
            or calibration.residual_m > contract.max_calibration_residual_m,
            "ground_truth_calibration_residual",
        ),
        (
            calibration.scene_sha256 != identity.get("scene_sha256")
            or calibration.scene_sha256 != identity.get("live_scene_sha256"),
            "ground_truth_scene_identity",
        ),
        (
            calibration.map_sha256 != identity.get("site_map_sha256")
            or calibration.map_sha256 != identity.get("live_map_sha256"),
            "ground_truth_map_identity",
        ),
        (
            not calibration_frame
            or calibration_frame != configured_frame
            or calibration_frame != live_frame,
            "ground_truth_map_frame",
        ),
    )
    failures = [failure for failed, failure in checks if failed]
    return calibration, failures


def _validated_final_window_samples(
    window: dict[str, Any],
    *,
    contract: DifferentialMeasurementContract,
    ground_truth_calibration: GroundTruthCalibration | None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[str],
]:
    raw_map, map_schema = _evidence_items(window.get("map_pose_samples"))
    raw_amcl, amcl_schema = _evidence_items(window.get("valid_amcl_samples"))
    raw_odom, odom_schema = _evidence_items(window.get("valid_odom_samples"))
    raw_ground_truth, ground_truth_schema = _evidence_items(
        window.get("verified_ground_truth_samples")
    )
    map_samples = [sample for sample in raw_map if _valid_map_window_sample(sample)]
    amcl_samples = _valid_final_amcl(
        raw_amcl,
        max_covariance_xy=contract.max_covariance_xy,
    )
    odom_samples = _valid_final_odom(raw_odom)
    ground_truth_samples = (
        [
            sample
            for sample in raw_ground_truth
            if _valid_ground_truth_window_sample(sample, ground_truth_calibration)
        ]
        if ground_truth_calibration is not None
        else []
    )
    map_clock_samples = [
        {**sample, "source_stamp_ns": sample["capture_clock_ns"]} for sample in map_samples
    ]
    sample_checks: tuple[tuple[bool, str], ...] = (
        (
            not map_schema or len(map_samples) != len(raw_map),
            "final_window_map_sample_schema",
        ),
        (
            not amcl_schema or len(amcl_samples) != len(raw_amcl),
            "final_window_amcl_sample_schema",
        ),
        (
            not odom_schema or len(odom_samples) != len(raw_odom),
            "final_window_odom_sample_schema",
        ),
        (
            not ground_truth_schema or len(ground_truth_samples) != len(raw_ground_truth),
            "final_window_ground_truth_sample_schema",
        ),
    )
    return (
        map_samples,
        map_clock_samples,
        amcl_samples,
        odom_samples,
        ground_truth_samples,
        [failure for failed, failure in sample_checks if failed],
    )


def _final_window_coverage_failures(
    window: dict[str, Any],
    *,
    contract: DifferentialMeasurementContract,
    map_clock_samples: list[dict[str, Any]],
    amcl_samples: list[dict[str, Any]],
    odom_samples: list[dict[str, Any]],
    ground_truth_samples: list[dict[str, Any]],
    ground_truth_required: bool,
    required_duration_ns: int,
    coverage_slack_ns: int,
) -> list[str]:
    start_host_ns = window.get("start_host_monotonic_ns")
    end_host_ns = window.get("end_host_monotonic_ns")
    start_clock_ns = window.get("start_clock_ns")
    end_clock_ns = window.get("end_clock_ns")
    typed_start_clock = start_clock_ns if type(start_clock_ns) is int else None
    typed_end_clock = end_clock_ns if type(end_clock_ns) is int else None
    failures = _clock_window_failures(
        window.get("clock_samples"),
        start_host_ns=start_host_ns if type(start_host_ns) is int else None,
        end_host_ns=end_host_ns if type(end_host_ns) is int else None,
        start_clock_ns=typed_start_clock,
        end_clock_ns=typed_end_clock,
        coverage_slack_ns=coverage_slack_ns,
    )
    for name, samples, minimum in (
        ("map", map_clock_samples, contract.min_final_pose_samples),
        ("amcl", amcl_samples, contract.min_final_state_samples),
        ("odom", odom_samples, contract.min_final_state_samples),
    ):
        failures.extend(
            _stream_window_failures(
                name,
                samples,
                min_samples=minimum,
                start_clock_ns=typed_start_clock,
                end_clock_ns=typed_end_clock,
                required_duration_ns=required_duration_ns,
                coverage_slack_ns=coverage_slack_ns,
            )
        )
    if ground_truth_required:
        failures.extend(
            _stream_window_failures(
                "ground_truth",
                ground_truth_samples,
                min_samples=contract.min_final_ground_truth_samples,
                start_clock_ns=typed_start_clock,
                end_clock_ns=typed_end_clock,
                required_duration_ns=required_duration_ns,
                coverage_slack_ns=coverage_slack_ns,
            )
        )
    return failures


def _final_window_evidence_failures(
    window: object,
    *,
    contract: DifferentialMeasurementContract,
    ground_truth_calibration: GroundTruthCalibration | None,
    final_map_pose: Pose2D | None,
    final_ground_truth_pose: Pose2D | None,
) -> list[str]:
    if not isinstance(window, dict):
        return ["final_window_missing"]
    start_host_ns = window.get("start_host_monotonic_ns")
    end_host_ns = window.get("end_host_monotonic_ns")
    terminal_host_ns = window.get("terminal_host_monotonic_ns")
    start_clock_ns = window.get("start_clock_ns")
    end_clock_ns = window.get("end_clock_ns")
    required_duration_ns = int(contract.final_sample_s * 1_000_000_000)
    scheduled_delay_ns = int(contract.final_window_start_delay_s * 1_000_000_000)
    schedule_release_ns = window.get("schedule_release_host_monotonic_ns")
    expected_slack_ns = int(
        min(contract.max_topic_age_s, contract.sample_interval_s * 2.0) * 1_000_000_000
    )
    ground_truth_required = ground_truth_calibration is not None
    (
        map_samples,
        map_clock_samples,
        amcl_samples,
        odom_samples,
        ground_truth_samples,
        sample_failures,
    ) = _validated_final_window_samples(
        window,
        contract=contract,
        ground_truth_calibration=ground_truth_calibration,
    )
    computed_stationary = bool(odom_samples) and all(
        float(sample["linear_velocity_mps"]) <= contract.max_start_speed_mps
        and abs(float(sample["angular_velocity_rps"])) <= contract.max_start_yaw_rate_rps
        for sample in odom_samples
    )
    checks = (
        (
            window.get("status") != "PASS" or window.get("failures") not in ([], ()),
            "final_window_status",
        ),
        (
            type(start_host_ns) is not int
            or type(end_host_ns) is not int
            or type(terminal_host_ns) is not int
            or start_host_ns < terminal_host_ns
            or end_host_ns < start_host_ns,
            "final_window_terminal_binding",
        ),
        (
            scheduled_delay_ns > 0
            and (
                type(start_host_ns) is not int
                or type(terminal_host_ns) is not int
                or type(schedule_release_ns) is not int
                or window.get("scheduled_start_delay_ns") != scheduled_delay_ns
                or schedule_release_ns < terminal_host_ns + scheduled_delay_ns
                or schedule_release_ns > start_host_ns
                or start_host_ns - (terminal_host_ns + scheduled_delay_ns)
                > int(contract.sample_interval_s * 1_000_000_000)
            ),
            "final_window_terminal_schedule",
        ),
        (
            type(start_clock_ns) is not int
            or type(end_clock_ns) is not int
            or end_clock_ns - start_clock_ns < required_duration_ns
            or window.get("required_duration_ns") != required_duration_ns
            or window.get("clock_backwards") is not False,
            "final_window_ros_duration",
        ),
        (window.get("coverage_slack_ns") != expected_slack_ns, "final_window_coverage_contract"),
        (len(map_samples) < contract.min_final_pose_samples, "final_window_map_samples"),
        (
            window.get("stationary") is not True or not computed_stationary,
            "final_window_stationary",
        ),
        (
            window.get("ground_truth_required") is not ground_truth_required,
            "final_window_ground_truth_requirement",
        ),
        (
            not _pose_matches(_derived_median(map_samples, pose_key="pose"), final_map_pose),
            "final_map_median_not_derived",
        ),
        (
            ground_truth_required
            and not _pose_matches(
                _derived_median(ground_truth_samples, pose_key="map_pose"),
                final_ground_truth_pose,
            ),
            "final_ground_truth_median_not_derived",
        ),
        (
            not ground_truth_required
            and (bool(ground_truth_samples) or final_ground_truth_pose is not None),
            "unverified_ground_truth_present",
        ),
    )
    failures = [*sample_failures, *(failure for failed, failure in checks if failed)]
    failures.extend(
        _final_window_coverage_failures(
            window,
            contract=contract,
            map_clock_samples=map_clock_samples,
            amcl_samples=amcl_samples,
            odom_samples=odom_samples,
            ground_truth_samples=ground_truth_samples,
            ground_truth_required=ground_truth_required,
            required_duration_ns=required_duration_ns,
            coverage_slack_ns=expected_slack_ns,
        )
    )
    return list(dict.fromkeys(failures))


def _cleanup_evidence_failures(
    cleanup: object,
    *,
    primary_runtime_identity: object = None,
) -> list[str]:
    if not isinstance(cleanup, dict):
        return ["cleanup_missing"]
    halt = cleanup.get("final_halt")
    unwatch = cleanup.get("unwatch")
    shutdown = cleanup.get("bridge_shutdown")
    halt_confirmed = (
        isinstance(halt, dict)
        and halt.get("status") == "PASS"
        and halt.get("zero_velocity_command_published") is True
        and (
            halt.get("navigation_cancel_requested") is not True
            or halt.get("navigation_cancel_acknowledged") is True
        )
    )
    checks = (
        (
            cleanup.get("status") != "PASS" or cleanup.get("failures") not in ([], ()),
            "cleanup_status",
        ),
        (not halt_confirmed, "cleanup_final_halt_evidence"),
        (
            not isinstance(unwatch, dict)
            or unwatch.get("status") != "PASS"
            or unwatch.get("failures") not in ([], ()),
            "cleanup_unwatch",
        ),
        (
            not isinstance(shutdown, dict) or shutdown.get("status") != "PASS",
            "cleanup_bridge_shutdown",
        ),
    )
    failures = [failure for failed, failure in checks if failed]
    rescue = cleanup.get("rescue_bridge")
    if rescue is None:
        return failures
    primary_halt = cleanup.get("primary_halt")
    if not isinstance(rescue, dict):
        return [*failures, "cleanup_rescue_schema"]
    try:
        primary = BridgeRuntimeIdentity.from_payload(primary_runtime_identity)
        replacement = BridgeRuntimeIdentity.from_payload(rescue.get("runtime_identity"))
    except BridgeError:
        return [*failures, "cleanup_rescue_identity"]
    rescue_halt = rescue.get("final_halt")
    rescue_shutdown = rescue.get("bridge_shutdown")
    compatible = (
        replacement.pid != primary.pid
        and (
            replacement.boot_id != primary.boot_id
            or replacement.process_start_ticks != primary.process_start_ticks
        )
        and replacement.launch_nonce != primary.launch_nonce
        and _pairable_ros_middleware_identity(replacement.to_payload())
        == _pairable_ros_middleware_identity(primary.to_payload())
    )
    rescue_checks = (
        (
            not isinstance(primary_halt, dict) or primary_halt.get("status") != "FAIL",
            "cleanup_rescue_without_primary_failure",
        ),
        (rescue.get("status") != "PASS", "cleanup_rescue_status"),
        (rescue.get("failures") not in ([], ()), "cleanup_rescue_failures"),
        (
            rescue.get("identity_compatible") is not True or not compatible,
            "cleanup_rescue_identity",
        ),
        (rescue_halt != halt, "cleanup_rescue_halt_binding"),
        (
            not isinstance(rescue_shutdown, dict) or rescue_shutdown.get("status") != "PASS",
            "cleanup_rescue_shutdown",
        ),
    )
    failures.extend(failure for failed, failure in rescue_checks if failed)
    return failures


def _input_continuity_evidence_failures(artifact: dict[str, Any]) -> list[str]:
    identity = artifact.get("runtime_identity")
    timeline = artifact.get("t1_goal_dispatch")
    state = timeline.get("state_before_forward") if isinstance(timeline, dict) else None
    pre = state.get("input_continuity") if isinstance(state, dict) else None
    post = artifact.get("post_cleanup_input_continuity")
    if not isinstance(identity, dict):
        return ["input_continuity_runtime_identity"]
    expected_hashes = {
        field: identity.get(field)
        for field in ("config_sha256", "locations_sha256", "bridge_script_sha256")
    }
    failures: list[str] = []
    for label, evidence in (("pre_dispatch", pre), ("post_cleanup", post)):
        if not isinstance(evidence, dict):
            failures.append(f"{label}_input_continuity_missing")
            continue
        observed_ns = evidence.get("observed_host_monotonic_ns")
        checks = (
            (evidence.get("status") != "PASS", f"{label}_input_continuity_status"),
            (evidence.get("failures") not in ([], ()), f"{label}_input_continuity_failures"),
            (
                evidence.get("observed_hashes") != expected_hashes,
                f"{label}_input_continuity_hashes",
            ),
            (
                evidence.get("observed_git_sha") != identity.get("git_sha"),
                f"{label}_input_continuity_revision",
            ),
            (
                evidence.get("observed_git_dirty") is not identity.get("git_dirty"),
                f"{label}_input_continuity_dirty",
            ),
            (type(observed_ns) is not int or observed_ns < 0, f"{label}_input_continuity_time"),
        )
        failures.extend(failure for failed, failure in checks if failed)
    return failures


def _runtime_stack_continuity_failures(artifact: dict[str, Any]) -> list[str]:
    identity = artifact.get("runtime_identity")
    timeline = artifact.get("t1_goal_dispatch")
    state = timeline.get("state_before_forward") if isinstance(timeline, dict) else None
    pre = state.get("runtime_stack_checkpoint") if isinstance(state, dict) else None
    post = artifact.get("post_final_window_runtime_stack_checkpoint")
    if not isinstance(identity, dict):
        return ["runtime_stack_identity_missing"]
    expected = _runtime_stack_projection(identity)
    failures: list[str] = []
    for label, checkpoint in (("pre_dispatch", pre), ("post_final_window", post)):
        if not isinstance(checkpoint, dict):
            failures.append(f"{label}_runtime_stack_missing")
            continue
        observed_ns = checkpoint.get("observed_host_monotonic_ns")
        checks = (
            (checkpoint.get("label") != label, f"{label}_runtime_stack_label"),
            (checkpoint.get("status") != "PASS", f"{label}_runtime_stack_status"),
            (checkpoint.get("failures") not in ([], ()), f"{label}_runtime_stack_failures"),
            (checkpoint.get("expected") != expected, f"{label}_runtime_stack_expected"),
            (checkpoint.get("observed") != expected, f"{label}_runtime_stack_changed"),
            (type(observed_ns) is not int or observed_ns < 0, f"{label}_runtime_stack_time"),
        )
        failures.extend(failure for failed, failure in checks if failed)

    dispatch_ns = (
        timeline.get("nav_send_forwarded_host_monotonic_ns") if isinstance(timeline, dict) else None
    )
    pre_ns = pre.get("observed_host_monotonic_ns") if isinstance(pre, dict) else None
    window = artifact.get("final_observation_window")
    window_end_ns = window.get("end_host_monotonic_ns") if isinstance(window, dict) else None
    post_ns = post.get("observed_host_monotonic_ns") if isinstance(post, dict) else None
    if not (type(pre_ns) is int and type(dispatch_ns) is int and pre_ns <= dispatch_ns):
        failures.append("pre_dispatch_runtime_stack_timing")
    if not (type(window_end_ns) is int and type(post_ns) is int and window_end_ns <= post_ns):
        failures.append("post_final_window_runtime_stack_timing")
    return list(dict.fromkeys(failures))


def _valid_map_identity_payload(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    geometry = value.get("geometry")
    return (
        value.get("algorithm") == "sha256-occupancy-grid-v1"
        and _valid_sha256(value.get("digest"))
        and isinstance(value.get("frame_id"), str)
        and bool(value.get("frame_id"))
        and isinstance(value.get("source"), str)
        and bool(value.get("source"))
        and isinstance(geometry, dict)
        and type(geometry.get("width")) is int
        and geometry["width"] > 0
        and type(geometry.get("height")) is int
        and geometry["height"] > 0
        and all(
            _finite_number(geometry.get(field))
            for field in ("resolution", "origin_x", "origin_y", "origin_yaw")
        )
        and float(geometry["resolution"]) > 0.0
    )


def _map_identity_continuity_failures(artifact: dict[str, Any]) -> list[str]:
    identity = artifact.get("runtime_identity")
    timeline = artifact.get("t1_goal_dispatch")
    state = timeline.get("state_before_forward") if isinstance(timeline, dict) else None
    pre = state.get("map_identity_checkpoint") if isinstance(state, dict) else None
    terminal_checkpoint = artifact.get("terminal_map_identity_checkpoint")
    post = artifact.get("post_final_window_map_identity_checkpoint")
    if not isinstance(identity, dict):
        return ["map_identity_runtime_identity"]
    expected = identity.get("live_map_identity_initial")
    failures: list[str] = []
    if not _valid_map_identity_payload(expected):
        failures.append("initial_map_identity_invalid")
    elif isinstance(expected, dict) and (
        expected.get("digest") != identity.get("live_map_sha256")
        or expected.get("frame_id") != identity.get("live_map_frame")
    ):
        failures.append("initial_map_identity_alias_mismatch")
    for label, checkpoint in (
        ("pre_dispatch", pre),
        ("terminal", terminal_checkpoint),
        ("post_final_window", post),
    ):
        if not isinstance(checkpoint, dict):
            failures.append(f"{label}_map_identity_missing")
            continue
        observed_ns = checkpoint.get("observed_host_monotonic_ns")
        checks = (
            (checkpoint.get("label") != label, f"{label}_map_identity_label"),
            (checkpoint.get("status") != "PASS", f"{label}_map_identity_status"),
            (checkpoint.get("failures") not in ([], ()), f"{label}_map_identity_failures"),
            (checkpoint.get("identity") != expected, f"{label}_map_identity_changed"),
            (type(observed_ns) is not int or observed_ns < 0, f"{label}_map_identity_time"),
        )
        failures.extend(failure for failed, failure in checks if failed)

    dispatch_ns = (
        timeline.get("nav_send_forwarded_host_monotonic_ns") if isinstance(timeline, dict) else None
    )
    pre_ns = pre.get("observed_host_monotonic_ns") if isinstance(pre, dict) else None
    terminal = artifact.get("nav2_terminal")
    terminal_ns = terminal.get("observed_host_monotonic_ns") if isinstance(terminal, dict) else None
    terminal_checkpoint_ns = (
        terminal_checkpoint.get("observed_host_monotonic_ns")
        if isinstance(terminal_checkpoint, dict)
        else None
    )
    window = artifact.get("final_observation_window")
    window_start_ns = window.get("start_host_monotonic_ns") if isinstance(window, dict) else None
    window_end_ns = window.get("end_host_monotonic_ns") if isinstance(window, dict) else None
    post_ns = post.get("observed_host_monotonic_ns") if isinstance(post, dict) else None
    if not (type(pre_ns) is int and type(dispatch_ns) is int and pre_ns <= dispatch_ns):
        failures.append("pre_dispatch_map_identity_timing")
    if not (
        type(terminal_ns) is int
        and type(terminal_checkpoint_ns) is int
        and type(window_start_ns) is int
        and type(window_end_ns) is int
        and type(post_ns) is int
        and terminal_ns <= terminal_checkpoint_ns <= window_start_ns <= window_end_ns <= post_ns
    ):
        failures.append("terminal_to_post_final_map_identity_timing")
    return list(dict.fromkeys(failures))


def _accepted_goal_observation(timeline: dict[str, Any]) -> dict[str, Any] | None:
    observations = timeline.get("accepted_goal_observations")
    if not isinstance(observations, list) or len(observations) != 1:
        return None
    observation = observations[0]
    return observation if isinstance(observation, dict) else None


def _valid_goal_uuid(timeline: dict[str, Any], *, max_age_ns: int) -> bool:
    goal_uuid = timeline.get("accepted_goal_uuid")
    observation = _accepted_goal_observation(timeline)
    status = observation.get("status") if observation is not None else None
    goal_stamp_ns = observation.get("goal_stamp_ns") if observation is not None else None
    capture_clock_ns = observation.get("capture_clock_ns") if observation is not None else None
    stored_age_ns = observation.get("goal_stamp_age_ns") if observation is not None else None
    return (
        isinstance(goal_uuid, str)
        and len(goal_uuid) == 32
        and all(character in "0123456789abcdef" for character in goal_uuid)
        and timeline.get("goal_uuid_evidence") == "INFERRED_UNIQUE_ACTION_STATUS"
        and observation is not None
        and observation.get("goal_uuid") == goal_uuid
        and type(status) is int
        and 1 <= status <= 6
        and type(goal_stamp_ns) is int
        and type(capture_clock_ns) is int
        and type(stored_age_ns) is int
        and stored_age_ns == capture_clock_ns - goal_stamp_ns
        and 0 <= stored_age_ns <= max_age_ns
        and observation.get("goal_stamp_fresh") is True
        and type(observation.get("observed_host_monotonic_ns")) is int
    )


def _dispatch_evidence_failures(
    artifact: dict[str, Any],
    *,
    contract: DifferentialMeasurementContract,
    canonical_goal: CanonicalGoal,
    ground_truth_calibration: GroundTruthCalibration | None,
) -> list[str]:
    timeline = artifact.get("t1_goal_dispatch")
    if not isinstance(timeline, dict):
        return ["t1_timeline_missing"]
    actual_goal = _actual_dispatch_goal(artifact)
    observations = timeline.get("dispatch_observations")
    dispatch = (
        observations[0]
        if isinstance(observations, list)
        and len(observations) == 1
        and isinstance(observations[0], dict)
        else None
    )
    try:
        nested_goal = (
            CanonicalGoal.model_validate(dispatch.get("actual_goal"))
            if isinstance(dispatch, dict)
            else None
        )
    except ValueError:
        nested_goal = None
    checks = (
        (
            timeline.get("status") != "PASS" or timeline.get("failures") not in ([], ()),
            "t1_timeline_status",
        ),
        (timeline.get("dispatch_count") != 1, "t1_dispatch_count"),
        (
            type(timeline.get("nav_send_forwarded_host_monotonic_ns")) is not int,
            "t1_forward_timestamp",
        ),
        (
            not _valid_goal_uuid(
                timeline,
                max_age_ns=int(contract.max_topic_age_s * 1_000_000_000),
            ),
            "t1_goal_uuid",
        ),
        (actual_goal is None, "actual_dispatch_goal_missing"),
        (
            actual_goal is None
            or nested_goal is None
            or not compare_goals(actual_goal, nested_goal).equivalent,
            "actual_dispatch_goal_copy_mismatch",
        ),
        (
            actual_goal is not None and not compare_goals(canonical_goal, actual_goal).equivalent,
            "actual_dispatch_goal_not_canonical",
        ),
    )
    failures = [failure for failed, failure in checks if failed]
    failures.extend(
        _state_evidence_failures(
            timeline.get("state_before_forward"),
            contract=contract,
            ground_truth_calibration=ground_truth_calibration,
            expected_epoch=canonical_goal.simulation_epoch,
            label="t1",
        )
    )
    return failures


def _terminal_evidence_failure(artifact: dict[str, Any]) -> str | None:
    terminal = artifact.get("nav2_terminal")
    valid = (
        isinstance(terminal, dict)
        and type(terminal.get("observed_host_monotonic_ns")) is int
        and terminal.get("status") in _NAV2_TERMINAL_STATUSES
    )
    return None if valid else "nav2_terminal_missing"


def _nondecreasing_ints(*values: object) -> bool:
    if any(type(value) is not int for value in values):
        return False
    integers = [cast(int, value) for value in values]
    return all(right >= left for left, right in zip(integers, integers[1:], strict=False))


def _between_int(value: object, lower: object, upper: object) -> bool:
    return (
        type(value) is int and type(lower) is int and type(upper) is int and lower <= value <= upper
    )


def _lifecycle_evidence_failures(artifact: dict[str, Any]) -> list[str]:
    timeline = artifact.get("t1_goal_dispatch")
    terminal = artifact.get("nav2_terminal")
    window = artifact.get("final_observation_window")
    if (
        not isinstance(timeline, dict)
        or not isinstance(terminal, dict)
        or not isinstance(window, dict)
    ):
        return ["lifecycle_evidence_missing"]
    observations = timeline.get("dispatch_observations")
    dispatch = (
        observations[0]
        if isinstance(observations, list)
        and len(observations) == 1
        and isinstance(observations[0], dict)
        else None
    )
    accepted = _accepted_goal_observation(timeline)
    request_ns = timeline.get("request_host_monotonic_ns")
    invoked_ns = dispatch.get("nav_send_invoked_host_monotonic_ns") if dispatch else None
    forwarded_ns = timeline.get("nav_send_forwarded_host_monotonic_ns")
    completed_ns = dispatch.get("forward_completed_host_monotonic_ns") if dispatch else None
    accepted_ns = accepted.get("observed_host_monotonic_ns") if accepted else None
    terminal_ns = terminal.get("observed_host_monotonic_ns")
    returned_ns = timeline.get("return_host_monotonic_ns")
    final_terminal_ns = window.get("terminal_host_monotonic_ns")
    final_start_ns = window.get("start_host_monotonic_ns")
    final_end_ns = window.get("end_host_monotonic_ns")
    public_t1_state = timeline.get("state_before_forward")
    dispatch_t1_state = dispatch.get("state_before_forward") if dispatch else None
    t1_cutoff_ns = (
        public_t1_state.get("cutoff_host_monotonic_ns")
        if isinstance(public_t1_state, dict)
        else None
    )
    ordered = (
        request_ns,
        invoked_ns,
        forwarded_ns,
        completed_ns,
        terminal_ns,
        returned_ns,
        final_start_ns,
        final_end_ns,
    )
    checks = (
        (any(type(value) is not int for value in ordered), "lifecycle_timestamp_missing"),
        (not _nondecreasing_ints(*ordered), "lifecycle_timestamp_order"),
        (
            not _between_int(accepted_ns, forwarded_ns, terminal_ns),
            "goal_status_observation_order",
        ),
        (
            type(t1_cutoff_ns) is not int
            or type(invoked_ns) is not int
            or t1_cutoff_ns < invoked_ns,
            "t1_cutoff_precedes_nav_send_invocation",
        ),
        (public_t1_state != dispatch_t1_state, "t1_state_copy_mismatch"),
        (final_terminal_ns != terminal_ns, "final_window_terminal_mismatch"),
    )
    return [failure for failed, failure in checks if failed]


def _mode_specific_evidence_failures(artifact: dict[str, Any]) -> list[str]:
    mode = artifact.get("mode")
    timeline = artifact.get("t1_goal_dispatch")
    terminal = artifact.get("nav2_terminal")
    observations = timeline.get("dispatch_observations") if isinstance(timeline, dict) else None
    dispatch = (
        observations[0]
        if isinstance(observations, list)
        and len(observations) == 1
        and isinstance(observations[0], dict)
        else None
    )
    dispatch_tag = dispatch.get("tag") if isinstance(dispatch, dict) else None
    terminal_tag = terminal.get("tag") if isinstance(terminal, dict) else None
    failures = [
        "command_tag_binding"
        for invalid in (
            not isinstance(dispatch_tag, str) or not dispatch_tag or terminal_tag != dispatch_tag,
        )
        if invalid
    ]
    execution_status = artifact.get("execution_status")
    if mode == DifferentialMode.R1_BRIDGE_NAV2.value:
        checks = (
            (
                execution_status not in _NAV2_TERMINAL_STATUSES,
                "r1_execution_status_vocabulary",
            ),
            (
                not isinstance(terminal, dict) or execution_status != terminal.get("status"),
                "r1_execution_status_binding",
            ),
        )
        failures.extend(failure for failed, failure in checks if failed)
        return failures
    result = artifact.get("jenai_result")
    if not isinstance(result, dict):
        return [*failures, "r2_jenai_result_missing"]
    result_status = result.get("execution_status")
    effective = result.get("effective_experimental_config")
    attempts = result.get("navigation_attempts")
    attempt = (
        attempts[0]
        if isinstance(attempts, list) and len(attempts) == 1 and isinstance(attempts[0], dict)
        else None
    )
    observed_results = result.get("observed_nav_results")
    matching_results = (
        [
            item
            for item in observed_results
            if isinstance(item, dict) and item.get("tag") == dispatch_tag
        ]
        if isinstance(observed_results, list)
        else []
    )
    r2_checks = (
        (
            not isinstance(effective, dict) or effective.get("nav_endpoint_retry_limit") != 0,
            "r2_retry_limit_not_zero",
        ),
        (
            attempt is None,
            "r2_navigation_attempt_count",
        ),
        (
            attempt is None or attempt.get("tag") != dispatch_tag,
            "r2_attempt_tag_binding",
        ),
        (
            len(matching_results) != 1 or matching_results[0] != terminal,
            "r2_observed_result_binding",
        ),
        (
            result_status not in _JENAI_NAVIGATION_EXECUTION_STATUSES,
            "r2_execution_status_vocabulary",
        ),
        (
            execution_status != result_status,
            "r2_execution_status_binding",
        ),
    )
    failures.extend(failure for failed, failure in r2_checks if failed)
    return failures


def _artifact_contract_and_goal(
    artifact: dict[str, Any],
) -> tuple[DifferentialMeasurementContract, CanonicalGoal] | None:
    try:
        return (
            DifferentialMeasurementContract.model_validate(artifact.get("measurement_contract")),
            CanonicalGoal.model_validate(artifact.get("canonical_goal")),
        )
    except ValueError:
        return None


def _raw_stream_recorder(
    samples: object,
    *,
    required: bool,
    allow_empty: bool,
    label: str,
    stream: str,
) -> tuple[_TopicRecorder | None, str | None]:
    if not required and samples is None:
        return None, None
    if not isinstance(samples, list) or (not samples and not allow_empty):
        return None, f"{label}_{stream}_missing"
    valid_samples: list[dict[str, Any]] = []
    for sample in samples:
        if (
            not isinstance(sample, dict)
            or set(sample) != {"host_monotonic_ns", "message"}
            or type(sample.get("host_monotonic_ns")) is not int
            or not isinstance(sample.get("message"), dict)
        ):
            return None, f"{label}_{stream}_malformed"
        valid_samples.append(copy.deepcopy(sample))
    hosts = [int(sample["host_monotonic_ns"]) for sample in valid_samples]
    if any(right <= left for left, right in zip(hosts, hosts[1:], strict=False)):
        return None, f"{label}_{stream}_host_order"
    recorder = _TopicRecorder()
    recorder.samples = valid_samples
    return recorder, None


def _raw_topic_recorders(
    value: object,
    *,
    required_streams: set[str],
    optional_streams: set[str] | None = None,
    allow_empty_streams: set[str] | None = None,
    label: str,
) -> tuple[dict[str, _TopicRecorder] | None, list[str]]:
    if not isinstance(value, dict):
        return None, [f"{label}_missing"]
    failures: list[str] = []
    recorders: dict[str, _TopicRecorder] = {}
    for stream in required_streams | (optional_streams or set()):
        recorder, failure = _raw_stream_recorder(
            value.get(stream),
            required=stream in required_streams,
            allow_empty=stream in (allow_empty_streams or set()),
            label=label,
            stream=stream,
        )
        if failure is not None:
            failures.append(failure)
        elif recorder is not None:
            recorders[stream] = recorder
    if failures:
        return None, failures
    clock_values = [
        _clock_ns(cast(dict[str, Any], sample["message"])) for sample in recorders["clock"].samples
    ]
    if any(value is None for value in clock_values):
        failures.append(f"{label}_clock_malformed")
    else:
        typed_clocks = [cast(int, item) for item in clock_values]
        if any(right < left for left, right in zip(typed_clocks, typed_clocks[1:], strict=False)):
            failures.append(f"{label}_clock_backwards")
    for sample in recorders["action_status"].samples:
        message = cast(dict[str, Any], sample["message"])
        statuses = message.get("status_list")
        if not isinstance(statuses, list) or len(_goal_status_records(message)) != len(statuses):
            failures.append(f"{label}_action_status_malformed")
            break
    return (recorders if not failures else None), failures


def _offline_state_options(
    artifact: dict[str, Any],
    contract: DifferentialMeasurementContract,
    goal: CanonicalGoal,
) -> DifferentialCaptureOptions:
    return DifferentialCaptureOptions.model_construct(
        output=Path("/offline-differential-artifact.json"),
        location="offline",
        pair_id=str(artifact.get("pair_id") or "offline"),
        mode=DifferentialMode(str(artifact.get("mode"))),
        simulation_epoch=str(goal.simulation_epoch or ""),
        reset_policy=ResetPolicy(str(artifact.get("reset_policy"))),
        preflight_sample_s=contract.preflight_sample_s,
        final_sample_s=contract.final_sample_s,
        final_window_start_delay_s=contract.final_window_start_delay_s,
        sample_interval_s=contract.sample_interval_s,
        max_start_speed_mps=contract.max_start_speed_mps,
        max_start_yaw_rate_rps=contract.max_start_yaw_rate_rps,
        max_topic_age_s=contract.max_topic_age_s,
        max_calibration_residual_m=contract.max_calibration_residual_m,
        min_final_pose_samples=contract.min_final_pose_samples,
        min_final_state_samples=contract.min_final_state_samples,
        min_final_ground_truth_samples=contract.min_final_ground_truth_samples,
        final_wall_timeout_s=contract.final_wall_timeout_s,
        max_covariance_xy=contract.max_covariance_xy,
        max_pair_start_position_delta_m=contract.max_pair_start_position_delta_m,
        max_pair_start_yaw_delta_rad=contract.max_pair_start_yaw_delta_rad,
    )


def _rederived_state(
    stored: object,
    *,
    recorders: dict[str, _TopicRecorder],
    pose_observations: dict[str, PoseLookupObservation],
    expected_purpose: PoseLookupPurpose,
    options: DifferentialCaptureOptions,
    ground_truth_calibration: GroundTruthCalibration | None,
) -> dict[str, Any] | None:
    if not isinstance(stored, dict):
        return None
    observation_id = stored.get("map_pose_observation_id")
    observation = pose_observations.get(observation_id) if isinstance(observation_id, str) else None
    if observation is None or observation.purpose is not expected_purpose:
        return None
    pose = (
        Pose2D(
            x=observation.result.x,
            y=observation.result.y,
            yaw=observation.result.yaw,
        )
        if observation.status == "SUCCESS" and observation.result is not None
        else None
    )
    cutoff = stored.get("cutoff_host_monotonic_ns")
    evaluated = stored.get("evaluated_host_monotonic_ns")
    if type(cutoff) is not int or type(evaluated) is not int:
        return None
    if not (
        cutoff
        <= observation.request_host_monotonic_ns
        <= observation.completed_host_monotonic_ns
        <= evaluated
    ):
        return None
    return _initial_state(
        pose=pose,
        map_pose_observation_id=observation.observation_id,
        clock=recorders["clock"],
        amcl=recorders["amcl"],
        odom=recorders["odom"],
        action_status=recorders["action_status"],
        options=options,
        ground_truth=recorders.get("ground_truth"),
        calibration=ground_truth_calibration,
        cutoff_host_monotonic_ns=cutoff,
        current_host_monotonic_ns=evaluated,
        action_status_observation_ready=(
            isinstance(stored.get("action_status_source"), dict)
            and stored["action_status_source"].get("observation") == "no_status_observed"
        ),
        nomotion_update_acknowledged=(stored.get("amcl_nomotion_update_acknowledged") is True),
    )


def _raw_snapshot_failures(
    artifact: dict[str, Any],
    *,
    full: dict[str, _TopicRecorder],
    dispatch: dict[str, _TopicRecorder],
) -> list[str]:
    timeline = artifact.get("t1_goal_dispatch")
    return_ns = timeline.get("return_host_monotonic_ns") if isinstance(timeline, dict) else None
    failures: list[str] = []
    for stream, snapshot_recorder in dispatch.items():
        full_recorder = full.get(stream)
        if full_recorder is None or full_recorder.samples[: len(snapshot_recorder.samples)] != (
            snapshot_recorder.samples
        ):
            failures.append(f"dispatch_snapshot_{stream}_not_prefix")
            continue
        if type(return_ns) is not int or any(
            int(sample["host_monotonic_ns"]) > return_ns for sample in snapshot_recorder.samples
        ):
            failures.append(f"dispatch_snapshot_{stream}_after_return")
    failures.extend(
        f"final_{stream}_raw_samples_missing"
        for stream in ("clock", "amcl", "odom")
        if stream in full
        and stream in dispatch
        and len(full[stream].samples) <= len(dispatch[stream].samples)
    )
    return failures


def _raw_state_failures(
    artifact: dict[str, Any],
    *,
    contract: DifferentialMeasurementContract,
    goal: CanonicalGoal,
    dispatch: dict[str, _TopicRecorder],
    pose_observations: dict[str, PoseLookupObservation],
    ground_truth_calibration: GroundTruthCalibration | None,
) -> list[str]:
    options = _offline_state_options(artifact, contract, goal)
    timeline = artifact.get("t1_goal_dispatch")
    observations = timeline.get("dispatch_observations") if isinstance(timeline, dict) else None
    nested_state = (
        observations[0].get("state_before_forward")
        if isinstance(observations, list)
        and len(observations) == 1
        and isinstance(observations[0], dict)
        else None
    )
    states = (
        ("t0", artifact.get("t0_scenario_start"), PoseLookupPurpose.T0_START),
        (
            "t1",
            timeline.get("state_before_forward") if isinstance(timeline, dict) else None,
            PoseLookupPurpose.T1_PRE_DISPATCH,
        ),
        ("t1_dispatch", nested_state, PoseLookupPurpose.T1_PRE_DISPATCH),
    )
    failures: list[str] = []
    for label, stored, purpose in states:
        sensor_state = copy.deepcopy(stored) if isinstance(stored, dict) else stored
        if isinstance(sensor_state, dict):
            sensor_state.pop("input_continuity", None)
            sensor_state.pop("map_identity_checkpoint", None)
            sensor_state.pop("runtime_stack_checkpoint", None)
        rederived = _rederived_state(
            sensor_state,
            recorders=dispatch,
            pose_observations=pose_observations,
            expected_purpose=purpose,
            options=options,
            ground_truth_calibration=ground_truth_calibration,
        )
        if rederived is None or rederived != sensor_state:
            failures.append(f"{label}_not_derived_from_raw")
    forwarded_ns = (
        timeline.get("nav_send_forwarded_host_monotonic_ns") if isinstance(timeline, dict) else None
    )
    t1_id = (
        cast(dict[str, Any], timeline.get("state_before_forward")).get("map_pose_observation_id")
        if isinstance(timeline, dict) and isinstance(timeline.get("state_before_forward"), dict)
        else None
    )
    t1_observation = pose_observations.get(t1_id) if isinstance(t1_id, str) else None
    if (
        t1_observation is None
        or type(forwarded_ns) is not int
        or t1_observation.completed_host_monotonic_ns > forwarded_ns
    ):
        failures.append("t1_pose_observation_after_nav_send")
    return failures


def _pose_observation_failures(
    observation: PoseLookupObservation,
    *,
    sequence: int,
    expected_frame: str,
    expected_base: str,
    duplicate: bool,
) -> list[str]:
    checks = (
        (observation.sequence != sequence, "pose_observation_sequence"),
        (duplicate, "pose_observation_duplicate_id"),
        (
            observation.frame_id.lstrip("/") != expected_frame
            or observation.base_frame.lstrip("/") != expected_base,
            "pose_observation_frame_mismatch",
        ),
        (
            observation.purpose is PoseLookupPurpose.R2_COMPLETION_VERDICT
            and (observation.request_clock_ns is None or observation.completed_clock_ns is None),
            "r2_endpoint_pose_clock_missing",
        ),
        (
            observation.request_clock_ns is not None
            and observation.completed_clock_ns is not None
            and observation.completed_clock_ns < observation.request_clock_ns,
            "pose_observation_clock_moved_backwards",
        ),
        (
            observation.status == "SUCCESS"
            and observation.result is not None
            and observation.completed_clock_ns is not None
            and observation.result.stamp_ns > observation.completed_clock_ns,
            "pose_observation_transform_from_future",
        ),
    )
    return [failure for failed, failure in checks if failed]


def _validated_pose_observation_index(
    artifact: dict[str, Any],
) -> tuple[dict[str, PoseLookupObservation] | None, list[str]]:
    raw = artifact.get("pose_observations")
    identity = artifact.get("runtime_identity")
    if not isinstance(raw, list) or not isinstance(identity, dict):
        return None, ["pose_observations_missing"]
    expected_frame = str(identity.get("site_map_frame") or "").lstrip("/")
    expected_base = str(identity.get("robot_base_frame") or "").lstrip("/")
    if not expected_frame or not expected_base:
        return None, ["pose_observation_frame_contract_missing"]
    index: dict[str, PoseLookupObservation] = {}
    failures: list[str] = []
    for sequence, payload in enumerate(raw):
        try:
            observation = PoseLookupObservation.model_validate(payload)
        except ValueError:
            failures.append("pose_observation_schema")
            continue
        failures.extend(
            _pose_observation_failures(
                observation,
                sequence=sequence,
                expected_frame=expected_frame,
                expected_base=expected_base,
                duplicate=observation.observation_id in index,
            )
        )
        index[observation.observation_id] = observation
    if failures or not index:
        return None, list(dict.fromkeys(failures or ["pose_observations_missing"]))
    return index, []


def _pose_observation_reference_failures(
    artifact: dict[str, Any],
    index: dict[str, PoseLookupObservation],
) -> list[str]:
    t0 = artifact.get("t0_scenario_start")
    timeline = artifact.get("t1_goal_dispatch")
    dispatches = timeline.get("dispatch_observations") if isinstance(timeline, dict) else None
    nested_t1 = (
        dispatches[0].get("state_before_forward")
        if isinstance(dispatches, list) and len(dispatches) == 1 and isinstance(dispatches[0], dict)
        else None
    )
    public_t1 = timeline.get("state_before_forward") if isinstance(timeline, dict) else None
    window = artifact.get("final_observation_window")
    attempts = window.get("map_pose_attempts") if isinstance(window, dict) else None
    t0_id = t0.get("map_pose_observation_id") if isinstance(t0, dict) else None
    t1_id = public_t1.get("map_pose_observation_id") if isinstance(public_t1, dict) else None
    nested_t1_id = nested_t1.get("map_pose_observation_id") if isinstance(nested_t1, dict) else None
    raw_final_ids = (
        [item.get("pose_observation_id") for item in attempts if isinstance(item, dict)]
        if isinstance(attempts, list)
        else []
    )
    final_ids = [item for item in raw_final_ids if isinstance(item, str)]
    result = artifact.get("jenai_result")
    raw_endpoint_ids = (
        result.get("endpoint_pose_observation_ids") if isinstance(result, dict) else []
    )
    endpoint_ids = (
        [item for item in raw_endpoint_ids if isinstance(item, str)]
        if isinstance(raw_endpoint_ids, list)
        else []
    )
    referenced = {
        item for item in (t0_id, t1_id, *endpoint_ids, *final_ids) if isinstance(item, str)
    }
    purpose_counts = {
        purpose: sum(observation.purpose is purpose for observation in index.values())
        for purpose in PoseLookupPurpose
    }
    checks = (
        (
            not isinstance(t0_id, str) or t0_id not in index,
            "t0_pose_observation_reference",
        ),
        (
            not isinstance(t1_id, str) or t1_id not in index or nested_t1_id != t1_id,
            "t1_pose_observation_reference",
        ),
        (
            len(final_ids) != len(raw_final_ids)
            or len(final_ids) != len(set(final_ids))
            or any(item not in index for item in final_ids),
            "final_pose_observation_references",
        ),
        (
            not isinstance(raw_endpoint_ids, list)
            or len(endpoint_ids) != len(raw_endpoint_ids)
            or len(endpoint_ids) != len(set(endpoint_ids))
            or any(item not in index for item in endpoint_ids),
            "r2_endpoint_pose_observation_references",
        ),
        (referenced != set(index), "pose_observation_unreferenced_or_missing"),
        (purpose_counts[PoseLookupPurpose.T0_START] != 1, "t0_pose_observation_count"),
        (
            purpose_counts[PoseLookupPurpose.T1_PRE_DISPATCH] != 1,
            "t1_pose_observation_count",
        ),
        (purpose_counts[PoseLookupPurpose.FINAL_WINDOW] != len(final_ids), "final_pose_count"),
        (
            purpose_counts[PoseLookupPurpose.R2_COMPLETION_VERDICT] != len(endpoint_ids),
            "r2_endpoint_pose_count",
        ),
    )
    return [failure for failed, failure in checks if failed]


def _r2_verdict_pose_failures(
    artifact: dict[str, Any],
    index: dict[str, PoseLookupObservation],
) -> list[str]:
    result = artifact.get("jenai_result")
    raw_ids = result.get("endpoint_pose_observation_ids") if isinstance(result, dict) else []
    ids = raw_ids if isinstance(raw_ids, list) else []
    endpoint_observations = [index[item] for item in ids if isinstance(item, str) and item in index]
    if artifact.get("mode") == DifferentialMode.R1_BRIDGE_NAV2.value:
        return ["r1_has_r2_verdict_pose"] if ids or endpoint_observations else []
    terminal = artifact.get("nav2_terminal")
    timeline = artifact.get("t1_goal_dispatch")
    attempts = result.get("navigation_attempts") if isinstance(result, dict) else None
    attempt_tag = (
        attempts[0].get("tag")
        if isinstance(attempts, list) and len(attempts) == 1 and isinstance(attempts[0], dict)
        else None
    )
    terminal_status = terminal.get("status") if isinstance(terminal, dict) else None
    terminal_ns = terminal.get("observed_host_monotonic_ns") if isinstance(terminal, dict) else None
    return_ns = timeline.get("return_host_monotonic_ns") if isinstance(timeline, dict) else None
    expected_count = 1 if terminal_status == "succeeded" else 0
    failures: list[str] = []
    if len(ids) != expected_count or len(endpoint_observations) != expected_count:
        failures.append("r2_endpoint_pose_count")
    for observation in endpoint_observations:
        if (
            observation.purpose is not PoseLookupPurpose.R2_COMPLETION_VERDICT
            or observation.attempt_tag != attempt_tag
        ):
            failures.append("r2_endpoint_pose_attempt_binding")
        if not (
            type(terminal_ns) is int
            and type(return_ns) is int
            and terminal_ns
            <= observation.request_host_monotonic_ns
            <= observation.completed_host_monotonic_ns
            <= return_ns
        ):
            failures.append("r2_endpoint_pose_timing")
        if artifact.get("execution_status") == "succeeded" and observation.status != "SUCCESS":
            failures.append("r2_success_without_endpoint_pose")
    return list(dict.fromkeys(failures))


def _raw_goal_uuid_failures(
    artifact: dict[str, Any],
    *,
    dispatch: dict[str, _TopicRecorder],
    contract: DifferentialMeasurementContract,
) -> list[str]:
    timeline = artifact.get("t1_goal_dispatch")
    state = timeline.get("state_before_forward") if isinstance(timeline, dict) else None
    if not isinstance(timeline, dict) or not isinstance(state, dict):
        return ["goal_uuid_not_derived_from_raw"]
    before = state.get("known_goal_ids")
    dispatched_at = timeline.get("nav_send_forwarded_host_monotonic_ns")
    if not isinstance(before, list) or type(dispatched_at) is not int:
        return ["goal_uuid_not_derived_from_raw"]
    observations = _new_goal_ids(
        dispatch["action_status"],
        dispatch["clock"],
        before={str(item) for item in before},
        dispatched_at_ns=dispatched_at,
        max_age_s=contract.max_topic_age_s,
    )
    expected_uuid = observations[0].get("goal_uuid") if len(observations) == 1 else None
    if (
        timeline.get("accepted_goal_observations") != observations
        or timeline.get("accepted_goal_uuid") != expected_uuid
        or timeline.get("goal_uuid_evidence")
        != (
            "INFERRED_UNIQUE_ACTION_STATUS"
            if expected_uuid
            else "UNAVAILABLE_NO_NEW_STATUS_UUID_OBSERVED"
        )
    ):
        return ["goal_uuid_not_derived_from_raw"]
    return []


def _validated_raw_map_attempts(
    attempts: object,
    *,
    start_host_ns: int,
    end_host_ns: int,
    start_clock_ns: int,
    end_clock_ns: int,
    expected_frame: str,
) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(attempts, list):
        return [], False
    valid: list[dict[str, Any]] = []
    success_keys = {
        "pose_observation_id",
        "requested_host_monotonic_ns",
        "observed_host_monotonic_ns",
        "capture_clock_ns",
        "fresh",
        "pose",
    }
    failure_keys = {
        "pose_observation_id",
        "requested_host_monotonic_ns",
        "capture_clock_ns",
        "fresh",
        "error",
    }
    for item in attempts:
        if not isinstance(item, dict):
            return [], False
        requested = item.get("requested_host_monotonic_ns")
        observation_id = item.get("pose_observation_id")
        capture_clock = item.get("capture_clock_ns")
        if item.get("fresh") is True:
            observed = item.get("observed_host_monotonic_ns")
            pose = item.get("pose")
            if (
                set(item) != success_keys
                or not isinstance(observation_id, str)
                or not observation_id
                or type(requested) is not int
                or type(observed) is not int
                or type(capture_clock) is not int
                or not (start_host_ns <= requested <= observed <= end_host_ns)
                or not (start_clock_ns <= capture_clock <= end_clock_ns)
                or not isinstance(pose, dict)
                or not _valid_pose_payload(pose)
                or str(pose.get("frame_id") or "").lstrip("/") != expected_frame
                or not isinstance(pose.get("source"), str)
                or not pose["source"]
            ):
                return [], False
            valid.append(item)
        elif (
            set(item) != failure_keys
            or not isinstance(observation_id, str)
            or not observation_id
            or type(requested) is not int
            or not (start_host_ns <= requested <= end_host_ns)
            or (capture_clock is not None and type(capture_clock) is not int)
            or not isinstance(item.get("error"), str)
            or not item["error"]
        ):
            return [], False
    return valid, True


def _raw_final_window_failures(
    artifact: dict[str, Any],
    *,
    contract: DifferentialMeasurementContract,
    full: dict[str, _TopicRecorder],
    pose_observations: dict[str, PoseLookupObservation],
    ground_truth_calibration: GroundTruthCalibration | None,
) -> list[str]:
    window = artifact.get("final_observation_window")
    if not isinstance(window, dict):
        return ["final_window_not_derived_from_raw"]
    start_host = window.get("start_host_monotonic_ns")
    end_host = window.get("end_host_monotonic_ns")
    start_clock = window.get("start_clock_ns")
    end_clock = window.get("end_clock_ns")
    if any(type(value) is not int for value in (start_host, end_host, start_clock, end_clock)):
        return ["final_window_not_derived_from_raw"]
    typed_start_host = cast(int, start_host)
    typed_end_host = cast(int, end_host)
    typed_start_clock = cast(int, start_clock)
    typed_end_clock = cast(int, end_clock)
    streams = _project_final_streams(
        full,
        start_host_ns=typed_start_host,
        end_host_ns=typed_end_host,
        start_clock_ns=typed_start_clock,
        end_clock_ns=typed_end_clock,
        max_topic_age_s=contract.max_topic_age_s,
        max_future_source_lead_s=contract.sample_interval_s,
        max_covariance_xy=contract.max_covariance_xy,
        max_speed_mps=contract.max_start_speed_mps,
        max_yaw_rate_rps=contract.max_start_yaw_rate_rps,
        calibration=ground_truth_calibration,
    )
    map_samples = window.get("map_pose_samples")
    expected_frame = str(
        cast(dict[str, Any], artifact.get("runtime_identity", {})).get("site_map_frame") or ""
    ).lstrip("/")
    raw_map_attempts = [
        _pose_attempt_from_observation(observation.model_dump(mode="json"))
        for observation in pose_observations.values()
        if observation.purpose is PoseLookupPurpose.FINAL_WINDOW
        and typed_start_host
        <= observation.request_host_monotonic_ns
        <= observation.completed_host_monotonic_ns
        <= typed_end_host
    ]
    valid_map_attempts, map_attempts_valid = _validated_raw_map_attempts(
        raw_map_attempts,
        start_host_ns=typed_start_host,
        end_host_ns=typed_end_host,
        start_clock_ns=typed_start_clock,
        end_clock_ns=typed_end_clock,
        expected_frame=expected_frame,
    )
    checks = (
        (
            window.get("clock_samples") != streams.clock_samples,
            "final_clock_not_derived_from_raw",
        ),
        (
            window.get("amcl_samples") != streams.amcl_samples,
            "final_amcl_window_not_derived_from_raw",
        ),
        (
            window.get("valid_amcl_samples") != streams.valid_amcl,
            "final_amcl_not_derived_from_raw",
        ),
        (
            window.get("odom_samples") != streams.odom_samples,
            "final_odom_window_not_derived_from_raw",
        ),
        (
            window.get("valid_odom_samples") != streams.valid_odom,
            "final_odom_not_derived_from_raw",
        ),
        (
            window.get("ground_truth_samples") != streams.raw_ground_truth,
            "final_ground_truth_window_not_derived_from_raw",
        ),
        (
            window.get("verified_ground_truth_samples") != streams.verified_ground_truth,
            "final_ground_truth_not_derived_from_raw",
        ),
        (
            window.get("stationary") is not streams.stationary,
            "final_stationary_not_derived_from_raw",
        ),
        (
            window.get("map_pose_attempts") != raw_map_attempts,
            "final_map_attempts_not_derived_from_pose_observations",
        ),
        (not map_attempts_valid, "final_map_attempt_schema"),
        (map_samples != valid_map_attempts, "final_map_samples_not_derived_from_attempts"),
        (
            artifact.get("final_map_pose_samples") != map_samples,
            "final_map_pose_alias_mismatch",
        ),
        (
            artifact.get("ground_truth_samples") != window.get("verified_ground_truth_samples"),
            "final_ground_truth_alias_mismatch",
        ),
    )
    return [failure for failed, failure in checks if failed]


def _raw_evidence_failures(
    artifact: dict[str, Any],
    *,
    contract: DifferentialMeasurementContract,
    goal: CanonicalGoal,
    ground_truth_calibration: GroundTruthCalibration | None,
) -> list[str]:
    if artifact.get("evidence_derivation_version") != 3:
        return ["evidence_derivation_version"]
    pose_observations, pose_failures = _validated_pose_observation_index(artifact)
    if pose_observations is None:
        return pose_failures
    pose_failures.extend(_pose_observation_reference_failures(artifact, pose_observations))
    pose_failures.extend(_r2_verdict_pose_failures(artifact, pose_observations))
    identity = artifact.get("runtime_identity")
    odom_topic = identity.get("controller_odom_topic") if isinstance(identity, dict) else None
    expected_streams: dict[str, dict[str, str | None]] = {
        "clock": {
            "topic": _CLOCK_TOPIC,
            "message_type": _CLOCK_TYPE,
            "qos_profile": "sensor_data",
        },
        "amcl": {
            "topic": _AMCL_TOPIC,
            "message_type": _AMCL_TYPE,
            "qos_profile": "transient_local",
        },
        "odom": {
            "topic": odom_topic,
            "message_type": _ODOM_TYPE,
            "qos_profile": "sensor_data",
        },
        "action_status": {
            "topic": _ACTION_STATUS_TOPIC,
            "message_type": _ACTION_STATUS_TYPE,
            "qos_profile": "transient_local",
        },
    }
    if contract.ground_truth_topic is not None:
        expected_streams["ground_truth"] = {
            "topic": contract.ground_truth_topic,
            "message_type": contract.ground_truth_type,
            "qos_profile": "sensor_data",
        }
    contract_failures = (
        []
        if artifact.get("topic_stream_contract") == expected_streams
        else ["topic_stream_contract"]
    )
    required = {"clock", "amcl", "odom", "action_status"}
    optional: set[str] = set()
    if ground_truth_calibration is not None:
        required.add("ground_truth")
    elif contract.ground_truth_topic is not None:
        optional.add("ground_truth")
    full, failures = _raw_topic_recorders(
        artifact.get("topic_samples"),
        required_streams=required,
        optional_streams=optional,
        allow_empty_streams={"action_status"},
        label="raw_topic_samples",
    )
    failures.extend(contract_failures)
    failures.extend(pose_failures)
    dispatch, dispatch_failures = _raw_topic_recorders(
        artifact.get("topic_samples_at_dispatch_end"),
        required_streams=required,
        optional_streams=optional,
        allow_empty_streams={"action_status"},
        label="dispatch_topic_samples",
    )
    failures.extend(dispatch_failures)
    if full is None or dispatch is None:
        return list(dict.fromkeys(failures))
    failures.extend(_raw_snapshot_failures(artifact, full=full, dispatch=dispatch))
    failures.extend(
        _raw_state_failures(
            artifact,
            contract=contract,
            goal=goal,
            dispatch=dispatch,
            pose_observations=pose_observations,
            ground_truth_calibration=ground_truth_calibration,
        )
    )
    failures.extend(
        _raw_goal_uuid_failures(
            artifact,
            dispatch=dispatch,
            contract=contract,
        )
    )
    failures.extend(
        _raw_final_window_failures(
            artifact,
            contract=contract,
            full=full,
            pose_observations=pose_observations,
            ground_truth_calibration=ground_truth_calibration,
        )
    )
    return list(dict.fromkeys(failures))


def _comparison_eligibility_failure(artifact: dict[str, Any], side: str) -> str | None:
    parsed = _artifact_contract_and_goal(artifact)
    if parsed is None:
        return f"{side} artifact contract or canonical goal is invalid."
    contract, canonical_goal = parsed
    identity = artifact.get("runtime_identity")
    identity_dict = identity if isinstance(identity, dict) else {}
    execution_status = artifact.get("execution_status")
    final_map_pose = _artifact_pose(artifact, "final_map_pose_median")
    final_ground_truth_pose = _artifact_pose(artifact, "final_ground_truth_map_median")
    ground_truth_calibration, ground_truth_failures = _ground_truth_requirement(
        artifact,
        contract=contract,
        identity=identity_dict,
    )
    checks = (
        (artifact.get("overall") != "captured", "overall_not_captured"),
        (artifact.get("execution_requested") is not True, "execution_not_requested"),
        (not isinstance(artifact.get("finished_at"), str), "finished_at_missing"),
        (final_map_pose is None, "final_map_pose_median_missing"),
        (
            not isinstance(execution_status, str)
            or not execution_status
            or execution_status == "unknown",
            "execution_status_missing",
        ),
    )
    failures = [failure for failed, failure in checks if failed]
    failures.extend(
        _runtime_identity_failures(identity_dict, require_end_generation=True)
        if identity_dict
        else ["runtime_identity_missing"]
    )
    failures.extend(ground_truth_failures)
    failures.extend(_target_binding_failures(artifact, canonical_goal))
    failures.extend(
        _state_evidence_failures(
            artifact.get("t0_scenario_start"),
            contract=contract,
            ground_truth_calibration=ground_truth_calibration,
            expected_epoch=canonical_goal.simulation_epoch,
            label="t0",
        )
    )
    failures.extend(
        _dispatch_evidence_failures(
            artifact,
            contract=contract,
            canonical_goal=canonical_goal,
            ground_truth_calibration=ground_truth_calibration,
        )
    )
    if terminal_failure := _terminal_evidence_failure(artifact):
        failures.append(terminal_failure)
    failures.extend(_lifecycle_evidence_failures(artifact))
    failures.extend(
        _final_window_evidence_failures(
            artifact.get("final_observation_window"),
            contract=contract,
            ground_truth_calibration=ground_truth_calibration,
            final_map_pose=final_map_pose,
            final_ground_truth_pose=final_ground_truth_pose,
        )
    )
    failures.extend(
        _cleanup_evidence_failures(
            artifact.get("cleanup"),
            primary_runtime_identity=identity_dict.get("ros_middleware"),
        )
    )
    failures.extend(_input_continuity_evidence_failures(artifact))
    failures.extend(_runtime_stack_continuity_failures(artifact))
    failures.extend(_map_identity_continuity_failures(artifact))
    failures.extend(_mode_specific_evidence_failures(artifact))
    failures.extend(
        _raw_evidence_failures(
            artifact,
            contract=contract,
            goal=canonical_goal,
            ground_truth_calibration=ground_truth_calibration,
        )
    )
    if failures:
        return f"{side} artifact is not comparison-eligible: {', '.join(dict.fromkeys(failures))}"
    return None


def _eligible_artifact_pair(
    left: dict[str, Any],
    right: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    left_valid = _validated_artifact(left)
    right_valid = _validated_artifact(right)
    if left_valid is None or right_valid is None:
        return None, None, "One or both differential artifacts failed schema validation."
    for artifact, side in ((left_valid, "left"), (right_valid, "right")):
        if detail := _comparison_eligibility_failure(artifact, side):
            return None, None, detail
    return left_valid, right_valid, None


def _pose_pair_exceeds(
    left: Pose2D,
    right: Pose2D,
    *,
    position_tolerance_m: float,
    yaw_tolerance_rad: float,
) -> bool:
    yaw_delta = math.atan2(math.sin(left.yaw - right.yaw), math.cos(left.yaw - right.yaw))
    return (
        math.hypot(left.x - right.x, left.y - right.y) > position_tolerance_m
        or abs(yaw_delta) > yaw_tolerance_rad
    )


def _state_pairing_gate(
    left_identity: dict[str, Any],
    right_identity: dict[str, Any],
    left_state: dict[str, Any],
    right_state: dict[str, Any],
    *,
    max_covariance_xy: float,
    max_start_position_delta_m: float,
    max_start_yaw_delta_rad: float,
) -> tuple[PairingGateResult | None, str | None]:
    left_start = _artifact_pose(left_state, "map_to_base")
    right_start = _artifact_pose(right_state, "map_to_base")
    if left_start is None or right_start is None:
        return None, "Comparable map poses are missing."
    try:
        left_covariance = float(left_state["amcl_covariance_xy"])
        right_covariance = float(right_state["amcl_covariance_xy"])
    except (KeyError, TypeError, ValueError):
        return None, "Comparable localization covariance is missing."
    if not all(
        math.isfinite(value) and value >= 0 for value in (left_covariance, right_covariance)
    ):
        return None, "Comparable localization covariance is invalid."
    return (
        evaluate_pairing_gate(
            left_runtime_fingerprint=str(left_identity.get("fingerprint") or ""),
            right_runtime_fingerprint=str(right_identity.get("fingerprint") or ""),
            left_epoch=str(left_state.get("simulation_epoch") or ""),
            right_epoch=str(right_state.get("simulation_epoch") or ""),
            left_start=left_start,
            right_start=right_start,
            left_covariance_xy=left_covariance,
            right_covariance_xy=right_covariance,
            left_stationary=left_state.get("stationary") is True,
            right_stationary=right_state.get("stationary") is True,
            left_active_goal=bool(left_state.get("active_goal_ids")),
            right_active_goal=bool(right_state.get("active_goal_ids")),
            max_start_position_delta_m=max_start_position_delta_m,
            max_start_yaw_delta_rad=max_start_yaw_delta_rad,
            max_covariance_xy=max_covariance_xy,
        ),
        None,
    )


def _terminal_relative_window_offset_delta_ns(
    left: dict[str, Any],
    right: dict[str, Any],
) -> int:
    offsets: list[int] = []
    for artifact in (left, right):
        terminal = artifact.get("nav2_terminal")
        window = artifact.get("final_observation_window")
        terminal_ns = (
            terminal.get("observed_host_monotonic_ns") if isinstance(terminal, dict) else None
        )
        window_start_ns = (
            window.get("start_host_monotonic_ns") if isinstance(window, dict) else None
        )
        if type(terminal_ns) is not int or type(window_start_ns) is not int:
            return sys.maxsize
        offsets.append(window_start_ns - terminal_ns)
    return abs(offsets[0] - offsets[1])


def _pairing_gate_from_artifacts(
    left: dict[str, Any],
    right: dict[str, Any],
) -> tuple[PairingGateResult | None, str | None]:
    left_identity = cast(dict[str, Any], left["runtime_identity"])
    right_identity = cast(dict[str, Any], right["runtime_identity"])
    left_t0 = cast(dict[str, Any], left["t0_scenario_start"])
    right_t0 = cast(dict[str, Any], right["t0_scenario_start"])
    left_t1 = cast(dict[str, Any], left["t1_goal_dispatch"])
    right_t1 = cast(dict[str, Any], right["t1_goal_dispatch"])
    left_dispatch = left_t1.get("state_before_forward")
    right_dispatch = right_t1.get("state_before_forward")
    left_binding = cast(dict[str, Any], left.get("target_binding") or {})
    right_binding = cast(dict[str, Any], right.get("target_binding") or {})
    if not isinstance(left_dispatch, dict) or not isinstance(right_dispatch, dict):
        return None, "Comparable T1 dispatch states are missing."
    try:
        contract = DifferentialMeasurementContract.model_validate(left["measurement_contract"])
    except ValueError:
        return None, "The left measurement contract is invalid."
    t0_gate, detail = _state_pairing_gate(
        left_identity,
        right_identity,
        left_t0,
        right_t0,
        max_covariance_xy=contract.max_covariance_xy,
        max_start_position_delta_m=contract.max_pair_start_position_delta_m,
        max_start_yaw_delta_rad=contract.max_pair_start_yaw_delta_rad,
    )
    if t0_gate is None:
        return None, detail
    t1_gate, detail = _state_pairing_gate(
        left_identity,
        right_identity,
        left_dispatch,
        right_dispatch,
        max_covariance_xy=contract.max_covariance_xy,
        max_start_position_delta_m=contract.max_pair_start_position_delta_m,
        max_start_yaw_delta_rad=contract.max_pair_start_yaw_delta_rad,
    )
    if t1_gate is None:
        return None, detail
    t1_failures = tuple(
        f"t1_{failure}" for failure in t1_gate.failures if failure != "runtime_fingerprint"
    )
    metadata_checks = (
        (left.get("pair_id") != right.get("pair_id"), "pair_id"),
        (left.get("reset_policy") != right.get("reset_policy"), "reset_policy"),
        (
            left.get("measurement_contract") != right.get("measurement_contract"),
            "measurement_contract",
        ),
        (
            left.get("ground_truth_calibration") != right.get("ground_truth_calibration"),
            "ground_truth_calibration",
        ),
        (
            left_binding.get("resolved_id") != right_binding.get("resolved_id")
            or left_binding.get("resolved_name") != right_binding.get("resolved_name")
            or left_binding.get("locations_sha256") != right_binding.get("locations_sha256")
            or left_binding.get("capability_id") != right_binding.get("capability_id"),
            "target_binding",
        ),
        (
            {str(left.get("mode")), str(right.get("mode"))}
            != {
                DifferentialMode.R1_BRIDGE_NAV2.value,
                DifferentialMode.R2_JENAI_NO_RETRY.value,
            },
            "differential_modes",
        ),
        (
            _terminal_relative_window_offset_delta_ns(left, right)
            > int(contract.sample_interval_s * 1_000_000_000),
            "final_window_terminal_offset",
        ),
    )
    metadata_failures = tuple(failure for failed, failure in metadata_checks if failed)
    physical_failures: list[str] = []
    calibration = left.get("ground_truth_calibration")
    if isinstance(calibration, dict) and calibration.get("status") == "VERIFIED":
        for label, left_state, right_state in (
            ("ground_truth_start", left_t0, right_t0),
            ("ground_truth_dispatch", left_dispatch, right_dispatch),
        ):
            left_pose = _pose_payload(left_state.get("ground_truth_map_pose"))
            right_pose = _pose_payload(right_state.get("ground_truth_map_pose"))
            if left_pose is None or right_pose is None:
                physical_failures.append(f"{label}_missing")
                continue
            if _pose_pair_exceeds(
                left_pose,
                right_pose,
                position_tolerance_m=contract.max_pair_start_position_delta_m,
                yaw_tolerance_rad=contract.max_pair_start_yaw_delta_rad,
            ):
                physical_failures.append(label)
    failures = tuple(
        dict.fromkeys((*t0_gate.failures, *t1_failures, *metadata_failures, *physical_failures))
    )
    gate = t0_gate.model_copy(
        update={
            "status": PairingGate.FAILED if failures else PairingGate.PASSED,
            "failures": failures,
            "dispatch_position_delta_m": t1_gate.start_position_delta_m,
            "dispatch_yaw_delta_rad": t1_gate.start_yaw_delta_rad,
        }
    )
    return gate, None


def compare_differential_artifacts(
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
    """Compare one R1/R2 pair and preserve every exclusion reason."""

    pair_id = left.get("pair_id") or right.get("pair_id")
    left_valid, right_valid, detail = _eligible_artifact_pair(left, right)
    if left_valid is None or right_valid is None:
        return _insufficient_report(pair_id, detail or "Artifacts are ineligible.")
    gate, detail = _pairing_gate_from_artifacts(left_valid, right_valid)
    if gate is None:
        return _insufficient_report(pair_id, detail or "Pairing evidence is unavailable.")
    left_goal = _actual_dispatch_goal(left_valid)
    right_goal = _actual_dispatch_goal(right_valid)
    if left_goal is None or right_goal is None:
        return _insufficient_report(pair_id, "Actual dispatch goals are missing.")
    classifications = classify_pair(
        left_goal=left_goal,
        right_goal=right_goal,
        pairing_gate=gate,
        left_final_map=_artifact_pose(left_valid, "final_map_pose_median"),
        right_final_map=_artifact_pose(right_valid, "final_map_pose_median"),
        left_final_ground_truth=_artifact_pose(left_valid, "final_ground_truth_map_median"),
        right_final_ground_truth=_artifact_pose(right_valid, "final_ground_truth_map_median"),
        left_execution_status=(
            str(left_valid["execution_status"]) if left_valid.get("execution_status") else None
        ),
        right_execution_status=(
            str(right_valid["execution_status"]) if right_valid.get("execution_status") else None
        ),
    )
    excluded = {
        PairClassification.GOAL_PAYLOAD_DIFFERENCE,
        PairClassification.INSUFFICIENT_EVIDENCE,
    }
    return {
        "schema_version": 1,
        "pair_id": left_valid.get("pair_id") or right_valid.get("pair_id"),
        "included": gate.status is PairingGate.PASSED and excluded.isdisjoint(classifications),
        "pairing_gate": gate.model_dump(mode="json"),
        "classifications": classifications,
        "detail": (
            "Pairing gate failed: " + ", ".join(gate.failures)
            if gate.status is PairingGate.FAILED
            else None
        ),
    }


def load_differential_artifact(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Differential artifact must contain a JSON object.")
    return DifferentialArtifact.model_validate(payload).model_dump(mode="json")


def load_and_compare(left_path: Path, right_path: Path, output: Path) -> dict[str, Any]:
    try:
        left = load_differential_artifact(left_path)
        right = load_differential_artifact(right_path)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        report = _insufficient_report(None, f"Artifact load failed: {exc}")
    else:
        report = compare_differential_artifacts(left, right)
    return _write_comparison_report(output, report)
