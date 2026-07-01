from __future__ import annotations

from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Collapsible, Static

from jenai.schemas import ApprovalRequest


class ApprovalCard(Vertical):
    """Human-in-the-loop approval prompt, per UX.md: title, summary, collapsed
    raw_action, risk/scope badges, justification, Enter=approve/Esc=reject.
    """

    can_focus = True

    class Decision(Message):
        def __init__(self, tool_call_id: str, approved: bool) -> None:
            self.tool_call_id = tool_call_id
            self.approved = approved
            super().__init__()

    def __init__(self, approval: ApprovalRequest) -> None:
        super().__init__(classes="approval-card")
        self.approval = approval

    def compose(self):
        approval = self.approval
        yield Static(f"⚠ Approval Required · {approval.title}", classes="approval-title")
        yield Static(approval.summary, classes="approval-summary")
        with Collapsible(title="raw action", collapsed=True):
            yield Static(approval.raw_action)
        yield Static(
            f"Risk: {approval.risk_level} · Scope: {approval.effect_scope}",
            classes="approval-meta",
        )
        yield Static(approval.justification, classes="approval-justification")
        yield Static(
            "[bold]Enter[/] Approve    [bold]Esc[/] Reject",
            classes="approval-footer",
        )

    def on_mount(self) -> None:
        self.focus()

    def on_key(self, event) -> None:
        if event.key == "enter":
            self.post_message(self.Decision(self.approval.tool_call_id, True))
            event.stop()
        elif event.key == "escape":
            self.post_message(self.Decision(self.approval.tool_call_id, False))
            event.stop()
