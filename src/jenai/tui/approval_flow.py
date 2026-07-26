"""Approval-card lifecycle shared by direct commands and agent runs.

This module owns only approval decisions and task scheduling. Tool execution
lives in ``DirectExecutionMixin``, so policy cannot bypass its audit or
error-finalisation paths.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from textual.css.query import NoMatches
from textual.widgets import Input

from jenai.agent import orchestrator
from jenai.agent.context import JenAIRunContext
from jenai.schemas import ApprovalRequest, ApprovalStatus, RunStatus, ToolCallStatus
from jenai.state.runs import TERMINAL_STATUSES
from jenai.tui.approval_policy import can_remember_approval
from jenai.tui.host_contract import TuiHostContract
from jenai.tui.panels import TimelineItem
from jenai.tui.widgets import ApprovalCard


class ApprovalFlowMixin(TuiHostContract):
    """Resolve approval cards without owning the actions they authorize."""

    async def on_approval_card_decision(self, message: ApprovalCard.Decision) -> None:
        # Two approval sources share one card + message: deterministic slash
        # commands tracked in _pending_direct_approvals and agent-driven /run
        # interruptions tracked in _pending_approvals.
        if message.tool_call_id in self._pending_direct_approvals:
            # auto_key (not kind): /dock reuses route execution, but approval
            # memory must never leak between distinct commands.
            pending = self._pending_direct_approvals[message.tool_call_id]
            approval = pending.get("approval")
            if (
                message.approved
                and message.remember
                and approval is not None
                and can_remember_approval(approval)
            ):
                kind = str(pending.get("auto_key", pending["kind"]))
                self._auto_approved.add(kind)
                await self._mount_event(
                    TimelineItem("muted", f"Auto-approving '{kind}' for the rest of this session.")
                )
            await self._resolve_direct_approval(message.tool_call_id, message.approved)
            return

        run_id = self._find_run_id_for_call(message.tool_call_id)
        if run_id is not None:
            # Agent-flow memory is by tool_name; later interruptions for the
            # same tool are auto-approved by _render_run_update.
            if message.approved and message.remember:
                approval = self._approval_by_call_id(message.tool_call_id)
                if approval is not None and approval.tool_name and can_remember_approval(approval):
                    self._auto_approved.add(approval.tool_name)
                    await self._mount_event(
                        TimelineItem(
                            "muted",
                            f"Auto-approving '{approval.tool_name}' for the rest of this session.",
                        )
                    )
            await self._resolve_agent_approval(run_id, message.tool_call_id, message.approved)

    def _approval_by_call_id(self, tool_call_id: str) -> ApprovalRequest | None:
        for candidate in cast(Any, self).query(ApprovalCard):
            card = cast(ApprovalCard, candidate)
            if card.approval.tool_call_id == tool_call_id:
                return card.approval
        return None

    def _find_run_id_for_call(self, tool_call_id: str) -> str | None:
        for run_id, pending in self._pending_approvals.items():
            if tool_call_id in pending["expected"]:
                return run_id
        return None

    async def _remove_approval_card(self, tool_call_id: str) -> None:
        app = cast(Any, self)
        for card in app.query(ApprovalCard):
            if card.approval.tool_call_id == tool_call_id:
                await card.remove()
                break
        remaining = list(app.query(ApprovalCard))
        if remaining:
            remaining[0].focus()
        else:
            app.query_one("#composer", Input).focus()

    async def _reject_pending_approvals_for_emergency_stop(self) -> int:
        """Reject every paused action so a pre-stop card can never resume it."""
        rejected = 0
        for run_id, pending in list(self._pending_approvals.items()):
            self._pending_approvals.pop(run_id, None)
            ctx: JenAIRunContext = pending["ctx"]
            for tool_call_id in pending["expected"]:
                await self._remove_approval_card(tool_call_id)
                self.run_store.resolve_interruption(ctx.run, tool_call_id, ApprovalStatus.REJECTED)
                rejected += 1
            self.run_store.discard_pending_state(run_id)
            if ctx.run.status not in TERMINAL_STATUSES:
                self.run_store.finish(
                    ctx.run,
                    status=RunStatus.BLOCKED,
                    final_output="Superseded by an emergency stop. No pending action was executed.",
                )

        for tool_call_id, pending in list(self._pending_direct_approvals.items()):
            self._pending_direct_approvals.pop(tool_call_id, None)
            ctx = pending["ctx"]
            await self._remove_approval_card(tool_call_id)
            self.run_store.resolve_interruption(ctx.run, tool_call_id, ApprovalStatus.REJECTED)
            self._finish_direct_tool(
                pending,
                ok=False,
                summary="superseded by emergency stop",
                status=ToolCallStatus.REJECTED,
            )
            if ctx.run.status not in TERMINAL_STATUSES:
                self.run_store.finish(
                    ctx.run,
                    status=RunStatus.BLOCKED,
                    final_output="Superseded by an emergency stop. No pending action was executed.",
                )
            rejected += 1
        return rejected

    async def _resolve_direct_approval(self, tool_call_id: str, approved: bool) -> None:
        pending = self._pending_direct_approvals.pop(tool_call_id)
        ctx: JenAIRunContext = pending["ctx"]
        await self._remove_approval_card(tool_call_id)

        status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        self.run_store.resolve_interruption(ctx.run, tool_call_id, status)

        if not approved:
            self._finish_direct_tool(
                pending,
                ok=False,
                summary="rejected by operator",
                status=ToolCallStatus.REJECTED,
            )
            self.run_store.finish(ctx.run, status=RunStatus.BLOCKED)
            await self._mount_event(TimelineItem("warn", "Rejected. No action was taken."))
            self._scroll_to_bottom()
            self._start_next_queued()
            return

        # Long approved actions become the active task so Esc can interrupt.
        if self._active_task is not None and not self._active_task.done():
            await self._execute_direct(pending)
            return
        self._active_task = asyncio.create_task(self._run_direct_task(pending))

    async def _run_direct_task(self, pending: dict[str, Any]) -> None:
        self._start_spinner("Executing")
        ctx: JenAIRunContext = pending["ctx"]
        try:
            await self._execute_direct(pending)
        except asyncio.CancelledError:
            self._finish_direct_tool(pending, ok=False, summary="interrupted")
            if ctx.run.status not in TERMINAL_STATUSES:
                self.run_store.finish(
                    ctx.run, status=RunStatus.INTERRUPTED, final_output="interrupted"
                )
            if self.is_running:
                try:
                    await self._mount_event(
                        TimelineItem("warn", "Interrupted — the action was cancelled.")
                    )
                    self._scroll_to_bottom()
                except NoMatches:
                    pass
        finally:
            if self._active_task is asyncio.current_task():
                self._stop_spinner()
                self._active_task = None
                self._active_task_is_stop = False
                self._start_next_queued()

    async def _resolve_agent_approval(self, run_id: str, tool_call_id: str, approved: bool) -> None:
        pending = self._pending_approvals[run_id]
        pending["decisions"][tool_call_id] = approved
        await self._remove_approval_card(tool_call_id)
        if set(pending["decisions"]) < pending["expected"]:
            return
        await self._finalize_agent_approvals(run_id)

    async def _finalize_agent_approvals(self, run_id: str) -> None:
        """Resume a paused agent run once every interruption has a decision."""
        pending = self._pending_approvals.pop(run_id)
        self._scroll_to_bottom()
        # Remembered approvals may be resolved while the original submission
        # task is still active. In that case, keep using that task. A decision
        # made from an approval card runs in Textual's message handler after
        # the original task has ended, so it must be promoted to the active
        # slot; otherwise Esc has no task to cancel.
        if self._active_task is not None and not self._active_task.done():
            await self._resume_agent_run(pending)
            return
        self._active_task = asyncio.create_task(self._run_resumed_agent_task(pending))
        self._active_task_is_stop = False
        cast(Any, self)._update_statusbar()
        # Preserve the event-handler contract for fast resumptions: one event
        # loop turn is enough for a non-blocking resume to update the UI.
        await asyncio.sleep(0)

    async def _resume_agent_run(self, pending: dict[str, Any]) -> None:
        run = await self._run_with_agent_progress(
            pending["ctx"],
            orchestrator.resume_with_approvals(
                pending["agent"], pending["ctx"], pending["decisions"]
            ),
        )
        await self._render_run_update(pending["ctx"], run, agent=pending["agent"])

    async def _run_resumed_agent_task(self, pending: dict[str, Any]) -> None:
        """Own a resumed run so Esc and shutdown cancel its full tool chain."""
        try:
            await self._resume_agent_run(pending)
        except asyncio.CancelledError:
            await self._finalize_interrupted_run()
            if self.is_running:
                try:
                    await self._mount_event(TimelineItem("warn", "Interrupted."))
                    self._scroll_to_bottom()
                except NoMatches:
                    pass
        finally:
            if self._active_task is asyncio.current_task():
                self._stop_spinner()
                self._active_task = None
                self._active_task_is_stop = False
                self._start_next_queued()
