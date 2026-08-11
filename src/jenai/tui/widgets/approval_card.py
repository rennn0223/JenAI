"""Approval card widget: risk-aware numbered options and Esc rejection."""

from __future__ import annotations

from rich.text import Text
from textual.events import Key
from textual.message import Message
from textual.widgets import Static

from jenai.schemas import ApprovalRequest
from jenai.tui.approval_policy import can_remember_approval, should_default_to_reject

ACCENT = "#e8683f"
GREEN = "#8fbf6f"
MUTED = "#8f897f"
TEXT = "#f2ede4"
WARN = "⚠"

# (label, approved, remember)
_REMEMBER_OPTIONS = [
    ("本次允許", True, False),
    ("允許，這個 session 不再詢問這項工具", True, True),
    ("不允許", False, False),
]

_ONCE_OPTIONS = [
    ("本次允許", True, False),
    ("不允許", False, False),
]

# Plain-language description of what a tool actually does, keyed by effect scope,
# so the card never shows raw jargon like "Scope: sim_control".
_EFFECT_WORDS = {
    "read": "只讀取資料，不會執行動作。",
    "local_write": "會寫入這臺電腦上的檔案。",
    "sim_control": "可能移動已連線的機器人或模擬器。",
    "robot_control": "可能移動已連線的實體機器人。",
    "host_command": "會在這臺電腦上執行指令。",
    "none": "不會產生副作用。",
}


def _effect_line(effect_scope: str, risk_level: str) -> str:
    words = _EFFECT_WORDS.get(str(effect_scope), f"影響範圍：{effect_scope}")
    if str(risk_level) == "p2":
        words += " 允許前請再次確認。"
    return words


class ApprovalCard(Static):
    """Claude Code-style approval prompt with numbered options.

    Ordinary bounded capabilities can be remembered for the session. P2 and
    host-command approvals are one-shot; P2, host commands, and robot-control
    prompts default to No. Navigable
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
        if approval.tool_name == "navigation_golden_path":
            self._options = [
                ("Yes：本次允許", True, False),
                ("Auto：本 session 自動允許相同 exact Plan", True, True),
                ("No：不允許", False, False),
            ]
        else:
            self._options = _REMEMBER_OPTIONS if can_remember_approval(approval) else _ONCE_OPTIONS
        self._selected = len(self._options) - 1 if should_default_to_reject(approval) else 0

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
        body.append("要繼續嗎？\n", style=f"bold {TEXT}")
        for index, (label, _approved, _remember) in enumerate(self._options):
            selected = index == self._selected
            pointer = "❯ " if selected else "  "
            style = f"bold {GREEN}" if selected else TEXT
            body.append(f"{pointer}{index + 1}. {label}\n", style=style)
        number_keys = "/".join(str(index) for index in range(1, len(self._options) + 1))
        body.append(
            f"\nEsc 取消 · ↑/↓ 移動 · {number_keys} 或 Enter 確認",
            style=MUTED,
        )
        return body

    def _emit(self, index: int) -> None:
        _label, approved, remember = self._options[index]
        self.post_message(self.Decision(self.approval.tool_call_id, approved, remember))

    def on_key(self, event: Key) -> None:
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
