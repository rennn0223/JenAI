"""Build exact, redacted approval previews bound to server-held actions."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from typing import Any

from jenai.redaction import redact_sensitive_text
from jenai.schemas import ApprovalParameter, ApprovalPreview


def canonical_action_json(action: Mapping[str, Any]) -> str:
    """Return the single stable representation used to bind preview and execution."""
    return json.dumps(
        dict(action),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_action_sha256(action: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_action_json(action).encode("utf-8")).hexdigest()


def _parameter(label: str, value: object) -> ApprovalParameter:
    return ApprovalParameter(label=label, value=str(value))


def _finite_display(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return f"{number:.3f}"


def _route_parameters(action: Mapping[str, Any]) -> tuple[ApprovalParameter, ...]:
    outgoing = action.get("outgoing_action")
    if not isinstance(outgoing, Mapping):
        raise ValueError("navigation action has no outgoing_action")
    goal = outgoing.get("goal")
    if not isinstance(goal, Mapping):
        raise ValueError("navigation action has no typed goal")
    name = str(goal.get("name") or "").strip()
    if not name:
        raise ValueError("navigation action has no target name")
    parameters = [
        _parameter("能力", str(outgoing.get("capability_id") or "navigate")),
        _parameter("目標", name),
    ]
    frame = str(goal.get("frame_id") or "").strip()
    if not frame:
        raise ValueError("navigation action has no frame")
    parameters.append(_parameter("Frame", frame))
    pose = goal.get("pose")
    if not isinstance(pose, Mapping):
        raise ValueError("navigation action has no exact pose")
    for key, label in (("x", "X"), ("y", "Y"), ("yaw", "Yaw")):
        display = _finite_display(pose.get(key))
        if display is None:
            raise ValueError(f"navigation action has invalid pose {key}")
        parameters.append(_parameter(label, display))
    return tuple(parameters)


def _ros_parameters(
    action: Mapping[str, Any],
    *,
    secret_values: Iterable[str],
) -> tuple[ApprovalParameter, ...]:
    topic = str(action.get("topic") or "").strip()
    message_type = str(action.get("message_type") or "").strip()
    payload = action.get("payload")
    if not topic or not message_type or not isinstance(payload, Mapping):
        raise ValueError("ROS action is missing topic, message type, or payload")
    payload_json = canonical_action_json(payload)
    safe_payload = redact_sensitive_text(payload_json, secret_values=secret_values)
    parameters = [
        _parameter("Topic", topic),
        _parameter("Message type", message_type),
        _parameter("Payload", safe_payload),
    ]
    if str(action.get("type") or "") == "drive":
        duration = _finite_display(action.get("duration", 1.0))
        if duration is None:
            raise ValueError("drive action has invalid duration")
        parameters.append(_parameter("Duration", f"{duration} s"))
    return tuple(parameters)


def build_approval_preview(
    action: Mapping[str, Any],
    *,
    secret_values: Iterable[str] = (),
) -> ApprovalPreview:
    """Build a complete safe preview or reject an action that cannot be shown exactly."""
    action_kind = str(action.get("type") or "").strip()
    if action_kind == "route":
        parameters = _route_parameters(action)
        title = f"導航至 {parameters[1].value}"
    elif action_kind in {"drive", "pub"}:
        parameters = _ros_parameters(action, secret_values=secret_values)
        title = "驅動機器人" if action_kind == "drive" else "發布 ROS 訊息"
    else:
        raise ValueError(f"unsupported approval action: {action_kind or 'unknown'}")
    return ApprovalPreview(
        action_kind=action_kind,
        display_title=title,
        parameters=parameters,
        canonical_action_sha256=canonical_action_sha256(action),
    )
