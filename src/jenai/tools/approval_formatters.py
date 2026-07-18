"""Approval-card copy for actuating commands."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ApprovalCardFields:
    """Human-facing copy for one approval card (title/summary/raw/why)."""

    title: str
    summary: str
    raw_action: str
    justification: str


def _decode_json_arg(arguments: dict, key: str, *, max_layers: int = 1) -> object:
    """Return the decoded value for a `*_json` tool argument.

    Tool parameters carry structured payloads as JSON strings (e.g.
    ``payload_json``); decode them so the approval card shows the real content
    rather than an opaque string. Falls back to the raw value on bad JSON.
    """
    value = arguments.get(key)
    for _ in range(max_layers):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            break
    return value if value is not None else {}


def format_ros_pub_approval(arguments: dict) -> ApprovalCardFields:
    topic = arguments.get("topic", "?")
    message_type = arguments.get("message_type", "?")
    payload = _decode_json_arg(arguments, "payload_json")
    return ApprovalCardFields(
        title=f"Publish to {topic}",
        summary=f"Send a {message_type} message to ROS2 topic {topic}.",
        raw_action=f"ros2 topic pub --once {topic} {message_type} \"{json.dumps(payload)}\"",
        justification="The agent needs to publish this message to complete the requested task.",
    )


def format_ros_drive_approval(arguments: dict) -> ApprovalCardFields:
    topic = arguments.get("topic", "?")
    message_type = arguments.get("message_type", "?")
    duration = arguments.get("duration_seconds", 1.0)
    payload = _decode_json_arg(arguments, "payload_json")
    return ApprovalCardFields(
        title=f"Drive {topic} for {duration}s",
        summary=f"Publish a {message_type} to {topic} for {duration}s, then auto-stop.",
        raw_action=(
            f"ros2 topic pub --rate 10 {topic} {message_type} \"{json.dumps(payload)}\" "
            f"for {duration}s, then zero-stop"
        ),
        justification="The agent needs time-bounded motion to complete the requested task.",
    )


def format_route_approval(arguments: dict) -> ApprovalCardFields:
    # A few model/SDK combinations quote the already JSON-encoded action once.
    # Match route_execute_tool's bounded normalization so the approval card
    # shows the object that will actually be considered for navigation.
    action = _decode_json_arg(arguments, "outgoing_action_json", max_layers=2)
    return ApprovalCardFields(
        title="Send navigation route",
        summary="Send a navigation goal to the route adapter.",
        raw_action=json.dumps(action, ensure_ascii=False),
        justification=(
            "The agent needs to send this navigation goal to complete the requested route."
        ),
    )


def format_explore_approval(arguments: dict) -> ApprovalCardFields:
    duration = arguments.get("duration_minutes", 5.0)
    goals = arguments.get("max_goals", 8)
    failures = arguments.get("max_failures", 2)
    tag = arguments.get("tag") or "all eligible saved locations"
    seed = arguments.get("seed", -1)
    seed_text = (
        "fresh random order"
        if seed == -1
        else f"reproducible order (seed={seed}; same seed repeats)"
    )
    goal_word = "navigation goal" if goals == 1 else "navigation goals"
    return ApprovalCardFields(
        title=f"Explore · up to {goals} {goal_word}",
        summary=(
            f"Navigate among {tag} for up to {duration} minutes; stop after "
            f"{failures} consecutive failures; use a {seed_text}."
        ),
        raw_action=(
            f"bounded known-location exploration: duration={duration}m, goals={goals}, "
            f"failures={failures}, {seed_text}"
        ),
        justification=(
            "The agent needs one approved, bounded navigation run to explore the area."
        ),
    )


def format_shell_approval(arguments: dict) -> ApprovalCardFields:
    command = arguments.get("command", "?")
    cwd = arguments.get("cwd") or "(current directory)"
    return ApprovalCardFields(
        title="Run shell command",
        summary=f"Execute a host shell command in {cwd}.",
        raw_action=command,
        justification=(
            "The agent needs to run this shell command to complete the requested task."
        ),
    )


def format_generic_approval(tool_name: str, arguments: dict) -> ApprovalCardFields:
    return ApprovalCardFields(
        title=f"Run {tool_name}",
        summary=f"The agent wants to call {tool_name}.",
        raw_action=str(arguments),
        justification="This tool has side effects and requires approval before running.",
    )


APPROVAL_FORMATTERS: dict[str, Callable[[dict], ApprovalCardFields]] = {
    "ros_pub_execute_tool": format_ros_pub_approval,
    "ros_drive_execute_tool": format_ros_drive_approval,
    "ros_drive_verified_tool": format_ros_drive_approval,
    "route_execute_tool": format_route_approval,
    "explore_area_tool": format_explore_approval,
    "shell_run_tool": format_shell_approval,
}


def format_approval(tool_name: str, arguments: dict) -> ApprovalCardFields:
    formatter = APPROVAL_FORMATTERS.get(tool_name)
    if formatter is None:
        return format_generic_approval(tool_name, arguments)
    return formatter(arguments)
