from __future__ import annotations

from agents import Agent

from jenai.agent import orchestrator
from jenai.agent.context import JenAIRunContext
from jenai.agent.instructions import RUN_AGENT_INSTRUCTIONS
from jenai.agent.runtime import build_model
from jenai.config.models import AppConfig
from jenai.schemas import RunRecord
from jenai.tools.ros2_agent_tools import (
    ros_drive_execute_tool,
    ros_echo_tool,
    ros_pub_execute_tool,
    ros_pub_validate_tool,
    ros_schema_tool,
    ros_topic_info_tool,
    ros_topics_tool,
)
from jenai.tools.route_agent_tools import loc_lookup_tool, route_execute_tool, route_preview_tool
from jenai.tools.shell_agent_tools import shell_run_tool
from jenai.tools.vision_agent_tools import vision_image_tool

_RUN_TOOLS = [
    ros_topics_tool,
    ros_topic_info_tool,
    ros_schema_tool,
    ros_echo_tool,
    ros_pub_validate_tool,
    ros_pub_execute_tool,
    ros_drive_execute_tool,
    route_preview_tool,
    route_execute_tool,
    loc_lookup_tool,
    vision_image_tool,
    shell_run_tool,
]


def build_run_agent(config: AppConfig) -> Agent[JenAIRunContext]:
    return Agent(
        name="JenAI Runner",
        instructions=RUN_AGENT_INSTRUCTIONS,
        model=build_model(config, binding="chat"),
        tools=_RUN_TOOLS,
    )


async def run_task(ctx: JenAIRunContext, task: str) -> RunRecord:
    return await orchestrator.start_run(build_run_agent(ctx.config), ctx, task)
