from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ApprovalCardFields:
    title: str
    summary: str
    raw_action: str
    justification: str


def format_ros_pub_approval(arguments: dict) -> ApprovalCardFields:
    topic = arguments.get("topic", "?")
    message_type = arguments.get("message_type", "?")
    payload = arguments.get("payload", {})
    return ApprovalCardFields(
        title=f"Publish to {topic}",
        summary=f"Send a {message_type} message to ROS2 topic {topic}.",
        raw_action=f"ros2 topic pub --once {topic} {message_type} \"{payload}\"",
        justification="The agent needs to publish this message to complete the requested task.",
    )


def format_route_approval(arguments: dict) -> ApprovalCardFields:
    action = arguments.get("outgoing_action", arguments)
    return ApprovalCardFields(
        title="Send navigation route",
        summary="Send a navigation goal to the route adapter.",
        raw_action=str(action),
        justification=(
            "The agent needs to send this navigation goal to complete the requested route."
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
    "route_execute_tool": format_route_approval,
}


def format_approval(tool_name: str, arguments: dict) -> ApprovalCardFields:
    formatter = APPROVAL_FORMATTERS.get(tool_name)
    if formatter is None:
        return format_generic_approval(tool_name, arguments)
    return formatter(arguments)
