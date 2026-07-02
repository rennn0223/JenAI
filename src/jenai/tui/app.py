from __future__ import annotations

import asyncio
import time
from pathlib import Path

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.css.query import NoMatches
from textual.widgets import Input, Static

from jenai import __version__
from jenai.agent import build_run_agent, orchestrator, review_plan, run_plan
from jenai.agent.context import JenAIRunContext
from jenai.agent.session import JenAIFileSession
from jenai.bridge import RosBridgeClient
from jenai.config.models import AppConfig, ProviderProfile
from jenai.providers import (
    ProviderChatError,
    ask_provider,
    chat_model_name,
    resolve_model_alias,
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
from jenai.state import InputHistory, RunStore, create_session
from jenai.tools.mission_core import run_mission
from jenai.tools.ros2_core import (
    ros_drive,
    ros_pub_execute,
)
from jenai.tools.shell_core import assess_command, preview_command, run_shell
from jenai.tui.info_commands import InfoCommandsMixin
from jenai.tui.panels import (
    CommandPalette,
    OutputPanel,
    PromptPill,
    SlashCommand,
    TimelineItem,
    WelcomePanel,
    _short_cwd,
    status_color,
)
from jenai.tui.robot_commands import RobotCommandsMixin
from jenai.tui.widgets import ApprovalCard, ErrorBlock, PlanBlock, ToolBlock

APPROVAL_REQUIRED_COMMANDS = ("/ros pub", "/route", "/shell", "/run")

MODEL_BINDING_NAMES = ("chat", "plan", "vision", "route", "default")


SLASH_COMMANDS = [
    SlashCommand("/help", "Show available JenAI commands"),
    SlashCommand("/status", "Show provider, model, config, and doctor state"),
    SlashCommand("/doctor", "Run setup and environment checks"),
    SlashCommand("/providers", "List configured provider profiles"),
    SlashCommand("/model", "List provider models and switch (Ollama etc.)", "/model <name|number>"),
    SlashCommand("/models", "Show model bindings"),
    SlashCommand("/provider", "Show or switch the active provider profile", "/provider <name>"),
    SlashCommand("/permissions", "Show which commands require approval"),
    SlashCommand("/config", "Show config file details"),
    SlashCommand("/plan", "Plan a task without executing any tools", "/plan <task>"),
    SlashCommand("/run", "Execute a task, calling tools as needed", "/run <task>"),
    SlashCommand("/why", "Explain the current run's last decision"),
    SlashCommand("/review", "Re-plan and critique the current plan"),
    SlashCommand("/abort", "Abort the active run"),
    SlashCommand("/ros topics", "List ROS2 topics"),
    SlashCommand(
        "/ros topic-info", "Show a topic's type/publishers/subscribers", "/ros topic-info <topic>"
    ),
    SlashCommand("/ros schema", "Summarize a ROS2 topic's message schema", "/ros schema <topic>"),
    SlashCommand("/ros echo", "Snapshot recent messages on a topic", "/ros echo <topic> [count]"),
    SlashCommand(
        "/ros pub", "Publish once to a ROS2 topic (needs approval)", "/ros pub <topic> <payload>"
    ),
    SlashCommand(
        "/ros drive",
        "Drive for N seconds then auto-stop (needs approval)",
        "/ros drive <topic> <payload> [seconds]",
    ),
    SlashCommand(
        "/drive", "Drive by plain language (needs approval)", "/drive 前進兩秒"
    ),
    SlashCommand(
        "/mission", "Run a multi-step patrol mission (needs approval)", "/mission kitchen, lobby"
    ),
    SlashCommand(
        "/route", "Resolve and send a navigation route (needs approval)", "/route <text>"
    ),
    SlashCommand("/loc list", "List known locations"),
    SlashCommand(
        "/loc add", "Save the robot's current position as a location", "/loc add here <name>"
    ),
    SlashCommand("/loc show", "Show a location's details", "/loc show <name>"),
    SlashCommand("/vision image", "Analyze a local image with the VLM", "/vision image <path>"),
    SlashCommand(
        "/vision camera", "Capture a camera frame and describe it", "/vision camera [topic]"
    ),
    SlashCommand("/shell", "Run a host shell command (needs approval)", "/shell <cmd>"),
    SlashCommand("/clear", "Clear the output area"),
    SlashCommand("/quit", "Exit JenAI"),
]


def run_tui(
    config: AppConfig,
    *,
    config_path: Path,
    doctor_result: DoctorResult | None = None,
) -> None:
    JenAITuiApp(config=config, config_path=config_path, doctor_result=doctor_result).run()


class JenAITuiApp(InfoCommandsMixin, RobotCommandsMixin, App[None]):
    CSS = """
    Screen {
        background: #1c1b18;
        color: #d9d3c7;
    }

    #stage {
        width: 100%;
        height: 100%;
        padding: 0;
        background: #1c1b18;
    }

    #window {
        width: 100%;
        height: 100%;
        background: #1c1b18;
    }

    #body {
        height: 1fr;
        padding: 1 3 0 3;
        scrollbar-size-vertical: 1;
        scrollbar-background: #1c1b18;
        scrollbar-color: #332f28;
        scrollbar-color-hover: #3a352e;
        scrollbar-color-active: #3a352e;
    }

    #welcome {
        border: round #c15f3c;
        padding: 1 2;
        margin-bottom: 1;
        height: auto;
        align-horizontal: center;
    }

    .heading {
        color: #f2ede1;
        text-style: bold;
        text-align: center;
        width: 100%;
        margin-bottom: 1;
    }

    #pixel-mark {
        color: #d97757;
        text-align: center;
        width: 100%;
        margin: 0 0 1 0;
    }

    /* Narrow (mobile) terminals: hide the mascot so it is never squished. */
    #welcome.narrow #pixel-mark {
        display: none;
    }

    .meta {
        color: #9c9689;
        text-align: center;
        width: 100%;
    }

    .prompt-line {
        height: auto;
        margin: 1 0 0 0;
        color: #d9d3c7;
    }

    #events {
        height: auto;
        margin-bottom: 1;
    }

    .bullet-line {
        height: auto;
        margin-bottom: 1;
        color: #d9d3c7;
    }

    .approval-card {
        background: #242019;
        border-left: thick #c15f3c;
        padding: 0 2;
        margin-bottom: 1;
        height: auto;
    }

    #composer-wrap {
        height: auto;
        padding: 1 3 1 3;
        background: #1c1b18;
    }

    #palette {
        height: auto;
        max-height: 16;
        margin-bottom: 1;
        padding: 1 2;
        background: #141310;
        border: round #3a352e;
    }

    #composer {
        height: 3;
        background: #262420;
        color: #f2ede1;
        border: round #3a352e;
        padding: 0 1;
    }

    #composer:focus {
        border: round #d97757;
    }

    #spinner {
        height: auto;
        color: #d97757;
        margin-bottom: 1;
        display: none;
    }

    #spinner.active {
        display: block;
    }

    #statusbar {
        height: 1;
        color: #9c9689;
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+d", "quit", "Quit"),
        ("escape", "focus_composer", "Focus input"),
    ]

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
        self._command_matches: list[SlashCommand] = []
        self._selected_command_index = 0

        self.session = create_session(config, working_directory=str(Path.cwd()))
        self.run_store = RunStore()
        self.history = InputHistory(self.session)
        self._last_history_value: str | None = None
        self._last_plan_ctx: JenAIRunContext | None = None
        self._rendered_tool_call_ids: set[str] = set()
        # run_id -> {"agent", "ctx", "decisions", "expected"} for approvals raised
        # mid-Runner.run (agent-driven /run flow).
        self._pending_approvals: dict[str, dict] = {}
        # tool_call_id -> {"kind", "ctx", ...} for approvals from deterministic,
        # non-agent commands (/ros pub, /route) that skip the LLM entirely.
        self._pending_direct_approvals: dict[str, dict] = {}
        # Claude Code-style working indicator + interruptible execution.
        self._active_task: asyncio.Task | None = None
        self._spinner_timer = None
        self._spinner_frame = 0
        self._spinner_started = 0.0
        self._spinner_label = "Working"
        # Tool kinds the user chose to auto-approve for the rest of the session
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
                        doctor_result=self.doctor_result,
                    )
                    yield Vertical(id="events")
                with Container(id="composer-wrap"):
                    yield CommandPalette(id="palette")
                    yield Static("", id="spinner")
                    yield Input(
                        placeholder="Ask JenAI, / for commands, ! for shell",
                        id="composer",
                    )
                    yield Static(self._status_line(), id="statusbar")

    def on_mount(self) -> None:
        self.query_one("#palette", CommandPalette).display = False
        self.query_one("#composer", Input).focus()
        self._apply_responsive(self.size.width)

    def on_resize(self, event) -> None:
        self._apply_responsive(event.size.width)

    def _apply_responsive(self, width: int) -> None:
        # On a narrow (mobile) terminal, collapse decorative chrome so nothing
        # gets crushed. The mascot needs ~26 columns to render cleanly.
        try:
            self.query_one("#welcome").set_class(width < 56, "narrow")
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
        if self._active_task is not None and not self._active_task.done():
            return  # busy; ignore new submissions until the current one finishes
        self._active_task = asyncio.create_task(self._run_user_text(value))

    async def _run_user_text(self, value: str) -> None:
        """Run one submission with a working spinner; cancellable via Esc."""
        self._start_spinner(self._spinner_label_for(value))
        try:
            await self.handle_user_text(value)
        except asyncio.CancelledError:
            # Esc interrupt (or app shutdown). CancelledError is a BaseException,
            # so orchestrator's `except Exception` never finalises the run —
            # finish it here or it is orphaned in RUNNING forever. Only report if
            # the UI is still mounted (during quit the widgets are already gone).
            self._finalize_interrupted_run()
            if self.is_running:
                try:
                    await self._mount_event(TimelineItem("warn", "Interrupted."))
                    self._scroll_to_bottom()
                except NoMatches:
                    pass
        finally:
            self._stop_spinner()
            self._active_task = None

    def _finalize_interrupted_run(self) -> None:
        """Mark an in-flight run as stopped so an Esc interrupt doesn't leave it
        stuck in a non-terminal state (RUNNING/UNDERSTANDING/PLANNING)."""
        run_id = self.session.current_run_id
        if run_id is None:
            return
        run = self.run_store.get(run_id)
        in_flight = (RunStatus.RUNNING, RunStatus.UNDERSTANDING, RunStatus.PLANNING)
        if run is not None and run.status in in_flight:
            self.run_store.finish(run, status=RunStatus.BLOCKED)

    def on_key(self, event) -> None:
        # Key routing priority: (1) Esc interrupts a running task, (2) the slash
        # palette owns up/down/tab while open, (3) up/down otherwise walks the
        # input history. Each branch stops the event so only one thing reacts.
        if event.key == "escape" and self._active_task is not None and not self._active_task.done():
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
        if value == "/clear":
            await self._clear_events()
            # Also reset the persisted conversation memory, so /clear truly starts
            # fresh rather than the agent silently remembering the old thread.
            await JenAIFileSession(self.session.session_id).clear_session()
            await self._mount_event(TimelineItem("success", "Session output and memory cleared."))
            return

        await self._mount_event(PromptPill(value))
        if value.startswith("!"):
            # Bash mode: everything after ! runs as a (still approval-gated)
            # shell command, mirroring Claude Code's ! prefix.
            await self._show_shell(value[1:].strip())
        elif value.startswith("/"):
            await self._handle_command(value)
        else:
            self._scroll_to_bottom()
            try:
                response = await ask_provider(self.config, value)
            except ProviderChatError as exc:
                await self._mount_event(TimelineItem("error", str(exc)))
                self._scroll_to_bottom()
                return

            await self._mount_event(TimelineItem("assistant", escape(response.content)))
        self._scroll_to_bottom()

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
        name_matches = [
            command for command in SLASH_COMMANDS if command.name[1:].lower().startswith(query)
        ]
        description_matches = [
            command
            for command in SLASH_COMMANDS
            if command not in name_matches and query in command.description.lower()
        ]
        matches = name_matches + description_matches

        if not matches:
            self._hide_command_palette()
            return

        self._command_matches = matches
        self._selected_command_index = min(self._selected_command_index, max(len(matches) - 1, 0))
        palette.display = True
        palette.update_matches(matches, self._selected_command_index)

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
        self._selected_command_index = (
            self._selected_command_index + delta
        ) % len(self._command_matches)
        self.query_one("#palette", CommandPalette).update_matches(
            self._command_matches,
            self._selected_command_index,
        )

    def _should_complete_command(self, value: str) -> bool:
        if not self._palette_is_visible() or not self._command_matches:
            return False
        known_values = {command.name for command in SLASH_COMMANDS}
        known_values.update(command.completion for command in SLASH_COMMANDS)
        return value not in known_values

    def _complete_selected_command(self) -> None:
        if not self._command_matches:
            return

        command = self._command_matches[self._selected_command_index]
        composer = self.query_one("#composer", Input)
        # Bare (no-argument) commands complete with a trailing space, ready to
        # submit; templated ones (e.g. "/ros pub <topic> <payload>") complete
        # as-is, with the cursor placed at the first "<placeholder>" so the
        # user can type straight over it.
        completion = command.completion
        composer.value = completion if completion != command.name else f"{completion} "
        placeholder_index = composer.value.find("<")
        composer.cursor_position = (
            placeholder_index if placeholder_index != -1 else len(composer.value)
        )
        self._hide_command_palette()
        composer.focus()

    # Palette completions like "/model <name|number>" insert their usage
    # placeholder into the composer; a submitted placeholder must never reach a
    # handler (it once saved the literal "<name|number>" as a model binding).
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
                    f"Unknown command [bold #f2ede1]{command}[/]. Try [bold #f2ede1]/help[/].",
                )
            )
            return

        try:
            await handler(handler_arg)
        except Exception as exc:
            await self._mount_event(TimelineItem("error", f"{command} failed: {exc}"))

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
            }
            return loc_handlers.get(subcommand), rest.strip()

        handlers = {
            "/help": self._show_help,
            "/status": self._show_status,
            "/doctor": self._show_doctor,
            "/providers": self._show_providers,
            "/models": self._show_models,
            "/model": self._show_model,
            "/provider": self._show_provider,
            "/permissions": self._show_permissions,
            "/config": self._show_config,
            "/plan": self._show_plan,
            "/run": self._show_run,
            "/why": self._show_why,
            "/review": self._show_review,
            "/abort": self._show_abort,
            "/route": self._show_route,
            "/drive": self._show_drive,
            "/mission": self._show_mission,
            "/vision": self._show_vision,
            "/shell": self._show_shell,
            "/quit": self._quit_from_command,
            "/exit": self._quit_from_command,
        }
        return handlers.get(command), arg

    async def on_unmount(self) -> None:
        if self._bridge is not None:
            await self._bridge.stop()

    def _refresh_model_display(self) -> None:
        profile = self._active_profile()
        try:
            self.query_one("#statusbar", Static).update(self._status_line())
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

        for tool_call in run.tool_calls:
            if tool_call.tool_call_id not in self._rendered_tool_call_ids:
                self._rendered_tool_call_ids.add(tool_call.tool_call_id)
                await self._mount_event(ToolBlock(tool_call))

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
                    # "Don't ask again": tools the user remembered this session
                    # are auto-approved without another card.
                    if approval.tool_name and approval.tool_name in self._auto_approved:
                        entry["decisions"][approval.tool_call_id] = True
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
                await self._mount_event(OutputPanel("Result", run.final_output))
            await self._mount_event(TimelineItem("success", "Done."))
        elif run.status == "failed":
            if run.error:
                await self._mount_event(ErrorBlock(run.error))
            else:
                await self._mount_event(TimelineItem("error", "Run failed."))
        elif run.status == "blocked":
            await self._mount_event(TimelineItem("warn", "Run blocked."))

        self._scroll_to_bottom()

    async def _show_plan(self, arg: str) -> None:
        if not arg:
            await self._mount_event(TimelineItem("warn", "Usage: /plan <task description>"))
            return

        ctx = self._new_run_context(arg)
        self._last_plan_ctx = ctx
        self._scroll_to_bottom()
        run = await run_plan(ctx, arg)
        await self._render_run_update(ctx, run)

    async def _show_run(self, arg: str) -> None:
        if not arg:
            await self._mount_event(TimelineItem("warn", "Usage: /run <task description>"))
            return

        ctx = self._new_run_context(arg)
        agent = build_run_agent(self.config)
        self._scroll_to_bottom()
        run = await orchestrator.start_run(agent, ctx, arg)
        await self._render_run_update(ctx, run, agent=agent)

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
        if "shell" in self._auto_approved:
            await self._execute_direct({"kind": "shell", "ctx": ctx, "command": command})
            return
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
        }
        await self._mount_event(ApprovalCard(approval))
        self._scroll_to_bottom()

    async def on_approval_card_decision(self, message: ApprovalCard.Decision) -> None:
        # Two approval sources share one card + message: deterministic slash
        # commands (/ros pub, /route, /shell) tracked in _pending_direct_approvals,
        # and agent-driven /run interruptions tracked in _pending_approvals.
        if message.tool_call_id in self._pending_direct_approvals:
            # Option 2 ("don't ask again") remembers this command kind so future
            # cards of the same kind are auto-approved for the session.
            if message.approved and message.remember:
                kind = self._pending_direct_approvals[message.tool_call_id]["kind"]
                self._auto_approved.add(kind)
                await self._mount_event(
                    TimelineItem("muted", f"Auto-approving '{kind}' for the rest of this session.")
                )
            await self._resolve_direct_approval(message.tool_call_id, message.approved)
            return

        run_id = self._find_run_id_for_call(message.tool_call_id)
        if run_id is not None:
            # Agent-flow "don't ask again": remember by tool_name so later
            # interruptions for the same tool auto-approve (see _render_run_update).
            if message.approved and message.remember:
                approval = self._approval_by_call_id(message.tool_call_id)
                if approval is not None and approval.tool_name:
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
            self.run_store.finish(ctx.run, status=RunStatus.BLOCKED)
            await self._mount_event(TimelineItem("warn", "Rejected. No action was taken."))
            self._scroll_to_bottom()
            return

        # Run the approved action as the active task so long executions (live
        # Nav2 goals, missions) show the working spinner and stop on Esc.
        if self._active_task is not None and not self._active_task.done():
            await self._execute_direct(pending)  # already inside a task
            return
        self._active_task = asyncio.create_task(self._run_direct_task(pending))

    async def _run_direct_task(self, pending: dict) -> None:
        self._start_spinner("Executing")
        ctx: JenAIRunContext = pending["ctx"]
        try:
            await self._execute_direct(pending)
        except asyncio.CancelledError:
            # Esc: nav_live already cancelled the Nav2 goal; close out the run.
            if ctx.run.status not in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.BLOCKED):
                self.run_store.finish(
                    ctx.run, status=RunStatus.BLOCKED, final_output="interrupted"
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
            self._stop_spinner()
            self._active_task = None

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
            if ctx.run.status not in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.BLOCKED):
                self.run_store.finish(ctx.run, status=RunStatus.FAILED, final_output=str(exc))
            await self._mount_event(TimelineItem("error", f"Action failed: {exc}"))
            self._scroll_to_bottom()

    async def _run_direct(self, pending: dict) -> None:
        ctx: JenAIRunContext = pending["ctx"]
        self.run_store.set_status(ctx.run, RunStatus.RUNNING)
        if pending["kind"] in ("ros_pub", "drive"):
            if pending["kind"] == "drive":
                output = await ros_drive(
                    pending["topic"],
                    pending["message_type"],
                    pending["payload"],
                    duration_s=pending["duration"],
                )
            else:
                output = await ros_pub_execute(
                    pending["topic"], pending["message_type"], pending["payload"]
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
                await self._mount_event(
                    TimelineItem(
                        "success" if result.status == "succeeded" else "warn",
                        f"{result.kind} {result.target}: {result.status} — {result.detail}",
                    )
                )
                self._scroll_to_bottom()

            report = await run_mission(
                self.config,
                pending["locations"],
                pending["steps"],
                on_step=_on_step,
                navigate=self._execute_route_action,
            )
            ok = all(r.status == "succeeded" for r in report.results)
            self.run_store.finish(
                ctx.run,
                status=RunStatus.COMPLETED if ok else RunStatus.BLOCKED,
                final_output=report.summary,
            )
            await self._mount_event(OutputPanel("Mission report", report.summary))
        elif pending["kind"] == "shell":
            shell_output = await run_shell(pending["command"])
            ok = shell_output.exit_code == 0
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
        run = await orchestrator.resume_with_approvals(
            pending["agent"], pending["ctx"], pending["decisions"]
        )
        await self._render_run_update(pending["ctx"], run, agent=pending["agent"])

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

    def _status_line(self) -> str:
        profile = self._active_profile()
        provider = profile.provider if profile else "no-provider"
        model = self._chat_model_display()
        return f"[#9c9689]⏵⏵ {provider} · {model} · {_short_cwd()}[/]"

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
                f"[#d97757]{frame}[/] {self._spinner_label}… "
                f"[#9c9689]({elapsed}s · esc to interrupt)[/]"
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


