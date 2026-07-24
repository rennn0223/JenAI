"""OpenAI Agent SDK adapter for the shared semantic area-patrol service."""

from __future__ import annotations

from agents import RunContextWrapper, function_tool

from jenai.agent.context import JenAIRunContext
from jenai.tools.area_patrol_service import (
    AREA_PATROL_RISK_INFO,
    ToolOutput,
    run_area_patrol_capability,
)
from jenai.tools.registry import register_tool


@function_tool(needs_approval=True)
async def area_patrol_workflow_tool(
    ctx: RunContextWrapper[JenAIRunContext],
    target: str = "all",
    max_navigation_retries: int = 1,
    return_home: bool = True,
) -> ToolOutput:
    """Cover a configured semantic area and preserve evidence at every inspection point.

    Use this for goal-level requests such as "inspect the whole laboratory and
    return home." Do not use it for an explicit user-provided waypoint sequence
    or random known-location exploration.
    """

    return await run_area_patrol_capability(
        ctx.context, target, max_navigation_retries, return_home
    )


register_tool("area_patrol_workflow_tool", AREA_PATROL_RISK_INFO)
