"""Regression tests for the code-review findings fixed in this change."""

from __future__ import annotations

import json

from jenai.adapters import locations as loc
from jenai.adapters.ros2_adapter import _parse_topic_info, _safe_int
from jenai.schemas import Location, Pose2D
from jenai.tools import route_core
from jenai.tools.approval_formatters import (
    format_explore_approval,
    format_ros_pub_approval,
    format_route_approval,
)


def test_ros_pub_approval_reads_payload_json() -> None:
    # The tool parameter is `payload_json` (a JSON string), not `payload`.
    fields = format_ros_pub_approval(
        {
            "topic": "/cmd_vel",
            "message_type": "geometry_msgs/msg/Twist",
            "payload_json": '{"linear": {"x": 0.5}}',
        }
    )
    assert "0.5" in fields.raw_action
    assert "{}" not in fields.raw_action


def test_route_approval_reads_outgoing_action_json() -> None:
    fields = format_route_approval(
        {"outgoing_action_json": '{"start": "lab", "goal": "kitchen"}'}
    )
    assert "lab" in fields.raw_action and "kitchen" in fields.raw_action
    assert "outgoing_action_json" not in fields.raw_action


def test_route_approval_reads_once_double_encoded_action() -> None:
    action = '{"goal": {"name": "dock"}}'
    fields = format_route_approval({"outgoing_action_json": json.dumps(action)})

    assert json.loads(fields.raw_action) == {"goal": {"name": "dock"}}


def test_explore_approval_shows_all_hard_bounds() -> None:
    fields = format_explore_approval(
        {
            "duration_minutes": 3,
            "max_goals": 5,
            "max_failures": 2,
            "tag": "room",
            "seed": 7,
        }
    )
    assert "5 navigation goals" in fields.title
    assert "3 minutes" in fields.summary
    assert "2 consecutive failures" in fields.summary
    assert "seed=7" in fields.raw_action
    assert "same seed repeats" in fields.summary


def test_route_regex_does_not_match_to_inside_words() -> None:
    assert route_core._extract_via_regex("from photo lab to kitchen") == (
        "photo lab",
        "kitchen",
    )
    assert route_core._extract_via_regex("from Toronto to Ottawa") == (
        "Toronto",
        "Ottawa",
    )


def test_route_regex_still_handles_chinese() -> None:
    assert route_core._extract_via_regex("從實驗室到廚房") == ("實驗室", "廚房")


def test_safe_int_tolerates_non_integer() -> None:
    assert _safe_int(" 3 ") == 3
    assert _safe_int("unknown") == 0


def test_parse_topic_info_does_not_crash_on_bad_count() -> None:
    raw = "Type: std_msgs/msg/String\nPublisher count: unknown\nSubscriber count: 2\n"
    info = _parse_topic_info("/chatter", raw)
    assert info.message_type == "std_msgs/msg/String"
    assert info.publisher_count == 0
    assert info.subscriber_count == 2


def test_locations_roundtrip_with_control_chars(tmp_path) -> None:
    path = tmp_path / "locations.toml"
    location = Location(
        id="loc1",
        name="Lab",
        aliases=["實驗室"],
        frame_id="map",
        pose=Pose2D(x=1.0, y=2.0, yaw=0.0),
        tags=[],
        description="line1\nline2\twith tab",
    )
    loc.save_locations([location], path)
    reloaded = loc.load_locations(path)
    assert len(reloaded) == 1
    assert reloaded[0].description == "line1\nline2\twith tab"
    # And the file is valid TOML that round-trips the escaped content.
    assert "\\n" in path.read_text(encoding="utf-8")
    json.dumps(reloaded[0].model_dump(mode="json"))  # smoke: fully serializable
