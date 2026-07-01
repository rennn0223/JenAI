from __future__ import annotations

import asyncio

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
