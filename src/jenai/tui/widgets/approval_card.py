"""Approval card widget: risk-aware numbered options and Esc rejection."""

from __future__ import annotations

from rich.text import Text
from textual.message import Message
from textual.widgets import Static

from jenai.schemas import ApprovalRequest
from jenai.tui.approval_policy import can_remember_approval

ACCENT = "#e8683f"
GREEN = "#8fbf6f"
MUTED = "#8f897f"
TEXT = "#f2ede4"
WARN = "⚠"

# (label, approved, remember)
_REMEMBER_OPTIONS = [
    ("Yes", True, False),
    ("Yes, and remember this tool for this session", True, True),
    ("No", False, False),
]

_ONCE_OPTIONS = [
    ("Yes", True, False),
    ("No", False, False),
]

# Plain-language description of what a tool actually does, keyed by effect scope,
# so the card never shows raw jargon like "Scope: sim_control".
_EFFECT_WORDS = {
    "read": "Only reads data — safe.",
    "local_write": "Writes files on this computer.",
    "sim_control": "May move the connected robot or simulator.",
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

    Ordinary bounded capabilities can be remembered for the session. P2 and
    host-command approvals are one-shot; P2 also defaults to No. Navigable
    with ↑/↓ + Enter or a displayed number key; Esc always rejects.
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
        self._options = _REMEMBER_OPTIONS if can_remember_approval(approval) else _ONCE_OPTIONS
        self._selected = len(self._options) - 1 if str(approval.risk_level) == "p2" else 0

    def on_mount(self) -> None:
        self.focus()

    def render(self) -> Text:
        approval = self.approval
        body = Text()
        body.append(f"{WARN} {approval.title}\n", style=f"bold {ACCENT}")
        body.append(f"{approval.raw_action}\n", style=TEXT)
        body.append(f"{approval.summary}\n", style=MUTED)
        body.append(
            f"{_effect_line(approval.effect_scope, approval.risk_level)}\n\n",
            style=MUTED,
        )
        body.append("Do you want to proceed?\n", style=f"bold {TEXT}")
        for index, (label, _approved, _remember) in enumerate(self._options):
            selected = index == self._selected
            pointer = "❯ " if selected else "  "
            style = f"bold {GREEN}" if selected else TEXT
            body.append(f"{pointer}{index + 1}. {label}\n", style=style)
        number_keys = "/".join(str(index) for index in range(1, len(self._options) + 1))
        body.append(
            f"\nEsc to cancel · ↑/↓ to move · {number_keys} or Enter to confirm",
            style=MUTED,
        )
        return body

    def _emit(self, index: int) -> None:
        _label, approved, remember = self._options[index]
        self.post_message(self.Decision(self.approval.tool_call_id, approved, remember))

    def on_key(self, event) -> None:
        if event.key == "down":
            self._selected = (self._selected + 1) % len(self._options)
            self.refresh()
        elif event.key == "up":
            self._selected = (self._selected - 1) % len(self._options)
            self.refresh()
        elif event.key.isdigit() and 1 <= int(event.key) <= len(self._options):
            self._emit(int(event.key) - 1)
        elif event.key == "enter":
            self._emit(self._selected)
        elif event.key == "escape":
            self._emit(len(self._options) - 1)  # last option is "No"
        else:
            return
        event.stop()
