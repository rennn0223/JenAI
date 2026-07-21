"""Approval-card lifecycle shared by direct commands and agent runs.

This module owns only approval decisions and task scheduling. Tool execution
lives in ``DirectExecutionMixin``, so policy cannot bypass its audit or
error-finalisation paths.
"""

from __future__ import annotations

import asyncio

from textual.css.query import NoMatches
from textual.widgets import Input

from jenai.agent import orchestrator
from jenai.agent.context import JenAIRunContext
from jenai.schemas import ApprovalRequest, ApprovalStatus, RunStatus, ToolCallStatus
from jenai.tui.approval_policy import can_remember_approval
from jenai.tui.panels import TimelineItem
from jenai.tui.widgets import ApprovalCard


class ApprovalFlowMixin:
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
                kind = pending.get("auto_key", pending["kind"])
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
        for card in self.query(ApprovalCard):
            if card.approval.tool_call_id == tool_call_id:
                return card.approval
        return None

    def _find_run_id_for_call(self, tool_call_id: str) -> str | None:
        for run_id, pending in self._pending_approvals.items():
            if tool_call_id in pending["expected"]:
                return run_id
        return None

    async def _remove_approval_card(self, tool_call_id: str) -> None:
        for card in self.query(ApprovalCard):
            if card.approval.tool_call_id == tool_call_id:
                await card.remove()
                break
        remaining = list(self.query(ApprovalCard))
        if remaining:
            remaining[0].focus()
        else:
            self.query_one("#composer", Input).focus()

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

    async def _run_direct_task(self, pending: dict) -> None:
        self._start_spinner("Executing")
        ctx: JenAIRunContext = pending["ctx"]
        try:
            await self._execute_direct(pending)
        except asyncio.CancelledError:
            self._finish_direct_tool(pending, ok=False, summary="interrupted")
            if ctx.run.status not in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.BLOCKED):
                self.run_store.finish(ctx.run, status=RunStatus.BLOCKED, final_output="interrupted")
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
        run = await self._run_with_agent_progress(
            pending["ctx"],
            orchestrator.resume_with_approvals(
                pending["agent"], pending["ctx"], pending["decisions"]
            ),
        )
        await self._render_run_update(pending["ctx"], run, agent=pending["agent"])
        self._start_next_queued()
