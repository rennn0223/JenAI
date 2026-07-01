from __future__ import annotations

from textual.widgets import Static

from jenai.schemas import JenAIError, PlanStep, ToolCallRecord

# Claude Code-style markers (kept local to avoid importing the app module).
BULLET = "⏺"
ELBOW = "⎿"
ACCENT = "#dd9460"
GREEN = "#6fbf73"
ERROR = "#e06c75"
MUTED = "#7c8893"
TEXT = "#e8ecef"

_STEP_ICONS = {
    "pending": "○",
    "active": "◐",
    "done": "●",
    "skipped": "–",
    "failed": "✗",
}


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
        header = f"[{marker_color}]{BULLET}[/] [bold {TEXT}]{call.tool_name}[/]"
        if call.input_summary:
            header += f" [{MUTED}]({call.input_summary})[/]"
        lines = [header]
        result = call.output_summary or f"status: {call.status}"
        lines.append(f"  [{MUTED}]{ELBOW}[/] [{MUTED}]{result}[/]")
        super().__init__("\n".join(lines), classes="bullet-line")
        self.tool_call = tool_call


class ErrorBlock(Static):
    """An error rendered as a red bullet with elbow-indented detail."""

    def __init__(self, error: JenAIError) -> None:
        lines = [f"[{ERROR}]{BULLET}[/] [bold {ERROR}]{error.error_type}[/]"]
        lines.append(f"  [{MUTED}]{ELBOW}[/] [{TEXT}]{error.message}[/]")
        if error.fix_suggestion:
            lines.append(f"     [{MUTED}]fix: {error.fix_suggestion}[/]")
        super().__init__("\n".join(lines), classes="bullet-line")
        self.error = error
