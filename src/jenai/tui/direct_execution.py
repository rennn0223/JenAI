"""Execution lifecycle for approved deterministic TUI commands.

Approval policy lives in ``approval_flow``.  This module dispatches only
already-authorised work; each command family has one small handler so lifecycle
and durable outcome rules remain visible during review.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from jenai.agent.context import JenAIRunContext
from jenai.schemas import RunStatus, TaskOutcome, ToolCallStatus
from jenai.state.reports import save_patrol_log
from jenai.tools.mission_core import run_mission
from jenai.tools.ros2_core import ros_drive, ros_pub_execute
from jenai.tools.shell_core import run_shell
from jenai.tools.skills import run_explore, run_patrol
from jenai.tools.vision_core import capture_and_analyze
from jenai.tui.host_contract import TuiHostContract
from jenai.tui.panels import OutputPanel, TimelineItem

PendingCommand = dict[str, Any]
DirectHandler = Callable[[PendingCommand], Awaitable[None]]


class DirectExecutionMixin(TuiHostContract):
    """Execute already-approved slash commands and persist their outcomes."""

    async def _mount_step_line(self, status: str, body: str) -> None:
        """One rendering for every skill/mission step — success green, rest warn."""
        await self._mount_event(TimelineItem("success" if status == "succeeded" else "warn", body))
        self._scroll_to_bottom()

    async def _execute_direct(self, pending: PendingCommand) -> None:
        """Run an approved command and always close failures durably."""
        ctx: JenAIRunContext = pending["ctx"]
        try:
            await self._run_direct(pending)
        except Exception as exc:
            self._finish_direct_tool(pending, ok=False, summary=str(exc))
            if ctx.run.status not in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.BLOCKED):
                self.run_store.finish(ctx.run, status=RunStatus.FAILED, final_output=str(exc))
            await self._mount_event(TimelineItem("error", f"Action failed: {exc}"))
            self._scroll_to_bottom()

    def _finish_direct_tool(
        self,
        pending: PendingCommand,
        *,
        ok: bool,
        summary: str,
        status: ToolCallStatus | None = None,
    ) -> None:
        """Keep direct-command ToolCallRecord and durable audit status in sync."""
        tool_call_id = pending.get("tool_call_id")
        if not tool_call_id:
            return
        ctx: JenAIRunContext = pending["ctx"]
        self.run_store.update_tool_call(
            ctx.run,
            tool_call_id,
            status=status or (ToolCallStatus.SUCCEEDED if ok else ToolCallStatus.FAILED),
            output_summary=summary,
        )

    def _complete_direct_run(
        self,
        pending: PendingCommand,
        *,
        ok: bool,
        summary: str,
        failure_status: RunStatus,
        outcome: TaskOutcome | None = None,
    ) -> None:
        """Apply the shared tool-call and run terminal transition consistently."""
        if ok and outcome is None:
            outcome = TaskOutcome.SUCCEEDED
        ctx: JenAIRunContext = pending["ctx"]
        self._finish_direct_tool(pending, ok=ok, summary=summary)
        self.run_store.finish(
            ctx.run,
            status=RunStatus.COMPLETED if ok else failure_status,
            final_output=summary,
            outcome=outcome,
        )

    async def _run_direct(self, pending: PendingCommand) -> None:
        ctx: JenAIRunContext = pending["ctx"]
        self.run_store.set_status(ctx.run, RunStatus.RUNNING)
        handlers: dict[str, DirectHandler] = {
            "ros_pub": self._run_ros_command,
            "drive": self._run_ros_command,
            "route": self._run_route_command,
            "mission": self._run_mission_command,
            "patrol": self._run_patrol_command,
            "explore": self._run_explore_command,
            "shell": self._run_shell_command,
        }
        kind = str(pending.get("kind", ""))
        handler = handlers.get(kind)
        if handler is None:
            raise ValueError(f"unsupported direct command kind: {kind or '<missing>'}")
        await handler(pending)
        self._scroll_to_bottom()

    async def _run_ros_command(self, pending: PendingCommand) -> None:
        vehicle = self.config.vehicle
        if pending["kind"] == "drive":
            output = await ros_drive(
                pending["topic"],
                pending["message_type"],
                pending["payload"],
                duration_s=pending["duration"],
                max_linear=vehicle.max_linear,
                max_angular=vehicle.max_angular,
            )
        else:
            output = await ros_pub_execute(
                pending["topic"],
                pending["message_type"],
                pending["payload"],
                max_linear=vehicle.max_linear,
                max_angular=vehicle.max_angular,
            )
        ok = output.execution_status == "succeeded"
        self._complete_direct_run(
            pending,
            ok=ok,
            summary=output.result_message,
            failure_status=RunStatus.FAILED,
        )
        await self._mount_event(TimelineItem("success" if ok else "error", output.result_message))

    async def _run_route_command(self, pending: PendingCommand) -> None:
        output = await self._execute_route_action(pending["outgoing_action"])
        sent = output.execution_status == "succeeded"
        if sent:
            outcome = TaskOutcome(pending.get("success_outcome", TaskOutcome.SUCCEEDED))
        elif output.execution_status == "endpoint_mismatch":
            outcome = TaskOutcome.ENDPOINT_MISMATCH
        else:
            outcome = None
        self._complete_direct_run(
            pending,
            ok=sent,
            summary=output.route_preview,
            failure_status=RunStatus.BLOCKED,
            outcome=outcome,
        )
        # Honest rendering: warn when no backend actually sent the goal.
        await self._mount_event(TimelineItem("success" if sent else "warn", output.route_preview))

    async def _run_mission_command(self, pending: PendingCommand) -> None:
        async def on_step(result: Any) -> None:
            await self._mount_step_line(
                result.status,
                f"{result.kind} {result.target}: {result.status} — {result.detail}",
            )

        report = await run_mission(
            self.config,
            pending["locations"],
            pending["steps"],
            on_step=on_step,
            navigate=self._execute_route_action,
        )
        ok = all(result.status == "succeeded" for result in report.results)
        self._complete_direct_run(
            pending,
            ok=ok,
            summary=report.summary,
            failure_status=RunStatus.BLOCKED,
        )
        await self._mount_event(OutputPanel("Mission report", report.summary))

    async def _run_patrol_command(self, pending: PendingCommand) -> None:
        spec = pending["spec"]

        async def on_step(result: Any) -> None:
            loop_tag = f" (loop {result.loop})" if spec.loops > 1 else ""
            body = f"{result.point}{loop_tag}: {result.status} — {result.detail}"
            if result.observation:
                body += f"\n[#9c9689]👁 {result.observation}[/]"
            await self._mount_step_line(result.status, body)

        report = await run_patrol(
            self.config,
            pending["locations"],
            spec,
            navigate=self._execute_route_action,
            on_step=on_step,
            observe=self._observe_camera if spec.photo else None,
        )
        ok = all(result.status in {"succeeded", "partial"} for result in report.results)
        outcome = (
            TaskOutcome.PARTIAL if any(r.status == "partial" for r in report.results) else None
        )
        self._complete_direct_run(
            pending,
            ok=ok,
            summary=report.summary,
            failure_status=RunStatus.BLOCKED,
            outcome=outcome,
        )
        await self._mount_event(OutputPanel("Patrol report", report.summary))
        await self._save_patrol_report(report)

    async def _save_patrol_report(self, report: Any) -> None:
        try:
            log_path = save_patrol_log(report, self.config_path)
            await self._mount_event(
                TimelineItem("success", f"Log saved — view with /report · {log_path.name}")
            )
        except OSError as exc:  # a full disk must not eat the patrol result
            await self._mount_event(TimelineItem("warn", f"Patrol log not saved: {exc}"))

    async def _run_explore_command(self, pending: PendingCommand) -> None:
        spec = pending["spec"]

        async def on_step(result: Any) -> None:
            body = (
                f"Goal {result.attempt}/{spec.max_goals} · {result.point}: "
                f"{result.status} — {result.detail}"
            )
            if result.observation:
                body += f"\n[#9c9689]👁 {result.observation}[/]"
            await self._mount_step_line(result.status, body)

        report = await run_explore(
            self.config,
            pending["locations"],
            spec,
            navigate=self._execute_route_action,
            on_step=on_step,
            observe=self._observe_camera if spec.photo else None,
        )
        ok = report.completed_normally and report.success_count > 0
        outcome = (
            TaskOutcome.PARTIAL
            if ok and any(result.status != "succeeded" for result in report.results)
            else None
        )
        self._complete_direct_run(
            pending,
            ok=ok,
            summary=report.summary,
            failure_status=RunStatus.BLOCKED,
            outcome=outcome,
        )
        await self._mount_event(OutputPanel("Exploration report", report.summary))

    async def _observe_camera(self) -> str | None:
        bridge = await self._get_bridge()
        output = await capture_and_analyze(self.config, bridge, self.config.vehicle.camera_topic)
        return output.summary

    async def _run_shell_command(self, pending: PendingCommand) -> None:
        output = await run_shell(pending["command"])
        ok = output.exit_code == 0
        summary = f"exit {output.exit_code}"
        self._complete_direct_run(
            pending,
            ok=ok,
            summary=summary,
            failure_status=RunStatus.FAILED,
        )
        body = output.stdout_summary or "(no stdout)"
        if output.stderr_summary:
            body += f"\n[bold #d99a86]stderr:[/]\n{output.stderr_summary}"
        await self._mount_event(OutputPanel(f"$ {output.command} (exit {output.exit_code})", body))
