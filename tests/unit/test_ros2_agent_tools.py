from __future__ import annotations

from jenai.schemas.outputs import RosTopicsOutput, TopicItem
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


def test_agent_topic_inventory_is_bounded_and_filterable() -> None:
    output = RosTopicsOutput(
        topics=[TopicItem(name=f"/topic_{index}", kind_hint="unknown") for index in range(100)]
        + [
            TopicItem(name="/front_scan", kind_hint="sensor"),
            TopicItem(name="/rear_scan", kind_hint="sensor"),
        ]
    )

    default = ros2_agent_tools._bounded_topic_inventory(output, "", 40)
    assert default["total_count"] == 102
    assert default["returned_count"] == 40
    assert default["truncated"] is True

    filtered = ros2_agent_tools._bounded_topic_inventory(output, "scan", 40)
    assert [item["name"] for item in filtered["topics"]] == ["/front_scan", "/rear_scan"]
    assert filtered["matched_count"] == 2
    assert filtered["truncated"] is False


def test_agent_robot_state_truncates_large_snapshots_only() -> None:
    state = {
        "pose_topic": "/amcl_pose",
        "pose": "short pose",
        "odom": None,
        "scan": "x" * 5000,
    }

    compact = ros2_agent_tools._compact_robot_state(state)

    assert compact["pose"] == "short pose"
    assert compact["odom"] is None
    assert compact["availability"] == {"pose": True, "odom": False, "scan": True}
    assert compact["scan"].endswith("... [snapshot truncated]")
    assert compact["truncated_fields"] == ["scan"]


def test_combined_robot_status_keeps_state_and_nav2_in_one_payload() -> None:
    nav2 = {
        "ready": True,
        "checks": {"map": True},
        "activity": "not_observed",
    }

    combined = ros2_agent_tools._combined_robot_status(
        {"pose": "current", "odom": None, "scan": "current scan"},
        nav2,
    )

    assert combined["availability"] == {"pose": True, "odom": False, "scan": True}
    assert combined["nav2"] is nav2
