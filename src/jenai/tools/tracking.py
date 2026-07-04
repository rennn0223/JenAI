"""Navigation event correlation helpers."""

from __future__ import annotations

from agents import RunContextWrapper

from jenai.agent.context import JenAIRunContext
from jenai.schemas import ToolCallCategory, ToolCallRecord, ToolCallStatus
from jenai.tools.registry import ToolRiskInfo


def record_tool_call(
    ctx: RunContextWrapper[JenAIRunContext],
    tool_name: str,
    category: ToolCallCategory,
    input_summary: str,
    info: ToolRiskInfo,
) -> ToolCallRecord:
    """Register a running tool call on the active run and return its record."""
    run_ctx = ctx.context
    call = ToolCallRecord(
        tool_name=tool_name,
        category=category,
        input_summary=input_summary,
        status=ToolCallStatus.RUNNING,
        risk_level=info.risk_level,
        effect_scope=info.effect_scope,
    )
    run_ctx.run_store.add_tool_call(run_ctx.run, call)
    return call


def finish_tool_call(
    ctx: RunContextWrapper[JenAIRunContext],
    call: ToolCallRecord,
    *,
    ok: bool,
    summary: str,
) -> None:
    """Mark a previously recorded tool call as succeeded or failed."""
    run_ctx = ctx.context
    run_ctx.run_store.update_tool_call(
        run_ctx.run,
        call.tool_call_id,
        status=ToolCallStatus.SUCCEEDED if ok else ToolCallStatus.FAILED,
        output_summary=summary,
    )
