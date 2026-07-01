from __future__ import annotations

from jenai.tools import ros2_agent_tools
from jenai.tools.registry import TOOL_RISK_REGISTRY


def test_ros_pub_execute_requires_approval() -> None:
    assert ros2_agent_tools.ros_pub_execute_tool.needs_approval is True


def test_read_only_tools_do_not_require_approval() -> None:
    assert ros2_agent_tools.ros_topics_tool.needs_approval is False
    assert ros2_agent_tools.ros_schema_tool.needs_approval is False
    assert ros2_agent_tools.ros_pub_validate_tool.needs_approval is False


def test_tools_registered_with_correct_risk_levels() -> None:
    assert TOOL_RISK_REGISTRY["ros_pub_execute_tool"].risk_level == "p1"
    assert TOOL_RISK_REGISTRY["ros_pub_execute_tool"].effect_scope == "sim_control"
    assert TOOL_RISK_REGISTRY["ros_topics_tool"].risk_level == "p0"
