"""Textual App shell: input dispatch, streaming chat, approval flow, task execution."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.css.query import NoMatches
from textual.markup import escape
from textual.widgets import Input, Static

from jenai import __version__
from jenai.agent import build_run_agent, orchestrator, review_plan, run_plan
from jenai.agent.context import JenAIRunContext
from jenai.agent.instructions import CHAT_INSTRUCTIONS
from jenai.agent.session import JenAIFileSession
from jenai.bridge import RosBridgeClient
from jenai.config.models import AppConfig, ProviderProfile
from jenai.providers import (
    ProviderChatError,
    chat_model_name,
    resolve_model_alias,
    stream_provider,
)
from jenai.schemas import (
    ApprovalRequest,
    ApprovalStatus,
    DoctorResult,
    RunRecord,
    RunStatus,
    ToolCallCategory,
    ToolCallRecord,
)
from jenai.state import AuditStore, InputHistory, RunStore, create_session
from jenai.tools.shell_core import assess_command, preview_command
from jenai.tools.user_skills import load_user_skills
from jenai.tui.approval_flow import ApprovalFlowMixin
from jenai.tui.approval_policy import may_auto_approve
from jenai.tui.catalog import SLASH_COMMANDS, TUI_CSS, is_casual_greeting
from jenai.tui.direct_execution import DirectExecutionMixin
from jenai.tui.info_commands import InfoCommandsMixin
from jenai.tui.panels import (
    CommandPalette,
    OutputPanel,
    PromptPill,
    SlashCommand,
    TimelineItem,
    WelcomePanel,
    _short_cwd,
    pixel_mark,
    status_color,
)
from jenai.tui.robot_commands import RobotCommandsMixin
from jenai.tui.widgets import (
    AgentProgressBlock,
    ApprovalCard,
    ErrorBlock,
    ModelPicker,
    PlanBlock,
    ToolBlock,
)


def run_tui(
    config: AppConfig,
    *,
    config_path: Path,
    doctor_result: DoctorResult | None = None,
) -> None:
    JenAITuiApp(config=config, config_path=config_path, doctor_result=doctor_result).run()


class JenAITuiApp(
    ApprovalFlowMixin,
    DirectExecutionMixin,
    InfoCommandsMixin,
    RobotCommandsMixin,
    App[None],
):
    CSS = TUI_CSS

    # priority=True: dispatch checks the focused widget first, so without it
    # these keys never reach the App — Screen grabs shift+tab (focus_previous)
    # and the composer Input grabs ctrl+c (copy) / ctrl+d (delete_right).
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("ctrl+d", "quit", "Quit", priority=True),
        ("escape", "focus_composer", "Focus input"),
        Binding("shift+tab", "cycle_mode", "Mode", priority=True),
    ]

    # Permission modes (shift+tab cycles). They decide two things: where a
    # plain-language line goes, and whether approval cards pause execution.
    #   approve — NL → /run agent; actions raise approval cards (default)
    #   plan    — NL → /plan; nothing can execute (plan agent has no tools)
    #   auto    — NL → /run agent; bounded non-host P0/P1 actions may
    #             auto-approve. HOST_COMMAND and P2 always ask once per action.
    #             Hard clamps, Twin Gate, watchdog and /stop remain mandatory.
    PERMISSION_MODES = ("approve", "plan", "auto")
    COMMAND_QUEUE_LIMIT = 20
    _MODE_LABEL = {
        "approve": "[#5fb1c0]approve[/] · 自然語言交給 agent,動作先過批准卡",
        "plan": "[#f0c84e]plan[/] · 只規劃與教學,不執行任何動作",
        "auto": "[#d99a86]auto[/] · P0/P1 可自動批准;host/P2 仍逐次詢問",
    }

    def __init__(
        self,
        *,
        config: AppConfig,
        config_path: Path,
        doctor_result: DoctorResult | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.config_path = config_path
        self.doctor_result = doctor_result
        # Startup checks intentionally skip slow Nav2/Twin probes. The flag
        # becomes true only after the user explicitly runs /doctor.
        self._doctor_is_full = False
        self._command_matches: list[SlashCommand] = []
        self._selected_command_index = 0
        # File-defined skills (skills/*.toml): loaded once at startup; /skills
        # lists them (and any load warnings) without restarting.
        self._user_skills, self._skill_warnings = load_user_skills(config_path)
        self._mode = "approve"  # shift+tab cycles; see PERMISSION_MODES

        self.session = create_session(config, working_directory=str(Path.cwd()))
        audit_store = AuditStore.best_effort(config_path.parent / "audit.sqlite3")
        self.run_store = RunStore(
            pending_dir=config_path.parent / "pending-runs",
            audit_store=audit_store,
        )
        # Freeze the startup set before Textual schedules restoration. A live
        # /run may begin while the callback is waiting; scanning the mutable
        # store then would mount its approval card twice.
        self._runs_to_restore = [
            run.run_id for run in self.run_store.list_runs() if run.status == "awaiting_approval"
        ]
        self.history = InputHistory(self.session)
        self._last_history_value: str | None = None
        self._last_plan_ctx: JenAIRunContext | None = None
        self._tool_blocks: dict[str, ToolBlock] = {}
        # run_id -> {"agent", "ctx", "decisions", "expected"} for approvals raised
        # mid-Runner.run (agent-driven /run flow).
        self._pending_approvals: dict[str, dict] = {}
        # tool_call_id -> {"kind", "ctx", ...} for approvals from deterministic,
        # non-agent commands (/ros pub, /route) that skip the LLM entirely.
        self._pending_direct_approvals: dict[str, dict] = {}
        self._command_queue: deque[str] = deque()
        # Claude Code-style working indicator + interruptible execution.
        self._active_task: asyncio.Task | None = None
        # True while the active task IS the emergency stop — Esc must not
        # cancel it, and a preempted task must not clear its spinner.
        self._active_task_is_stop = False
        # Continuous camera→VLM loop (started via /perception start).
        self._perception = None
        self._spinner_timer = None
        self._spinner_frame = 0
        self._spinner_started = 0.0
        self._spinner_label = "Working"
        # Eligible bounded tool kinds remembered for the rest of this session.
        # via the approval card's "Yes, and don't ask again" option.
        self._auto_approved: set[str] = set()
        # Provider model ids fetched by /model, so "/model 2" can pick by number.
        self._available_models: list[str] = []
        # Lazily-started rclpy bridge for live Nav2 feedback / pose / camera.
        self._bridge: RosBridgeClient | None = None

    def compose(self) -> ComposeResult:
        profile = self._active_profile()
        with Container(id="stage"):
            with Vertical(id="window"):
                with ScrollableContainer(id="body"):
                    yield WelcomePanel(
                        version=__version__,
                        provider_name=profile.name if profile else "provider missing",
                        provider_kind=profile.provider if profile else "unknown",
                        model_name=self._chat_model_display(),
                        config_path=self.config_path,
                    )
                    yield Vertical(id="events")
                with Container(id="composer-wrap"):
                    yield CommandPalette(id="palette")
                    yield Static("", id="spinner")
                    with Container(id="composer-frame"):
                        with Horizontal(id="composer-line"):
                            yield Static(">", id="composer-prompt")
                            yield Input(
                                placeholder='Try "check the robot status"',
                                id="composer",
                            )
                    with Horizontal(id="statusbar"):
                        yield Static(self._status_left(), id="status-left")
                        yield Static(self._status_right(), id="status-right")

    def on_mount(self) -> None:
        self.query_one("#palette", CommandPalette).display = False
        self.query_one("#composer", Input).focus()
        self._apply_responsive(self.size.width, self.size.height)
        # Mascot heartbeat: idle wag/blink; gallops while a task runs. Cheap
        # (one small Text rebuild every 600 ms) and skipped when hidden.
        self._mascot_frame = 0
        self.set_interval(0.6, self._animate_mascot)
        self.call_after_refresh(self._restore_pending_runs)

    async def _restore_pending_runs(self) -> None:
        """Rebuild approval cards for SDK runs paused before the last exit."""
        run_ids, self._runs_to_restore = self._runs_to_restore, []
        for run_id in run_ids:
            run = self.run_store.get(run_id)
            if run is None or run.status != "awaiting_approval":
                continue
            self.session.current_run_id = run.run_id
            restored_session = self.session.model_copy(
                update={"session_id": run.session_id, "current_run_id": run.run_id}
            )
            ctx = JenAIRunContext(
                config=self.config,
                config_path=self.config_path,
                session=restored_session,
                run=run,
                run_store=self.run_store,
            )
            await self._render_run_update(ctx, run, agent=build_run_agent(self.config))

    def _animate_mascot(self) -> None:
        try:
            mark = self.query_one("#pixel-mark", Static)
        except NoMatches:
            return
        if not mark.display or not self.query_one("#welcome").display:
            return  # narrow layout hides the mascot — don't waste the repaint
        self._mascot_frame += 1
        if mark.has_class("full-mascot"):
            return  # the supplied 40×15 ANSI artwork is a single faithful frame
        running = self._active_task is not None and not self._active_task.done()
        mark.update(pixel_mark(self._mascot_frame, running=running))

    def on_resize(self, event) -> None:
        self._apply_responsive(event.size.width, event.size.height)

    def _apply_responsive(self, width: int, height: int) -> None:
        # Match Claude Code's adaptive shell: the utility column disappears
        # before it can crush the transcript, while the mascot stays visible
        # until the terminal is genuinely compact.
        try:
            welcome = self.query_one("#welcome")
            welcome.set_class(width < 92, "narrow")
            welcome.set_class(width < 56 or height < 27, "compact")
            compact_status = width < 70
            if compact_status != getattr(self, "_compact_status", False):
                self._compact_status = compact_status
                self._update_statusbar()
            palette = self.query_one("#palette", CommandPalette)
            # The normal 16-row menu is unchanged on common terminals.  On
            # short screens cap it so the composer and status line stay visible.
            palette.styles.max_height = max(3, min(16, height - 8))
            if palette.display and self._command_matches:
                # ``CommandPalette`` derives its visible window from the live
                # screen height, so redraw an open palette after a resize.
                palette.update_matches(self._command_matches, self._selected_command_index)
        except NoMatches:
            pass

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.value == self._last_history_value:
            # Recalled from history (not live typing) — don't pop the slash
            # palette open, or it would hijack the next up/down keypress.
            return
        self.history.reset_cursor()
        self._sync_command_palette(event.value)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if self._should_complete_command(value):
            self._complete_selected_command()
            return

        event.input.value = ""
        self._hide_command_palette()
        if not value:
            return
        command = value.split()[0].lower()
        # Recognize any spelling of the stop command ('/STOP', '/stop now'):
        # in an emergency the operator must not need the exact five characters.
        is_stop = command == "/stop"
        is_abort = command == "/abort"
        is_queue_command = command == "/queue"
        if is_stop:
            value = "/stop"
        active = self._active_task is not None and not self._active_task.done()

        if is_queue_command:
            # Queue management must remain available while another command is
            # running; otherwise `/queue` itself would disappear at the tail.
            await self.handle_user_text(value)
            return

        if is_stop:
            # Never run old intent after an emergency stop. Stop preempts the
            # active command and invalidates everything that was queued behind it.
            cleared = len(self._command_queue)
            self._command_queue.clear()
            predecessor = self._active_task if active else None
            # The stop task sends an immediate bridge halt before cancelling the
            # predecessor, then waits for reap and sends a final halt. Replacing
            # the active slot here also makes Esc unable to cancel that sequence.
            if cleared:
                await self._mount_event(
                    TimelineItem("warn", f"Emergency stop cleared {cleared} queued command(s).")
                )
            self._start_user_submission(value, is_stop=True, predecessor=predecessor)
            return

        if is_abort and active:
            # Abort affects only the current command. Its task-finally hook
            # starts the next queued item after cancellation has unwound.
            self._active_task.cancel()
            await self._mount_event(
                TimelineItem(
                    "warn",
                    f"Abort requested; {len(self._command_queue)} queued command(s) remain.",
                )
            )
            self._scroll_to_bottom()
            return

        if active or (self._has_pending_approvals() and not is_abort):
            await self._enqueue_submission(value)
            return

        self._start_user_submission(value)

    def _start_user_submission(
        self,
        value: str,
        *,
        is_stop: bool = False,
        predecessor: asyncio.Task | None = None,
    ) -> None:
        self._active_task = asyncio.create_task(self._run_user_text(value, predecessor=predecessor))
        self._active_task_is_stop = is_stop
        self._update_statusbar()

    async def _enqueue_submission(self, value: str) -> None:
        if len(self._command_queue) >= self.COMMAND_QUEUE_LIMIT:
            await self._mount_event(
                TimelineItem(
                    "warn",
                    f"Queue full ({self.COMMAND_QUEUE_LIMIT}); command was not added.",
                )
            )
            self._scroll_to_bottom()
            return
        self._command_queue.append(value)
        await self._mount_event(
            TimelineItem(
                "muted",
                f"Queued #{len(self._command_queue)}: {value}",
            )
        )
        self._update_statusbar()
        self._scroll_to_bottom()

    def _has_pending_approvals(self) -> bool:
        return bool(self._pending_approvals or self._pending_direct_approvals)

    def _start_next_queued(self) -> None:
        active = self._active_task is not None and not self._active_task.done()
        if active or self._has_pending_approvals() or not self.is_running:
            self._update_statusbar()
            return
        if not self._command_queue:
            self._update_statusbar()
            return
        value = self._command_queue.popleft()
        self._start_user_submission(value)

    async def _run_user_text(
        self,
        value: str,
        *,
        predecessor: asyncio.Task | None = None,
    ) -> None:
        """Run one submission with a working spinner; cancellable via Esc."""
        self._start_spinner(self._spinner_label_for(value))
        try:
            if predecessor is not None:
                # Stop in two phases: brake immediately while the old action is
                # still known, then cancel/kill/reap it, then handle /stop once
                # more for a final zero after no stale publisher can reactivate.
                if value == "/stop":
                    try:
                        await self._halt_before_cancel()
                    except Exception as exc:
                        # Unexpected UI/bridge faults must not prevent reap or
                        # the final stop attempt; surface them without aborting.
                        await self._mount_event(
                            TimelineItem("warn", f"Immediate stop pulse failed: {exc}")
                        )
                    finally:
                        predecessor.cancel()
                try:
                    await predecessor
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            await self.handle_user_text(value)
        except asyncio.CancelledError:
            # Esc interrupt (or app shutdown). CancelledError is a BaseException,
            # so orchestrator's `except Exception` never finalises the run —
            # finish it here or it is orphaned in RUNNING forever. Only report if
            # the UI is still mounted (during quit the widgets are already gone).
            await self._finalize_interrupted_run()
            if self.is_running:
                try:
                    await self._mount_event(TimelineItem("warn", "Interrupted."))
                    self._scroll_to_bottom()
                except NoMatches:
                    pass
        finally:
            # A /stop submission may have replaced us as the active task —
            # then the spinner AND the slot belong to the stop task now:
            # touching either would blank the STOPPING indicator mid-halt.
            if self._active_task is asyncio.current_task():
                self._stop_spinner()
                self._active_task = None
                self._active_task_is_stop = False
                self._start_next_queued()

    async def _finalize_interrupted_run(self) -> None:
        """Mark an in-flight run as stopped so an Esc interrupt doesn't leave it
        stuck in a non-terminal state (RUNNING/UNDERSTANDING/PLANNING)."""
        run_id = self.session.current_run_id
        if run_id is None:
            return
        run = self.run_store.get(run_id)
        in_flight = (RunStatus.RUNNING, RunStatus.UNDERSTANDING, RunStatus.PLANNING)
        if run is not None and run.status in in_flight:
            self.run_store.finish(run, status=RunStatus.BLOCKED)
            try:
                session = JenAIFileSession(run.session_id)
                tail = await session.get_items(limit=1)
                if not tail or tail[-1].get("role") == "assistant":
                    return
                await session.add_items(
                    [
                        {
                            "role": "assistant",
                            "content": (
                                "The previous JenAI run was interrupted before completion. "
                                "Do not assume any unreported action succeeded."
                            ),
                        }
                    ]
                )
            except Exception:
                # Run status is authoritative; memory repair must not hide the interrupt.
                pass

    def on_key(self, event) -> None:
        # Key routing priority: (1) Esc interrupts a running task, (2) the slash
        # palette owns up/down/tab while open, (3) up/down otherwise walks the
        # input history. Each branch stops the event so only one thing reacts.
        if event.key == "escape" and self._active_task is not None and not self._active_task.done():
            if self._active_task_is_stop:
                # Esc must never abort the emergency stop itself — the reflex
                # 'Esc interrupts everything' would otherwise kill the halt
                # before it reaches the bridge.
                event.prevent_default()
                event.stop()
                return
            self._active_task.cancel()
            event.prevent_default()
            event.stop()
            return

        if self._palette_is_visible():
            if event.key == "down":
                self._move_command_selection(1)
            elif event.key == "up":
                self._move_command_selection(-1)
            elif event.key == "tab":
                self._complete_selected_command()
            else:
                return
            event.prevent_default()
            event.stop()
            return

        # No palette open: up/down scrolls the per-session input history.
        if event.key in ("up", "down"):
            composer = self.query_one("#composer", Input)
            if not composer.has_focus:
                return
            value = self.history.previous() if event.key == "up" else self.history.next()
            if value is None:
                return
            self._last_history_value = value
            composer.value = value
            composer.cursor_position = len(composer.value)
            event.prevent_default()
            event.stop()

    async def handle_user_text(self, value: str) -> None:
        self.history.record(value)
        welcome = self.query_one(WelcomePanel)
        if value == "/clear":
            welcome.clear_activity()
            await self._clear_events()
            # Also reset the persisted conversation memory, so /clear truly starts
            # fresh rather than the agent silently remembering the old thread.
            await JenAIFileSession(self.session.session_id).clear_session()
            await self._mount_event(TimelineItem("success", "Session output and memory cleared."))
            return

        welcome.record_activity(value)
        await self._mount_event(PromptPill(value))
        if value.startswith("!"):
            # Bash mode: everything after ! runs as a (still approval-gated)
            # shell command, mirroring Claude Code's ! prefix.
            await self._show_shell(value[1:].strip())
        elif value.startswith("/"):
            await self._handle_command(value)
        else:
            self._scroll_to_bottom()
            # Plain language is mode-routed: the point of the modes is that a
            # bare sentence DOES something (plans or acts) instead of the model
            # telling you which command to type. Wrapped like _handle_command so
            # a provider error or non-conforming model output (run_plan does not
            # catch these) surfaces as a clean message instead of an unhandled
            # task exception — the exception net the removed chat stream had.
            try:
                if is_casual_greeting(value):
                    # A greeting needs neither robot state nor an agent handoff.
                    # Keep it structurally tool-free so weak local models cannot
                    # turn "hi" into a visible specialist/tool call.
                    await self._stream_chat_reply(value)
                elif self._mode == "plan":
                    await self._show_plan(value)
                elif orchestrator.is_read_only_state_request(value):
                    await self._show_state_inspection(value)
                elif await self._try_explicit_route_reflex(value):
                    # Exact one-place imperatives are reflexes: preserve the
                    # approval + Nav2 feedback boundary without spending a
                    # model turn to rediscover a saved location.
                    pass
                else:  # approve / auto — the run agent answers questions too
                    await self._show_run(value)
            except Exception as exc:
                await self._mount_event(TimelineItem("error", f"Failed: {escape(str(exc))}"))
        self._scroll_to_bottom()

    async def _stream_chat_reply(self, prompt: str) -> None:
        """Stream a tool-free chat reply into one timeline item."""
        item = TimelineItem("assistant", "…")
        await self._mount_event(item)
        parts: list[str] = []

        def _paint() -> None:
            item.set_body(escape("".join(parts)))

        async def _keep_or_drop() -> None:
            if parts:
                _paint()
            else:
                await item.remove()

        last_paint = 0.0
        try:
            async for delta in stream_provider(
                self.config, prompt, system_prompt=CHAT_INSTRUCTIONS
            ):
                parts.append(delta)
                now = time.monotonic()
                if now - last_paint >= 0.05:
                    _paint()
                    self._scroll_to_bottom()
                    last_paint = now
        except asyncio.CancelledError:
            try:
                await _keep_or_drop()
            except Exception:
                pass
            raise
        except Exception as exc:
            await _keep_or_drop()
            message = str(exc) if isinstance(exc, ProviderChatError) else f"Chat failed: {exc!r}"
            await self._mount_event(TimelineItem("error", escape(message)))
            return

        if not parts:
            await item.remove()
            await self._mount_event(TimelineItem("error", "Provider returned an empty response."))
            return
        _paint()
        # This turn bypassed the run agent, so persist it ourselves — otherwise
        # the agent's next run would have no memory the exchange ever happened.
        await JenAIFileSession(self.session.session_id).add_items(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "".join(parts)},
            ]
        )

    def action_focus_composer(self) -> None:
        self._hide_command_palette()
        self.query_one("#composer", Input).focus()

    def _sync_command_palette(self, value: str) -> None:
        palette = self.query_one("#palette", CommandPalette)
        raw = value.lstrip()
        if not raw.startswith("/"):
            self._hide_command_palette()
            return

        # Match against the full (possibly multi-word, e.g. "ros pub") command
        # name so a space mid-command ("/ros ") keeps narrowing subcommands
        # instead of being treated as "done, now typing free-form args" — that
        # only kicks in once the query is no longer a prefix of any command.
        query = raw[1:].lower()
        commands = self._all_slash_commands()
        name_matches = [
            command for command in commands if command.name[1:].lower().startswith(query)
        ]
        description_matches = [
            command
            for command in commands
            if command not in name_matches and query in command.description.lower()
        ]
        matches = name_matches + description_matches

        if not matches:
            # Typing arguments of a known command → show its format as a dim,
            # non-interactive hint (the completion inserts only the name, so
            # the palette carries the "what goes next" knowledge instead).
            hint = self._argument_hint(raw)
            if hint is None:
                self._hide_command_palette()
                return
            self._command_matches = []
            self._selected_command_index = 0
            palette.display = True
            palette.update_hint(hint)
            return

        self._command_matches = matches
        self._selected_command_index = min(self._selected_command_index, max(len(matches) - 1, 0))
        palette.display = True
        palette.update_matches(matches, self._selected_command_index)

    def _argument_hint(self, raw: str) -> SlashCommand | None:
        """The command whose arguments are being typed, if it has a template.

        Longest name wins so "/loc add …" hints /loc add, not a shorter
        prefix command.
        """
        lowered = raw.lower()
        candidates = [
            command
            for command in self._all_slash_commands()
            if "<" in command.template and lowered.startswith(command.name.lower() + " ")
        ]
        return max(candidates, key=lambda command: len(command.name), default=None)

    def _hide_command_palette(self) -> None:
        self.query_one("#palette", CommandPalette).display = False
        self._command_matches = []
        self._selected_command_index = 0

    def _palette_is_visible(self) -> bool:
        return bool(self.query_one("#palette", CommandPalette).display)

    def _move_command_selection(self, delta: int) -> None:
        if not self._command_matches:
            return

        # Wrap over the full match list (not just the visible window); the
        # palette's scroll window follows this index, so every command is
        # reachable by holding up/down.
        self._selected_command_index = (self._selected_command_index + delta) % len(
            self._command_matches
        )
        self.query_one("#palette", CommandPalette).update_matches(
            self._command_matches,
            self._selected_command_index,
        )

    def _should_complete_command(self, value: str) -> bool:
        if not self._palette_is_visible() or not self._command_matches:
            return False
        known_values = {command.name for command in self._all_slash_commands()}
        known_values.update(command.completion for command in self._all_slash_commands())
        return value not in known_values

    def _complete_selected_command(self) -> None:
        if not self._command_matches:
            return

        command = self._command_matches[self._selected_command_index]
        composer = self.query_one("#composer", Input)
        # Complete the command NAME only, never the "<placeholder>" template —
        # an inserted template had to be deleted before typing real arguments.
        # The argument format shows as a dim palette HINT instead (see the
        # hint branch in _sync_command_palette).
        composer.value = f"{command.name} "
        composer.cursor_position = len(composer.value)
        self._sync_command_palette(composer.value)  # command list → format hint
        composer.focus()

    # A submitted "<placeholder>" must never reach a handler (it once saved the
    # literal "<name|number>" as a model binding). Completion no longer inserts
    # templates, but /help and docs still show them — keep the net for
    # copy-paste and manual input.
    _TEMPLATE_VALUES = frozenset(c.template for c in SLASH_COMMANDS if "<" in c.template)

    async def _handle_command(self, value: str) -> None:
        if value.strip() in self._TEMPLATE_VALUES:
            await self._mount_event(
                TimelineItem(
                    "warn",
                    "Replace the [bold #f2ede1]<placeholder>[/] with a real value first, "
                    f"e.g. [bold #f2ede1]{value.split()[0]} …[/]. See /help.",
                )
            )
            return

        command, _, arg = value.partition(" ")
        arg = arg.strip()

        handler, handler_arg = self._resolve_command_handler(command, arg)
        if handler is None:
            await self._mount_event(
                TimelineItem(
                    "warn",
                    f"Unknown command [bold #f2ede1]{escape(command)}[/]. "
                    "Try [bold #f2ede1]/help[/].",
                )
            )
            return

        try:
            await handler(handler_arg)
        except Exception as exc:
            await self._mount_event(
                TimelineItem("error", f"{escape(command)} failed: {escape(str(exc))}")
            )

    def _all_slash_commands(self) -> list[SlashCommand]:
        """Built-ins plus file-defined skills, so user skills get the same
        palette/completion treatment as native commands."""
        extras = [
            SlashCommand(f"/{s.name}", f"Skill: {s.description}")
            for s in self._user_skills.values()
        ]
        return SLASH_COMMANDS + extras

    async def _show_skills(self, _: str = "") -> None:
        from jenai.tools.user_skills import skills_dir

        lines: list[str] = []
        for s in self._user_skills.values():
            lines.append(f"[bold #f2ede1]/{s.name}[/] — {s.description}")
            lines.append(f"    [#9c9689]{s.steps}[/]")
        for warning in self._skill_warnings:
            lines.append(f"[#d99a86]⚠ {warning}[/]")
        if not lines:
            lines.append("No user skills yet.")
        lines.append("")
        lines.append(
            f"[#9c9689]Add one: {skills_dir(self.config_path)}/<name>.toml with "
            "name / description / steps(=/mission 語法) — restart to load.[/]"
        )
        await self._mount_event(OutputPanel("User skills", "\n".join(lines)))

    async def _run_user_skill(self, name: str) -> None:
        """A skill is a named mission: same parser, same approval card, same
        gated execution — files can only compose primitives, never bypass."""
        skill = self._user_skills[name]
        await self._mount_event(
            TimelineItem("success", f"Skill [bold #f2ede1]/{skill.name}[/] → {skill.steps}")
        )
        await self._show_mission(skill.steps)

    def _resolve_command_handler(self, command: str, arg: str):
        if command == "/ros":
            subcommand, _, rest = arg.partition(" ")
            ros_handlers = {
                "topics": self._show_ros_topics,
                "topic-info": self._show_ros_topic_info,
                "schema": self._show_ros_schema,
                "echo": self._show_ros_echo,
                "pub": self._show_ros_pub,
                "drive": self._show_ros_drive,
            }
            return ros_handlers.get(subcommand), rest.strip()

        if command == "/loc":
            subcommand, _, rest = arg.partition(" ")
            loc_handlers = {
                "list": self._show_loc_list,
                "add": self._show_loc_add,
                "show": self._show_loc_show,
                "rm": self._show_loc_rm,
                "rename": self._show_loc_rename,
                "move": self._show_loc_move,
            }
            return loc_handlers.get(subcommand), rest.strip()

        handlers = {
            "/stop": self._show_stop,
            "/help": self._show_help,
            "/status": self._show_status,
            "/doctor": self._show_doctor,
            "/providers": self._show_providers,
            "/models": self._show_models,
            "/model": self._show_model,
            "/provider": self._show_provider,
            "/permissions": self._show_permissions,
            "/mode": self._show_mode,
            "/config": self._show_config,
            "/plan": self._show_plan,
            "/run": self._show_run,
            "/why": self._show_why,
            "/review": self._show_review,
            "/abort": self._show_abort,
            "/queue": self._show_queue,
            "/route": self._show_route,
            "/drive": self._show_drive,
            "/mission": self._show_mission,
            "/patrol": self._show_patrol,
            "/explore": self._show_explore,
            "/dock": self._show_dock,
            "/report": self._show_report,
            "/skills": self._show_skills,
            "/vision": self._show_vision,
            "/perception": self._show_perception,
            "/shell": self._show_shell,
            "/quit": self._quit_from_command,
            "/exit": self._quit_from_command,
        }
        handler = handlers.get(command)
        if handler is not None:
            return handler, arg
        # File-defined skills: /name runs the skill's mission steps. Built-ins
        # always win (checked first), and loading already refused reserved names.
        skill_name = command[1:].lower()
        if skill_name in self._user_skills:
            return self._run_user_skill, skill_name
        return None, arg

    async def on_unmount(self) -> None:
        if self._perception is not None:
            await self._perception.stop()
        if self._bridge is not None:
            await self._bridge.stop()

    def _refresh_model_display(self) -> None:
        profile = self._active_profile()
        try:
            self._update_statusbar()
            self.query_one(WelcomePanel).update_model(
                self._chat_model_display(),
                provider_name=profile.name if profile else "provider missing",
                provider_kind=profile.provider if profile else "unknown",
            )
        except NoMatches:  # app shutting down / panel not mounted
            pass

    def _new_run_context(self, user_input: str) -> JenAIRunContext:
        run = self.run_store.create_run(self.session.session_id, user_input)
        self.session.current_run_id = run.run_id
        return JenAIRunContext(
            config=self.config,
            config_path=self.config_path,
            session=self.session,
            run=run,
            run_store=self.run_store,
        )

    def _current_run(self) -> RunRecord | None:
        if self.session.current_run_id is None:
            return None
        return self.run_store.get(self.session.current_run_id)

    async def _render_run_update(
        self,
        ctx: JenAIRunContext,
        run: RunRecord,
        *,
        agent=None,
    ) -> None:
        if run.plan_steps:
            await self._mount_event(
                PlanBlock(f"Plan: {run.task_summary or run.user_input}", run.plan_steps)
            )

        await self._render_live_tool_updates(run)

        if run.status == "awaiting_approval":
            pending_approvals = [a for a in run.interruptions if a.status == "pending"]
            if agent is not None and pending_approvals:
                entry = {
                    "agent": agent,
                    "ctx": ctx,
                    "decisions": {},
                    "expected": {a.tool_call_id for a in pending_approvals},
                }
                self._pending_approvals[run.run_id] = entry
                mounted_card = False
                for approval in pending_approvals:
                    remembered = bool(
                        approval.tool_name and approval.tool_name in self._auto_approved
                    )
                    if may_auto_approve(
                        approval,
                        auto_mode=self._mode == "auto",
                        remembered=remembered,
                    ):
                        entry["decisions"][approval.tool_call_id] = True
                        if self._mode == "auto":
                            await self._mount_event(
                                TimelineItem("warn", f"自動模式:已批准 {approval.title}")
                            )
                    else:
                        await self._mount_event(ApprovalCard(approval))
                        mounted_card = True
                if not mounted_card and set(entry["decisions"]) >= entry["expected"]:
                    await self._finalize_agent_approvals(run.run_id)
                    return
            else:
                for approval in pending_approvals:
                    await self._mount_event(ApprovalCard(approval))
        elif run.status == "completed":
            if run.final_output:
                # Normalize model-authored paragraph gaps; all transcript
                # entries otherwise use the same one-row line spacing.
                await self._mount_event(OutputPanel("Result", run.final_output, spaced=True))
            await self._mount_event(TimelineItem("success", "Done."))
        elif run.status == "failed":
            if run.error:
                await self._mount_event(ErrorBlock(run.error))
            else:
                await self._mount_event(TimelineItem("error", "Run failed."))
        elif run.status == "blocked":
            # The orchestrator writes WHY into final_output (e.g. the model-loop
            # stop with a suggested manual command) — swallowing it would hide
            # an honest report behind a four-word warning.
            if run.final_output:
                await self._mount_event(OutputPanel("Run blocked", run.final_output, spaced=True))
            else:
                await self._mount_event(TimelineItem("warn", "Run blocked."))

        self._scroll_to_bottom()

    async def _render_live_tool_updates(self, run: RunRecord) -> None:
        """Mount tool calls as they start and refresh their terminal outcome in place."""

        for tool_call in run.tool_calls:
            block = self._tool_blocks.get(tool_call.tool_call_id)
            if block is None:
                block = ToolBlock(tool_call)
                self._tool_blocks[tool_call.tool_call_id] = block
                await self._mount_event(block)
            else:
                block.set_tool_call(tool_call)

    async def _run_with_agent_progress(self, ctx: JenAIRunContext, awaitable) -> RunRecord:
        """Keep Agent stages and recorded tools visible while the model is running."""

        progress = AgentProgressBlock(ctx.run)
        await self._mount_event(progress)
        task = asyncio.create_task(awaitable)
        try:
            while not task.done():
                progress.set_run(ctx.run)
                await self._render_live_tool_updates(ctx.run)
                self._scroll_to_bottom()
                await asyncio.sleep(0.1)
            run = await task
        except asyncio.CancelledError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            raise
        await self._render_live_tool_updates(ctx.run)
        progress.set_run(ctx.run)
        return run

    async def _show_plan(self, arg: str) -> None:
        if not arg:
            await self._mount_event(TimelineItem("warn", "Usage: /plan <task description>"))
            return

        ctx = self._new_run_context(arg)
        self._last_plan_ctx = ctx
        self._scroll_to_bottom()
        run = await self._run_with_agent_progress(ctx, run_plan(ctx, arg))
        await self._render_run_update(ctx, run)

    async def _show_run(self, arg: str) -> None:
        if not arg:
            await self._mount_event(TimelineItem("warn", "Usage: /run <task description>"))
            return

        ctx = self._new_run_context(arg)
        agent = build_run_agent(self.config)
        self._scroll_to_bottom()
        run = await self._run_with_agent_progress(ctx, orchestrator.start_run(agent, ctx, arg))
        await self._render_run_update(ctx, run, agent=agent)

    async def _show_state_inspection(self, arg: str) -> None:
        """Fast path for explicit read-only pose/scan/Nav2 status questions."""

        ctx = self._new_run_context(arg)
        self._scroll_to_bottom()
        run = await self._run_with_agent_progress(
            ctx, orchestrator.start_read_only_state_run(ctx)
        )
        await self._render_run_update(ctx, run)

    async def _show_why(self, _: str = "") -> None:
        run = self._current_run()
        if run is None:
            await self._mount_event(TimelineItem("warn", "No active run yet."))
            return

        lines: list[str] = []
        active_step = next(
            (step for step in run.plan_steps if step.status in ("active", "pending")), None
        )
        if active_step is not None:
            lines.append(f"Current step: [bold #f2ede1]{active_step.title}[/]")
            lines.append(f"Reason: {active_step.reason}")
        if run.interruptions:
            last_approval = run.interruptions[-1]
            lines.append(f"Approval justification: {last_approval.justification}")
        if not lines:
            lines.append("No recorded plan steps or approvals to explain yet.")
        await self._mount_event(OutputPanel("Why", "\n".join(lines)))

    async def _show_review(self, arg: str) -> None:
        if self._last_plan_ctx is None:
            await self._mount_event(
                TimelineItem("warn", "No plan to review yet. Run /plan <task> first.")
            )
            return

        ctx = self._last_plan_ctx
        task = arg or ctx.run.user_input
        self._scroll_to_bottom()
        run = await review_plan(ctx, task)
        await self._render_run_update(ctx, run)

    async def _show_abort(self, _: str = "") -> None:
        run = self._current_run()
        if run is None:
            await self._mount_event(TimelineItem("warn", "No active run to abort."))
            return

        pending = self._pending_approvals.pop(run.run_id, None)
        if pending:
            for tool_call_id in pending["expected"]:
                await self._remove_approval_card(tool_call_id)
                self.run_store.resolve_interruption(run, tool_call_id, ApprovalStatus.REJECTED)
        for tool_call_id, direct in list(self._pending_direct_approvals.items()):
            if direct["ctx"].run.run_id == run.run_id:
                await self._remove_approval_card(tool_call_id)
                self.run_store.resolve_interruption(run, tool_call_id, ApprovalStatus.REJECTED)
                del self._pending_direct_approvals[tool_call_id]

        self.run_store.finish(run, status=RunStatus.BLOCKED)
        await self._mount_event(TimelineItem("warn", "Run aborted."))

    async def _show_queue(self, arg: str = "") -> None:
        choice = arg.strip().lower()
        if choice == "clear":
            count = len(self._command_queue)
            self._command_queue.clear()
            self._update_statusbar()
            await self._mount_event(TimelineItem("success", f"Cleared {count} queued command(s)."))
            return
        if choice:
            await self._mount_event(TimelineItem("warn", "Usage: /queue [clear]"))
            return
        if not self._command_queue:
            await self._mount_event(TimelineItem("muted", "Command queue is empty."))
            return
        lines = [f"{index}. {value}" for index, value in enumerate(self._command_queue, 1)]
        await self._mount_event(OutputPanel("Command queue", "\n".join(lines)))

    # -- ROS2 ---------------------------------------------------------------

    def _locations_path(self) -> Path | None:
        return self.config.resolved_locations_path(self.config_path)

    async def _show_shell(self, arg: str) -> None:
        command = arg.strip()
        if not command:
            await self._mount_event(TimelineItem("warn", "Usage: /shell <command>"))
            return

        preview = preview_command(command)
        risk = assess_command(command)
        ctx = self._new_run_context(f"/shell {command}")
        tool_call = ToolCallRecord(
            tool_name="shell_run_tool",
            category=ToolCallCategory.SHELL,
            input_summary=command,
            risk_level=risk.risk_level,
            effect_scope=risk.effect_scope,
        )
        self.run_store.add_tool_call(ctx.run, tool_call)
        approval = ApprovalRequest(
            run_id=ctx.run.run_id,
            tool_call_id=tool_call.tool_call_id,
            title="Run shell command",
            summary=f"Execute in {preview.working_directory}. {risk.risk_summary}",
            raw_action=command,
            risk_level=risk.risk_level,
            effect_scope=risk.effect_scope,
            justification="Requested via /shell.",
        )
        self.run_store.add_interruption(ctx.run, approval)
        self.run_store.set_status(ctx.run, RunStatus.AWAITING_APPROVAL)

        self._pending_direct_approvals[approval.tool_call_id] = {
            "kind": "shell",
            "ctx": ctx,
            "command": command,
            "tool_call_id": tool_call.tool_call_id,
            "approval": approval,
        }
        await self._mount_event(ApprovalCard(approval))
        self._scroll_to_bottom()

    async def on_model_picker_selected(self, message: ModelPicker.Selected) -> None:
        for picker in self.query(ModelPicker):
            await picker.remove()
        if message.model_id is not None:
            # Bare /model picks the conversation model (chat + default fallback),
            # matching `/model <name>`; specialised bindings stay untouched.
            await self._apply_model_choice(message.model_id, ("chat", "default"))
        self.query_one("#composer", Input).focus()

    async def _quit_from_command(self, _: str = "") -> None:
        self.exit()

    async def _clear_events(self) -> None:
        events = self.query_one("#events", Vertical)
        await events.remove_children()

    async def _mount_event(self, widget: Static | Horizontal | Vertical) -> None:
        await self.query_one("#events", Vertical).mount(widget)

    def _scroll_to_bottom(self) -> None:
        self.query_one("#body", ScrollableContainer).scroll_end(animate=False)

    # -- Status line + working spinner ---------------------------------------

    _SPINNER_FRAMES = "✻✳✢✦✳"

    _MODE_CHIP = {
        "approve": "[#5fb1c0]approve[/]",
        "plan": "[#f0c84e]plan[/]",
        "auto": "[#d99a86]auto[/]",
    }

    def _status_left(self) -> str:
        chip = self._MODE_CHIP.get(getattr(self, "_mode", "approve"), "")
        if getattr(self, "_compact_status", False):
            return chip
        queued = len(getattr(self, "_command_queue", ()))
        queue_text = f" · queue {queued}" if queued else ""
        return f"{chip} [#7a756c]shift+tab · ? shortcuts{queue_text}[/]"

    def _status_right(self) -> str:
        profile = self._active_profile()
        provider = profile.provider if profile else "no-provider"
        if getattr(self, "_compact_status", False):
            return f"[#7a756c]{provider} · {self._chat_model_display()}[/]"
        return f"[#7a756c]{provider} · {self._chat_model_display()} · {_short_cwd()}[/]"

    def _status_line(self) -> str:
        """Plain combined form retained for logs and compatibility."""
        return f"{self._status_left()}  {self._status_right()}"

    def _update_statusbar(self) -> None:
        try:
            self.query_one("#status-left", Static).update(self._status_left())
            self.query_one("#status-right", Static).update(self._status_right())
        except NoMatches:
            pass

    def action_cycle_mode(self) -> None:
        """shift+tab: approve → plan → auto → approve."""
        modes = list(self.PERMISSION_MODES)
        self._set_mode(modes[(modes.index(self._mode) + 1) % len(modes)])

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self._update_statusbar()
        # Timeline note so the transcript records WHEN the gate posture changed
        # (an auto-approved action is only auditable if the switch is visible).
        self.call_later(self._announce_mode)

    # Typed fallback for terminals that never deliver Shift+Tab (common over
    # SSH clients and embedded terminals) — same state, same announcement.
    _MODE_ALIASES = {
        "approve": "approve",
        "審批": "approve",
        "plan": "plan",
        "規劃": "plan",
        "auto": "auto",
        "自動": "auto",
    }

    async def _show_mode(self, arg: str = "") -> None:
        """/mode [approve|plan|auto] — set the permission mode; no arg cycles."""
        choice = arg.strip().lower()
        if not choice:
            self.action_cycle_mode()
            return
        mode = self._MODE_ALIASES.get(choice)
        if mode is None:
            await self._mount_event(
                TimelineItem("error", "用法:/mode [approve|plan|auto](不帶參數 = 循環切換)")
            )
            return
        self._set_mode(mode)

    async def _announce_mode(self) -> None:
        await self._mount_event(
            TimelineItem(
                "warn" if self._mode == "auto" else "success", self._MODE_LABEL[self._mode]
            )
        )
        self._scroll_to_bottom()

    def _spinner_label_for(self, value: str) -> str:
        if value.startswith("/plan"):
            return "Planning"
        if value.startswith("/run"):
            return "Running"
        if value.startswith("/review"):
            return "Reviewing"
        if value.startswith(("/", "!")):
            return "Working"
        return "Thinking"

    def _start_spinner(self, label: str) -> None:
        if self._spinner_timer is not None:
            # A preempted task's spinner may still be ticking — stop its
            # timer before overwriting the reference, or it leaks and keeps
            # repainting forever.
            self._spinner_timer.stop()
        self._spinner_label = label
        self._spinner_started = time.monotonic()
        self._spinner_frame = 0
        spinner = self.query_one("#spinner", Static)
        spinner.add_class("active")
        self._render_spinner()
        self._spinner_timer = self.set_interval(0.2, self._render_spinner)

    def _render_spinner(self) -> None:
        frame = self._SPINNER_FRAMES[self._spinner_frame % len(self._SPINNER_FRAMES)]
        self._spinner_frame += 1
        elapsed = int(time.monotonic() - self._spinner_started)
        try:
            self.query_one("#spinner", Static).update(
                f"[#e8683f]{frame}[/] {self._spinner_label}… "
                f"[#7a756c]({elapsed}s · esc to interrupt)[/]"
            )
        except NoMatches:  # widget gone (app shutting down)
            pass

    def _stop_spinner(self) -> None:
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        try:
            spinner = self.query_one("#spinner", Static)
        except NoMatches:  # widget gone (app shutting down)
            return
        spinner.remove_class("active")
        spinner.update("")

    def _active_profile(self) -> ProviderProfile | None:
        return self.config.active_profile()

    def _chat_model_display(self) -> str:
        raw_model = chat_model_name(self.config)
        if raw_model is None:
            return "not configured"
        return resolve_model_alias(raw_model, self._active_profile())

    def _format_profile(self, profile: ProviderProfile | None) -> str:
        if profile is None:
            return "[#cb6250]missing[/]"
        return f"[bold #f2ede1]{profile.name}[/] · {profile.provider}"

    def _format_status(self, value: str) -> str:
        return f"[bold {status_color(value)}]{value}[/]"
