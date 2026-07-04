"""/run visual blocks: PlanBlock, ToolBlock, ErrorBlock."""

from __future__ import annotations

from textual.widgets import Static

from jenai.schemas import JenAIError, PlanStep, ToolCallRecord

# Claude Code-style markers (kept local to avoid importing the app module).
BULLET = "⏺"
ELBOW = "⎿"
ACCENT = "#d97757"
GREEN = "#7d9b6a"
ERROR = "#cb6250"
MUTED = "#9c9689"
TEXT = "#f2ede1"

_STEP_ICONS = {
    "pending": "○",
    "active": "◐",
    "done": "●",
    "skipped": "–",
    "failed": "✗",
}

# Human-readable names so the transcript never shows raw tool identifiers.
_FRIENDLY_TOOL = {
    "ros_topics_tool": "List topics",
    "ros_topic_info_tool": "Topic info",
    "ros_schema_tool": "Read message format",
    "ros_echo_tool": "Peek messages",
    "ros_pub_validate_tool": "Check message",
    "ros_pub_execute_tool": "Publish",
    "ros_drive_execute_tool": "Drive",
    "route_preview_tool": "Plan route",
    "route_execute_tool": "Send route",
    "loc_lookup_tool": "Find place",
    "vision_image_tool": "Look at image",
    "shell_run_tool": "Run command",
}

_FRIENDLY_ERROR = {
    "tool_error": "Something went wrong",
    "model_error": "The AI hit a limit",
    "env_error": "Environment problem",
    "config_error": "Config problem",
    "validation_error": "Invalid input",
    "approval_rejected": "You declined this",
}


def _friendly_tool(name: str) -> str:
    return _FRIENDLY_TOOL.get(name, name.removesuffix("_tool").replace("_", " ").capitalize())


class PlanBlock(Static):
    """A plan rendered as a bullet with elbow-indented step lines."""

    def __init__(self, title: str, steps: list[PlanStep]) -> None:
        lines = [f"[{ACCENT}]{BULLET}[/] [bold {TEXT}]{title}[/]"]
        for step in steps:
            icon = _STEP_ICONS.get(step.status, "○")
            approval = f" [bold {ACCENT}](needs approval)[/]" if step.requires_approval else ""
            lines.append(f"  [{MUTED}]{ELBOW}[/] [{TEXT}]{icon} {step.title}[/]{approval}")
            if step.description:
                lines.append(f"     [{MUTED}]{step.description}[/]")
        super().__init__("\n".join(lines), classes="bullet-line")
        self.title_text = title
        self.steps = steps


class ToolBlock(Static):
    """A tool call rendered as `⏺ tool(args)` with an elbow result line."""

    def __init__(self, tool_call: ToolCallRecord) -> None:
        call = tool_call
        marker_color = GREEN if call.status == "succeeded" else ACCENT
        header = f"[{marker_color}]{BULLET}[/] [bold {TEXT}]{_friendly_tool(call.tool_name)}[/]"
        if call.input_summary:
            header += f" [{MUTED}]· {call.input_summary}[/]"
        lines = [header]
        result = call.output_summary or f"status: {call.status}"
        lines.append(f"  [{MUTED}]{ELBOW}[/] [{MUTED}]{result}[/]")
        super().__init__("\n".join(lines), classes="bullet-line")
        self.tool_call = tool_call


class ErrorBlock(Static):
    """An error rendered as a red bullet with elbow-indented detail."""

    def __init__(self, error: JenAIError) -> None:
        label = _FRIENDLY_ERROR.get(str(error.error_type), str(error.error_type))
        lines = [f"[{ERROR}]{BULLET}[/] [bold {ERROR}]{label}[/]"]
        lines.append(f"  [{MUTED}]{ELBOW}[/] [{TEXT}]{error.message}[/]")
        if error.fix_suggestion:
            lines.append(f"     [{MUTED}]fix: {error.fix_suggestion}[/]")
        super().__init__("\n".join(lines), classes="bullet-line")
        self.error = error
