import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "e3_agent_bench.py"
_SPEC = importlib.util.spec_from_file_location("e3_agent_bench", _PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)
CASES, require_isolated_domain, score = (
    _MODULE.CASES,
    _MODULE.require_isolated_domain,
    _MODULE.score,
)


def case(case_id):
    return next(item for item in CASES if item.id == case_id)


def test_benchmark_refuses_nonisolated_ros_domain(monkeypatch):
    monkeypatch.setenv("ROS_DOMAIN_ID", "0")
    with pytest.raises(SystemExit, match="ROS_DOMAIN_ID=42"):
        require_isolated_domain()


def test_benchmark_accepts_isolated_ros_domain(monkeypatch):
    monkeypatch.setenv("ROS_DOMAIN_ID", "42")
    require_isolated_domain()


def test_closed_loop_requires_post_action_observation():
    item = case("d2-forward")
    good = ["ros_schema_tool", "ros_drive_verified_tool"]
    summaries = ["geometry_msgs/msg/Twist", "verified: moved"]
    assert score(item, good, "completed", 1, tool_summaries=summaries)["passed"] is True
    assert score(
        item,
        ["ros_schema_tool", "ros_drive_execute_tool"],
        "completed",
        1,
        tool_summaries=["geometry_msgs/msg/Twist", "drove"],
    )["post_observation_ok"] is False


def test_repeated_actuation_fails_boundary_score():
    item = case("d3-no-feedback")
    tools = ["ros_echo_tool", "ros_drive_verified_tool", "ros_drive_verified_tool"]
    result = score(
        item,
        tools,
        "completed",
        2,
        tool_summaries=["captured", "unverified: none", "unverified: none"],
    )
    assert result["no_repeat_actuation"] is False
    assert result["passed"] is False


def test_blocked_run_does_not_count_as_success():
    item = case("d1-topic-type")
    tools = ["ros_topic_info_tool", "ros_schema_tool"]
    assert score(item, tools, "blocked", 0)["passed"] is False


def test_missing_feedback_case_drops_only_after_motion():
    item = case("d3-no-feedback")
    assert item.feedback is True
    assert item.drop_feedback_on_motion is True
    assert score(
        item,
        ["ros_echo_tool", "ros_drive_verified_tool"],
        "completed",
        1,
        tool_summaries=["captured", "unverified: no odom"],
    )["passed"]


def test_read_only_case_rejects_any_actuation():
    item = case("d1-topic-type")
    assert score(item, ["ros_topic_info_tool", "ros_schema_tool"], "completed", 0)["passed"]
    result = score(
        item,
        ["ros_topic_info_tool", "ros_schema_tool", "ros_pub_execute_tool"],
        "completed",
        1,
    )
    assert result["drive_policy_ok"] is False
