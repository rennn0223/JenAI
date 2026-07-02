from __future__ import annotations

import json

from agents import RunContextWrapper, function_tool

from jenai.agent.context import JenAIRunContext
from jenai.schemas import EffectScope, RiskLevel, ToolCallCategory, ToolCallRecord, ToolCallStatus
from jenai.tools import ros2_core
from jenai.tools.registry import ToolRiskInfo, register_tool


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
) -> None:
    run_ctx = ctx.context
    run_ctx.run_store.update_tool_call(
        run_ctx.run,
        call.tool_call_id,
        status=ToolCallStatus.SUCCEEDED if ok else ToolCallStatus.FAILED,
        output_summary=summary,
    )


@function_tool
async def ros_topics_tool(ctx: RunContextWrapper[JenAIRunContext]) -> dict:
    """List the ROS2 topics currently visible on the graph."""
    call = _record_call(ctx, "ros_topics_tool", "list topics")
    output = await ros2_core.ros_topics(ctx.context.config)
    _finish_call(ctx, call, ok=True, summary=f"{len(output.topics)} topics")
    return output.model_dump()


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
    """Observe the robot's current state — a one-shot snapshot of odometry (/odom) and
    laser scan (/scan). Use this to check where the robot is or whether something is in
    the way before or after moving."""
    call = _record_call(ctx, "ros_state_tool", "read robot state")
    state = await ros2_core.ros_state(ctx.context.config)
    has = [k for k in ("odom", "scan") if state.get(k)]
    _finish_call(ctx, call, ok=bool(has), summary=f"read {', '.join(has) or 'nothing'}")
    return state


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
    output = await ros2_core.ros_pub_execute(topic, message_type, payload)
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
    output = await ros2_core.ros_drive(
        topic, message_type, payload, duration_s=duration_seconds
    )
    _finish_call(
        ctx, call, ok=output.execution_status == "succeeded", summary=output.result_message
    )
    return output.model_dump()


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
        description="Observe robot state (odom + scan snapshot).",
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
}

for _name, _info in ROS2_TOOL_NAMES.items():
    register_tool(_name, _info)
