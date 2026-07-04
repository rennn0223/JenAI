"""Agent-tool wrappers around vision_core."""

from __future__ import annotations

from agents import RunContextWrapper, function_tool

from jenai.agent.context import JenAIRunContext
from jenai.schemas import EffectScope, RiskLevel, ToolCallCategory
from jenai.tools import vision_core
from jenai.tools.registry import ToolRiskInfo, register_tool
from jenai.tools.tracking import finish_tool_call, record_tool_call

_VISION_IMAGE_INFO = ToolRiskInfo(
    risk_level=RiskLevel.P0,
    effect_scope=EffectScope.READ,
    needs_approval=False,
    description="Analyze a local image with the vision model.",
)


@function_tool
async def vision_image_tool(
    ctx: RunContextWrapper[JenAIRunContext], path: str, task_context: str = ""
) -> dict:
    """Analyze a local image file with the vision model and return a structured summary
    (objects, anomalies, relevance to the current task, suggested next actions)."""
    call = record_tool_call(
        ctx, "vision_image_tool", ToolCallCategory.VISION, f"analyze {path}", _VISION_IMAGE_INFO
    )
    try:
        output = await vision_core.analyze_image(
            ctx.context.config, path, task_context=task_context
        )
    except vision_core.VisionError as exc:
        finish_tool_call(ctx, call, ok=False, summary=str(exc))
        return {"source": path, "error": str(exc)}
    finish_tool_call(ctx, call, ok=True, summary=output.summary)
    return output.model_dump()


register_tool("vision_image_tool", _VISION_IMAGE_INFO)
