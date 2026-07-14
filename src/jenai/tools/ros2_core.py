"""ROS2 topic ops (topics/echo/schema/pub/drive); vehicle hard speed clamp lives here."""

from __future__ import annotations

import asyncio
import copy
import json
import math
from dataclasses import dataclass

from jenai.adapters import ros2_adapter
from jenai.config.models import AppConfig
from jenai.schemas import (
    ErrorType,
    JenAIError,
    RosEchoOutput,
    RosPubOutput,
    RosSchemaOutput,
    RosTopicInfoOutput,
    RosTopicsOutput,
    TopicItem,
)
from jenai.tools.summaries import summarize_ros_schema

# Ordered: first match wins, so plumbing beats domain words (e.g.
# /amcl/transition_event is infra, not nav).
_KIND_HINTS = (
    (
        (
            "transition_event",
            "parameter_events",
            "rosout",
            "bond",
            "clock",
            "lifecycle",
            "diagnostics",
            "_action/",
        ),
        "infra",
    ),
    (("cmd",), "control"),
    (
        (
            "scan",
            "image",
            "imu",
            "odom",
            "camera",
            "lidar",
            "pointcloud",
            "points",
            "depth",
            "battery",
            "joint_state",
            "gps",
            "/fix",
        ),
        "sensor",
    ),
    (
        (
            "map",
            "plan",
            "path",
            "goal",
            "waypoint",
            "behavior_tree",
            "navigate",
            "amcl",
            "particle",
            "dock",
            "footprint",
            "cost_cloud",
            "route",
            "speed_limit",
            "collision_monitor",
            "controller_selector",
            "initialpose",
            "staging_pose",
            "clicked_point",
        ),
        "nav",
    ),
    (("/tf", "tf_static"), "tf"),
    (("debug",), "debug"),
)


def _kind_hint(topic: str) -> str:
    lowered = topic.lower()
    for keywords, kind in _KIND_HINTS:
        if any(keyword in lowered for keyword in keywords):
            return kind
    return "unknown"


async def ros_topics(config: AppConfig) -> RosTopicsOutput:
    _ = config
    topics = await asyncio.to_thread(ros2_adapter.list_topics)
    return RosTopicsOutput(
        topics=[TopicItem(name=name, kind_hint=_kind_hint(name)) for name in topics]
    )


def _fuzzy_topic_candidates(topic: str, topics: list[str], limit: int = 5) -> list[str]:
    needle = topic.strip().strip("/").lower()
    if not needle:
        return topics[:limit]
    return [name for name in topics if needle in name.lower()][:limit]


async def ros_topic_info(config: AppConfig, topic: str) -> RosTopicInfoOutput:
    """Resolve a single topic's type, publishers and subscribers.

    When the topic is not present on the graph, return fuzzy candidates rather
    than raising, so the caller can suggest alternatives.
    """
    _ = config
    topics = await asyncio.to_thread(ros2_adapter.list_topics)
    if topic not in topics:
        candidates = _fuzzy_topic_candidates(topic, topics)
        hint = (
            f"Did you mean: {', '.join(candidates)}?"
            if candidates
            else "Run /ros topics to see available topics."
        )
        return RosTopicInfoOutput(
            name=topic,
            summary=f"Topic '{topic}' was not found. {hint}",
            candidates=candidates,
        )

    info = await asyncio.to_thread(ros2_adapter.topic_info, topic)
    return RosTopicInfoOutput(
        name=info.name,
        message_type=info.message_type,
        publisher_count=info.publisher_count,
        subscriber_count=info.subscriber_count,
        publishers=info.publishers,
        subscribers=info.subscribers,
        summary=(
            f"{info.message_type or 'unknown type'} — "
            f"{info.publisher_count} publisher(s), {info.subscriber_count} subscriber(s)."
        ),
    )


async def ros_state(
    config: AppConfig, *, odom_topic: str = "/odom", scan_topic: str = "/scan"
) -> dict:
    """Snapshot the robot's current state (localized pose, odometry, laser scan)
    so the agent can *observe* before deciding — the closed-loop primitive behind
    "drive until arrived" / "stop if there's an obstacle". Each field is the raw
    one-shot message, or None if that topic is idle/absent (honest, never faked).
    """
    _ = config

    async def _snap(topic: str, *, latched: bool = False) -> str | None:
        try:
            blocks = await asyncio.to_thread(
                ros2_adapter.topic_echo, topic, count=1, latched=latched
            )
        except ros2_adapter.Ros2AdapterError:
            return None
        return blocks[0] if blocks else None

    # /amcl_pose is the map-frame truth and must be read latched (AMCL only
    # re-publishes on updates). Vehicles may also rename odom (Carter uses
    # /chassis/odom), so pose must never depend on /odom alone.
    return {
        "pose_topic": "/amcl_pose",
        "odom_topic": odom_topic,
        "scan_topic": scan_topic,
        "pose": await _snap("/amcl_pose", latched=True),
        "odom": await _snap(odom_topic),
        "scan": await _snap(scan_topic),
    }


async def ros_echo(config: AppConfig, topic: str, *, limit: int = 1) -> RosEchoOutput:
    """Capture a snapshot of up to `limit` messages from a topic.

    An idle topic makes `ros2 topic echo --once` time out; treat that as an
    empty (but friendly) snapshot rather than surfacing a raw command error.
    A missing `ros2` binary still propagates as an environment error.
    """
    _ = config
    try:
        blocks = await asyncio.to_thread(ros2_adapter.topic_echo, topic, count=limit)
    except ros2_adapter.Ros2NotAvailableError:
        raise
    except ros2_adapter.Ros2CommandError as exc:
        return RosEchoOutput(
            topic=topic,
            mode="snapshot",
            messages=[],
            summary=(
                f"No messages captured from '{topic}': {exc}. The topic may be "
                "idle, have no publisher, or use an incompatible QoS."
            ),
        )
    messages = [{"raw": block} for block in blocks]
    if not messages:
        summary = f"No messages received on '{topic}' (topic idle or timed out)."
    else:
        summary = f"Captured {len(messages)} message(s) from '{topic}'."
    return RosEchoOutput(topic=topic, mode="snapshot", messages=messages, summary=summary)


def _primitive_default(field_type: str):
    base = field_type.split("[")[0]  # strip array suffix, e.g. float64[3] -> float64
    if "float" in base or "double" in base:
        return 0.0
    if "int" in base:  # covers int8..int64 and uint8..uint64
        return 0
    if base == "bool":
        return False
    if base in ("string", "wstring"):
        return ""
    return {}


def _naive_example_payload(raw_interface: str) -> dict:
    """Build an example payload from `ros2 interface show` output.

    Nested message types are expanded inline with tab/space indentation (e.g.
    a Twist shows `Vector3 linear` followed by indented `float64 x/y/z`), so we
    track indentation to reconstruct the nested structure instead of flattening
    every field to the top level. Arrays become a single-element example list;
    constants (lines with `=`) are skipped.
    """
    lines = [
        line
        for line in raw_interface.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]  # (indent, container)

    for i, line in enumerate(lines):
        indent = len(line) - len(line.lstrip())
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        field_type, rest = parts
        # Take the token before any trailing comment. Split first, then index
        # safely: a line like `float64 # note` leaves nothing before the comment,
        # and `"".split()[0]` would raise IndexError.
        pre_comment = rest.split("#", 1)[0].split()
        name = pre_comment[0] if pre_comment else ""
        if not name or "=" in name:  # skip blanks and constants (e.g. `uint8 FOO=1`)
            continue

        # Dedent to the container that owns this field.
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]

        next_indent = (
            len(lines[i + 1]) - len(lines[i + 1].lstrip()) if i + 1 < len(lines) else -1
        )
        is_array = field_type.rstrip().endswith("]")
        if next_indent > indent:
            # Complex type with expanded sub-fields: recurse into a child dict.
            child: dict = {}
            parent[name] = [child] if is_array else child
            stack.append((indent, child))
        else:
            parent[name] = [] if is_array else _primitive_default(field_type)
    return root


async def ros_schema(config: AppConfig, topic: str) -> RosSchemaOutput:
    info = await asyncio.to_thread(ros2_adapter.topic_info, topic)
    raw_interface = await asyncio.to_thread(ros2_adapter.interface_show, info.message_type)
    field_summary = await summarize_ros_schema(config, info.message_type, raw_interface)
    return RosSchemaOutput(
        topic=topic,
        message_type=info.message_type,
        raw_interface=raw_interface,
        field_summary=field_summary,
        example_payload=_naive_example_payload(raw_interface),
    )


@dataclass
class Ros2PubValidation:
    ok: bool
    message_type: str = ""
    payload_preview: dict | None = None
    error: JenAIError | None = None


async def ros_pub_validate(topic: str, payload: dict) -> Ros2PubValidation:
    try:
        topics = await asyncio.to_thread(ros2_adapter.list_topics)
    except ros2_adapter.Ros2AdapterError as exc:
        return Ros2PubValidation(
            ok=False,
            error=JenAIError(error_type=ErrorType.ENV_ERROR, message=str(exc)),
        )

    if topic not in topics:
        return Ros2PubValidation(
            ok=False,
            error=JenAIError(
                error_type=ErrorType.VALIDATION_ERROR,
                message=f"Topic '{topic}' was not found.",
                details={"candidates": [t for t in topics if topic.strip("/") in t][:5]},
                fix_suggestion="Run /ros topics to see available topics.",
            ),
        )

    try:
        info = await asyncio.to_thread(ros2_adapter.topic_info, topic)
    except ros2_adapter.Ros2AdapterError as exc:
        return Ros2PubValidation(
            ok=False,
            error=JenAIError(error_type=ErrorType.ENV_ERROR, message=str(exc)),
        )

    if not isinstance(payload, dict):
        return Ros2PubValidation(
            ok=False,
            error=JenAIError(
                error_type=ErrorType.VALIDATION_ERROR,
                message="Payload must be a JSON object.",
            ),
        )

    return Ros2PubValidation(ok=True, message_type=info.message_type, payload_preview=payload)


# Deterministic safety limits for velocity commands (m/s and rad/s). Applied at
# execution regardless of what the model or user asked — a hard floor under the
# LLM-side guardrails so a bad number can never send the robot flying. These
# are the fallback; callers with a config pass the vehicle profile's limits.
MAX_LINEAR = 1.0
MAX_ANGULAR = 2.0


def _clamp(value, limit):
    # bool is a subclass of int, so guard it explicitly — a JSON `true` must not
    # be treated as the number 1 and clamped to full speed. Non-numeric values
    # pass through untouched so ros2 rejects them honestly rather than us faking
    # a plausible velocity.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    if not math.isfinite(value):
        return 0
    # AppConfig rejects invalid limits, but this primitive is also called
    # directly by tools. Fail closed instead of turning a negative limit into
    # positive full speed or letting infinity disable the safety boundary.
    if isinstance(limit, bool) or not isinstance(limit, (int, float)):
        return 0
    if limit < 0 or not math.isfinite(limit):
        return 0
    return max(-limit, min(limit, value))


def _clamp_velocities(
    node, max_linear: float = MAX_LINEAR, max_angular: float = MAX_ANGULAR
) -> None:
    """Recursively clamp every ``linear``/``angular`` velocity dict found anywhere
    in the payload, mutating in place.

    This covers plain ``geometry_msgs/Twist`` (top-level ``linear``/``angular``)
    *and* nested variants such as ``geometry_msgs/TwistStamped``
    (``{"twist": {"linear": ...}}``), which ROS2 Jazzy/Nav2 use on ``/cmd_vel``.
    Other message families are not inferred from field names here. Ackermann
    ``speed``/``steering_angle`` is handled separately, with its explicit ROS
    message type, in ``_safety_clamp``.
    """
    if isinstance(node, dict):
        for key, limit in (("linear", max_linear), ("angular", max_angular)):
            axes = node.get(key)
            if isinstance(axes, dict):
                for axis in ("x", "y", "z"):
                    if axis in axes:
                        axes[axis] = _clamp(axes[axis], limit)
        for value in node.values():
            _clamp_velocities(value, max_linear, max_angular)
    elif isinstance(node, list):
        for item in node:
            _clamp_velocities(item, max_linear, max_angular)


def _safety_clamp(
    payload: dict,
    max_linear: float = MAX_LINEAR,
    max_angular: float = MAX_ANGULAR,
    *,
    message_type: str = "",
) -> dict:
    """Return a copy with supported Twist/Ackermann velocities safely clamped.

    Other payload fields pass through unchanged so ROS2 can validate them.
    """
    if not isinstance(payload, dict):
        return payload
    clamped = copy.deepcopy(payload)  # copy.deepcopy, not a JSON round-trip:
    # the payload may hold non-JSON-native values and a round-trip would raise
    # or silently coerce types inside what must be a transparent copy-and-clamp.
    _clamp_velocities(clamped, max_linear, max_angular)
    if message_type.rsplit("/", 1)[-1] in {"AckermannDrive", "AckermannDriveStamped"}:
        # AckermannDrive stores speed/steering_angle directly; the stamped
        # variant nests them under ``drive``. Keep the same vehicle limits as
        # Twist so changing the configured command topic cannot bypass them.
        drive = clamped.get("drive", clamped)
        if isinstance(drive, dict):
            if "speed" in drive:
                drive["speed"] = _clamp(drive["speed"], max_linear)
            if "steering_angle" in drive:
                drive["steering_angle"] = _clamp(drive["steering_angle"], max_angular)
    return clamped


async def ros_pub_execute(
    topic: str,
    message_type: str,
    payload: dict,
    *,
    max_linear: float = MAX_LINEAR,
    max_angular: float = MAX_ANGULAR,
) -> RosPubOutput:
    payload = _safety_clamp(payload, max_linear, max_angular, message_type=message_type)
    payload_yaml = _payload_to_yaml(payload)
    result = await asyncio.to_thread(ros2_adapter.topic_pub, topic, message_type, payload_yaml)
    return RosPubOutput(
        topic=topic,
        message_type=message_type,
        payload_preview=payload,
        approval_status="approved",
        execution_status="succeeded" if result.ok else "failed",
        result_message=result.message,
    )


async def ros_drive(
    topic: str,
    message_type: str,
    payload: dict,
    *,
    duration_s: float = 1.0,
    rate_hz: float = 10.0,
    max_linear: float = MAX_LINEAR,
    max_angular: float = MAX_ANGULAR,
) -> RosPubOutput:
    """Publish `payload` continuously for `duration_s` seconds, then send a zeroed
    message so the robot stops. Use this for "move for N seconds" requests where a
    single publish would only nudge the robot before the controller watchdog stops it.
    """
    duration_s = max(0.0, min(duration_s, 30.0))  # clamp to a safe window
    payload = _safety_clamp(payload, max_linear, max_angular, message_type=message_type)
    payload_yaml = _payload_to_yaml(payload)
    stop_yaml = _payload_to_yaml(_zero_like(payload))
    result = await asyncio.to_thread(
        ros2_adapter.topic_pub_for,
        topic,
        message_type,
        payload_yaml,
        rate_hz=rate_hz,
        duration_s=duration_s,
        stop_yaml=stop_yaml,
    )
    return RosPubOutput(
        topic=topic,
        message_type=message_type,
        payload_preview=payload,
        approval_status="approved",
        execution_status="succeeded" if result.ok else "failed",
        result_message=result.message,
    )


def _zero_like(value):
    """Return the same structure with every number set to 0 (for a stop message)."""
    if isinstance(value, dict):
        return {key: _zero_like(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_zero_like(inner) for inner in value]
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return 0
    return value


def _payload_to_yaml(payload: dict) -> str:
    # ros2 topic pub accepts a YAML-flow-style mapping; a JSON object is valid
    # YAML flow syntax, so this avoids pulling in a YAML dependency.
    return json.dumps(payload)
