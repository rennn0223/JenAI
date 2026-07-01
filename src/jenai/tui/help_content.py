from __future__ import annotations

from jenai import __version__
from jenai.schemas import CommandGroup, HelpOutput, KeyboardShortcut

_COMMAND_GROUPS = [
    CommandGroup(
        name="Session",
        commands=["/help", "/status", "/doctor", "/clear"],
    ),
    CommandGroup(
        name="Planning",
        commands=["/plan <task>", "/run <task>", "/why", "/review", "/abort"],
    ),
    CommandGroup(
        name="ROS2",
        commands=["/ros topics", "/ros schema <topic>", "/ros pub <topic> <payload>"],
    ),
    CommandGroup(
        name="Route",
        commands=["/route <text>", "/loc list", "/loc show <name>"],
    ),
    CommandGroup(
        name="Provider / Model",
        commands=["/provider", "/providers", "/model", "/models", "/permissions"],
    ),
]

_EXAMPLES = [
    "/plan patrol area A and record anomalies",
    "/ros schema /cmd_vel",
    "/route from Engineering Building to Mechanical Hall",
]

_KEYBOARD_SHORTCUTS = [
    KeyboardShortcut(key="Enter", action="Submit input / approve an approval card"),
    KeyboardShortcut(key="Esc", action="Close palette / reject an approval card"),
    KeyboardShortcut(key="Tab", action="Complete the selected command"),
    KeyboardShortcut(key="↑ / ↓", action="Navigate input history or the command palette"),
]


def build_help_output(section: str | None = None) -> HelpOutput:
    groups = _COMMAND_GROUPS
    title = f"JenAI v{__version__} — ROS2 AI Agent Terminal"
    if section:
        lowered = section.strip().lower()
        groups = [g for g in _COMMAND_GROUPS if lowered in g.name.lower()]
        if groups:
            title = f"JenAI help: {groups[0].name}"

    return HelpOutput(
        title=title,
        summary=(
            "Plan and execute robot tasks (/plan, /run), explore ROS2 topics (/ros), "
            "route to named locations (/route, /loc), and check provider/model status."
        ),
        command_groups=groups,
        examples=_EXAMPLES if not section else [],
        keyboard_shortcuts=_KEYBOARD_SHORTCUTS if not section else [],
    )
