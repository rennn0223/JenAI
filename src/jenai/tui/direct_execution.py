"""Execution lifecycle for approved deterministic TUI commands.

Approval policy lives in ``approval_flow``; this module starts the authorised
operation, records its durable outcome, and renders the result. Keeping policy
and execution separate makes both independently reviewable.
"""

from __future__ import annotations

from jenai.agent.context import JenAIRunContext
from jenai.schemas import RunStatus, ToolCallStatus
from jenai.state.reports import save_patrol_log
from jenai.tools.mission_core import run_mission
from jenai.tools.ros2_core import ros_drive, ros_pub_execute
from jenai.tools.shell_core import run_shell
from jenai.tools.skills import run_explore, run_patrol
from jenai.tools.vision_core import capture_and_analyze
from jenai.tui.panels import OutputPanel, TimelineItem


class DirectExecutionMixin:
    """Execute already-approved slash commands and persist their outcomes."""

    async def _mount_step_line(self, status: str, body: str) -> None:
        """One rendering for every skill/mission step — success green, rest warn."""
        await self._mount_event(TimelineItem("success" if status == "succeeded" else "warn", body))
        self._scroll_to_bottom()

    async def _execute_direct(self, pending: dict) -> None:
        """Run an approved direct command, finalising the run even on failure.

        Reached from the ApprovalCard decision handler, which runs outside the
        command-dispatch try/except — so a raising tool (e.g. a ROS error) would
        otherwise escape unhandled and leave the run stuck RUNNING. Mirror the
        WebUI/agent contract: finish FAILED and surface the error.
        """
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
        pending: dict,
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

    async def _run_direct(self, pending: dict) -> None:
        ctx: JenAIRunContext = pending["ctx"]
        self.run_store.set_status(ctx.run, RunStatus.RUNNING)
        if pending["kind"] in ("ros_pub", "drive"):
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
            self._finish_direct_tool(
                pending,
                ok=output.execution_status == "succeeded",
                summary=output.result_message,
            )
            self.run_store.finish(
                ctx.run,
                status=RunStatus.COMPLETED
                if output.execution_status == "succeeded"
                else RunStatus.FAILED,
                final_output=output.result_message,
            )
            await self._mount_event(
                TimelineItem(
                    "success" if output.execution_status == "succeeded" else "error",
                    output.result_message,
                )
            )
        elif pending["kind"] == "route":
            output = await self._execute_route_action(pending["outgoing_action"])
            sent = output.execution_status == "succeeded"
            self._finish_direct_tool(pending, ok=sent, summary=output.route_preview)
            self.run_store.finish(
                ctx.run,
                status=RunStatus.COMPLETED if sent else RunStatus.BLOCKED,
                final_output=output.route_preview,
            )
            # Honest rendering: warn (not success) when no backend actually sent it.
            await self._mount_event(
                TimelineItem("success" if sent else "warn", output.route_preview)
            )
        elif pending["kind"] == "mission":

            async def _on_step(result):
                await self._mount_step_line(
                    result.status,
                    f"{result.kind} {result.target}: {result.status} — {result.detail}",
                )

            report = await run_mission(
                self.config,
                pending["locations"],
                pending["steps"],
                on_step=_on_step,
                navigate=self._execute_route_action,
            )
            ok = all(r.status == "succeeded" for r in report.results)
            self._finish_direct_tool(pending, ok=ok, summary=report.summary)
            self.run_store.finish(
                ctx.run,
                status=RunStatus.COMPLETED if ok else RunStatus.BLOCKED,
                final_output=report.summary,
            )
            await self._mount_event(OutputPanel("Mission report", report.summary))
        elif pending["kind"] == "patrol":
            spec = pending["spec"]

            async def _on_patrol_step(result):
                loop_tag = f" (loop {result.loop})" if spec.loops > 1 else ""
                body = f"{result.point}{loop_tag}: {result.status} — {result.detail}"
                if result.observation:
                    body += f"\n[#9c9689]👁 {result.observation}[/]"
                await self._mount_step_line(result.status, body)

            async def _observe() -> str | None:
                bridge = await self._get_bridge()
                output = await capture_and_analyze(
                    self.config, bridge, self.config.vehicle.camera_topic
                )
                return output.summary

            report = await run_patrol(
                self.config,
                pending["locations"],
                spec,
                navigate=self._execute_route_action,
                on_step=_on_patrol_step,
                observe=_observe if spec.photo else None,
            )
            ok = all(r.status == "succeeded" for r in report.results)
            self._finish_direct_tool(pending, ok=ok, summary=report.summary)
            self.run_store.finish(
                ctx.run,
                status=RunStatus.COMPLETED if ok else RunStatus.BLOCKED,
                final_output=report.summary,
            )
            await self._mount_event(OutputPanel("Patrol report", report.summary))
            try:
                log_path = save_patrol_log(report, self.config_path)
                await self._mount_event(
                    TimelineItem("success", f"Log saved — view with /report · {log_path.name}")
                )
            except OSError as exc:  # a full disk must not eat the patrol result
                await self._mount_event(TimelineItem("warn", f"Patrol log not saved: {exc}"))
        elif pending["kind"] == "explore":
            spec = pending["spec"]

            async def _on_explore_step(result):
                body = (
                    f"Goal {result.attempt}/{spec.max_goals} · {result.point}: "
                    f"{result.status} — {result.detail}"
                )
                if result.observation:
                    body += f"\n[#9c9689]👁 {result.observation}[/]"
                await self._mount_step_line(result.status, body)

            async def _observe_explore() -> str | None:
                bridge = await self._get_bridge()
                output = await capture_and_analyze(
                    self.config, bridge, self.config.vehicle.camera_topic
                )
                return output.summary

            report = await run_explore(
                self.config,
                pending["locations"],
                spec,
                navigate=self._execute_route_action,
                on_step=_on_explore_step,
                observe=_observe_explore if spec.photo else None,
            )
            ok = report.completed_normally and report.success_count > 0
            self._finish_direct_tool(pending, ok=ok, summary=report.summary)
            self.run_store.finish(
                ctx.run,
                status=RunStatus.COMPLETED if ok else RunStatus.BLOCKED,
                final_output=report.summary,
            )
            await self._mount_event(OutputPanel("Exploration report", report.summary))
        elif pending["kind"] == "shell":
            shell_output = await run_shell(pending["command"])
            ok = shell_output.exit_code == 0
            self._finish_direct_tool(
                pending,
                ok=ok,
                summary=f"exit {shell_output.exit_code}",
            )
            self.run_store.finish(
                ctx.run,
                status=RunStatus.COMPLETED if ok else RunStatus.FAILED,
                final_output=f"exit {shell_output.exit_code}",
            )
            body = shell_output.stdout_summary or "(no stdout)"
            if shell_output.stderr_summary:
                body += f"\n[bold #d99a86]stderr:[/]\n{shell_output.stderr_summary}"
            await self._mount_event(
                OutputPanel(f"$ {shell_output.command} (exit {shell_output.exit_code})", body)
            )
        self._scroll_to_bottom()
