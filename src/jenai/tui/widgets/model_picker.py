"""Model picker widget: arrow-navigable list of endpoint models, Enter selects.

Mirrors ApprovalCard's pattern — a focusable Static that owns its keys and posts
one Message — so /model gets a Claude-Code-style interactive picker instead of
forcing the operator to read a numbered list and retype `/model <n>`.
"""

from __future__ import annotations

from rich.text import Text
from textual.message import Message
from textual.widgets import Static

ACCENT = "#d97757"
GREEN = "#7d9b6a"
MUTED = "#9c9689"
TEXT = "#f2ede1"


class ModelPicker(Static):
    """Arrow-navigable model list.

    ❯ qwen3.6:35b   ← current
      llama3.2:3b
    ↑/↓ move · Enter select · Esc cancel · number jumps.
    """

    can_focus = True
    WINDOW = 12  # rows shown at once; the window scrolls to follow the selection

    class Selected(Message):
        """model_id is None when the operator cancelled (Esc)."""

        def __init__(self, model_id: str | None) -> None:
            self.model_id = model_id
            super().__init__()

    def __init__(self, models: list[str], current: str | None = None) -> None:
        super().__init__(classes="approval-card")
        self._models = models
        self._current = current
        # Start the cursor on the active model so Enter-without-moving is a no-op
        # rather than a surprise switch.
        self._selected = models.index(current) if current in models else 0

    def on_mount(self) -> None:
        self.focus()

    def render(self) -> Text:
        total = len(self._models)
        if total <= self.WINDOW:
            start = 0
        else:
            start = min(max(self._selected - self.WINDOW // 2, 0), total - self.WINDOW)
        end = min(start + self.WINDOW, total)

        body = Text()
        body.append("Select model ", style=f"bold {ACCENT}")
        body.append(f"({self._selected + 1}/{total})\n", style=MUTED)
        if start > 0:
            body.append(f"  ↑ {start} more\n", style=MUTED)
        for index in range(start, end):
            model_id = self._models[index]
            selected = index == self._selected
            pointer = "❯ " if selected else "  "
            style = f"bold {GREEN}" if selected else TEXT
            body.append(pointer, style=GREEN if selected else MUTED)
            body.append(f"{index + 1:>2}. {model_id}", style=style)
            if model_id == self._current:
                body.append("  · current", style=MUTED)
            body.append("\n")
        if end < total:
            body.append(f"  ↓ {total - end} more\n", style=MUTED)
        body.append("\n↑/↓ move · Enter select · Esc cancel", style=MUTED)
        return body

    def on_key(self, event) -> None:
        if event.key == "down":
            self._selected = (self._selected + 1) % len(self._models)
            self.refresh()
        elif event.key == "up":
            self._selected = (self._selected - 1) % len(self._models)
            self.refresh()
        elif event.key.isdigit() and event.key != "0":
            index = int(event.key) - 1
            if index < len(self._models):
                self._selected = index
                self.refresh()
        elif event.key == "enter":
            self.post_message(self.Selected(self._models[self._selected]))
        elif event.key == "escape":
            self.post_message(self.Selected(None))
        else:
            return
        event.stop()
