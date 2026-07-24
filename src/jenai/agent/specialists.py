"""Specialist agents (ROS/Navigation/Perception) and the handoff graph."""

from __future__ import annotations

from typing import Any

from agents import (
    Agent,
    FunctionToolResult,
    Handoff,
    ModelSettings,
    RunContextWrapper,
    Tool,
    ToolsToFinalOutputResult,
)
from openai import AsyncOpenAI
from openai.types.shared.reasoning import Reasoning

from jenai.agent.context import JenAIRunContext
from jenai.agent.guardrails import unsafe_command_guardrail
from jenai.agent.instructions import (
    AREA_PATROL_SELECTOR_INSTRUCTIONS,
    NAVIGATION_AGENT_INSTRUCTIONS,
    PERCEPTION_AGENT_INSTRUCTIONS,
    ROS_DEVELOPER_INSTRUCTIONS,
    ROS_EXPLORER_INSTRUCTIONS,
    SUPERVISOR_INSTRUCTIONS,
)
from jenai.agent.orchestrator import is_read_only_state_request
from jenai.agent.runtime import build_model
from jenai.capabilities import capability_prompt, registered_capability_ids
from jenai.config.models import AppConfig
from jenai.providers.agent_model import make_agent_client
from jenai.tools.area_patrol_agent_tools import area_patrol_workflow_tool
from jenai.tools.ros2_agent_tools import (
    ros_echo_tool,
    ros_schema_tool,
    ros_state_tool,
    ros_topic_info_tool,
    ros_topics_tool,
)
from jenai.tools.route_agent_tools import (
    explore_area_tool,
    loc_lookup_tool,
    patrol_area_tool,
    route_execute_tool,
    route_preview_tool,
)
from jenai.tools.shell_agent_tools import shell_run_tool
from jenai.tools.vision_agent_tools import vision_image_tool

# JenAI is a multi-agent system built on the openai-agents SDK's *handoffs*: a
# Supervisor `Agent` lists specialist `Agent`s in `handoffs=[...]`, and the model
# transfers control to whichever specialist fits the request. Each specialist
# carries only its own focused toolset, which keeps tool-selection reliable.


def _finish_read_only_state(
    ctx: RunContextWrapper[JenAIRunContext],
    results: list[FunctionToolResult],
) -> ToolsToFinalOutputResult:
    """Skip a redundant second 35B turn after a complete state snapshot.

    The orchestrator renders this payload with its deterministic state report.
    Combined requests such as "check status, then go to dock" keep running the
    normal model loop so the requested action is not silently dropped.
    """

    state_only = (
        len(results) == 1
        and results[0].tool.name == "ros_state_tool"
        and is_read_only_state_request(ctx.context.run.user_input)
    )
    return ToolsToFinalOutputResult(
        is_final_output=state_only,
        final_output=results[0].output if state_only else None,
    )


def _capability_set(config: AppConfig) -> frozenset[str]:
    return frozenset(registered_capability_ids(config))


def _navigation_tools(config: AppConfig) -> list[Tool]:
    capabilities = _capability_set(config)
    tools: list[Tool] = []
    if capabilities & {"navigate", "dock_approach"}:
        tools.extend(
            [
                loc_lookup_tool,
                route_preview_tool,
                route_execute_tool,
            ]
        )
    if "explore_known_locations" in capabilities:
        tools.append(explore_area_tool)
    if "patrol_photo" in capabilities:
        tools.append(patrol_area_tool)
    if "area_patrol" in capabilities:
        tools.append(area_patrol_workflow_tool)
    return tools


def _ros_developer_tools(config: AppConfig) -> list[Tool]:
    del config
    return [
        ros_topics_tool,
        ros_topic_info_tool,
        ros_schema_tool,
        ros_echo_tool,
        ros_state_tool,
    ]


def build_ros_explorer_agent(
    config: AppConfig, client: AsyncOpenAI | None = None
) -> Agent[JenAIRunContext]:
    return Agent[JenAIRunContext](
        name="ROS Explorer",
        handoff_description="Look up ROS2 topics, message types and formats (read-only).",
        instructions=ROS_EXPLORER_INSTRUCTIONS,
        model=build_model(config, binding="chat", client=client),
        tool_use_behavior=_finish_read_only_state,
        tools=[
            ros_topics_tool,
            ros_topic_info_tool,
            ros_schema_tool,
            ros_echo_tool,
            ros_state_tool,
        ],
    )


def build_ros_developer_agent(
    config: AppConfig, client: AsyncOpenAI | None = None
) -> Agent[JenAIRunContext]:
    """Read-only specialist for live interface discovery and feedback."""
    return Agent[JenAIRunContext](
        name="ROS Developer",
        handoff_description=(
            "Discover and validate a ROS2 interface without publishing or moving the robot."
        ),
        instructions=ROS_DEVELOPER_INSTRUCTIONS,
        model=build_model(config, binding="chat", client=client),
        tools=_ros_developer_tools(config),
    )


def build_navigation_agent(
    config: AppConfig, client: AsyncOpenAI | None = None
) -> Agent[JenAIRunContext]:
    return Agent[JenAIRunContext](
        name="Navigation",
        handoff_description=(
            "Navigate to a named location or run bounded known-location exploration "
            "(needs approval)."
        ),
        instructions=NAVIGATION_AGENT_INSTRUCTIONS,
        model=build_model(config, binding="route", client=client),
        tools=_navigation_tools(config),
    )


def build_area_patrol_selector_agent(
    config: AppConfig, client: AsyncOpenAI | None = None
) -> Agent[JenAIRunContext]:
    """Use one LLM turn to bind an explicit coverage mission to one workflow.

    Candidate narrowing is deterministic, but the model still interprets the
    user's retry and return-home intent. The workflow result is final, so a
    normal patrol never incurs a second LLM turn after every point or after the
    completed workflow.
    """

    profile = config.active_profile()
    reasoning = (
        Reasoning(effort="none") if profile and profile.provider.lower() == "ollama" else None
    )
    return Agent[JenAIRunContext](
        name="Area Patrol Workflow Selector",
        instructions=AREA_PATROL_SELECTOR_INSTRUCTIONS,
        model=build_model(config, binding="route", client=client),
        model_settings=ModelSettings(
            temperature=0.0,
            tool_choice="required",
            parallel_tool_calls=False,
            reasoning=reasoning,
        ),
        tools=[area_patrol_workflow_tool],
        tool_use_behavior="stop_on_first_tool",
    )


def build_perception_agent(
    config: AppConfig, client: AsyncOpenAI | None = None
) -> Agent[JenAIRunContext]:
    return Agent[JenAIRunContext](
        name="Perception",
        handoff_description="Analyze an image from the robot's camera.",
        instructions=PERCEPTION_AGENT_INSTRUCTIONS,
        model=build_model(config, binding="vision", client=client),
        tools=[vision_image_tool],
    )


def build_supervisor_agent(config: AppConfig) -> Agent[JenAIRunContext]:
    """The top-level agent: keeps a couple of general tools and hands off the
    domain work to specialists via the SDK's handoff mechanism.

    One AsyncOpenAI client is shared across the supervisor and all four
    specialists, so a `/run` opens a single connection pool rather than five.
    """
    client = make_agent_client(config)
    navigation_tools = _navigation_tools(config)
    handoffs: list[Agent[Any] | Handoff[JenAIRunContext, Any]] = [
        build_ros_developer_agent(config, client),
        build_ros_explorer_agent(config, client),
    ]
    if navigation_tools:
        handoffs.append(build_navigation_agent(config, client))
    handoffs.append(build_perception_agent(config, client))
    return Agent[JenAIRunContext](
        name="JenAI",
        instructions=f"{SUPERVISOR_INSTRUCTIONS}\n\n{capability_prompt(config)}",
        model=build_model(config, binding="chat", client=client),
        tool_use_behavior=_finish_read_only_state,
        # Mirror the complete bounded navigation workflow on the supervisor.
        # Some OpenAI-compatible local models select a specialist tool by name
        # but omit the handoff wrapper, especially on a follow-up turn. Keeping
        # these exact tools reachable prevents an SDK ``Tool not found`` while
        # preserving route_execute/explore approval and NavigationGateway safety.
        tools=[
            shell_run_tool,
            ros_topics_tool,
            ros_topic_info_tool,
            ros_state_tool,
        ]
        + navigation_tools,
        input_guardrails=[unsafe_command_guardrail],
        handoffs=handoffs,
    )
