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
import time
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

import jenai
from jenai.acceptance.nav_differential import (
    CanonicalGoal,
    GroundTruthCalibration,
    PairClassification,
    PairingGate,
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
    calibration_path: Path | None = None
    ground_truth_topic: str | None = None
    ground_truth_type: str = "geometry_msgs/msg/PoseStamped"
    execute: bool = False
    confirmation: str = ""
    overwrite: bool = False
    timeout_s: float = Field(default=300.0, gt=0, allow_inf_nan=False)
    preflight_sample_s: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    final_sample_s: float = Field(default=2.0, gt=0, allow_inf_nan=False)
    sample_interval_s: float = Field(default=0.2, gt=0, allow_inf_nan=False)
    max_start_speed_mps: float = Field(default=0.02, ge=0, allow_inf_nan=False)
    max_start_yaw_rate_rps: float = Field(default=0.03, ge=0, allow_inf_nan=False)
    max_covariance_xy: float = Field(default=0.1, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def execution_requires_confirmation(self) -> DifferentialCaptureOptions:
        if self.execute and self.confirmation != DIFFERENTIAL_EXECUTION_CONFIRMATION:
            raise ValueError(
                "Live differential capture requires the exact confirmation text: "
                f"{DIFFERENTIAL_EXECUTION_CONFIRMATION}"
            )
        if self.execute and (self.scene_path is None or not self.scene_path.is_file()):
            raise ValueError(
                "Live differential capture requires an existing absolute USD scene path."
            )
        if self.output.exists() and not self.overwrite:
            raise ValueError(f"Output already exists: {self.output}")
        if bool(self.ground_truth_topic) != bool(self.calibration_path):
            raise ValueError("ground_truth_topic and calibration_path must be configured together")
        return self


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


def _command_output(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _runtime_identity(
    config: AppConfig,
    config_path: Path,
    *,
    scene_path: Path | None,
    simulation_epoch: str,
) -> dict[str, Any]:
    locations_path = config.resolved_locations_path(config_path)
    nav_params_path = os.environ.get("JENAI_NAV2_OVERRIDE_PARAMS")
    if not nav_params_path:
        uid = os.getuid()
        session = os.environ.get("JENAI_NAV2_TMUX_SESSION", "nav2")
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/tmp/jenai-nav2-{uid}")
        nav_params_path = str(Path(runtime_dir) / f"{session}-params.yaml")
    revision = _command_output(["git", "rev-parse", "HEAD"])
    dirty_output = _command_output(["git", "status", "--porcelain"])
    identity = {
        "git_sha": revision,
        "git_dirty": None if dirty_output is None else bool(dirty_output),
        "jenai_import_path": str(Path(jenai.__file__).resolve()),
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
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
        "ros_domain_id": os.environ.get("ROS_DOMAIN_ID", "0"),
        "rmw_implementation": os.environ.get("RMW_IMPLEMENTATION"),
        "dds_profile": os.environ.get("FASTRTPS_DEFAULT_PROFILES_FILE"),
        "dds_profile_sha256": _sha256(
            Path(os.environ["FASTRTPS_DEFAULT_PROFILES_FILE"])
            if os.environ.get("FASTRTPS_DEFAULT_PROFILES_FILE")
            else None
        ),
        "scene_path": str(scene_path.resolve()) if scene_path else config.site.reference_scene,
        "scene_sha256": _sha256(scene_path),
        "simulation_epoch": simulation_epoch,
        "ros_nodes": _command_output(["ros2", "node", "list"]),
        "controller_lifecycle": _command_output(["ros2", "lifecycle", "get", "/controller_server"]),
        "planner_lifecycle": _command_output(["ros2", "lifecycle", "get", "/planner_server"]),
        "bt_navigator_lifecycle": _command_output(["ros2", "lifecycle", "get", "/bt_navigator"]),
        "process_inventory": _command_output(
            [
                "pgrep",
                "-af",
                "nav2|amcl|controller_server|planner_server|bt_navigator|ros_bridge",
            ]
        ),
    }
    fingerprint_fields = {
        key: identity[key]
        for key in (
            "git_sha",
            "git_dirty",
            "jenai_import_path",
            "config_sha256",
            "site_id",
            "site_version",
            "site_map_sha256",
            "site_locations_sha256",
            "nav_params_sha256",
            "ros_domain_id",
            "rmw_implementation",
            "dds_profile_sha256",
            "scene_path",
            "scene_sha256",
            "controller_lifecycle",
            "planner_lifecycle",
            "bt_navigator_lifecycle",
        )
    }
    identity["fingerprint"] = hashlib.sha256(
        json.dumps(fingerprint_fields, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return identity


def _clock_ns(message: dict[str, Any]) -> int | None:
    clock = message.get("clock")
    if not isinstance(clock, dict):
        return None
    sec = clock.get("sec")
    nanosec = clock.get("nanosec")
    if type(sec) is not int or type(nanosec) is not int:
        return None
    return sec * 1_000_000_000 + nanosec


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
        return max(float(covariance[0]), float(covariance[7]))
    except (TypeError, ValueError):
        return None


def _velocity(message: dict[str, Any]) -> tuple[float, float] | None:
    twist = _nested_dict(message, "twist", "twist")
    if twist is None:
        return None
    linear = twist.get("linear")
    angular = twist.get("angular")
    if not isinstance(linear, dict) or not isinstance(angular, dict):
        return None
    try:
        return float(linear["x"]), float(angular["z"])
    except (KeyError, TypeError, ValueError):
        return None


def _goal_ids(message: dict[str, Any], *, active_only: bool) -> set[str]:
    statuses = message.get("status_list")
    if not isinstance(statuses, list):
        return set()
    result: set[str] = set()
    for entry in statuses:
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        if active_only and status not in {1, 2, 3}:
            continue
        goal_info = entry.get("goal_info")
        goal_id = goal_info.get("goal_id") if isinstance(goal_info, dict) else None
        raw_uuid = goal_id.get("uuid") if isinstance(goal_id, dict) else None
        if isinstance(raw_uuid, list):
            try:
                result.add(bytes(int(item) for item in raw_uuid).hex())
            except (TypeError, ValueError):
                continue
    return result


def _latest_message(recorder: _TopicRecorder) -> dict[str, Any] | None:
    if not recorder.samples:
        return None
    message = recorder.samples[-1].get("message")
    return message if isinstance(message, dict) else None


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
    amcl_message = _latest_message(amcl)
    odom_message = _latest_message(odom)
    action_message = _latest_message(action_status)
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
        and abs(velocity[0]) <= options.max_start_speed_mps
        and abs(velocity[1]) <= options.max_start_yaw_rate_rps
    )
    failures: list[str] = []
    if pose is None:
        failures.append("map_pose_unavailable")
    if not clock_advancing or clock_backwards:
        failures.append("clock_not_advancing")
    if covariance is None or covariance > options.max_covariance_xy:
        failures.append("amcl_covariance")
    if not stationary:
        failures.append("robot_not_stationary")
    if active_goals:
        failures.append("active_nav2_goal")
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "simulation_epoch": options.simulation_epoch,
        "map_to_base": pose.model_dump(mode="json") if pose else None,
        "amcl_pose": amcl_pose.model_dump(mode="json") if amcl_pose else None,
        "amcl_covariance_xy": covariance,
        "odom_pose": odom_pose.model_dump(mode="json") if odom_pose else None,
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
    await bridge.nav_send(
        goal.x,
        goal.y,
        goal.yaw,
        frame_id=goal.frame_id,
        tag=tag,
    )
    terminal, terminal_ns = await result_task
    terminal["observed_host_monotonic_ns"] = terminal_ns
    return terminal, None


async def _run_r2(
    bridge: RosBridgeClient,
    config: AppConfig,
    config_path: Path,
    outgoing_action: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    no_retry_vehicle = config.vehicle.model_copy(update={"nav_endpoint_retry_limit": 0})
    execution_config = config.model_copy(update={"vehicle": no_retry_vehicle})
    ambient_domain = os.environ.get("ROS_DOMAIN_ID", "0")
    if (
        execution_config.deployment_mode == "simulation"
        and execution_config.twin.enabled
        and str(execution_config.twin.domain_id) == ambient_domain
    ):
        execution_config = execution_config.model_copy(
            update={"twin": execution_config.twin.model_copy(update={"enabled": False})}
        )

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
    }


async def _return_bridge(bridge: RosBridgeClient) -> RosBridgeClient:
    return bridge


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
            timeout_s=options.timeout_s,
        )
    return await _run_r2(bridge, config, config_path, bound_action)


def _new_goal_ids(
    recorder: _TopicRecorder,
    *,
    before: set[str],
    dispatched_at_ns: int,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    seen = set(before)
    for sample in recorder.samples:
        host_ns = sample.get("host_monotonic_ns")
        message = sample.get("message")
        if type(host_ns) is not int or host_ns < dispatched_at_ns or not isinstance(message, dict):
            continue
        current = _goal_ids(message, active_only=False)
        observations.extend(
            {
                "goal_uuid": goal_id,
                "observed_host_monotonic_ns": host_ns,
            }
            for goal_id in sorted(current - seen)
        )
        seen.update(current)
    return observations


async def _sample_final_map_pose(
    bridge: RosBridgeClient,
    config: AppConfig,
    options: DifferentialCaptureOptions,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    deadline = time.monotonic() + options.final_sample_s
    while time.monotonic() < deadline:
        requested_ns = time.monotonic_ns()
        try:
            pose = await bridge.get_pose(
                timeout=max(0.5, options.sample_interval_s * 2.0),
                fresh=True,
                frame_id=config.site.map_frame,
                base_frame=config.vehicle.robot_base_frame,
            )
        except BridgeError as exc:
            samples.append(
                {
                    "requested_host_monotonic_ns": requested_ns,
                    "error": str(exc),
                }
            )
        else:
            samples.append(
                {
                    "requested_host_monotonic_ns": requested_ns,
                    "observed_host_monotonic_ns": time.monotonic_ns(),
                    "pose": {
                        "x": pose.x,
                        "y": pose.y,
                        "yaw": pose.yaw,
                        "frame_id": pose.frame_id,
                        "source": pose.source,
                    },
                }
            )
        await asyncio.sleep(options.sample_interval_s)
    return samples


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


def _load_calibration(
    options: DifferentialCaptureOptions,
    runtime_identity: dict[str, Any],
) -> GroundTruthCalibration:
    if options.calibration_path is None:
        return GroundTruthCalibration(
            status="GROUND_TRUTH_UNAVAILABLE",
            scene_sha256=str(runtime_identity.get("scene_sha256") or "0" * 64),
            map_sha256=str(runtime_identity.get("site_map_sha256") or "0" * 64),
            source="no map/world calibration configured",
        )
    calibration = GroundTruthCalibration.model_validate_json(
        options.calibration_path.read_text(encoding="utf-8")
    )
    if calibration.scene_sha256 != runtime_identity.get(
        "scene_sha256"
    ) or calibration.map_sha256 != runtime_identity.get("site_map_sha256"):
        return GroundTruthCalibration(
            status="GROUND_TRUTH_UNAVAILABLE",
            scene_sha256=str(runtime_identity.get("scene_sha256") or "0" * 64),
            map_sha256=str(runtime_identity.get("site_map_sha256") or "0" * 64),
            source="calibration scene/map identity does not match this run",
        )
    return calibration


def _ground_truth_samples(
    recorder: _TopicRecorder,
    calibration: GroundTruthCalibration,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for sample in recorder.samples:
        message = sample.get("message")
        if not isinstance(message, dict):
            continue
        world_pose = _pose_from_message(message)
        if world_pose is None:
            continue
        map_pose = calibration.world_to_map(world_pose)
        results.append(
            {
                "host_monotonic_ns": sample["host_monotonic_ns"],
                "world_pose": world_pose.model_dump(mode="json"),
                "map_pose": map_pose.model_dump(mode="json") if map_pose else None,
            }
        )
    return results


def _write_artifact(path: Path, artifact: dict[str, Any], *, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if overwrite else "x"
    with path.open(mode, encoding="utf-8") as stream:
        json.dump(artifact, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def _runtime_identity_failures(identity: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if identity.get("git_dirty") is not False:
        failures.append("clean_git_revision_required")
    required_hashes = (
        "config_sha256",
        "site_map_sha256",
        "site_locations_sha256",
        "nav_params_sha256",
        "scene_sha256",
    )
    failures.extend(f"missing_{field}" for field in required_hashes if not identity.get(field))
    for field in ("controller_lifecycle", "planner_lifecycle", "bt_navigator_lifecycle"):
        value = identity.get(field)
        if not isinstance(value, str) or not value.startswith("active"):
            failures.append(f"inactive_{field}")
    return failures


def _prepare_capture(
    options: DifferentialCaptureOptions,
) -> tuple[Path, AppConfig, dict[str, Any], CanonicalGoal, GroundTruthCalibration, dict[str, Any]]:
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
        simulation_epoch=options.simulation_epoch,
    )
    calibration = _load_calibration(options, identity)
    artifact: dict[str, Any] = {
        "schema_version": 1,
        "run_id": f"nav-diff-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}",
        "pair_id": options.pair_id,
        "mode": options.mode,
        "reset_policy": options.reset_policy,
        "execution_requested": options.execute,
        "started_at": _utc_now(),
        "runtime_identity": identity,
        "canonical_goal": goal.model_dump(mode="json"),
        "ground_truth_calibration": calibration.model_dump(mode="json"),
        "checks": [],
    }
    return config_path, config, bound_action, goal, calibration, artifact


async def _collect_start_state(
    bridge: RosBridgeClient,
    config: AppConfig,
    recorders: dict[str, _TopicRecorder],
    options: DifferentialCaptureOptions,
) -> dict[str, Any]:
    await asyncio.sleep(options.preflight_sample_s)
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
    goal: CanonicalGoal,
    goal_observations: list[dict[str, Any]],
    terminal: dict[str, Any] | None,
    *,
    dispatched_ns: int,
    returned_ns: int,
) -> dict[str, Any]:
    accepted_ns = (
        int(goal_observations[0]["observed_host_monotonic_ns"]) if goal_observations else None
    )
    terminal_ns = terminal.get("observed_host_monotonic_ns") if terminal else None
    return {
        "request_host_monotonic_ns": dispatched_ns,
        "return_host_monotonic_ns": returned_ns,
        "accepted_goal_observations": goal_observations,
        "accepted_goal_uuid": goal_observations[0]["goal_uuid"] if goal_observations else None,
        "goal_uuid_evidence": (
            "NAV2_ACTION_STATUS" if goal_observations else "UNAVAILABLE_NO_NEW_STATUS_UUID_OBSERVED"
        ),
        "canonical_goal": goal.model_dump(mode="json"),
        "latency_ms": {
            "request_to_accept": (
                round((accepted_ns - dispatched_ns) / 1_000_000.0, 3)
                if accepted_ns is not None
                else None
            ),
            "accept_to_terminal": (
                round((int(terminal_ns) - accepted_ns) / 1_000_000.0, 3)
                if accepted_ns is not None and type(terminal_ns) is int
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
    status_before = set(t0["known_goal_ids"])
    dispatched_ns = time.monotonic_ns()
    terminal, jenai_result = await _dispatch_mode(
        options, bridge, config, config_path, goal, bound_action
    )
    returned_ns = time.monotonic_ns()
    final_map_samples = await _sample_final_map_pose(bridge, config, options)
    goal_observations = _new_goal_ids(
        recorders["action_status"],
        before=status_before,
        dispatched_at_ns=dispatched_ns,
    )
    artifact["t1_goal_dispatch"] = _dispatch_timeline(
        goal,
        goal_observations,
        terminal,
        dispatched_ns=dispatched_ns,
        returned_ns=returned_ns,
    )
    artifact["nav2_terminal"] = terminal
    artifact["jenai_result"] = jenai_result
    artifact["final_map_pose_samples"] = final_map_samples
    median = _median_pose(final_map_samples)
    artifact["final_map_pose_median"] = median.model_dump(mode="json") if median else None
    gt_samples = _ground_truth_samples(recorders["ground_truth"], calibration)
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
        median is not None and bool(goal_observations) and len(recorders["clock"].samples) >= 2
    )
    artifact["overall"] = "captured" if evidence_complete else "insufficient_evidence"


async def _cleanup_live_capture(
    bridge: RosBridgeClient,
    config: AppConfig,
    watch_ids: list[int],
    heartbeat: asyncio.Task[None] | None,
    artifact: dict[str, Any],
    *,
    motion_attempted: bool,
) -> None:
    halt_evidence: dict[str, Any] | None = None
    if motion_attempted and bridge.running:
        with contextlib.suppress(BridgeError):
            evidence = await bridge.halt_with_evidence(
                config.vehicle.cmd_vel_topic,
                config.vehicle.cmd_vel_stamped,
            )
            halt_evidence = {
                "zero_velocity_command_published": evidence.zero_velocity_command_published,
                "navigation_cancel_requested": evidence.navigation_cancel_requested,
                "navigation_cancel_acknowledged": evidence.navigation_cancel_acknowledged,
            }
    artifact["final_halt"] = halt_evidence
    if heartbeat is not None:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError, BridgeError):
            await heartbeat
    for watch_id in watch_ids:
        with contextlib.suppress(BridgeError):
            await bridge.unwatch(watch_id)
    with contextlib.suppress(BridgeError):
        await bridge.stop()


async def capture_navigation_differential(
    options: DifferentialCaptureOptions,
) -> dict[str, Any]:
    """Capture one R1 or R2 run and always persist its evidence artifact."""

    options = DifferentialCaptureOptions.model_validate(options)
    config_path, config, bound_action, goal, calibration, artifact = _prepare_capture(options)
    if not options.execute:
        artifact["overall"] = "preflight_only"
        artifact["checks"].append(
            {
                "id": "execution",
                "status": "SKIP",
                "detail": "Live motion was not requested.",
            }
        )
        _write_artifact(options.output, artifact, overwrite=options.overwrite)
        return artifact

    identity_failures = _runtime_identity_failures(artifact["runtime_identity"])
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
        artifact["finished_at"] = _utc_now()
        _write_artifact(options.output, artifact, overwrite=options.overwrite)
        return artifact

    bridge = RosBridgeClient(domain_id=config.vehicle.domain_id)
    recorders = {
        key: _TopicRecorder() for key in ("clock", "amcl", "odom", "action_status", "ground_truth")
    }
    watch_ids: list[int] = []
    heartbeat: asyncio.Task[None] | None = None
    motion_attempted = False
    try:
        await bridge.configure_safety(
            watchdog_s=6.0,
            cmd_vel_topic=config.vehicle.cmd_vel_topic,
            stamped=config.vehicle.cmd_vel_stamped,
            pose_jump_threshold_m=config.vehicle.pose_jump_threshold_m,
            pose_jump_window_s=config.vehicle.pose_jump_window_s,
        )
        await bridge.start()
        watch_ids = await _watch_topics(bridge, recorders, options)
        heartbeat = asyncio.create_task(_heartbeat(bridge))
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
    except (BridgeError, TimeoutError) as exc:
        artifact["overall"] = "failed"
        artifact["failure"] = {"type": type(exc).__name__, "detail": str(exc)}
    finally:
        await _cleanup_live_capture(
            bridge,
            config,
            watch_ids,
            heartbeat,
            artifact,
            motion_attempted=motion_attempted,
        )
        artifact["finished_at"] = _utc_now()
        _write_artifact(options.output, artifact, overwrite=options.overwrite)
    return artifact


def _artifact_pose(artifact: dict[str, Any], key: str) -> Pose2D | None:
    value = artifact.get(key)
    if not isinstance(value, dict):
        return None
    try:
        return Pose2D.model_validate(value)
    except ValueError:
        return None


def compare_differential_artifacts(
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
    """Compare one R1/R2 pair and preserve every exclusion reason."""

    left_identity = left.get("runtime_identity")
    right_identity = right.get("runtime_identity")
    left_t0 = left.get("t0_scenario_start")
    right_t0 = right.get("t0_scenario_start")
    if not all(
        isinstance(value, dict) for value in (left_identity, right_identity, left_t0, right_t0)
    ):
        return {
            "pair_id": left.get("pair_id") or right.get("pair_id"),
            "included": False,
            "classifications": [PairClassification.INSUFFICIENT_EVIDENCE],
            "detail": "Runtime identity or T0 start evidence is missing.",
        }
    left_identity = cast(dict[str, Any], left_identity)
    right_identity = cast(dict[str, Any], right_identity)
    left_t0 = cast(dict[str, Any], left_t0)
    right_t0 = cast(dict[str, Any], right_t0)
    left_start = _artifact_pose(left_t0, "map_to_base")
    right_start = _artifact_pose(right_t0, "map_to_base")
    if left_start is None or right_start is None:
        return {
            "pair_id": left.get("pair_id") or right.get("pair_id"),
            "included": False,
            "classifications": [PairClassification.INSUFFICIENT_EVIDENCE],
            "detail": "Comparable start poses are missing.",
        }
    gate = evaluate_pairing_gate(
        left_runtime_fingerprint=str(left_identity.get("fingerprint") or ""),
        right_runtime_fingerprint=str(right_identity.get("fingerprint") or ""),
        left_epoch=str(left_t0.get("simulation_epoch") or ""),
        right_epoch=str(right_t0.get("simulation_epoch") or ""),
        left_start=left_start,
        right_start=right_start,
        left_covariance_xy=(
            math.inf
            if left_t0.get("amcl_covariance_xy") is None
            else float(left_t0["amcl_covariance_xy"])
        ),
        right_covariance_xy=(
            math.inf
            if right_t0.get("amcl_covariance_xy") is None
            else float(right_t0["amcl_covariance_xy"])
        ),
        left_stationary=left_t0.get("stationary") is True,
        right_stationary=right_t0.get("stationary") is True,
        left_active_goal=bool(left_t0.get("active_goal_ids")),
        right_active_goal=bool(right_t0.get("active_goal_ids")),
    )
    metadata_failures: list[str] = []
    if left.get("pair_id") != right.get("pair_id"):
        metadata_failures.append("pair_id")
    if left.get("reset_policy") != right.get("reset_policy"):
        metadata_failures.append("reset_policy")
    observed_modes = {str(left.get("mode")), str(right.get("mode"))}
    expected_modes = {
        DifferentialMode.R1_BRIDGE_NAV2.value,
        DifferentialMode.R2_JENAI_NO_RETRY.value,
    }
    if observed_modes != expected_modes:
        metadata_failures.append("differential_modes")
    if metadata_failures:
        gate = gate.model_copy(
            update={
                "status": PairingGate.FAILED,
                "failures": (*gate.failures, *metadata_failures),
            }
        )
    left_goal = CanonicalGoal.model_validate(left["canonical_goal"])
    right_goal = CanonicalGoal.model_validate(right["canonical_goal"])
    classifications = classify_pair(
        left_goal=left_goal,
        right_goal=right_goal,
        pairing_gate=gate,
        left_final_map=_artifact_pose(left, "final_map_pose_median"),
        right_final_map=_artifact_pose(right, "final_map_pose_median"),
        left_final_ground_truth=_artifact_pose(left, "final_ground_truth_map_median"),
        right_final_ground_truth=_artifact_pose(right, "final_ground_truth_map_median"),
        left_execution_status=(
            str(left["execution_status"]) if left.get("execution_status") else None
        ),
        right_execution_status=(
            str(right["execution_status"]) if right.get("execution_status") else None
        ),
    )
    return {
        "pair_id": left.get("pair_id") or right.get("pair_id"),
        "included": gate.status.value == "PASSED",
        "pairing_gate": gate.model_dump(mode="json"),
        "classifications": classifications,
    }


def load_and_compare(left_path: Path, right_path: Path, output: Path) -> dict[str, Any]:
    left = json.loads(left_path.read_text(encoding="utf-8"))
    right = json.loads(right_path.read_text(encoding="utf-8"))
    if not isinstance(left, dict) or not isinstance(right, dict):
        raise ValueError("Differential artifacts must contain JSON objects.")
    report = compare_differential_artifacts(left, right)
    _write_artifact(output, report, overwrite=False)
    return report
