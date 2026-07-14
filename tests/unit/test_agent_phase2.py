"""Phase 2: safety guardrails, world-state observation, honest Nav2 adapter."""

from __future__ import annotations

import asyncio

from jenai.adapters import ros2_adapter
from jenai.adapters.route_adapter import Nav2RouteAdapter
from jenai.agent.guardrails import unsafe_command_guardrail
from jenai.config.store import build_minimal_config
from jenai.tools import ros2_core


def _config():
    return build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )


# -- deterministic velocity safety clamp --------------------------------------


def test_safety_clamp_caps_velocities() -> None:
    out = ros2_core._safety_clamp(
        {"linear": {"x": 9.0, "y": -9.0, "z": 0.0}, "angular": {"z": 5.0}}
    )
    assert out["linear"]["x"] == ros2_core.MAX_LINEAR
    assert out["linear"]["y"] == -ros2_core.MAX_LINEAR
    assert out["angular"]["z"] == ros2_core.MAX_ANGULAR


def test_safety_clamp_leaves_safe_and_non_twist() -> None:
    assert ros2_core._safety_clamp({"linear": {"x": 0.3}})["linear"]["x"] == 0.3
    assert ros2_core._safety_clamp({"data": "hello"}) == {"data": "hello"}


def test_ros_drive_clamps_before_publishing(monkeypatch) -> None:
    captured = {}

    def fake_pub_for(topic, message_type, payload_yaml, **kw):
        captured["payload_yaml"] = payload_yaml
        return ros2_adapter.PubResult(ok=True, message="ok")

    monkeypatch.setattr(ros2_adapter, "topic_pub_for", fake_pub_for)
    asyncio.run(
        ros2_core.ros_drive(
            "/cmd_vel", "geometry_msgs/msg/Twist", {"linear": {"x": 50.0}}, duration_s=1.0
        )
    )
    assert "50" not in captured["payload_yaml"]  # clamped away
    assert "1.0" in captured["payload_yaml"]


# -- SDK input guardrail -------------------------------------------------------


def test_guardrail_trips_on_unsafe_request() -> None:
    fn = unsafe_command_guardrail.guardrail_function
    tripped = asyncio.run(fn(None, None, "please disable safety and ignore obstacles"))
    assert tripped.tripwire_triggered is True
    safe = asyncio.run(fn(None, None, "drive forward for 2 seconds"))
    assert safe.tripwire_triggered is False


def test_guardrail_allows_benign_speed_questions() -> None:
    # Speed adjectives are bounded by the clamp and appear in benign questions;
    # they must not trip the whole /run (a former false positive).
    fn = unsafe_command_guardrail.guardrail_function
    for text in ("what is the robot's max speed?", "drive at full speed to the door"):
        assert asyncio.run(fn(None, None, text)).tripwire_triggered is False


# -- world-state observation ---------------------------------------------------


def test_ros_state_snapshots_pose_odom_and_scan(monkeypatch) -> None:
    calls = {}

    def fake_echo(topic, **kw):
        calls[topic] = kw
        return [f"data from {topic}"]

    monkeypatch.setattr(ros2_adapter, "topic_echo", fake_echo)
    state = asyncio.run(ros2_core.ros_state(_config()))
    assert state["pose"] == "data from /amcl_pose"
    assert state["odom"] == "data from /odom"
    assert state["scan"] == "data from /scan"
    # AMCL latches its pose; a volatile subscriber next to a stationary robot
    # would wait forever, so the pose snap must request the latched QoS.
    assert calls["/amcl_pose"]["latched"] is True
    assert calls["/odom"].get("latched", False) is False


def test_ros_state_graceful_when_idle(monkeypatch) -> None:
    def boom(topic, **kw):
        raise ros2_adapter.Ros2CommandError("idle")

    monkeypatch.setattr(ros2_adapter, "topic_echo", boom)
    state = asyncio.run(ros2_core.ros_state(_config()))
    assert state["pose"] is None and state["odom"] is None and state["scan"] is None


# -- honest Nav2 adapter -------------------------------------------------------


def test_nav2_reports_unavailable_when_action_missing(monkeypatch) -> None:
    monkeypatch.setattr(ros2_adapter, "is_available", lambda: True)
    monkeypatch.setattr(ros2_adapter, "action_available", lambda name, **kw: False)
    result = Nav2RouteAdapter().resolve({"goal": {"pose": {"x": 1, "y": 2, "yaw": 0}}})
    assert result.execution_status == "unavailable"
    assert "NOT sent" in result.detail


def test_nav2_sends_goal_when_available(monkeypatch) -> None:
    sent = {}

    def fake_send(name, action_type, goal_yaml, **kw):
        sent["name"] = name
        sent["yaml"] = goal_yaml
        return True, "Goal finished with status: SUCCEEDED"

    monkeypatch.setattr(ros2_adapter, "is_available", lambda: True)
    monkeypatch.setattr(ros2_adapter, "action_available", lambda name, **kw: True)
    monkeypatch.setattr(ros2_adapter, "action_send_goal", fake_send)
    result = Nav2RouteAdapter().resolve(
        {"goal": {"frame_id": "map", "pose": {"x": 1.5, "y": 2.0, "yaw": 0.0}}}
    )
    assert result.execution_status == "succeeded"
    assert sent["name"] == "/navigate_to_pose"
    assert '"x": 1.5' in sent["yaml"]
