from __future__ import annotations

import json
from dataclasses import dataclass

from jenai.adapters import ros2_adapter
from jenai.config.models import AppConfig
from jenai.schemas import (
    ErrorType,
    JenAIError,
    RosPubOutput,
    RosSchemaOutput,
    RosTopicsOutput,
    TopicItem,
)
from jenai.tools.summaries import summarize_ros_schema

_KIND_HINTS = (
    (("cmd",), "control"),
    (("scan", "image", "imu", "odom", "camera"), "sensor"),
    (("debug", "diagnostics"), "debug"),
)


def _kind_hint(topic: str) -> str:
    lowered = topic.lower()
    for keywords, kind in _KIND_HINTS:
        if any(keyword in lowered for keyword in keywords):
            return kind
    return "unknown"


async def ros_topics(config: AppConfig) -> RosTopicsOutput:
    _ = config
    topics = ros2_adapter.list_topics()
    return RosTopicsOutput(
        topics=[TopicItem(name=name, kind_hint=_kind_hint(name)) for name in topics]
    )


def _naive_example_payload(raw_interface: str) -> dict:
    example: dict = {}
    for line in raw_interface.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            continue
        field_type, field_name = parts
        field_name = field_name.split("#", 1)[0].strip()
        if not field_name:
            continue
        if field_type.endswith("[]"):
            example[field_name] = []
        elif "float" in field_type or "double" in field_type:
            example[field_name] = 0.0
        elif "int" in field_type or "uint" in field_type:
            example[field_name] = 0
        elif field_type == "bool":
            example[field_name] = False
        elif field_type == "string":
            example[field_name] = ""
        else:
            example[field_name] = {}
    return example


async def ros_schema(config: AppConfig, topic: str) -> RosSchemaOutput:
    info = ros2_adapter.topic_info(topic)
    raw_interface = ros2_adapter.interface_show(info.message_type)
    field_summary = await summarize_ros_schema(config, info.message_type, raw_interface)
    return RosSchemaOutput(
        topic=topic,
        message_type=info.message_type,
        raw_interface=raw_interface,
        field_summary=field_summary,
        example_payload=_naive_example_payload(raw_interface),
    )


@dataclass
class Ros2PubValidation:
    ok: bool
    message_type: str = ""
    payload_preview: dict | None = None
    error: JenAIError | None = None


async def ros_pub_validate(topic: str, payload: dict) -> Ros2PubValidation:
    try:
        topics = ros2_adapter.list_topics()
    except ros2_adapter.Ros2AdapterError as exc:
        return Ros2PubValidation(
            ok=False,
            error=JenAIError(error_type=ErrorType.ENV_ERROR, message=str(exc)),
        )

    if topic not in topics:
        return Ros2PubValidation(
            ok=False,
            error=JenAIError(
                error_type=ErrorType.VALIDATION_ERROR,
                message=f"Topic '{topic}' was not found.",
                details={"candidates": [t for t in topics if topic.strip("/") in t][:5]},
                fix_suggestion="Run /ros topics to see available topics.",
            ),
        )

    try:
        info = ros2_adapter.topic_info(topic)
    except ros2_adapter.Ros2AdapterError as exc:
        return Ros2PubValidation(
            ok=False,
            error=JenAIError(error_type=ErrorType.ENV_ERROR, message=str(exc)),
        )

    if not isinstance(payload, dict):
        return Ros2PubValidation(
            ok=False,
            error=JenAIError(
                error_type=ErrorType.VALIDATION_ERROR,
                message="Payload must be a JSON object.",
            ),
        )

    return Ros2PubValidation(ok=True, message_type=info.message_type, payload_preview=payload)


async def ros_pub_execute(topic: str, message_type: str, payload: dict) -> RosPubOutput:
    payload_yaml = _payload_to_yaml(payload)
    result = ros2_adapter.topic_pub(topic, message_type, payload_yaml)
    return RosPubOutput(
        topic=topic,
        message_type=message_type,
        payload_preview=payload,
        approval_status="approved",
        execution_status="succeeded" if result.ok else "failed",
        result_message=result.message,
    )


def _payload_to_yaml(payload: dict) -> str:
    # ros2 topic pub accepts a YAML-flow-style mapping; a JSON object is valid
    # YAML flow syntax, so this avoids pulling in a YAML dependency.
    return json.dumps(payload)
