"""Specialist agents (ROS/Motion/Navigation/Perception) and the handoff graph."""

from __future__ import annotations

from agents import Agent
from openai import AsyncOpenAI

from jenai.agent.context import JenAIRunContext
from jenai.agent.guardrails import unsafe_command_guardrail
from jenai.agent.instructions import (
    MOTION_AGENT_INSTRUCTIONS,
    NAVIGATION_AGENT_INSTRUCTIONS,
    PERCEPTION_AGENT_INSTRUCTIONS,
    ROS_DEVELOPER_INSTRUCTIONS,
    ROS_EXPLORER_INSTRUCTIONS,
    SUPERVISOR_INSTRUCTIONS,
)
from jenai.agent.runtime import build_model
from jenai.config.models import AppConfig
from jenai.providers.agent_model import make_agent_client
from jenai.tools.ros2_agent_tools import (
    ros_drive_execute_tool,
    ros_drive_verified_tool,
    ros_echo_tool,
    ros_pub_execute_tool,
    ros_pub_validate_tool,
    ros_schema_tool,
    ros_state_tool,
    ros_topic_info_tool,
    ros_topics_tool,
)
from jenai.tools.route_agent_tools import (
    explore_area_tool,
    loc_lookup_tool,
    route_execute_tool,
    route_preview_tool,
)
from jenai.tools.shell_agent_tools import shell_run_tool
from jenai.tools.vision_agent_tools import vision_image_tool

# JenAI is a multi-agent system built on the openai-agents SDK's *handoffs*: a
# Supervisor `Agent` lists specialist `Agent`s in `handoffs=[...]`, and the model
# transfers control to whichever specialist fits the request. Each specialist
# carries only its own focused toolset, which keeps tool-selection reliable.


def build_ros_explorer_agent(
    config: AppConfig, client: AsyncOpenAI | None = None
) -> Agent[JenAIRunContext]:
    return Agent[JenAIRunContext](
        name="ROS Explorer",
        handoff_description="Look up ROS2 topics, message types and formats (read-only).",
        instructions=ROS_EXPLORER_INSTRUCTIONS,
        model=build_model(config, binding="chat", client=client),
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
    """One bounded specialist for live interface discovery, action, and feedback."""
    return Agent[JenAIRunContext](
        name="ROS Developer",
        handoff_description=(
            "Discover a ROS2 interface, run one approved bounded test, and verify feedback."
        ),
        instructions=ROS_DEVELOPER_INSTRUCTIONS,
        model=build_model(config, binding="chat", client=client),
        tools=[
            ros_topics_tool,
            ros_topic_info_tool,
            ros_schema_tool,
            ros_echo_tool,
            ros_state_tool,
            ros_pub_validate_tool,
            ros_pub_execute_tool,
            ros_drive_verified_tool,
        ],
    )


def build_motion_agent(
    config: AppConfig, client: AsyncOpenAI | None = None
) -> Agent[JenAIRunContext]:
    return Agent[JenAIRunContext](
        name="Motion",
        handoff_description="Publish/drive commands to move the robot (needs approval).",
        instructions=MOTION_AGENT_INSTRUCTIONS,
        model=build_model(config, binding="chat", client=client),
        tools=[ros_pub_validate_tool, ros_pub_execute_tool, ros_drive_execute_tool],
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
        tools=[
            loc_lookup_tool,
            route_preview_tool,
            route_execute_tool,
            explore_area_tool,
        ],
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

    One AsyncOpenAI client is shared across the supervisor and all five
    specialists, so a `/run` opens a single connection pool rather than six.
    """
    client = make_agent_client(config)
    return Agent[JenAIRunContext](
        name="JenAI",
        instructions=SUPERVISOR_INSTRUCTIONS,
        model=build_model(config, binding="chat", client=client),
        # Mirror the complete bounded navigation workflow on the supervisor.
        # Some OpenAI-compatible local models select a specialist tool by name
        # but omit the handoff wrapper, especially on a follow-up turn. Keeping
        # these exact tools reachable prevents an SDK ``Tool not found`` while
        # preserving route_execute/explore approval and NavigationGateway safety.
        tools=[
            shell_run_tool,
            # Keep common live-state inspection directly reachable. Local
            # models otherwise tend to choose the supervisor's shell tool
            # instead of emitting a handoff for a simple read-only question.
            ros_topics_tool,
            ros_topic_info_tool,
            ros_state_tool,
            loc_lookup_tool,
            route_preview_tool,
            route_execute_tool,
            explore_area_tool,
        ],
        input_guardrails=[unsafe_command_guardrail],
        handoffs=[
            build_ros_developer_agent(config, client),
            build_ros_explorer_agent(config, client),
            build_motion_agent(config, client),
            build_navigation_agent(config, client),
            build_perception_agent(config, client),
        ],
    )
