from __future__ import annotations

from agents import Agent, OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from jenai.agent.context import JenAIRunContext
from jenai.agent.instructions import PLAN_AGENT_INSTRUCTIONS, REVIEW_AGENT_INSTRUCTIONS
from jenai.config.models import AppConfig
from jenai.providers.agent_model import ModelBinding, build_agent_model
from jenai.schemas import PlanOutput


def build_model(
    config: AppConfig,
    *,
    binding: ModelBinding = "chat",
    client: AsyncOpenAI | None = None,
) -> OpenAIChatCompletionsModel:
    return build_agent_model(config, binding=binding, client=client)


def build_plan_agent(config: AppConfig) -> Agent[JenAIRunContext]:
    """A tool-less agent: `/plan` must never call a side-effect tool.

    `tools=[]` makes this structurally true (there is nothing to call), rather
    than relying on the system prompt alone.
    """
    return Agent(
        name="JenAI Planner",
        instructions=PLAN_AGENT_INSTRUCTIONS,
        model=build_model(config, binding="plan"),
        tools=[],
        output_type=PlanOutput,
    )


def build_review_agent(config: AppConfig) -> Agent[JenAIRunContext]:
    return Agent(
        name="JenAI Plan Reviewer",
        instructions=REVIEW_AGENT_INSTRUCTIONS,
        model=build_model(config, binding="plan"),
        tools=[],
        output_type=PlanOutput,
    )
