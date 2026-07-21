from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

from jenai.adapters import ros2_adapter
from jenai.config.store import build_minimal_config
from jenai.schemas import RosEchoOutput, RosPubOutput
from jenai.tools import ros2_core, summaries


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
        lambda **kw: [
            "/cmd_vel",
            "/scan",
            "/rosout",
            "/diagnostics",
            "/global_costmap/costmap",
            "/amcl/transition_event",
            "/tf_static",
            "/front_3d_lidar/lidar_points",
        ],
    )

    output = asyncio.run(ros2_core.ros_topics(_config()))

    by_name = {item.name: item.kind_hint for item in output.topics}
    assert by_name["/cmd_vel"] == "control"
    assert by_name["/scan"] == "sensor"
    assert by_name["/rosout"] == "infra"
    assert by_name["/diagnostics"] == "infra"
    assert by_name["/global_costmap/costmap"] == "nav"
    # plumbing beats the domain word: amcl's lifecycle event is infra, not nav
    assert by_name["/amcl/transition_event"] == "infra"
    assert by_name["/tf_static"] == "tf"
    assert by_name["/front_3d_lidar/lidar_points"] == "sensor"


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
    monkeypatch.setattr(ros2_adapter, "list_topics", lambda **kw: ["/cmd_vel", "/cmd_vel_stamped"])

    output = asyncio.run(ros2_core.ros_topic_info(_config(), "cmd_vel_"))

    assert output.message_type == ""
    assert "/cmd_vel_stamped" in output.candidates


def test_ros_nav_status_reports_readiness_without_inventing_activity(monkeypatch) -> None:
    monkeypatch.setattr(
        ros2_adapter,
        "list_topics",
        lambda **kw: ["/map", "/amcl_pose", "/scan", "/cmd_vel"],
    )
    monkeypatch.setattr(ros2_adapter, "list_actions", lambda **kw: ["/navigate_to_pose"])
    monkeypatch.setattr(
        ros2_adapter,
        "topic_info",
        lambda topic, **kw: ros2_adapter.TopicInfo(name=topic, subscriber_count=1),
    )

    status = asyncio.run(ros2_core.ros_nav_status(_config()))

    assert status["ready"] is True
    assert all(status["checks"].values())
    assert status["activity"] == "NOT_MEASURED"
    assert status["activity_observed"] is False
    assert "did not measure" in status["activity_report"]
    assert "no-current-goal" in status["activity_note"]
    assert "another client" in status["activity_note"]


def test_ros_nav_status_retries_a_cold_partial_topic_graph(monkeypatch) -> None:
    calls = 0

    def list_topics(**kwargs):
        nonlocal calls
        calls += 1
        return [] if calls == 1 else ["/map", "/amcl_pose", "/scan", "/cmd_vel"]

    monkeypatch.setattr(ros2_adapter, "list_topics", list_topics)
    monkeypatch.setattr(ros2_adapter, "list_actions", lambda **kw: ["/navigate_to_pose"])
    monkeypatch.setattr(
        ros2_adapter,
        "topic_info",
        lambda topic, **kw: ros2_adapter.TopicInfo(name=topic, subscriber_count=1),
    )

    status = asyncio.run(ros2_core.ros_nav_status(_config()))

    assert calls == 2
    assert status["ready"] is True


def test_ros_nav_status_fails_readiness_when_action_or_controller_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ros2_adapter,
        "list_topics",
        lambda **kw: ["/map", "/amcl_pose", "/scan", "/cmd_vel"],
    )
    monkeypatch.setattr(ros2_adapter, "list_actions", lambda **kw: [])
    monkeypatch.setattr(
        ros2_adapter,
        "topic_info",
        lambda topic, **kw: (_ for _ in ()).throw(ros2_adapter.Ros2CommandError("missing")),
    )

    status = asyncio.run(ros2_core.ros_nav_status(_config()))

    assert status["ready"] is False
    assert status["checks"]["navigate_to_pose"] is False
    assert status["checks"]["cmd_vel_subscriber"] is False


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
    raw = "float64[] ranges\ngeometry_msgs/Point[] points\n\tfloat64 x\n\tfloat64 y\n"
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

    async def fake_pub_for(topic, message_type, payload_yaml, *, rate_hz, duration_s, stop_yaml):
        captured.update(topic=topic, duration_s=duration_s, rate_hz=rate_hz, stop_yaml=stop_yaml)
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
    assert '"x": 0' in captured["stop_yaml"] and "0.2" not in captured["stop_yaml"]


def test_ros_drive_clamps_duration(monkeypatch) -> None:
    captured = {}

    async def fake_pub_for(topic, message_type, payload_yaml, *, rate_hz, duration_s, stop_yaml):
        captured["duration_s"] = duration_s
        return ros2_adapter.PubResult(ok=True, message="ok")

    monkeypatch.setattr(ros2_adapter, "topic_pub_for", fake_pub_for)
    asyncio.run(ros2_core.ros_drive("/cmd_vel", "geometry_msgs/msg/Twist", {}, duration_s=999))
    assert captured["duration_s"] == 30.0  # clamped to the safe window


def _odom(x: float, y: float = 0.0, z: float = 0.0, w: float = 1.0) -> str:
    return (
        "pose:\n"
        "  pose:\n"
        "    position:\n"
        f"      x: {x}\n"
        f"      y: {y}\n"
        "      z: 0.0\n"
        "    orientation:\n"
        "      x: 0.0\n"
        "      y: 0.0\n"
        f"      z: {z}\n"
        f"      w: {w}\n"
    )


def test_planar_odom_pose_extracts_position_and_yaw() -> None:
    pose = ros2_core._planar_odom_pose(_odom(1.25, -0.5, z=0.1, w=0.995))
    assert pose is not None
    assert pose["x"] == 1.25
    assert pose["y"] == -0.5
    assert pose["yaw"] > 0.19


def test_verified_drive_observes_position_change(monkeypatch) -> None:
    snapshots = iter([_odom(0.0), _odom(0.1)])
    drives = 0

    async def fake_echo(config, topic, *, limit):
        return RosEchoOutput(
            topic=topic,
            mode="snapshot",
            messages=[{"raw": next(snapshots)}],
            summary="captured",
        )

    async def fake_drive(*args, **kwargs):
        nonlocal drives
        drives += 1
        return RosPubOutput(
            topic="/cmd_vel",
            message_type="geometry_msgs/msg/Twist",
            payload_preview={},
            approval_status="approved",
            execution_status="succeeded",
            result_message="stopped",
        )

    monkeypatch.setattr(ros2_core, "ros_echo", fake_echo)
    monkeypatch.setattr(ros2_core, "ros_drive", fake_drive)
    result = asyncio.run(
        ros2_core.ros_drive_verified(
            _config(),
            "/cmd_vel",
            "geometry_msgs/msg/Twist",
            {"linear": {"x": 0.1}},
        )
    )
    assert result["verdict"] == "verified"
    assert result["position_change"] == pytest.approx(0.1)
    assert drives == 1


def test_verified_drive_does_not_move_without_baseline(monkeypatch) -> None:
    async def no_echo(config, topic, *, limit):
        return RosEchoOutput(topic=topic, mode="snapshot", messages=[], summary="none")

    async def forbidden_drive(*args, **kwargs):
        raise AssertionError("drive must not run without a baseline")

    monkeypatch.setattr(ros2_core, "ros_echo", no_echo)
    monkeypatch.setattr(ros2_core, "ros_drive", forbidden_drive)
    result = asyncio.run(
        ros2_core.ros_drive_verified(
            _config(),
            "/cmd_vel",
            "geometry_msgs/msg/Twist",
            {"linear": {"x": 0.1}},
        )
    )
    assert result["verdict"] == "not_executed"
    assert result["actuation_performed"] is False


def test_verified_drive_missing_post_feedback_is_unverified(monkeypatch) -> None:
    snapshots = iter(
        [
            RosEchoOutput(
                topic="/odom",
                mode="snapshot",
                messages=[{"raw": _odom(0.0)}],
                summary="captured",
            ),
            RosEchoOutput(topic="/odom", mode="snapshot", messages=[], summary="none"),
        ]
    )
    drives = 0

    async def fake_echo(config, topic, *, limit):
        return next(snapshots)

    async def fake_drive(*args, **kwargs):
        nonlocal drives
        drives += 1
        return RosPubOutput(
            topic="/cmd_vel",
            message_type="geometry_msgs/msg/Twist",
            payload_preview={},
            approval_status="approved",
            execution_status="succeeded",
            result_message="stopped",
        )

    monkeypatch.setattr(ros2_core, "ros_echo", fake_echo)
    monkeypatch.setattr(ros2_core, "ros_drive", fake_drive)
    result = asyncio.run(
        ros2_core.ros_drive_verified(
            _config(),
            "/cmd_vel",
            "geometry_msgs/msg/Twist",
            {"linear": {"x": 0.1}},
        )
    )
    assert result["verdict"] == "unverified"
    assert result["post_pose"] is None
    assert drives == 1


def test_ros_pub_execute_reports_success(monkeypatch) -> None:
    async def fake_pub(*args, **kwargs):
        return ros2_adapter.PubResult(ok=True, message="published")

    monkeypatch.setattr(ros2_adapter, "topic_pub_async", fake_pub)

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


def test_safety_clamp_bounds_ackermann_drive_variants() -> None:
    direct = ros2_core._safety_clamp(
        {"speed": 50.0, "steering_angle": -9.0},
        1.0,
        2.0,
        message_type="ackermann_msgs/msg/AckermannDrive",
    )
    stamped = ros2_core._safety_clamp(
        {"drive": {"speed": -50.0, "steering_angle": 9.0}},
        1.0,
        2.0,
        message_type="ackermann_msgs/msg/AckermannDriveStamped",
    )

    assert direct == {"speed": 1.0, "steering_angle": -2.0}
    assert stamped == {"drive": {"speed": -1.0, "steering_angle": 2.0}}


def test_ros_pub_execute_applies_ackermann_vehicle_limits(monkeypatch) -> None:
    captured = {}

    async def fake_pub(topic, message_type, payload_yaml, **kwargs):
        captured["payload"] = json.loads(payload_yaml)
        captured["cancel_stop"] = json.loads(kwargs["cancel_stop_yaml"])
        return ros2_adapter.PubResult(ok=True, message="published")

    monkeypatch.setattr(ros2_adapter, "topic_pub_async", fake_pub)
    asyncio.run(
        ros2_core.ros_pub_execute(
            "/ackermann_cmd",
            "ackermann_msgs/msg/AckermannDriveStamped",
            {"drive": {"speed": 99.0, "steering_angle": 9.0}},
            max_linear=0.5,
            max_angular=0.4,
        )
    )

    assert captured["payload"] == {"drive": {"speed": 0.5, "steering_angle": 0.4}}
    assert captured["cancel_stop"] == {"drive": {"speed": 0.0, "steering_angle": 0.0}}


def test_safety_clamp_does_not_treat_bool_as_speed() -> None:
    # bool is a subclass of int; a JSON `true` must not become full speed 1.0.
    out = ros2_core._safety_clamp({"linear": {"x": True}})
    assert out["linear"]["x"] is True


def test_topic_pub_for_reports_publisher_failure(monkeypatch) -> None:
    captured = {}

    async def fake_run(args, **kwargs):
        captured["args"] = args
        captured["timeout"] = kwargs["timeout"]
        return ros2_adapter.subprocess.CompletedProcess(
            args=args, returncode=1, stdout="", stderr="invalid message type"
        )

    monkeypatch.setattr(ros2_adapter, "is_available", lambda: True)
    monkeypatch.setattr(ros2_adapter, "run_process_async", fake_run)

    result = asyncio.run(ros2_adapter.topic_pub_for("/cmd_vel", "bad/type", "{}", duration_s=0.5))

    assert result.ok is False
    assert "exited with code 1" in result.message
    assert "invalid message type" in result.message
    assert captured["args"][0] == "/usr/bin/python3"
    assert captured["args"][1].endswith("bridge/ros_bounded_publisher.py")
    assert captured["args"][2:] == ["/cmd_vel", "bad/type", "{}", "{}", "10.0", "0.5", "5"]
    assert captured["timeout"] == 8.5


@pytest.mark.skipif(os.name != "posix", reason="process-group cleanup is POSIX-specific")
def test_ros_drive_cancellation_reaps_publisher_before_final_zero(
    monkeypatch, tmp_path: Path
) -> None:
    script = tmp_path / "fake_publisher.py"
    events = tmp_path / "events.log"
    pid_file = tmp_path / "publisher.pid"
    script.write_text(
        """import os
import signal
import sys
import time
from pathlib import Path

events, pid_file = map(Path, sys.argv[1:3])
running = True

def stop(signum, frame):
    global running
    running = False

signal.signal(signal.SIGTERM, stop)
pid_file.write_text(str(os.getpid()))
while running:
    with events.open("a") as stream:
        stream.write("motion\\n")
    time.sleep(0.02)
with events.open("a") as stream:
    stream.write("publisher-stopped\\n")
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(ros2_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        ros2_adapter,
        "_bounded_publisher_args",
        lambda *args, **kwargs: [sys.executable, str(script), str(events), str(pid_file)],
    )

    async def final_zero(*args, **kwargs):
        with events.open("a") as stream:
            stream.write("zero\n")

    monkeypatch.setattr(ros2_adapter, "_best_effort_stop", final_zero)

    async def scenario() -> int:
        task = asyncio.create_task(
            ros2_core.ros_drive(
                "/cmd_vel",
                "geometry_msgs/msg/Twist",
                {"linear": {"x": 0.2}},
                duration_s=30.0,
            )
        )
        for _ in range(100):
            if pid_file.exists() and events.exists():
                break
            await asyncio.sleep(0.01)
        assert pid_file.exists() and events.exists()
        pid = int(pid_file.read_text().strip())

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return pid

    pid = asyncio.run(scenario())
    lines_after_cancel = events.read_text().splitlines()
    time.sleep(0.15)

    assert lines_after_cancel[-2:] == ["publisher-stopped", "zero"]
    assert events.read_text().splitlines() == lines_after_cancel
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_topic_pub_for_zero_duration_sends_stop_only(monkeypatch) -> None:
    published = []

    monkeypatch.setattr(ros2_adapter, "is_available", lambda: True)

    async def fake_stop_pub(topic, message_type, payload, **kwargs):
        published.append(payload)
        return ros2_adapter.PubResult(ok=True, message="published")

    monkeypatch.setattr(ros2_adapter, "topic_pub_async", fake_stop_pub)

    async def unexpected_motion(*args, **kwargs):
        raise AssertionError("motion publisher must not start for zero duration")

    monkeypatch.setattr(ros2_adapter, "run_process_async", unexpected_motion)

    result = asyncio.run(
        ros2_adapter.topic_pub_for(
            "/cmd_vel",
            "geometry_msgs/msg/Twist",
            "{linear: {x: 1.0}}",
            duration_s=0.0,
            stop_yaml="{linear: {x: 0.0}}",
        )
    )
    assert result.ok is True
    assert published == ["{linear: {x: 0.0}}"]


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


def test_safety_clamp_fails_closed_for_invalid_limits() -> None:
    payload = {"linear": {"x": -5.0}, "angular": {"z": 3.0}}

    negative = ros2_core._safety_clamp(payload, -1.0, -2.0)
    infinite = ros2_core._safety_clamp(payload, float("inf"), float("nan"))
    invalid_values = ros2_core._safety_clamp(
        {"linear": {"x": float("nan")}, "angular": {"z": float("inf")}}
    )

    assert negative == {"linear": {"x": 0}, "angular": {"z": 0}}
    assert infinite == {"linear": {"x": 0}, "angular": {"z": 0}}
    assert invalid_values == {"linear": {"x": 0}, "angular": {"z": 0}}


def test_schema_summary_timeout_falls_back_to_deterministic_fields(monkeypatch) -> None:
    async def slow_ask_json(config, prompt):
        await asyncio.sleep(1)
        return []

    monkeypatch.setattr(summaries, "ask_json", slow_ask_json)
    monkeypatch.setattr(summaries, "SCHEMA_SUMMARY_TIMEOUT_SECONDS", 0.001)

    result = asyncio.run(
        summaries.summarize_ros_schema(
            _config(),
            "geometry_msgs/msg/Point",
            "float64 x\nfloat64 y\n",
        )
    )

    assert [(field.field_name, field.field_type) for field in result] == [
        ("x", "float64"),
        ("y", "float64"),
    ]
