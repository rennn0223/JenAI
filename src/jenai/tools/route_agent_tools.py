"""Agent-tool wrappers around route_core."""

from __future__ import annotations

import json

from agents import RunContextWrapper, function_tool

from jenai.adapters.locations import (
    LocationNotFoundError,
    ensure_locations_file,
    find_location,
    load_locations,
)
from jenai.agent.context import JenAIRunContext
from jenai.schemas import EffectScope, RiskLevel, ToolCallCategory, ToolCallRecord, ToolCallStatus
from jenai.tools import route_core
from jenai.tools.navigation_gateway import execute_navigation
from jenai.tools.registry import ToolRiskInfo, register_tool


def _locations_path(ctx: RunContextWrapper[JenAIRunContext]):
    run_ctx = ctx.context
    return run_ctx.config.resolved_locations_path(run_ctx.config_path)


def _load_locations(ctx: RunContextWrapper[JenAIRunContext]) -> list:
    path = _locations_path(ctx)
    if path is None:
        return []
    ensure_locations_file(path)
    return load_locations(path)


def _record_call(
    ctx: RunContextWrapper[JenAIRunContext],
    tool_name: str,
    input_summary: str,
) -> ToolCallRecord:
    run_ctx = ctx.context
    info = ROUTE_TOOL_NAMES[tool_name]
    call = ToolCallRecord(
        tool_name=tool_name,
        category=ToolCallCategory.ROUTE,
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
async def route_preview_tool(ctx: RunContextWrapper[JenAIRunContext], text: str) -> dict:
    """Resolve a natural-language route request (start/goal) against known locations and
    produce a preview. Always call this before route_execute_tool."""
    call = _record_call(ctx, "route_preview_tool", text)
    locations = _load_locations(ctx)
    output = await route_core.route_preview(ctx.context.config, locations, text)
    _finish_call(ctx, call, ok=bool(output.outgoing_action), summary=output.route_preview)
    return output.model_dump(mode="json")


def _unwrap_outgoing_action(parsed: dict) -> dict:
    """Tolerate a model quoting the whole preview response instead of just the
    action: unwrap ``{"outgoing_action": {...}}`` (weak local models do this).
    The pose validation at the navigation exit still rejects anything unsound."""
    inner = parsed.get("outgoing_action")
    if isinstance(inner, dict) and "goal" not in parsed:
        return inner
    return parsed


@function_tool(needs_approval=True)
async def route_execute_tool(
    ctx: RunContextWrapper[JenAIRunContext],
    outgoing_action_json: str,
) -> dict:
    """Send a previewed route action. Requires human approval. Only call this after
    route_preview_tool has produced a resolved outgoing_action. `outgoing_action_json` is
    the outgoing_action dict from route_preview_tool's response, JSON-encoded."""
    call = _record_call(ctx, "route_execute_tool", "execute route")
    try:
        outgoing_action = _unwrap_outgoing_action(json.loads(outgoing_action_json))
    except json.JSONDecodeError as exc:
        _finish_call(ctx, call, ok=False, summary="invalid JSON action")
        return {
            "input_text": "",
            "outgoing_action": {},
            "approval_status": "approved",
            "execution_status": "failed",
            "route_preview": f"outgoing_action_json is not valid JSON: {exc}",
        }
    run_ctx = ctx.context
    output = await execute_navigation(
        run_ctx.config,
        outgoing_action,
        audit_store=run_ctx.run_store.audit_store,
        run_id=run_ctx.run.run_id,
        session_id=run_ctx.run.session_id,
    )
    ok = output.execution_status == "succeeded"
    _finish_call(ctx, call, ok=ok, summary=output.execution_status if ok else output.route_preview)
    return output.model_dump(mode="json")


@function_tool
async def loc_lookup_tool(ctx: RunContextWrapper[JenAIRunContext], name: str) -> dict:
    """Look up a known location by name or alias (fuzzy-matched)."""
    call = _record_call(ctx, "loc_lookup_tool", name)
    locations = _load_locations(ctx)
    try:
        location = find_location(locations, name)
    except LocationNotFoundError as exc:
        _finish_call(ctx, call, ok=False, summary="not found")
        return {"found": False, "candidates": [loc.name for loc in exc.candidates]}
    _finish_call(ctx, call, ok=True, summary=location.name)
    return {"found": True, "location": location.model_dump(mode="json")}


ROUTE_TOOL_NAMES: dict[str, ToolRiskInfo] = {
    "route_preview_tool": ToolRiskInfo(
        risk_level=RiskLevel.P0,
        effect_scope=EffectScope.READ,
        needs_approval=False,
        description="Resolve a route request without sending it.",
    ),
    "route_execute_tool": ToolRiskInfo(
        risk_level=RiskLevel.P1,
        effect_scope=EffectScope.SIM_CONTROL,
        needs_approval=True,
        description="Send a navigation route.",
    ),
    "loc_lookup_tool": ToolRiskInfo(
        risk_level=RiskLevel.P0,
        effect_scope=EffectScope.READ,
        needs_approval=False,
        description="Look up a known location.",
    ),
}

for _name, _info in ROUTE_TOOL_NAMES.items():
    register_tool(_name, _info)
