"""Pure slash-command routing rules.

The App owns bound handlers. This module owns command grammar and selects the
handler name, which keeps routing testable without constructing a Textual App.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedCommand:
    handler_name: str
    argument: str


_ROS_HANDLERS = {
    "topics": "_show_ros_topics",
    "topic-info": "_show_ros_topic_info",
    "schema": "_show_ros_schema",
    "echo": "_show_ros_echo",
    "pub": "_show_ros_pub",
    "drive": "_show_ros_drive",
}

_LOCATION_HANDLERS = {
    "list": "_show_loc_list",
    "add": "_show_loc_add",
    "show": "_show_loc_show",
    "rm": "_show_loc_rm",
    "rename": "_show_loc_rename",
    "move": "_show_loc_move",
}

_HANDLERS = {
    "/stop": "_show_stop",
    "/help": "_show_help",
    "/status": "_show_status",
    "/doctor": "_show_doctor",
    "/providers": "_show_providers",
    "/models": "_show_models",
    "/model": "_show_model",
    "/provider": "_show_provider",
    "/permissions": "_show_permissions",
    "/mode": "_show_mode",
    "/config": "_show_config",
    "/plan": "_show_plan",
    "/run": "_show_run",
    "/why": "_show_why",
    "/review": "_show_review",
    "/abort": "_show_abort",
    "/queue": "_show_queue",
    "/route": "_show_route",
    "/drive": "_show_drive",
    "/mission": "_show_mission",
    "/patrol": "_show_patrol",
    "/explore": "_show_explore",
    "/dock": "_show_dock",
    "/report": "_show_report",
    "/skills": "_show_skills",
    "/vision": "_show_vision",
    "/perception": "_show_perception",
    "/shell": "_show_shell",
    "/quit": "_quit_from_command",
    "/exit": "_quit_from_command",
}


def resolve_command(
    command: str,
    argument: str,
    *,
    user_skills: Collection[str] = (),
) -> ResolvedCommand | None:
    """Resolve one command without invoking UI or robot behavior."""
    if command == "/ros":
        return _resolve_subcommand(argument, _ROS_HANDLERS)
    if command == "/loc":
        return _resolve_subcommand(argument, _LOCATION_HANDLERS)

    handler_name = _HANDLERS.get(command)
    if handler_name is not None:
        return ResolvedCommand(handler_name, argument)

    skill_name = command.removeprefix("/").lower()
    if command.startswith("/") and skill_name in user_skills:
        return ResolvedCommand("_run_user_skill", skill_name)
    return None


def _resolve_subcommand(
    argument: str,
    handlers: dict[str, str],
) -> ResolvedCommand | None:
    subcommand, _, rest = argument.partition(" ")
    handler_name = handlers.get(subcommand)
    if handler_name is None:
        return None
    return ResolvedCommand(handler_name, rest.strip())
