from __future__ import annotations

from rich.text import Text
from textual.message import Message
from textual.widgets import Static

from jenai.schemas import ApprovalRequest

ACCENT = "#d97757"
GREEN = "#7d9b6a"
MUTED = "#9c9689"
TEXT = "#f2ede1"
WARN = "⚠"

# (label, approved, remember)
_OPTIONS = [
    ("Yes", True, False),
    ("Yes, and don't ask again this session", True, True),
    ("No, and tell JenAI what to do differently (Esc)", False, False),
]

# Plain-language description of what a tool actually does, keyed by effect scope,
# so the card never shows raw jargon like "Scope: sim_control".
_EFFECT_WORDS = {
    "read": "Only reads data — safe.",
    "local_write": "Writes files on this computer.",
    "sim_control": "Moves the robot (simulation).",
    "host_command": "Runs a command on this computer.",
    "none": "No side effects.",
}


def _effect_line(effect_scope: str, risk_level: str) -> str:
    words = _EFFECT_WORDS.get(str(effect_scope), f"Effect: {effect_scope}")
    if str(risk_level) == "p2":
        words += " Double-check before approving."
    return words


class ApprovalCard(Static):
    """Claude Code-style approval prompt with numbered options.

    ❯ 1. Yes   2. Yes, and don't ask again   3. No (Esc)
    Navigable with ↑/↓ + Enter, or the number keys 1/2/3; Esc rejects.
    """

    can_focus = True

    class Decision(Message):
        def __init__(self, tool_call_id: str, approved: bool, remember: bool = False) -> None:
            self.tool_call_id = tool_call_id
            self.approved = approved
            self.remember = remember
            super().__init__()

    def __init__(self, approval: ApprovalRequest) -> None:
        super().__init__(classes="approval-card")
        self.approval = approval
        self._selected = 0

    def on_mount(self) -> None:
        self.focus()

    def render(self) -> Text:
        approval = self.approval
        body = Text()
        body.append(f"{WARN} ", style=ACCENT)
        body.append(f"{approval.title}\n", style=f"bold {TEXT}")
        body.append(f"  {approval.summary}\n", style=TEXT)
        body.append(f"  {approval.raw_action}\n", style=MUTED)
        body.append(
            f"  {_effect_line(approval.effect_scope, approval.risk_level)}\n\n",
            style=MUTED,
        )
        for index, (label, _approved, _remember) in enumerate(_OPTIONS):
            selected = index == self._selected
            pointer = "❯ " if selected else "  "
            style = f"bold {GREEN}" if selected else MUTED
            body.append(f"{pointer}{index + 1}. {label}\n", style=style)
        return body

    def _emit(self, index: int) -> None:
        _label, approved, remember = _OPTIONS[index]
        self.post_message(self.Decision(self.approval.tool_call_id, approved, remember))

    def on_key(self, event) -> None:
        if event.key == "down":
            self._selected = (self._selected + 1) % len(_OPTIONS)
            self.refresh()
        elif event.key == "up":
            self._selected = (self._selected - 1) % len(_OPTIONS)
            self.refresh()
        elif event.key in ("1", "2", "3"):
            self._emit(int(event.key) - 1)
        elif event.key == "enter":
            self._emit(self._selected)
        elif event.key == "escape":
            self._emit(len(_OPTIONS) - 1)  # last option is "No"
        else:
            return
        event.stop()
