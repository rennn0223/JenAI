"""Grouped command content rendered by /help."""

from __future__ import annotations

from jenai import __version__
from jenai.schemas import CommandGroup, HelpOutput, KeyboardShortcut
from jenai.tui.catalog import COMMAND_GROUPS

_COMMAND_GROUPS = [
    CommandGroup(name=name, commands=[command.completion for command in commands])
    for name, commands in COMMAND_GROUPS
]

_EXAMPLES = [
    "/plan patrol area A and record anomalies",
    "/ros schema /cmd_vel",
    "/route from Engineering Building to Mechanical Hall",
    "/explore 5m goals=8 tag=room",
    "/model llama3.2  (or /model 2 after listing with /model)",
]

_KEYBOARD_SHORTCUTS = [
    KeyboardShortcut(
        key="Enter", action="Submit input (queues while busy) / choose an approval option"
    ),
    KeyboardShortcut(key="!", action="Run the rest of the line as a shell command"),
    KeyboardShortcut(
        key="Esc", action="Interrupt the current task and continue the queue / reject approval"
    ),
    KeyboardShortcut(
        key="1 / 2 / 3",
        action="Pick a shown approval option; host/P2 prompts are one-shot",
    ),
    KeyboardShortcut(key="Tab", action="Complete the selected command"),
    KeyboardShortcut(key="↑ / ↓", action="History, command palette, or approval options"),
    KeyboardShortcut(
        key="Shift+Tab", action="Cycle permission mode (approve/plan/auto); /mode if unsupported"
    ),
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
