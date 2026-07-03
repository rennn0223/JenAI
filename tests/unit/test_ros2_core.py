from __future__ import annotations

import asyncio

import pytest

from jenai.adapters import ros2_adapter
from jenai.config.store import build_minimal_config
from jenai.tools import ros2_core


def _config():
    return build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="gpt-test",
        api_key_env="",
    )


def test_ros_topics_classifies_kind_hints(monkeypatch) -> None:
    monkeypatch.setattr(
        ros2_adapter,
        "list_topics",
        lambda **kw: ["/cmd_vel", "/scan", "/rosout", "/diagnostics"],
    )

    output = asyncio.run(ros2_core.ros_topics(_config()))

    by_name = {item.name: item.kind_hint for item in output.topics}
    assert by_name["/cmd_vel"] == "control"
    assert by_name["/scan"] == "sensor"
    assert by_name["/rosout"] == "unknown"
    assert by_name["/diagnostics"] == "debug"


def test_ros_schema_summarizes_via_llm_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        ros2_adapter,
        "topic_info",
        lambda topic, **kw: ros2_adapter.TopicInfo(
            name=topic, message_type="geometry_msgs/msg/Point"
        ),
    )
    monkeypatch.setattr(
        ros2_adapter,
        "interface_show",
        lambda message_type, **kw: "float64 x\nfloat64 y\n",
    )

    async def fake_summarize(config, message_type, raw_interface):
        return []

    monkeypatch.setattr("jenai.tools.ros2_core.summarize_ros_schema", fake_summarize)

    output = asyncio.run(ros2_core.ros_schema(_config(), "/point"))

    assert output.message_type == "geometry_msgs/msg/Point"
    assert output.example_payload == {"x": 0.0, "y": 0.0}


def test_ros_topic_info_success(monkeypatch) -> None:
    monkeypatch.setattr(ros2_adapter, "list_topics", lambda **kw: ["/cmd_vel"])
    monkeypatch.setattr(
        ros2_adapter,
        "topic_info",
        lambda topic, **kw: ros2_adapter.TopicInfo(
            name=topic,
            message_type="geometry_msgs/msg/Twist",
            publisher_count=1,
            publishers=["/teleop"],
        ),
    )

    output = asyncio.run(ros2_core.ros_topic_info(_config(), "/cmd_vel"))

    assert output.message_type == "geometry_msgs/msg/Twist"
    assert output.publisher_count == 1
    assert output.candidates == []


def test_ros_topic_info_missing_gives_fuzzy_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        ros2_adapter, "list_topics", lambda **kw: ["/cmd_vel", "/cmd_vel_stamped"]
    )

    output = asyncio.run(ros2_core.ros_topic_info(_config(), "cmd_vel_"))

    assert output.message_type == ""
    assert "/cmd_vel_stamped" in output.candidates


def test_example_payload_handles_nested_types() -> None:
    # `ros2 interface show geometry_msgs/msg/Twist` expands nested Vector3 types
    # with tab indentation; the draft payload must preserve that nesting.
    raw = (
        "# comment line\n"
        "Vector3  linear\n"
        "\tfloat64 x\n"
        "\tfloat64 y\n"
        "\tfloat64 z\n"
        "Vector3  angular\n"
        "\tfloat64 x\n"
        "\tfloat64 y\n"
        "\tfloat64 z\n"
    )
    payload = ros2_core._naive_example_payload(raw)
    assert payload == {
        "linear": {"x": 0.0, "y": 0.0, "z": 0.0},
        "angular": {"x": 0.0, "y": 0.0, "z": 0.0},
    }


def test_example_payload_arrays() -> None:
    raw = (
        "float64[] ranges\n"
        "geometry_msgs/Point[] points\n"
        "\tfloat64 x\n"
        "\tfloat64 y\n"
    )
    payload = ros2_core._naive_example_payload(raw)
    assert payload == {"ranges": [], "points": [{"x": 0.0, "y": 0.0}]}


def test_example_payload_skips_constants() -> None:
    raw = "uint8 FOO=1\nuint8 status\n"
    assert ros2_core._naive_example_payload(raw) == {"status": 0}


def test_ros_echo_timeout_is_graceful(monkeypatch) -> None:
    def boom(topic, **kw):
        raise ros2_adapter.Ros2CommandError("ros2 topic echo timed out")

    monkeypatch.setattr(ros2_adapter, "topic_echo", boom)

    output = asyncio.run(ros2_core.ros_echo(_config(), "/idle", limit=1))

    assert output.messages == []
    assert "idle" in output.summary.lower()


def test_ros_echo_not_available_propagates(monkeypatch) -> None:
    def boom(topic, **kw):
        raise ros2_adapter.Ros2NotAvailableError("ros2 not found")

    monkeypatch.setattr(ros2_adapter, "topic_echo", boom)

    with pytest.raises(ros2_adapter.Ros2NotAvailableError):
        asyncio.run(ros2_core.ros_echo(_config(), "/x", limit=1))


def test_ros_echo_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(
        ros2_adapter, "topic_echo", lambda topic, **kw: ["data: hello", "data: world"]
    )

    output = asyncio.run(ros2_core.ros_echo(_config(), "/chatter", limit=2))

    assert output.mode == "snapshot"
    assert len(output.messages) == 2
    assert output.messages[0] == {"raw": "data: hello"}


def test_ros_echo_idle_topic(monkeypatch) -> None:
    monkeypatch.setattr(ros2_adapter, "topic_echo", lambda topic, **kw: [])

    output = asyncio.run(ros2_core.ros_echo(_config(), "/idle", limit=1))

    assert output.messages == []
    assert "No messages" in output.summary


def test_ros_pub_validate_rejects_unknown_topic(monkeypatch) -> None:
    monkeypatch.setattr(ros2_adapter, "list_topics", lambda **kw: ["/cmd_vel"])

    result = asyncio.run(ros2_core.ros_pub_validate("/unknown", {}))

    assert result.ok is False
    assert result.error is not None
    assert result.error.error_type == "validation_error"


def test_ros_pub_validate_rejects_non_dict_payload(monkeypatch) -> None:
    monkeypatch.setattr(ros2_adapter, "list_topics", lambda **kw: ["/cmd_vel"])
    monkeypatch.setattr(
        ros2_adapter,
        "topic_info",
        lambda topic, **kw: ros2_adapter.TopicInfo(
            name=topic, message_type="geometry_msgs/msg/Twist"
        ),
    )

    result = asyncio.run(ros2_core.ros_pub_validate("/cmd_vel", "not a dict"))  # type: ignore[arg-type]

    assert result.ok is False


def test_ros_pub_validate_success(monkeypatch) -> None:
    monkeypatch.setattr(ros2_adapter, "list_topics", lambda **kw: ["/cmd_vel"])
    monkeypatch.setattr(
        ros2_adapter,
        "topic_info",
        lambda topic, **kw: ros2_adapter.TopicInfo(
            name=topic, message_type="geometry_msgs/msg/Twist"
        ),
    )

    result = asyncio.run(ros2_core.ros_pub_validate("/cmd_vel", {"linear": {"x": 0.5}}))

    assert result.ok is True
    assert result.message_type == "geometry_msgs/msg/Twist"


def test_zero_like_zeros_a_twist() -> None:
    payload = {"linear": {"x": 0.5, "y": 0.0, "z": 0.0}, "angular": {"x": 0.0, "y": 0.0, "z": 0.3}}
    assert ros2_core._zero_like(payload) == {
        "linear": {"x": 0, "y": 0, "z": 0},
        "angular": {"x": 0, "y": 0, "z": 0},
    }


def test_ros_drive_publishes_for_duration_then_stops(monkeypatch) -> None:
    captured = {}

    def fake_pub_for(topic, message_type, payload_yaml, *, rate_hz, duration_s, stop_yaml):
        captured.update(
            topic=topic, duration_s=duration_s, rate_hz=rate_hz, stop_yaml=stop_yaml
        )
        return ros2_adapter.PubResult(ok=True, message="drove then stopped")

    monkeypatch.setattr(ros2_adapter, "topic_pub_for", fake_pub_for)

    output = asyncio.run(
        ros2_core.ros_drive(
            "/cmd_vel",
            "geometry_msgs/msg/Twist",
            {"linear": {"x": 0.2, "y": 0.0, "z": 0.0}, "angular": {"x": 0.0, "y": 0.0, "z": 0.0}},
            duration_s=1.0,
        )
    )

    assert output.execution_status == "succeeded"
    assert captured["duration_s"] == 1.0
    # stop pulse is a zeroed Twist
    assert '"x": 0' in captured["stop_yaml"] and '0.2' not in captured["stop_yaml"]


def test_ros_drive_clamps_duration(monkeypatch) -> None:
    captured = {}

    def fake_pub_for(topic, message_type, payload_yaml, *, rate_hz, duration_s, stop_yaml):
        captured["duration_s"] = duration_s
        return ros2_adapter.PubResult(ok=True, message="ok")

    monkeypatch.setattr(ros2_adapter, "topic_pub_for", fake_pub_for)
    asyncio.run(ros2_core.ros_drive("/cmd_vel", "geometry_msgs/msg/Twist", {}, duration_s=999))
    assert captured["duration_s"] == 30.0  # clamped to the safe window


def test_ros_pub_execute_reports_success(monkeypatch) -> None:
    monkeypatch.setattr(
        ros2_adapter,
        "topic_pub",
        lambda *a, **kw: ros2_adapter.PubResult(ok=True, message="published"),
    )

    output = asyncio.run(
        ros2_core.ros_pub_execute("/cmd_vel", "geometry_msgs/msg/Twist", {"linear": {"x": 0.5}})
    )

    assert output.execution_status == "succeeded"
    assert output.result_message == "published"


def test_safety_clamp_bounds_top_level_twist() -> None:
    out = ros2_core._safety_clamp({"linear": {"x": 50.0}, "angular": {"z": -9.0}})
    assert out["linear"]["x"] == ros2_core.MAX_LINEAR
    assert out["angular"]["z"] == -ros2_core.MAX_ANGULAR


def test_safety_clamp_bounds_nested_twiststamped() -> None:
    # TwistStamped nests the velocities under `twist`; the clamp must reach them
    # rather than let a 50 m/s command through untouched.
    out = ros2_core._safety_clamp({"twist": {"linear": {"x": 50.0}, "angular": {"z": 9.0}}})
    assert out["twist"]["linear"]["x"] == ros2_core.MAX_LINEAR
    assert out["twist"]["angular"]["z"] == ros2_core.MAX_ANGULAR


def test_safety_clamp_does_not_treat_bool_as_speed() -> None:
    # bool is a subclass of int; a JSON `true` must not become full speed 1.0.
    out = ros2_core._safety_clamp({"linear": {"x": True}})
    assert out["linear"]["x"] is True


def test_topic_pub_for_reports_failure_when_process_exits_early(monkeypatch) -> None:
    class _DeadProc:
        returncode = 1

        def __init__(self) -> None:
            self.stderr = _Stderr()

        def poll(self):  # already exited on its own -> the drive failed
            return 1

        def terminate(self):  # pragma: no cover - not reached on the failure path
            raise AssertionError("should not terminate an already-dead process")

    class _Stderr:
        def read(self):
            return "invalid message type"

    monkeypatch.setattr(ros2_adapter, "is_available", lambda: True)
    monkeypatch.setattr(ros2_adapter.subprocess, "Popen", lambda *a, **kw: _DeadProc())
    monkeypatch.setattr(ros2_adapter.time, "sleep", lambda *_: None)

    result = ros2_adapter.topic_pub_for("/cmd_vel", "bad/type", "{}", duration_s=0.0)
    assert result.ok is False
    assert "exited early" in result.message
    assert "invalid message type" in result.message


def test_example_payload_survives_type_only_comment_line() -> None:
    # `<type> #comment` (no field name) must not crash the schema parser.
    raw = "float64 # reserved\nfloat64 x"
    assert ros2_core._naive_example_payload(raw) == {"x": 0.0}


def test_safety_clamp_uses_vehicle_limits() -> None:
    from jenai.tools.ros2_core import _safety_clamp

    payload = {"linear": {"x": 5.0}, "angular": {"z": -3.0}}

    # Default (fallback) limits: 1.0 / 2.0.
    default = _safety_clamp(payload)
    assert default["linear"]["x"] == 1.0 and default["angular"]["z"] == -2.0

    # Vehicle profile limits (e.g. Leatherback: 2.0 m/s, 0.53 rad/s).
    vehicle = _safety_clamp(payload, 2.0, 0.53)
    assert vehicle["linear"]["x"] == 2.0 and vehicle["angular"]["z"] == -0.53
