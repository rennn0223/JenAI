"""Grouped command content rendered by /help."""

from __future__ import annotations

from jenai import __version__
from jenai.schemas import CommandGroup, HelpOutput, KeyboardShortcut

_COMMAND_GROUPS = [
    CommandGroup(
        name="Safety",
        commands=["/stop  (EMERGENCY STOP — works even while a task is running)"],
    ),
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
        commands=[
            "/ros topics",
            "/ros topic-info <topic>",
            "/ros schema <topic>",
            "/ros echo <topic> [count]",
            "/ros pub <topic> <payload>",
        ],
    ),
    CommandGroup(
        name="Route",
        commands=[
            "/route <text>",
            "/loc list",
            "/loc show <name>",
            "/loc add here <name>",
            "/loc add gps <name> <lat> <lon>",
        ],
    ),
    CommandGroup(
        name="Skills",
        commands=[
            "/mission <place>, <place>, …",
            "/patrol <place>, <place> [xN] [photo]",
            "/dock",
            "/report [list]",
            "/skills(列出檔案定義技能;skills/*.toml 自訂 slash 指令)",
        ],
    ),
    CommandGroup(
        name="Vision",
        commands=[
            "/vision image <path>",
            "/vision camera [topic]",
            "/perception start [topic] [hz]",
            "/perception stop",
        ],
    ),
    CommandGroup(
        name="System",
        commands=["/shell <cmd>"],
    ),
    CommandGroup(
        name="Provider / Model",
        commands=[
            "/provider [name|number]",
            "/providers",
            "/model [name|number]",
            "/models",
            "/permissions",
        ],
    ),
]

_EXAMPLES = [
    "/plan patrol area A and record anomalies",
    "/ros schema /cmd_vel",
    "/route from Engineering Building to Mechanical Hall",
    "/model llama3.2  (or /model 2 after listing with /model)",
]

_KEYBOARD_SHORTCUTS = [
    KeyboardShortcut(key="Enter", action="Submit input / choose the selected approval option"),
    KeyboardShortcut(key="!", action="Run the rest of the line as a shell command"),
    KeyboardShortcut(key="Esc", action="Interrupt a running task / reject an approval"),
    KeyboardShortcut(key="1 / 2 / 3", action="Pick an approval option (Yes / Yes+remember / No)"),
    KeyboardShortcut(key="Tab", action="Complete the selected command"),
    KeyboardShortcut(key="↑ / ↓", action="History, command palette, or approval options"),
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
