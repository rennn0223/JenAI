from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import Static

from jenai.schemas import JenAIError, PlanStep, ToolCallRecord

_STEP_ICONS = {
    "pending": "○",
    "active": "◐",
    "done": "●",
    "skipped": "–",
    "failed": "✗",
}


class PlanBlock(Vertical):
    def __init__(self, title: str, steps: list[PlanStep]) -> None:
        super().__init__(classes="output-panel")
        self.title_text = title
        self.steps = steps

    def compose(self):
        yield Static(self.title_text, classes="panel-title")
        for step in self.steps:
            icon = _STEP_ICONS.get(step.status, "○")
            approval_tag = " [bold #dd9460](needs approval)[/]" if step.requires_approval else ""
            yield Static(
                f"{icon} [bold #e8ecef]{step.title}[/]{approval_tag}\n"
                f"  [#7c8893]{step.description}[/]",
                classes="panel-copy",
            )


class ToolBlock(Vertical):
    def __init__(self, tool_call: ToolCallRecord) -> None:
        super().__init__(classes="output-panel")
        self.tool_call = tool_call

    def compose(self):
        call = self.tool_call
        yield Static(f"⚙ {call.tool_name}", classes="panel-title")
        yield Static(f"[#7c8893]{call.input_summary}[/]", classes="panel-copy")
        if call.output_summary:
            yield Static(f"→ {call.output_summary}", classes="panel-copy")
        yield Static(f"[#7c8893]status: {call.status}[/]", classes="panel-copy")


class ErrorBlock(Vertical):
    def __init__(self, error: JenAIError) -> None:
        super().__init__(classes="output-panel")
        self.error = error

    def compose(self):
        yield Static(f"✗ {self.error.error_type}", classes="panel-title")
        yield Static(self.error.message, classes="panel-copy")
        if self.error.fix_suggestion:
            yield Static(f"[#7c8893]fix:[/] {self.error.fix_suggestion}", classes="panel-copy")
