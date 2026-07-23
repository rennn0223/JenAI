"""Agent-tool wrappers around route_core."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents import RunContextWrapper, function_tool

from jenai.adapters.locations import (
    LocationNotFoundError,
    ensure_locations_file,
    find_location,
    load_locations,
)
from jenai.agent.context import JenAIRunContext
from jenai.schemas import (
    EffectScope,
    Location,
    RiskLevel,
    RouteOutput,
    TaskOutcome,
    ToolCallCategory,
    ToolCallRecord,
    ToolCallStatus,
)
from jenai.tools import route_core
from jenai.tools.navigation_gateway import execute_navigation
from jenai.tools.registry import ToolRiskInfo, register_tool
from jenai.tools.route_action import normalize_route_action, unwrap_route_action
from jenai.tools.skills import ExploreSpec, exploration_candidates, run_explore

ToolOutput = dict[str, Any]


def _locations_path(ctx: RunContextWrapper[JenAIRunContext]) -> Path | None:
    run_ctx = ctx.context
    return run_ctx.config.resolved_locations_path(run_ctx.config_path)


def _load_locations(ctx: RunContextWrapper[JenAIRunContext]) -> list[Location]:
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
async def route_preview_tool(ctx: RunContextWrapper[JenAIRunContext], text: str) -> ToolOutput:
    """Resolve a natural-language route request (start/goal) against known locations and
    produce a preview. Always call this before route_execute_tool."""
    call = _record_call(ctx, "route_preview_tool", text)
    locations = _load_locations(ctx)
    output = await route_core.route_preview(ctx.context.config, locations, text)
    _finish_call(ctx, call, ok=bool(output.outgoing_action), summary=output.route_preview)
    return output.model_dump(mode="json")


def _unwrap_outgoing_action(parsed: ToolOutput) -> ToolOutput:
    """Backward-compatible alias for the canonical route normalizer helper."""
    return unwrap_route_action(parsed)


def _parse_outgoing_action(value: object) -> ToolOutput:
    """Backward-compatible alias used by route execution and focused tests."""
    return normalize_route_action(value)


@function_tool(needs_approval=True)
async def route_execute_tool(
    ctx: RunContextWrapper[JenAIRunContext],
    outgoing_action_json: str,
) -> ToolOutput:
    """Send a previewed route action. Requires human approval. Only call this after
    route_preview_tool has produced a resolved outgoing_action. `outgoing_action_json` is
    the outgoing_action dict from route_preview_tool's response, JSON-encoded."""
    call = _record_call(ctx, "route_execute_tool", "execute route")
    try:
        outgoing_action = _parse_outgoing_action(outgoing_action_json)
    except ValueError as exc:
        _finish_call(ctx, call, ok=False, summary="invalid JSON action")
        return {
            "input_text": "",
            "outgoing_action": {},
            "approval_status": "approved",
            "execution_status": "failed",
            "route_preview": f"outgoing_action_json is not a valid route action: {exc}",
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
    if ok:
        run_ctx.run.outcome = (
            TaskOutcome.ARRIVED_UNVERIFIED
            if outgoing_action.get("capability_id") == "dock_approach"
            else TaskOutcome.SUCCEEDED
        )
    elif output.execution_status == "endpoint_mismatch":
        run_ctx.run.outcome = TaskOutcome.ENDPOINT_MISMATCH
    elif output.execution_status == "unavailable":
        run_ctx.run.outcome = TaskOutcome.UNAVAILABLE
    elif output.execution_status == "blocked":
        run_ctx.run.outcome = TaskOutcome.BLOCKED
    else:
        run_ctx.run.outcome = TaskOutcome.FAILED
    _finish_call(ctx, call, ok=ok, summary=output.execution_status if ok else output.route_preview)
    return output.model_dump(mode="json")


@function_tool(needs_approval=True)
async def explore_area_tool(
    ctx: RunContextWrapper[JenAIRunContext],
    duration_minutes: float = 5.0,
    max_goals: int = 8,
    max_failures: int = 2,
    tag: str = "",
    seed: int = -1,
) -> ToolOutput:
    """Run one bounded, low-repeat exploration over eligible saved locations.

    This is known-location exploration, not frontier SLAM: the deterministic
    selector chooses among the least-visited saved poses and every goal still
    passes through the normal navigation gateway. ``seed=-1`` means a fresh
    random order; non-negative seeds make an experiment reproducible.
    """
    call = _record_call(
        ctx,
        "explore_area_tool",
        f"{duration_minutes:g} min, up to {max_goals} goals",
    )
    try:
        if seed < -1:
            raise ValueError("seed must be -1 or a non-negative integer")
        spec = ExploreSpec(
            duration_s=duration_minutes * 60,
            max_goals=max_goals,
            max_failures=max_failures,
            tag=tag.strip() or None,
            seed=None if seed == -1 else seed,
        )
    except ValueError as exc:
        message = f"Invalid exploration bounds: {exc}"
        _finish_call(ctx, call, ok=False, summary=message)
        return {"execution_status": "failed", "summary": message}

    locations = _load_locations(ctx)
    candidates = exploration_candidates(locations, spec.tag)
    if len(candidates) < 2:
        message = (
            f"Exploration requires at least two eligible saved locations; found {len(candidates)}."
        )
        _finish_call(ctx, call, ok=False, summary=message)
        return {
            "execution_status": "failed",
            "summary": message,
            "candidates": [location.name for location in candidates],
        }

    run_ctx = ctx.context

    async def _navigate(action: ToolOutput) -> RouteOutput:
        return await execute_navigation(
            run_ctx.config,
            action,
            audit_store=run_ctx.run_store.audit_store,
            run_id=run_ctx.run.run_id,
            session_id=run_ctx.run.session_id,
        )

    report = await run_explore(
        run_ctx.config,
        locations,
        spec,
        navigate=_navigate,
    )
    ok = report.completed_normally and report.success_count > 0
    _finish_call(ctx, call, ok=ok, summary=report.summary)
    return {
        "execution_status": "succeeded" if ok else "failed",
        "summary": report.summary,
        "stop_reason": report.stop_reason,
        "success_count": report.success_count,
        "attempt_count": len(report.results),
        "candidates": report.candidates,
        "results": [
            {
                "attempt": result.attempt,
                "point": result.point,
                "status": result.status,
                "detail": result.detail,
            }
            for result in report.results
        ],
    }


@function_tool
async def loc_lookup_tool(ctx: RunContextWrapper[JenAIRunContext], name: str) -> ToolOutput:
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
    "explore_area_tool": ToolRiskInfo(
        risk_level=RiskLevel.P1,
        effect_scope=EffectScope.SIM_CONTROL,
        needs_approval=True,
        description="Explore eligible saved locations within hard time and goal limits.",
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
