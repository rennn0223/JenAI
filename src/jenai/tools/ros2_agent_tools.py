"""Agent-tool wrappers around ros2_core."""

from __future__ import annotations

import asyncio
import json
import math
import re

from agents import RunContextWrapper, function_tool

from jenai.agent.context import JenAIRunContext
from jenai.schemas import EffectScope, RiskLevel, ToolCallCategory, ToolCallRecord, ToolCallStatus
from jenai.tools import ros2_core
from jenai.tools.registry import ToolRiskInfo, register_tool

_AGENT_TOPIC_LIMIT = 40
_AGENT_TOPIC_LIMIT_MAX = 80
_AGENT_SNAPSHOT_CHARS = 1600
_SCAN_SCALAR = re.compile(
    r"^(angle_min|angle_max|angle_increment|range_min|range_max):\s*([^\s]+)\s*$",
    re.MULTILINE,
)


def _bounded_topic_inventory(output, query: str, limit: int) -> dict:
    """Keep model context bounded while retaining queryable discovery.

    The interactive `/ros topics` command still renders the complete graph.
    The agent can request another filtered page with `query` when needed.
    """

    needle = query.strip().lower()
    matches = [item for item in output.topics if not needle or needle in item.name.lower()]
    bounded_limit = max(1, min(int(limit), _AGENT_TOPIC_LIMIT_MAX))
    selected = matches[:bounded_limit]
    truncated = len(matches) > len(selected)
    return {
        "query": query.strip(),
        "total_count": len(output.topics),
        "matched_count": len(matches),
        "returned_count": len(selected),
        "truncated": truncated,
        "topics": [item.model_dump() for item in selected],
        "next_step": (
            "Call ros_topics_tool again with a narrower query to inspect omitted topics."
            if truncated
            else "All matching topics are included."
        ),
    }


def _scan_snapshot_summary(raw: str) -> dict:
    """Reduce a ROS CLI LaserScan snapshot to factual, bounded measurements."""

    scalars: dict[str, float] = {}
    for name, raw_value in _SCAN_SCALAR.findall(raw):
        try:
            scalars[name] = float(raw_value)
        except ValueError:
            continue

    ranges_section = raw.partition("ranges:")[2].partition("intensities:")[0]
    ranges: list[float] = []
    observed_sample_count = 0
    ranges_truncated = False
    for raw_value in re.findall(r"^-\s+([^\s]+)\s*$", ranges_section, re.MULTILINE):
        normalized = raw_value.strip("'\"")
        if normalized == "...":
            ranges_truncated = True
            continue
        observed_sample_count += 1
        try:
            value = float(normalized)
        except ValueError:
            continue
        if math.isfinite(value):
            ranges.append(value)

    angle_min = scalars.get("angle_min")
    angle_max = scalars.get("angle_max")
    field_of_view_deg = (
        math.degrees(angle_max - angle_min)
        if angle_min is not None and angle_max is not None
        else None
    )
    increment = scalars.get("angle_increment")
    expected_sample_count = (
        round((angle_max - angle_min) / increment) + 1
        if angle_min is not None
        and angle_max is not None
        and increment is not None
        and increment > 0
        else None
    )
    sample_count_complete = not ranges_truncated and (
        expected_sample_count is None or observed_sample_count == expected_sample_count
    )
    return {
        **scalars,
        "field_of_view_deg": round(field_of_view_deg, 2) if field_of_view_deg is not None else None,
        "expected_sample_count": expected_sample_count,
        "observed_sample_count": observed_sample_count,
        "sample_count_complete": sample_count_complete,
        "ranges_truncated": ranges_truncated,
        "observed_finite_sample_count": len(ranges),
        "nearest_observed_valid_range_m": min(ranges) if ranges else None,
        "interpretation_note": (
            "field_of_view_deg is the total angular span. When ranges_truncated is true, "
            "observed counts and nearest range cover only the ROS CLI-displayed prefix. "
            "A range return is not an obstacle classification."
        ),
    }


def _pose_snapshot_summary(raw: str) -> dict:
    """Extract map position and yaw from an AMCL PoseWithCovariance snapshot."""

    position_text = raw.partition("position:")[2].partition("orientation:")[0]
    orientation_text = raw.partition("orientation:")[2].partition("covariance:")[0]

    def _values(block: str) -> dict[str, float]:
        values: dict[str, float] = {}
        for name, raw_value in re.findall(r"^\s+(x|y|z|w):\s*([^\s]+)\s*$", block, re.MULTILINE):
            try:
                values[name] = float(raw_value)
            except ValueError:
                continue
        return values

    position = _values(position_text)
    orientation = _values(orientation_text)
    yaw = None
    if {"x", "y", "z", "w"} <= orientation.keys():
        x, y, z, w = (orientation[name] for name in ("x", "y", "z", "w"))
        yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    frame = re.search(r"^\s*frame_id:\s*(\S+)\s*$", raw, re.MULTILINE)
    return {
        "frame_id": frame.group(1) if frame else None,
        "x": position.get("x"),
        "y": position.get("y"),
        "z": position.get("z"),
        "yaw_rad": yaw,
        "yaw_deg": math.degrees(yaw) if yaw is not None else None,
    }


def _compact_robot_state(state: dict) -> dict:
    """Retain state evidence without feeding full LaserScan arrays to the LLM."""

    compact = dict(state)
    pose = compact.get("pose")
    if isinstance(pose, str):
        compact["pose_summary"] = _pose_snapshot_summary(pose)
    scan = compact.get("scan")
    if isinstance(scan, str):
        compact["scan_summary"] = _scan_snapshot_summary(scan)
    truncated_fields: list[str] = []
    for field in ("pose", "odom", "scan"):
        value = compact.get(field)
        if isinstance(value, str) and len(value) > _AGENT_SNAPSHOT_CHARS:
            compact[field] = value[:_AGENT_SNAPSHOT_CHARS] + "\n... [snapshot truncated]"
            truncated_fields.append(field)
    compact["availability"] = {
        field: bool(compact.get(field)) for field in ("pose", "odom", "scan")
    }
    compact["truncated_fields"] = truncated_fields
    return compact


def _combined_robot_status(state: dict, nav2: dict) -> dict:
    """One timestamp-local payload for pose/scan evidence and Nav2 readiness."""

    compact = _compact_robot_state(state)
    compact["nav2"] = nav2
    compact["reporting_boundary"] = (
        "Report only measured fields. Nav2 activity=NOT_MEASURED means this tool did not "
        "check whether a goal exists; never claim no-current-goal, idle, stopped, or moving. "
        "LaserScan returns must not be classified as obstacles."
    )
    return compact


def _record_call(
    ctx: RunContextWrapper[JenAIRunContext],
    tool_name: str,
    input_summary: str,
) -> ToolCallRecord:
    run_ctx = ctx.context
    info = ROS2_TOOL_NAMES[tool_name]
    call = ToolCallRecord(
        tool_name=tool_name,
        category=ToolCallCategory.ROS2,
        input_summary=input_summary,
        status=ToolCallStatus.RUNNING,
        risk_level=info.risk_level,
        effect_scope=info.effect_scope,
    )
    run_ctx.run_store.add_tool_call(run_ctx.run, call)
    return call


def _finish_call(
    ctx: RunContextWrapper[JenAIRunContext],
    call: ToolCallRecord,
    *,
    ok: bool,
    summary: str,
    raw_output: dict | None = None,
) -> None:
    run_ctx = ctx.context
    fields = {
        "status": ToolCallStatus.SUCCEEDED if ok else ToolCallStatus.FAILED,
        "output_summary": summary,
    }
    if raw_output is not None:
        fields["raw_output"] = raw_output
    run_ctx.run_store.update_tool_call(run_ctx.run, call.tool_call_id, **fields)


@function_tool
async def ros_topics_tool(
    ctx: RunContextWrapper[JenAIRunContext],
    query: str = "",
    limit: int = _AGENT_TOPIC_LIMIT,
) -> dict:
    """List a bounded ROS2 topic inventory. Use `query` to filter by a name fragment when
    the response is truncated; the user's direct `/ros topics` command remains unbounded."""
    call = _record_call(ctx, "ros_topics_tool", "list topics")
    output = await ros2_core.ros_topics(ctx.context.config)
    _finish_call(ctx, call, ok=True, summary=f"{len(output.topics)} topics")
    return _bounded_topic_inventory(output, query, limit)


@function_tool
async def ros_topic_info_tool(ctx: RunContextWrapper[JenAIRunContext], topic: str) -> dict:
    """Get a ROS2 topic's message type, publisher and subscriber details. When the topic is
    not found, the response includes fuzzy `candidates`."""
    call = _record_call(ctx, "ros_topic_info_tool", f"info for {topic}")
    output = await ros2_core.ros_topic_info(ctx.context.config, topic)
    found = bool(output.message_type)
    _finish_call(ctx, call, ok=found, summary=output.summary)
    return output.model_dump()


@function_tool
async def ros_echo_tool(
    ctx: RunContextWrapper[JenAIRunContext], topic: str, count: int = 1
) -> dict:
    """Capture a snapshot of up to `count` recent messages published on a ROS2 topic."""
    call = _record_call(ctx, "ros_echo_tool", f"echo {topic}")
    output = await ros2_core.ros_echo(ctx.context.config, topic, limit=count)
    _finish_call(ctx, call, ok=bool(output.messages), summary=output.summary)
    return output.model_dump()


@function_tool
async def ros_state_tool(ctx: RunContextWrapper[JenAIRunContext]) -> dict:
    """Observe the robot's current state — a one-shot snapshot of the localized pose
    (/amcl_pose), odometry (/odom), laser scan (/scan), and Nav2 readiness. Use this to
    check where the robot is, whether scan feedback exists, and whether navigation is ready
    before or after moving. The pose field is the map-frame position; navigation only needs
    a destination, so a missing pose must never make you ask the human where the robot is."""
    call = _record_call(ctx, "ros_state_tool", "read robot state")
    state, nav2 = await asyncio.gather(
        ros2_core.ros_state(ctx.context.config),
        ros2_core.ros_nav_status(ctx.context.config),
    )
    has = [k for k in ("pose", "odom", "scan") if state.get(k)]
    result = _combined_robot_status(state, nav2)
    _finish_call(
        ctx,
        call,
        ok=bool(has) and nav2["ready"],
        summary=(
            f"read {', '.join(has) or 'nothing'}; Nav2 {'ready' if nav2['ready'] else 'not ready'}"
        ),
        raw_output=result,
    )
    return result


@function_tool
async def ros_schema_tool(ctx: RunContextWrapper[JenAIRunContext], topic: str) -> dict:
    """Resolve a ROS2 topic's message type and summarize its fields in plain language."""
    call = _record_call(ctx, "ros_schema_tool", f"schema for {topic}")
    output = await ros2_core.ros_schema(ctx.context.config, topic)
    _finish_call(ctx, call, ok=True, summary=output.message_type)
    return output.model_dump()


@function_tool
async def ros_pub_validate_tool(
    ctx: RunContextWrapper[JenAIRunContext],
    topic: str,
    payload_json: str,
) -> dict:
    """Validate a ROS2 topic publish request (topic exists, payload is a JSON object) before
    calling ros_pub_execute_tool. Always call this first. `payload_json` is the message payload
    encoded as a JSON object string, e.g. '{"linear": {"x": 0.5}}'."""
    call = _record_call(ctx, "ros_pub_validate_tool", f"validate publish to {topic}")
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        _finish_call(ctx, call, ok=False, summary="invalid JSON payload")
        return {"ok": False, "message_type": "", "payload_preview": None, "error": str(exc)}

    validation = await ros2_core.ros_pub_validate(topic, payload)
    _finish_call(ctx, call, ok=validation.ok, summary="valid" if validation.ok else "invalid")
    return {
        "ok": validation.ok,
        "message_type": validation.message_type,
        "payload_preview": validation.payload_preview,
        "error": validation.error.model_dump() if validation.error else None,
    }


@function_tool(needs_approval=True)
async def ros_pub_execute_tool(
    ctx: RunContextWrapper[JenAIRunContext],
    topic: str,
    message_type: str,
    payload_json: str,
) -> dict:
    """Publish a validated payload to a ROS2 topic. Requires human approval. Only call this
    after ros_pub_validate_tool has confirmed the request is valid. `payload_json` is the
    message payload encoded as a JSON object string."""
    call = _record_call(ctx, "ros_pub_execute_tool", f"publish to {topic}")
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        _finish_call(ctx, call, ok=False, summary="invalid JSON payload")
        return {
            "topic": topic,
            "message_type": message_type,
            "payload_preview": None,
            "approval_status": "approved",
            "execution_status": "failed",
            "result_message": f"payload_json is not valid JSON: {exc}",
        }
    vehicle = ctx.context.config.vehicle
    output = await ros2_core.ros_pub_execute(
        topic,
        message_type,
        payload,
        max_linear=vehicle.max_linear,
        max_angular=vehicle.max_angular,
    )
    _finish_call(
        ctx, call, ok=output.execution_status == "succeeded", summary=output.result_message
    )
    return output.model_dump()


@function_tool(needs_approval=True)
async def ros_drive_execute_tool(
    ctx: RunContextWrapper[JenAIRunContext],
    topic: str,
    message_type: str,
    payload_json: str,
    duration_seconds: float = 1.0,
) -> dict:
    """Drive the robot by publishing a payload continuously for `duration_seconds`, then
    automatically send a zeroed message so it stops. Use this for time-bounded motion like
    "move forward for 1 second" — do NOT loop ros_pub_execute_tool to sustain motion. Requires
    human approval. `payload_json` is the message payload (e.g. a Twist) as a JSON object string."""
    call = _record_call(ctx, "ros_drive_execute_tool", f"drive {topic} for {duration_seconds}s")
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        _finish_call(ctx, call, ok=False, summary="invalid JSON payload")
        return {
            "topic": topic,
            "message_type": message_type,
            "payload_preview": None,
            "approval_status": "approved",
            "execution_status": "failed",
            "result_message": f"payload_json is not valid JSON: {exc}",
        }
    vehicle = ctx.context.config.vehicle
    output = await ros2_core.ros_drive(
        topic,
        message_type,
        payload,
        duration_s=duration_seconds,
        max_linear=vehicle.max_linear,
        max_angular=vehicle.max_angular,
    )
    _finish_call(
        ctx, call, ok=output.execution_status == "succeeded", summary=output.result_message
    )
    return output.model_dump()


@function_tool(needs_approval=True)
async def ros_drive_verified_tool(
    ctx: RunContextWrapper[JenAIRunContext],
    topic: str,
    message_type: str,
    payload_json: str,
    duration_seconds: float = 1.0,
    feedback_topic: str = "/odom",
) -> dict:
    """Atomically read baseline odometry, drive once for a bounded duration, auto-stop,
    then read odometry again. Prefer this over ros_drive_execute_tool whenever the request
    asks whether motion actually occurred. Missing baseline prevents movement; missing
    post-action feedback returns unverified and never repeats actuation."""
    call = _record_call(
        ctx,
        "ros_drive_verified_tool",
        f"verified drive {topic} for {duration_seconds}s using {feedback_topic}",
    )
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        _finish_call(ctx, call, ok=False, summary="invalid JSON payload")
        return {
            "verdict": "not_executed",
            "actuation_performed": False,
            "message": f"payload_json is not valid JSON: {exc}",
        }
    vehicle = ctx.context.config.vehicle
    output = await ros2_core.ros_drive_verified(
        ctx.context.config,
        topic,
        message_type,
        payload,
        duration_s=duration_seconds,
        feedback_topic=feedback_topic,
        max_linear=vehicle.max_linear,
        max_angular=vehicle.max_angular,
    )
    verdict = output["verdict"]
    _finish_call(
        ctx,
        call,
        ok=verdict in {"verified", "unverified"},
        summary=f"{verdict}: {output['message']}",
    )
    return output


ROS2_TOOL_NAMES: dict[str, ToolRiskInfo] = {
    "ros_topics_tool": ToolRiskInfo(
        risk_level=RiskLevel.P0,
        effect_scope=EffectScope.READ,
        needs_approval=False,
        description="List ROS2 topics.",
    ),
    "ros_topic_info_tool": ToolRiskInfo(
        risk_level=RiskLevel.P0,
        effect_scope=EffectScope.READ,
        needs_approval=False,
        description="Show a ROS2 topic's type, publishers and subscribers.",
    ),
    "ros_schema_tool": ToolRiskInfo(
        risk_level=RiskLevel.P0,
        effect_scope=EffectScope.READ,
        needs_approval=False,
        description="Summarize a ROS2 topic's message schema.",
    ),
    "ros_state_tool": ToolRiskInfo(
        risk_level=RiskLevel.P0,
        effect_scope=EffectScope.READ,
        needs_approval=False,
        description="Observe robot state (pose + odom + scan snapshot).",
    ),
    "ros_echo_tool": ToolRiskInfo(
        risk_level=RiskLevel.P0,
        effect_scope=EffectScope.READ,
        needs_approval=False,
        description="Snapshot recent messages from a ROS2 topic.",
    ),
    "ros_pub_validate_tool": ToolRiskInfo(
        risk_level=RiskLevel.P0,
        effect_scope=EffectScope.READ,
        needs_approval=False,
        description="Validate a ROS2 publish request without sending it.",
    ),
    "ros_pub_execute_tool": ToolRiskInfo(
        risk_level=RiskLevel.P1,
        effect_scope=EffectScope.SIM_CONTROL,
        needs_approval=True,
        description="Publish a message to a ROS2 topic.",
    ),
    "ros_drive_execute_tool": ToolRiskInfo(
        risk_level=RiskLevel.P1,
        effect_scope=EffectScope.SIM_CONTROL,
        needs_approval=True,
        description="Drive the robot for a fixed duration, then auto-stop.",
    ),
    "ros_drive_verified_tool": ToolRiskInfo(
        risk_level=RiskLevel.P1,
        effect_scope=EffectScope.SIM_CONTROL,
        needs_approval=True,
        description="Read odometry, drive once, auto-stop, and verify post-action odometry.",
    ),
}

for _name, _info in ROS2_TOOL_NAMES.items():
    register_tool(_name, _info)
