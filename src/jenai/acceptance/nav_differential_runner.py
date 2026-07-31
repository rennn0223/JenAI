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
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

import jenai
from jenai.acceptance.nav_differential import (
    CanonicalGoal,
    GroundTruthCalibration,
    PairClassification,
    PairingGate,
    PairingGateResult,
    Pose2D,
    classify_pair,
    evaluate_pairing_gate,
)
from jenai.adapters.locations import find_location, load_locations
from jenai.bridge import BridgeError, RosBridgeClient
from jenai.config import default_config_path, load_config
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
_ODOM_TOPIC = "/odom"
_ODOM_TYPE = "nav_msgs/msg/Odometry"


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
    scene_path: Path | None = None
    live_scene_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    calibration_path: Path | None = None
    ground_truth_topic: str | None = None
    ground_truth_type: str = "geometry_msgs/msg/PoseStamped"
    execute: bool = False
    confirmation: str = ""
    overwrite: bool = False
    preflight_sample_s: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    final_sample_s: float = Field(default=2.0, gt=0, allow_inf_nan=False)
    sample_interval_s: float = Field(default=0.2, gt=0, allow_inf_nan=False)
    max_start_speed_mps: float = Field(default=0.02, ge=0, allow_inf_nan=False)
    max_start_yaw_rate_rps: float = Field(default=0.03, ge=0, allow_inf_nan=False)
    max_topic_age_s: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    max_calibration_residual_m: float = Field(default=0.02, ge=0, allow_inf_nan=False)
    min_final_pose_samples: int = Field(default=10, ge=2)
    final_wall_timeout_s: float = Field(default=15.0, gt=0, allow_inf_nan=False)
    max_covariance_xy: float = Field(default=0.1, ge=0, allow_inf_nan=False)

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
        if self.execute and self.live_scene_sha256 is None:
            raise ValueError(
                "Live differential capture requires the active Isaac Stage root-layer SHA-256."
            )
        if self.output.exists() and not self.overwrite:
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
    sample_interval_s: float = Field(gt=0, allow_inf_nan=False)
    max_topic_age_s: float = Field(gt=0, allow_inf_nan=False)
    max_calibration_residual_m: float = Field(ge=0, allow_inf_nan=False)
    min_final_pose_samples: int = Field(ge=2)
    final_wall_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    max_start_speed_mps: float = Field(ge=0, allow_inf_nan=False)
    max_start_yaw_rate_rps: float = Field(ge=0, allow_inf_nan=False)
    max_covariance_xy: float = Field(ge=0, allow_inf_nan=False)


ArtifactOverall = Literal[
    "initializing",
    "preflight_only",
    "blocked",
    "captured",
    "insufficient_evidence",
    "failed",
    "cleanup_failed",
]


class DifferentialArtifact(BaseModel):
    """Reloadable envelope for every success, block, and failure artifact."""

    model_config = ConfigDict(extra="allow")

    schema_version: Literal[1] = 1
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
    ground_truth_calibration: GroundTruthCalibration | None = None
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


class _TopicRecorder:
    def __init__(self) -> None:
        self.samples: list[dict[str, Any]] = []

    def record(self, message: dict[str, Any]) -> None:
        self.samples.append(
            {
                "host_monotonic_ns": time.monotonic_ns(),
                "message": message,
            }
        )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    if config.vehicle.domain_id is not None:
        return str(config.vehicle.domain_id)
    return os.environ.get("ROS_DOMAIN_ID", "0")


def _apply_runtime_fingerprint(identity: dict[str, Any]) -> None:
    fields = {
        key: identity.get(key)
        for key in (
            "git_sha",
            "git_dirty",
            "jenai_import_path",
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
            "scene_path",
            "scene_sha256",
            "live_scene_sha256",
            "live_map_sha256",
            "live_map_frame",
            "controller_lifecycle",
            "planner_lifecycle",
            "bt_navigator_lifecycle",
            "runtime_parameter_sha256",
            "node_name_counts",
            "navigate_to_pose_action_count",
        )
    }
    identity["fingerprint"] = hashlib.sha256(
        json.dumps(fields, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _runtime_identity(
    config: AppConfig,
    config_path: Path,
    *,
    scene_path: Path | None,
    live_scene_sha256: str | None,
    simulation_epoch: str,
) -> dict[str, Any]:
    locations_path = config.resolved_locations_path(config_path)
    nav_params_path = os.environ.get("JENAI_NAV2_OVERRIDE_PARAMS")
    if not nav_params_path:
        uid = os.getuid()
        session = os.environ.get("JENAI_NAV2_TMUX_SESSION", "nav2")
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/tmp/jenai-nav2-{uid}")
        nav_params_path = str(Path(runtime_dir) / f"{session}-params.yaml")

    source_root = Path(jenai.__file__).resolve().parents[2]
    bridge_domain_id = _effective_ros_domain(config)
    ros_env = {"ROS_DOMAIN_ID": bridge_domain_id}
    revision = _command_output(["git", "rev-parse", "HEAD"], cwd=source_root)
    dirty_output = _command_output(["git", "status", "--porcelain"], cwd=source_root)
    ros_nodes = _command_output(["ros2", "node", "list"], env=ros_env)
    node_lines = [line.strip() for line in (ros_nodes or "").splitlines() if line.strip()]
    required_nodes = ("/amcl", "/controller_server", "/planner_server", "/bt_navigator")
    node_counts = {name: node_lines.count(name) for name in required_nodes}
    action_list = _command_output(["ros2", "action", "list", "-t"], env=ros_env)
    action_lines = [line.strip() for line in (action_list or "").splitlines() if line.strip()]
    action_count = sum(line.split(maxsplit=1)[0] == "/navigate_to_pose" for line in action_lines)
    parameter_snapshots = {
        node: _command_output(["ros2", "param", "dump", node], env=ros_env)
        for node in required_nodes
    }
    parameter_hashes = {
        node: _text_sha256(snapshot) for node, snapshot in parameter_snapshots.items()
    }
    identity: dict[str, Any] = {
        "git_sha": revision,
        "git_dirty": None if dirty_output is None else bool(dirty_output),
        "jenai_import_path": str(Path(jenai.__file__).resolve()),
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "deployment_mode": config.deployment_mode,
        "config_path": str(config_path.resolve()),
        "config_sha256": _sha256(config_path),
        "site_id": config.site.site_id,
        "site_version": config.site.version,
        "site_map_sha256": config.site.map_sha256,
        "site_locations_sha256": config.site.locations_sha256,
        "locations_path": str(locations_path.resolve()) if locations_path else None,
        "locations_sha256": _sha256(locations_path),
        "nav_params_path": nav_params_path,
        "nav_params_sha256": _sha256(Path(nav_params_path)),
        "ambient_ros_domain_id": os.environ.get("ROS_DOMAIN_ID", "0"),
        "bridge_domain_id": bridge_domain_id,
        "rmw_implementation": os.environ.get("RMW_IMPLEMENTATION"),
        "dds_profile": os.environ.get("FASTRTPS_DEFAULT_PROFILES_FILE"),
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
        "ros_nodes": ros_nodes,
        "node_name_counts": node_counts,
        "navigate_to_pose_actions": action_lines,
        "navigate_to_pose_action_count": action_count,
        "controller_lifecycle": _command_output(
            ["ros2", "lifecycle", "get", "/controller_server"], env=ros_env
        ),
        "planner_lifecycle": _command_output(
            ["ros2", "lifecycle", "get", "/planner_server"], env=ros_env
        ),
        "bt_navigator_lifecycle": _command_output(
            ["ros2", "lifecycle", "get", "/bt_navigator"], env=ros_env
        ),
        "runtime_parameters": parameter_snapshots,
        "runtime_parameter_sha256": parameter_hashes,
        "process_inventory": _command_output(
            [
                "pgrep",
                "-af",
                "nav2|amcl|controller_server|planner_server|bt_navigator|ros_bridge",
            ]
        ),
    }
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
) -> dict[str, Any]:
    host_ns = sample.get("host_monotonic_ns")
    message = sample.get("message")
    if type(host_ns) is not int or not isinstance(message, dict):
        return {"fresh": False, "failure": "malformed_sample"}
    source_stamp_ns = _header_stamp_ns(message)
    capture_clock_ns = _clock_at_host(clock, host_ns)
    age_ns = (
        capture_clock_ns - source_stamp_ns
        if capture_clock_ns is not None and source_stamp_ns is not None
        else None
    )
    fresh = age_ns is not None and 0 <= age_ns <= int(max_age_s * 1_000_000_000)
    return {
        "host_monotonic_ns": host_ns,
        "source_stamp_ns": source_stamp_ns,
        "capture_clock_ns": capture_clock_ns,
        "source_age_ns": age_ns,
        "fresh": fresh,
        "message": message,
    }


def _latest_topic_evidence(
    recorder: _TopicRecorder,
    clock: _TopicRecorder,
    *,
    max_age_s: float,
) -> dict[str, Any] | None:
    if not recorder.samples:
        return None
    return _topic_sample_evidence(recorder.samples[-1], clock, max_age_s=max_age_s)


def _window_topic_evidence(
    recorder: _TopicRecorder,
    clock: _TopicRecorder,
    *,
    start_host_ns: int,
    end_host_ns: int,
    max_age_s: float,
) -> list[dict[str, Any]]:
    return [
        _topic_sample_evidence(sample, clock, max_age_s=max_age_s)
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
        if (
            not isinstance(raw_uuid, list)
            or len(raw_uuid) != 16
            or any(type(item) is not int or item < 0 or item > 255 for item in raw_uuid)
        ):
            continue
        records.append(
            {
                "goal_uuid": bytes(raw_uuid).hex(),
                "status": entry.get("status"),
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
) -> dict[str, Any] | None:
    if not recorder.samples:
        return None
    sample = recorder.samples[-1]
    host_ns = sample.get("host_monotonic_ns")
    message = sample.get("message")
    if type(host_ns) is not int or not isinstance(message, dict):
        return None
    statuses = message.get("status_list")
    age_ns = time.monotonic_ns() - host_ns
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


def _initial_state(
    *,
    pose: Pose2D | None,
    clock: _TopicRecorder,
    amcl: _TopicRecorder,
    odom: _TopicRecorder,
    action_status: _TopicRecorder,
    options: DifferentialCaptureOptions,
) -> dict[str, Any]:
    clock_values = [
        value
        for sample in clock.samples
        if isinstance(sample.get("message"), dict)
        and (value := _clock_ns(sample["message"])) is not None
    ]
    amcl_evidence = _latest_topic_evidence(amcl, clock, max_age_s=options.max_topic_age_s)
    odom_evidence = _latest_topic_evidence(odom, clock, max_age_s=options.max_topic_age_s)
    action_evidence = _latest_action_status_evidence(
        action_status,
        max_age_s=options.max_topic_age_s,
    )
    amcl_message = (
        cast(dict[str, Any], amcl_evidence["message"])
        if amcl_evidence is not None and amcl_evidence.get("fresh") is True
        else None
    )
    odom_message = (
        cast(dict[str, Any], odom_evidence["message"])
        if odom_evidence is not None and odom_evidence.get("fresh") is True
        else None
    )
    action_message = (
        cast(dict[str, Any], action_evidence["message"])
        if action_evidence is not None and action_evidence.get("fresh") is True
        else None
    )
    covariance = _covariance_xy(amcl_message) if amcl_message else None
    velocity = _velocity(odom_message) if odom_message else None
    active_goals = _goal_ids(action_message or {}, active_only=True)
    known_goals = _goal_ids(action_message or {}, active_only=False)
    amcl_pose = _pose_from_message(amcl_message) if amcl_message else None
    odom_pose = _pose_from_message(odom_message, odometry=True) if odom_message else None
    clock_advancing = len(clock_values) >= 2 and clock_values[-1] > clock_values[0]
    clock_backwards = any(
        right < left for left, right in zip(clock_values, clock_values[1:], strict=False)
    )
    stationary = (
        velocity is not None
        and velocity[0] <= options.max_start_speed_mps
        and abs(velocity[1]) <= options.max_start_yaw_rate_rps
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
        action_status_fresh=(action_evidence is not None and action_evidence.get("fresh") is True),
        stationary=stationary,
        active_goals=active_goals,
        max_covariance_xy=options.max_covariance_xy,
    )

    def source_metadata(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
        if evidence is None:
            return None
        return {key: value for key, value in evidence.items() if key != "message"}

    failures = list(dict.fromkeys(failures))
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "simulation_epoch": options.simulation_epoch,
        "map_to_base": pose.model_dump(mode="json") if pose else None,
        "amcl_pose": amcl_pose.model_dump(mode="json") if amcl_pose else None,
        "amcl_covariance_xy": covariance,
        "amcl_source": source_metadata(amcl_evidence),
        "odom_pose": odom_pose.model_dump(mode="json") if odom_pose else None,
        "odom_source": source_metadata(odom_evidence),
        "action_status_source": source_metadata(action_evidence),
        "linear_velocity_mps": velocity[0] if velocity else None,
        "angular_velocity_rps": velocity[1] if velocity else None,
        "stationary": stationary,
        "active_goal_ids": sorted(active_goals),
        "known_goal_ids": sorted(known_goals),
        "clock_samples_ns": clock_values,
        "clock_advancing": clock_advancing,
        "clock_backwards": clock_backwards,
    }


async def _watch_topics(
    bridge: RosBridgeClient,
    recorders: dict[str, _TopicRecorder],
    options: DifferentialCaptureOptions,
) -> list[int]:
    specs = [
        ("clock", _CLOCK_TOPIC, _CLOCK_TYPE),
        ("amcl", _AMCL_TOPIC, _AMCL_TYPE),
        ("odom", _ODOM_TOPIC, _ODOM_TYPE),
        ("action_status", _ACTION_STATUS_TOPIC, _ACTION_STATUS_TYPE),
    ]
    if options.ground_truth_topic:
        specs.append(("ground_truth", options.ground_truth_topic, options.ground_truth_type))
    watch_ids: list[int] = []
    for key, topic, message_type in specs:
        watch_ids.append(
            await bridge.watch(
                topic,
                message_type,
                recorders[key].record,
                throttle=options.sample_interval_s,
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
) -> tuple[dict[str, Any], int]:
    future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()

    def record(event: dict[str, Any]) -> None:
        if event.get("tag") == tag and not future.done():
            future.set_result(dict(event))

    bridge.on_event("nav_result", record)
    try:
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
    result_task = asyncio.create_task(_await_tagged_result(bridge, tag=tag, timeout_s=timeout_s))
    await asyncio.sleep(0)
    try:
        await bridge.nav_send(
            goal.x,
            goal.y,
            goal.yaw,
            frame_id=goal.frame_id,
            tag=tag,
        )
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
        on_nav_send: Callable[[CanonicalGoal, str, int], Awaitable[dict[str, Any]]],
    ) -> None:
        self._delegate = delegate
        self._simulation_epoch = simulation_epoch
        self._on_nav_send = on_nav_send
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
        t1_state = await self._on_nav_send(goal, tag, invoked_ns)
        observation: dict[str, Any] = {
            "tag": tag,
            "nav_send_invoked_host_monotonic_ns": invoked_ns,
            "nav_send_forwarded_host_monotonic_ns": None,
            "forward_completed_host_monotonic_ns": None,
            "actual_goal": goal.model_dump(mode="json"),
            "state_before_forward": t1_state,
        }
        self.observations.append(observation)
        if t1_state.get("status") != "PASS":
            raise BridgeError(
                "Differential dispatch state gate failed before nav_send: "
                + ", ".join(str(item) for item in t1_state.get("failures", []))
            )
        observation["nav_send_forwarded_host_monotonic_ns"] = time.monotonic_ns()
        await self._delegate.nav_send(x, y, yaw, frame_id=frame_id, tag=tag)
        observation["forward_completed_host_monotonic_ns"] = time.monotonic_ns()


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
            if goal_id in seen:
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


async def _capture_map_pose_sample(
    bridge: RosBridgeClient,
    config: AppConfig,
    options: DifferentialCaptureOptions,
    *,
    requested_ns: int,
    observed_clock_ns: int | None,
) -> dict[str, Any]:
    try:
        pose = await bridge.get_pose(
            timeout=max(0.5, options.sample_interval_s * 2.0),
            fresh=True,
            frame_id=config.site.map_frame,
            base_frame=config.vehicle.robot_base_frame,
        )
    except BridgeError as exc:
        return {
            "requested_host_monotonic_ns": requested_ns,
            "capture_clock_ns": observed_clock_ns,
            "fresh": False,
            "error": str(exc),
        }
    frame_matches = pose.frame_id.lstrip("/") == config.site.map_frame.lstrip("/")
    return {
        "requested_host_monotonic_ns": requested_ns,
        "observed_host_monotonic_ns": time.monotonic_ns(),
        "capture_clock_ns": observed_clock_ns,
        "fresh": observed_clock_ns is not None and frame_matches,
        "pose": {
            "x": pose.x,
            "y": pose.y,
            "yaw": pose.yaw,
            "frame_id": pose.frame_id,
            "source": pose.source,
        },
    }


async def _collect_final_map_window(
    bridge: RosBridgeClient,
    config: AppConfig,
    options: DifferentialCaptureOptions,
    clock: _TopicRecorder,
) -> tuple[int, int, int | None, int | None, bool, list[dict[str, Any]], list[str]]:
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
                    requested_ns=requested_ns,
                    observed_clock_ns=observed_clock_ns,
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
        and isinstance(sample.get("pose"), dict)
        and isinstance(sample.get("covariance_xy"), (int, float))
        and math.isfinite(float(sample["covariance_xy"]))
        and 0 <= float(sample["covariance_xy"]) <= max_covariance_xy
    ]


def _valid_final_odom(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        sample
        for sample in samples
        if sample.get("fresh") is True
        and isinstance(sample.get("pose"), dict)
        and isinstance(sample.get("linear_velocity_mps"), (int, float))
        and isinstance(sample.get("angular_velocity_rps"), (int, float))
        and math.isfinite(float(sample["linear_velocity_mps"]))
        and math.isfinite(float(sample["angular_velocity_rps"]))
    ]


def _final_window_failures(
    *,
    failures: list[str],
    terminal_bound: bool,
    start_clock_ns: int | None,
    end_clock_ns: int | None,
    required_duration_ns: int,
    clock_values: list[int],
    valid_map_count: int,
    min_map_count: int,
    fresh_amcl: list[dict[str, Any]],
    fresh_odom: list[dict[str, Any]],
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
        (not fresh_amcl, "no_fresh_final_amcl_samples"),
        (not fresh_odom, "no_fresh_final_odom_samples"),
        (not stationary, "robot_not_stationary_in_final_window"),
    )
    return list(dict.fromkeys([*failures, *(failure for failed, failure in checks if failed)]))


async def _sample_final_observation_window(
    bridge: RosBridgeClient,
    config: AppConfig,
    options: DifferentialCaptureOptions,
    recorders: dict[str, _TopicRecorder],
    *,
    terminal_host_ns: int | None,
) -> dict[str, Any]:
    (
        start_host_ns,
        end_host_ns,
        start_clock_ns,
        end_clock_ns,
        clock_backwards,
        map_attempts,
        failures,
    ) = await _collect_final_map_window(bridge, config, options, recorders["clock"])
    required_duration_ns = int(options.final_sample_s * 1_000_000_000)
    map_samples = [
        sample
        for sample in map_attempts
        if sample.get("fresh") is True and isinstance(sample.get("pose"), dict)
    ]
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
        max_age_s=options.max_topic_age_s,
    )
    odom_samples = _window_topic_evidence(
        recorders["odom"],
        recorders["clock"],
        start_host_ns=start_host_ns,
        end_host_ns=end_host_ns,
        max_age_s=options.max_topic_age_s,
    )
    ground_truth_samples = _window_topic_evidence(
        recorders["ground_truth"],
        recorders["clock"],
        start_host_ns=start_host_ns,
        end_host_ns=end_host_ns,
        max_age_s=options.max_topic_age_s,
    )
    _annotate_final_localization_samples(amcl_samples, odom_samples)
    fresh_amcl = _valid_final_amcl(
        amcl_samples,
        max_covariance_xy=options.max_covariance_xy,
    )
    fresh_odom = _valid_final_odom(odom_samples)
    stationary = bool(fresh_odom) and all(
        float(sample["linear_velocity_mps"]) <= options.max_start_speed_mps
        and abs(float(sample["angular_velocity_rps"])) <= options.max_start_yaw_rate_rps
        for sample in fresh_odom
    )
    failures = _final_window_failures(
        failures=failures,
        terminal_bound=terminal_host_ns is not None and start_host_ns >= terminal_host_ns,
        start_clock_ns=start_clock_ns,
        end_clock_ns=end_clock_ns,
        required_duration_ns=required_duration_ns,
        clock_values=clock_values,
        valid_map_count=len(map_samples),
        min_map_count=options.min_final_pose_samples,
        fresh_amcl=fresh_amcl,
        fresh_odom=fresh_odom,
        stationary=stationary,
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
        "clock_backwards": clock_backwards,
        "clock_samples": clock_samples,
        "map_pose_samples": map_samples,
        "map_pose_attempts": map_attempts,
        "amcl_samples": amcl_samples,
        "odom_samples": odom_samples,
        "ground_truth_samples": ground_truth_samples,
        "stationary": stationary,
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


def _atomic_write_json(path: Path, payload: dict[str, Any], *, overwrite: bool) -> None:
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
        if overwrite:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_capture_artifact(
    path: Path,
    artifact: dict[str, Any],
    *,
    overwrite: bool,
) -> dict[str, Any]:
    validated = DifferentialArtifact.model_validate(artifact).model_dump(mode="json")
    _atomic_write_json(path, validated, overwrite=overwrite)
    return validated


def _write_comparison_report(path: Path, report: dict[str, Any]) -> dict[str, Any]:
    validated = DifferentialComparisonReport.model_validate(report).model_dump(mode="json")
    _atomic_write_json(path, validated, overwrite=False)
    return validated


def _valid_domain_id(value: object) -> bool:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return False
    return 0 <= parsed <= 232


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
        and all(bool(parameter_hashes[node]) for node in required_nodes)
    )
    checks = (
        (not unique_nodes, "nav2_node_uniqueness"),
        (
            identity.get("navigate_to_pose_action_count") != 1,
            "navigate_to_pose_action_uniqueness",
        ),
        (not complete_parameters, "runtime_parameter_snapshot"),
    )
    failures.extend(failure for failed, failure in checks if failed)
    return failures


def _runtime_identity_failures(identity: dict[str, Any]) -> list[str]:
    required_hashes = (
        "config_sha256",
        "site_map_sha256",
        "site_locations_sha256",
        "locations_sha256",
        "nav_params_sha256",
        "scene_sha256",
        "live_scene_sha256",
        "live_map_sha256",
    )
    checks = (
        (identity.get("git_sha") is None, "git_revision_unavailable"),
        (identity.get("git_dirty") is not False, "clean_git_revision_required"),
        (
            identity.get("deployment_mode") != "simulation",
            "simulation_deployment_mode_required",
        ),
        (
            identity.get("scene_sha256") != identity.get("live_scene_sha256"),
            "live_scene_identity_mismatch",
        ),
        (
            identity.get("live_map_sha256") != identity.get("site_map_sha256"),
            "live_map_identity_mismatch",
        ),
        (
            identity.get("live_map_frame") != identity.get("site_map_frame"),
            "live_map_frame_mismatch",
        ),
        (not _valid_domain_id(identity.get("bridge_domain_id")), "invalid_bridge_domain_id"),
    )
    failures = [failure for failed, failure in checks if failed]
    failures.extend(f"missing_{field}" for field in required_hashes if not identity.get(field))
    failures.extend(_runtime_stack_failures(identity))
    return list(dict.fromkeys(failures))


def _measurement_contract(
    options: DifferentialCaptureOptions,
) -> DifferentialMeasurementContract:
    return DifferentialMeasurementContract(
        preflight_sample_s=options.preflight_sample_s,
        final_sample_s=options.final_sample_s,
        sample_interval_s=options.sample_interval_s,
        max_topic_age_s=options.max_topic_age_s,
        max_calibration_residual_m=options.max_calibration_residual_m,
        min_final_pose_samples=options.min_final_pose_samples,
        final_wall_timeout_s=options.final_wall_timeout_s,
        max_start_speed_mps=options.max_start_speed_mps,
        max_start_yaw_rate_rps=options.max_start_yaw_rate_rps,
        max_covariance_xy=options.max_covariance_xy,
    )


def _base_artifact(options: DifferentialCaptureOptions) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": f"nav-diff-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}",
        "pair_id": options.pair_id,
        "mode": options.mode,
        "reset_policy": options.reset_policy,
        "execution_requested": options.execute,
        "measurement_contract": _measurement_contract(options).model_dump(mode="json"),
        "started_at": _utc_now(),
        "runtime_identity": {},
        "canonical_goal": None,
        "ground_truth_calibration": None,
        "checks": [],
        "overall": "initializing",
    }


def _prepare_capture(
    options: DifferentialCaptureOptions,
) -> tuple[Path, AppConfig, dict[str, Any], CanonicalGoal, dict[str, Any]]:
    config_path = (options.config_path or default_config_path()).expanduser().resolve()
    config = load_config(config_path)
    locations_path = config.resolved_locations_path(config_path)
    if locations_path is None:
        raise ValueError("No locations_path is configured.")
    location = find_location(load_locations(locations_path), options.location)
    raw_action: dict[str, Any] = {"goal": location.model_dump(mode="json")}
    bound_action = bind_navigation_action(config, config_path, raw_action)
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
        scene_path=options.scene_path,
        live_scene_sha256=options.live_scene_sha256,
        simulation_epoch=options.simulation_epoch,
    )
    identity["site_map_frame"] = config.site.map_frame
    _apply_runtime_fingerprint(identity)
    return config_path, config, bound_action, goal, identity


async def _collect_start_state(
    bridge: RosBridgeClient,
    config: AppConfig,
    recorders: dict[str, _TopicRecorder],
    options: DifferentialCaptureOptions,
) -> dict[str, Any]:
    await asyncio.sleep(options.preflight_sample_s)
    return await _collect_dispatch_state(bridge, config, recorders, options)


async def _collect_dispatch_state(
    bridge: RosBridgeClient,
    config: AppConfig,
    recorders: dict[str, _TopicRecorder],
    options: DifferentialCaptureOptions,
) -> dict[str, Any]:
    try:
        pose_info = await bridge.get_pose(
            timeout=3.0,
            fresh=True,
            frame_id=config.site.map_frame,
            base_frame=config.vehicle.robot_base_frame,
        )
        start_pose = Pose2D(x=pose_info.x, y=pose_info.y, yaw=pose_info.yaw)
    except BridgeError:
        start_pose = None
    return _initial_state(
        pose=start_pose,
        clock=recorders["clock"],
        amcl=recorders["amcl"],
        odom=recorders["odom"],
        action_status=recorders["action_status"],
        options=options,
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

    unique_candidates = {str(item.get("goal_uuid")) for item in goal_observations}
    fresh_candidates = [item for item in goal_observations if item.get("goal_stamp_fresh") is True]
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
    terminal_ns = terminal.get("observed_host_monotonic_ns") if terminal else None
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
    t0: dict[str, Any],
) -> None:
    status_before = set(cast(list[str], t0.get("known_goal_ids", [])))

    async def observe_nav_send(
        actual_goal: CanonicalGoal,
        tag: str,
        invoked_ns: int,
    ) -> dict[str, Any]:
        del actual_goal, tag, invoked_ns
        return await _collect_dispatch_state(bridge, config, recorders, options)

    observed_bridge = _ObservedNavBridge(
        bridge,
        simulation_epoch=options.simulation_epoch,
        on_nav_send=observe_nav_send,
    )
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
    finally:
        artifact["dispatch_observations"] = observed_bridge.observations
        artifact["topic_samples_at_dispatch_end"] = {
            key: recorder.samples for key, recorder in recorders.items() if recorder.samples
        }
    returned_ns = time.monotonic_ns()
    dispatch_ns = (
        observed_bridge.observations[0].get("nav_send_forwarded_host_monotonic_ns")
        if len(observed_bridge.observations) == 1
        else request_ns
    )
    goal_observations = _new_goal_ids(
        recorders["action_status"],
        recorders["clock"],
        before=status_before,
        dispatched_at_ns=int(dispatch_ns) if type(dispatch_ns) is int else request_ns,
        max_age_s=options.max_topic_age_s,
    )
    timeline = _dispatch_timeline(
        observed_bridge.observations,
        goal_observations,
        terminal,
        request_ns=request_ns,
        returned_ns=returned_ns,
    )
    artifact["t1_goal_dispatch"] = timeline
    artifact["nav2_terminal"] = terminal
    artifact["jenai_result"] = jenai_result

    terminal_ns = terminal.get("observed_host_monotonic_ns") if terminal else None
    final_window = await _sample_final_observation_window(
        bridge,
        config,
        options,
        recorders,
        terminal_host_ns=terminal_ns if type(terminal_ns) is int else None,
    )
    artifact["final_observation_window"] = final_window
    map_samples = cast(list[dict[str, Any]], final_window["map_pose_samples"])
    median = _median_pose(map_samples)
    artifact["final_map_pose_samples"] = map_samples
    artifact["final_map_pose_median"] = median.model_dump(mode="json") if median else None

    gt_samples = _ground_truth_samples(
        cast(list[dict[str, Any]], final_window["ground_truth_samples"]),
        calibration,
    )
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
    artifact["topic_samples"] = {
        key: recorder.samples for key, recorder in recorders.items() if recorder.samples
    }
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
    )
    artifact["overall"] = "captured" if evidence_complete else "insufficient_evidence"


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


async def _cleanup_live_capture(
    bridge: RosBridgeClient,
    config: AppConfig,
    watch_ids: list[int],
    heartbeat: asyncio.Task[None] | None,
    *,
    motion_attempted: bool,
) -> dict[str, Any]:
    halt, failures = await _cleanup_halt(bridge, config, motion_attempted=motion_attempted)
    if heartbeat is not None:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            failures.append({"step": "heartbeat", "detail": str(exc)})
    unwatch, unwatch_failures = await _cleanup_unwatch(bridge, watch_ids)
    shutdown, shutdown_failures = await _cleanup_bridge_stop(bridge)
    failures.extend(unwatch_failures)
    failures.extend(shutdown_failures)
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "final_halt": halt,
        "unwatch": unwatch,
        "bridge_shutdown": shutdown,
    }


async def _enrich_live_identity(
    bridge: RosBridgeClient,
    identity: dict[str, Any],
) -> None:
    live_map = await bridge.map_identity(timeout=3.0)
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
        artifact["ground_truth_calibration"] = calibration.model_dump(mode="json")
        artifact["overall"] = "preflight_only"
        artifact["checks"].append(
            {
                "id": "execution",
                "status": "SKIP",
                "detail": "Live motion was not requested.",
            }
        )
        return True
    if config.deployment_mode == "simulation":
        return False
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


async def _safe_cleanup_live_capture(
    bridge: RosBridgeClient,
    config: AppConfig,
    watch_ids: list[int],
    heartbeat: asyncio.Task[None] | None,
    *,
    motion_attempted: bool,
) -> dict[str, Any]:
    try:
        return await _cleanup_live_capture(
            bridge,
            config,
            watch_ids,
            heartbeat,
            motion_attempted=motion_attempted,
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


async def capture_navigation_differential(
    options: DifferentialCaptureOptions,
) -> dict[str, Any]:
    """Capture one R1 or R2 run and persist every valid request outcome."""

    options = DifferentialCaptureOptions.model_validate(options)
    artifact = _base_artifact(options)
    config: AppConfig | None = None
    bridge: RosBridgeClient | None = None
    watch_ids: list[int] = []
    heartbeat: asyncio.Task[None] | None = None
    motion_attempted = False
    cancelled: asyncio.CancelledError | None = None
    stage = "prepare"
    try:
        config_path, config, bound_action, goal, identity = _prepare_capture(options)
        artifact["runtime_identity"] = identity
        artifact["canonical_goal"] = goal.model_dump(mode="json")
        if not _complete_without_live_bridge(options, config, identity, artifact):
            stage = "bridge_start"
            bridge = RosBridgeClient(domain_id=config.vehicle.domain_id)
            await bridge.configure_safety(
                watchdog_s=6.0,
                cmd_vel_topic=config.vehicle.cmd_vel_topic,
                stamped=config.vehicle.cmd_vel_stamped,
                pose_jump_threshold_m=config.vehicle.pose_jump_threshold_m,
                pose_jump_window_s=config.vehicle.pose_jump_window_s,
            )
            await bridge.start()
            stage = "live_identity"
            await _enrich_live_identity(bridge, identity)
            calibration = _load_calibration(options, identity)
            artifact["ground_truth_calibration"] = calibration.model_dump(mode="json")
            identity_failures = _runtime_identity_failures(identity)
            if identity_failures:
                artifact["overall"] = "blocked"
                artifact["checks"].append(
                    {
                        "id": "runtime_identity_gate",
                        "status": "FAIL",
                        "detail": "Live runtime identity is incomplete or not release-clean.",
                        "failures": identity_failures,
                    }
                )
            else:
                stage = "topic_watch"
                recorders = {
                    key: _TopicRecorder()
                    for key in ("clock", "amcl", "odom", "action_status", "ground_truth")
                }
                watch_ids = await _watch_topics(bridge, recorders, options)
                heartbeat = asyncio.create_task(_heartbeat(bridge))
                stage = "start_gate"
                t0 = await _collect_start_state(bridge, config, recorders, options)
                artifact["t0_scenario_start"] = t0
                if t0["status"] != "PASS":
                    artifact["overall"] = "blocked"
                    artifact["checks"].append(
                        {
                            "id": "pairing_start_gate",
                            "status": "FAIL",
                            "detail": "Start state was not eligible for paired execution.",
                            "failures": t0["failures"],
                        }
                    )
                else:
                    stage = "motion_dispatch"
                    motion_attempted = True
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
                        t0,
                    )
    except asyncio.CancelledError as exc:
        cancelled = exc
        artifact["overall"] = "failed"
        artifact["failure"] = {
            "type": type(exc).__name__,
            "stage": stage,
            "detail": "Capture task was cancelled.",
        }
    except Exception as exc:
        artifact["overall"] = "failed"
        artifact["failure"] = {
            "type": type(exc).__name__,
            "stage": stage,
            "detail": str(exc),
        }
    finally:
        if bridge is not None and config is not None:
            cleanup = await _safe_cleanup_live_capture(
                bridge,
                config,
                watch_ids,
                heartbeat,
                motion_attempted=motion_attempted,
            )
            artifact["cleanup"] = cleanup
            artifact["final_halt"] = cleanup.get("final_halt")
            if cleanup["status"] != "PASS":
                artifact["overall_before_cleanup"] = artifact.get("overall")
                artifact["overall"] = "cleanup_failed"
        artifact["finished_at"] = _utc_now()
        artifact = _write_capture_artifact(
            options.output,
            artifact,
            overwrite=options.overwrite,
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


def _comparison_eligibility_failure(artifact: dict[str, Any], side: str) -> str | None:
    if artifact.get("overall") != "captured":
        return f"{side} artifact overall is not captured."
    t0 = artifact.get("t0_scenario_start")
    if not isinstance(t0, dict) or t0.get("status") != "PASS":
        return f"{side} T0 start gate did not pass."
    final_window = artifact.get("final_observation_window")
    if not isinstance(final_window, dict) or final_window.get("status") != "PASS":
        return f"{side} final observation window did not pass."
    cleanup = artifact.get("cleanup")
    if not isinstance(cleanup, dict) or cleanup.get("status") != "PASS":
        return f"{side} cleanup did not pass."
    if _actual_dispatch_goal(artifact) is None:
        return f"{side} actual dispatch goal evidence is unavailable."
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


def _pairing_gate_from_artifacts(
    left: dict[str, Any],
    right: dict[str, Any],
) -> tuple[PairingGateResult | None, str | None]:
    left_identity = cast(dict[str, Any], left["runtime_identity"])
    right_identity = cast(dict[str, Any], right["runtime_identity"])
    left_t0 = cast(dict[str, Any], left["t0_scenario_start"])
    right_t0 = cast(dict[str, Any], right["t0_scenario_start"])
    left_start = _artifact_pose(left_t0, "map_to_base")
    right_start = _artifact_pose(right_t0, "map_to_base")
    if left_start is None or right_start is None:
        return None, "Comparable start poses are missing."
    try:
        left_covariance = float(left_t0["amcl_covariance_xy"])
        right_covariance = float(right_t0["amcl_covariance_xy"])
    except (KeyError, TypeError, ValueError):
        return None, "Comparable localization covariance is missing."
    if not all(
        math.isfinite(value) and value >= 0 for value in (left_covariance, right_covariance)
    ):
        return None, "Comparable localization covariance is invalid."
    gate = evaluate_pairing_gate(
        left_runtime_fingerprint=str(left_identity.get("fingerprint") or ""),
        right_runtime_fingerprint=str(right_identity.get("fingerprint") or ""),
        left_epoch=str(left_t0.get("simulation_epoch") or ""),
        right_epoch=str(right_t0.get("simulation_epoch") or ""),
        left_start=left_start,
        right_start=right_start,
        left_covariance_xy=left_covariance,
        right_covariance_xy=right_covariance,
        left_stationary=left_t0.get("stationary") is True,
        right_stationary=right_t0.get("stationary") is True,
        left_active_goal=bool(left_t0.get("active_goal_ids")),
        right_active_goal=bool(right_t0.get("active_goal_ids")),
    )
    metadata_checks = (
        (left.get("pair_id") != right.get("pair_id"), "pair_id"),
        (left.get("reset_policy") != right.get("reset_policy"), "reset_policy"),
        (
            left.get("measurement_contract") != right.get("measurement_contract"),
            "measurement_contract",
        ),
        (
            {str(left.get("mode")), str(right.get("mode"))}
            != {
                DifferentialMode.R1_BRIDGE_NAV2.value,
                DifferentialMode.R2_JENAI_NO_RETRY.value,
            },
            "differential_modes",
        ),
    )
    metadata_failures = tuple(failure for failed, failure in metadata_checks if failed)
    if metadata_failures:
        gate = gate.model_copy(
            update={
                "status": PairingGate.FAILED,
                "failures": (*gate.failures, *metadata_failures),
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
        "detail": None,
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
