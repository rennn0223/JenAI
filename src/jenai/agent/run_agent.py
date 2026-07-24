"""Builds the runnable /run agent from the tool set and specialists."""

from __future__ import annotations

from agents import Agent

from jenai.agent import orchestrator
from jenai.agent.context import JenAIRunContext
from jenai.agent.intent_routing import RunAgentRoute, route_run_request
from jenai.agent.specialists import (
    build_area_patrol_selector_agent,
    build_supervisor_agent,
)
from jenai.capabilities import has_registered_capability
from jenai.config.models import AppConfig
from jenai.schemas import RunRecord


def build_run_agent(config: AppConfig, task: str | None = None) -> Agent[JenAIRunContext]:
    """The agent driving `/run`: a Supervisor that hands off to specialist agents
    (ROS Developer / Explorer / Navigation / Perception) via the
    openai-agents SDK.
    """
    if (
        task is not None
        and route_run_request(task) is RunAgentRoute.AREA_PATROL
        and has_registered_capability(config, "area_patrol")
    ):
        return build_area_patrol_selector_agent(config)
    return build_supervisor_agent(config)


async def run_task(ctx: JenAIRunContext, task: str) -> RunRecord:
    return await orchestrator.start_run(build_run_agent(ctx.config, task), ctx, task)
