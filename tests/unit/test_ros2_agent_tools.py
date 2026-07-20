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


def test_agent_scan_summary_reports_total_span_and_measured_returns() -> None:
    raw = """angle_min: -1.5708
angle_max: 1.5708
angle_increment: 1.5708
range_min: 0.05
range_max: 100.0
ranges:
- .inf
- 2.5
- 1.25
intensities:
- 0.0
"""

    summary = ros2_agent_tools._scan_snapshot_summary(raw)

    assert summary["field_of_view_deg"] == 180.0
    assert summary["expected_sample_count"] == 3
    assert summary["observed_sample_count"] == 3
    assert summary["sample_count_complete"] is True
    assert summary["ranges_truncated"] is False
    assert summary["observed_finite_sample_count"] == 2
    assert summary["nearest_observed_valid_range_m"] == 1.25
    assert "not an obstacle classification" in summary["interpretation_note"]


def test_agent_scan_summary_marks_ros_cli_sequence_truncation() -> None:
    raw = """angle_min: -1.0
angle_max: 1.0
angle_increment: 0.5
ranges:
- .inf
- 2.0
- '...'
intensities: []
"""

    summary = ros2_agent_tools._scan_snapshot_summary(raw)

    assert summary["expected_sample_count"] == 5
    assert summary["observed_sample_count"] == 2
    assert summary["ranges_truncated"] is True
    assert summary["sample_count_complete"] is False
    assert summary["nearest_observed_valid_range_m"] == 2.0


def test_agent_pose_summary_extracts_map_position_and_yaw() -> None:
    raw = """header:
  frame_id: map
pose:
  pose:
    position:
      x: -5.5
      y: 2.25
      z: 0.0
    orientation:
      x: 0.0
      y: 0.0
      z: 0.70710678
      w: 0.70710678
  covariance:
  - 0.1
"""

    summary = ros2_agent_tools._pose_snapshot_summary(raw)

    assert summary["frame_id"] == "map"
    assert summary["x"] == -5.5
    assert summary["y"] == 2.25
    assert round(summary["yaw_deg"], 3) == 90.0


def test_combined_robot_status_keeps_state_and_nav2_in_one_payload() -> None:
    nav2 = {
        "ready": True,
        "checks": {"map": True},
        "activity": "NOT_MEASURED",
        "activity_observed": False,
    }

    combined = ros2_agent_tools._combined_robot_status(
        {"pose": "current", "odom": None, "scan": "current scan"},
        nav2,
    )

    assert combined["availability"] == {"pose": True, "odom": False, "scan": True}
    assert combined["nav2"] is nav2
    assert "never claim no-current-goal" in combined["reporting_boundary"]
