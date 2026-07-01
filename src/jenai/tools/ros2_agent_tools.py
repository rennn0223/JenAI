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
    payload = json.loads(payload_json)
    output = await ros2_core.ros_pub_execute(topic, message_type, payload)
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
    "ros_schema_tool": ToolRiskInfo(
        risk_level=RiskLevel.P0,
        effect_scope=EffectScope.READ,
        needs_approval=False,
        description="Summarize a ROS2 topic's message schema.",
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
}

for _name, _info in ROS2_TOOL_NAMES.items():
    register_tool(_name, _info)
