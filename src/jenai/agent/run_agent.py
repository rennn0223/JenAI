from __future__ import annotations

from agents import Agent

from jenai.agent import orchestrator
from jenai.agent.context import JenAIRunContext
from jenai.agent.specialists import build_supervisor_agent
from jenai.config.models import AppConfig
from jenai.schemas import RunRecord


def build_run_agent(config: AppConfig) -> Agent[JenAIRunContext]:
    """The agent driving `/run`: a Supervisor that hands off to specialist agents
    (ROS Explorer / Motion / Navigation / Perception) via the openai-agents SDK.
    """
    return build_supervisor_agent(config)


async def run_task(ctx: JenAIRunContext, task: str) -> RunRecord:
    return await orchestrator.start_run(build_run_agent(ctx.config), ctx, task)
