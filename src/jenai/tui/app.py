from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import NamedTuple

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.css.query import NoMatches
from textual.widgets import Input, Static

from jenai import __version__
from jenai.adapters.locations import (
    LocationNotFoundError,
    LocationsFileError,
    ensure_locations_file,
    find_location,
    load_locations,
)
from jenai.agent import build_run_agent, orchestrator, review_plan, run_plan
from jenai.agent.context import JenAIRunContext
from jenai.config.models import AppConfig, ProviderProfile
from jenai.doctor import run_doctor
from jenai.providers import ProviderChatError, ask_provider, chat_model_name, resolve_model_alias
from jenai.schemas import (
    ApprovalRequest,
    ApprovalStatus,
    DoctorCheckItem,
    DoctorResult,
    DoctorStatus,
    EffectScope,
    Location,
    RiskLevel,
    RunRecord,
    RunStatus,
    ToolCallCategory,
    ToolCallRecord,
)
from jenai.state import InputHistory, RunStore, create_session
from jenai.tools.registry import TOOL_RISK_REGISTRY
from jenai.tools.ros2_core import (
    ros_drive,
    ros_echo,
    ros_pub_execute,
    ros_pub_validate,
    ros_schema,
    ros_topic_info,
    ros_topics,
)
from jenai.tools.route_core import route_execute, route_preview
from jenai.tools.shell_core import assess_command, preview_command, run_shell
from jenai.tools.vision_core import VisionError, analyze_image
from jenai.tui.help_content import build_help_output
from jenai.tui.widgets import ApprovalCard, ErrorBlock, PlanBlock, ToolBlock

APPROVAL_REQUIRED_COMMANDS = ("/ros pub", "/route", "/shell", "/run")

ACCENT = "#dd9460"
ACCENT_DARK = "#c8765a"
MUTED = "#7c8893"
GREEN = "#6fbf73"
ERROR = "#e06c75"
BLUE = "#8e9bf0"


class SlashCommand(NamedTuple):
    name: str
    description: str
    template: str = ""

    @property
    def completion(self) -> str:
        return self.template or self.name


SLASH_COMMANDS = [
    SlashCommand("/help", "Show available JenAI commands"),
    SlashCommand("/status", "Show provider, model, config, and doctor state"),
    SlashCommand("/doctor", "Run setup and environment checks"),
    SlashCommand("/providers", "List configured provider profiles"),
    SlashCommand("/models", "Show model bindings"),
    SlashCommand("/provider", "Show the active provider profile"),
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
        "/route", "Resolve and send a navigation route (needs approval)", "/route <text>"
    ),
    SlashCommand("/loc list", "List known locations"),
    SlashCommand("/loc show", "Show a location's details", "/loc show <name>"),
    SlashCommand("/vision image", "Analyze a local image with the VLM", "/vision image <path>"),
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


class JenAITuiApp(App[None]):
    CSS = """
    Screen {
        background: #1e242b;
        color: #c8cdd2;
    }

    #stage {
        width: 100%;
        height: 100%;
        padding: 0;
        background: #1e242b;
    }

    #window {
        width: 100%;
        height: 100%;
        background: #1e242b;
    }

    #body {
        height: 1fr;
        padding: 1 3 0 3;
        scrollbar-size-vertical: 1;
        scrollbar-background: #1e242b;
        scrollbar-color: #2c333c;
        scrollbar-color-hover: #3a424c;
        scrollbar-color-active: #3a424c;
    }

    #welcome {
        border: round #c8765a;
        padding: 1 3 2 3;
        margin-bottom: 1;
        height: auto;
    }

    .welcome-row {
        height: auto;
    }

    #welcome-left {
        width: 43%;
        height: auto;
        padding: 0 2 0 0;
        content-align: center middle;
    }

    #welcome-right {
        width: 1fr;
        height: auto;
        padding: 0 0 0 3;
        border-left: solid #c8765a;
    }

    .heading {
        color: #e8ecef;
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
    }

    #pixel-mark {
        color: #dd9460;
        text-align: center;
        margin: 1 0;
    }

    .meta {
        color: #7c8893;
        text-align: center;
    }

    .section-title {
        color: #dd9460;
        text-style: bold;
        margin-bottom: 1;
    }

    .rule {
        color: #70483c;
        margin: 1 0;
    }

    .prompt-line {
        height: auto;
        margin: 1 0 0 0;
        color: #c8cdd2;
    }

    #events {
        height: auto;
        margin-bottom: 1;
    }

    .bullet-line {
        height: auto;
        margin-bottom: 1;
        color: #c8cdd2;
    }

    .approval-card {
        background: #262b31;
        border-left: thick #b5794f;
        padding: 0 2;
        margin-bottom: 1;
        height: auto;
    }

    #composer-wrap {
        height: auto;
        padding: 1 3 1 3;
        background: #1e242b;
    }

    #palette {
        height: auto;
        max-height: 16;
        margin-bottom: 1;
        padding: 1 2;
        background: #0d0f12;
        border: round #34302a;
    }

    #composer {
        height: 3;
        background: #232a33;
        color: #e8ecef;
        border: round #3a3630;
        padding: 0 1;
    }

    #composer:focus {
        border: round #dd9460;
    }

    #spinner {
        height: auto;
        color: #dd9460;
        margin-bottom: 1;
        display: none;
    }

    #spinner.active {
        display: block;
    }

    #statusbar {
        height: 1;
        color: #7c8893;
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
            # Esc interrupt (or app shutdown). Only report if the UI is still
            # mounted — during quit the widgets are already gone.
            if self.is_running:
                try:
                    await self._mount_event(TimelineItem("warn", "Interrupted."))
                    self._scroll_to_bottom()
                except NoMatches:
                    pass
        finally:
            self._stop_spinner()
            self._active_task = None

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
            await self._mount_event(TimelineItem("success", "Session output cleared."))
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

    async def _handle_command(self, value: str) -> None:
        command, _, arg = value.partition(" ")
        arg = arg.strip()

        handler, handler_arg = self._resolve_command_handler(command, arg)
        if handler is None:
            await self._mount_event(
                TimelineItem(
                    "warn",
                    f"Unknown command [bold #e8ecef]{command}[/]. Try [bold #e8ecef]/help[/].",
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
                "show": self._show_loc_show,
            }
            return loc_handlers.get(subcommand), rest.strip()

        handlers = {
            "/help": self._show_help,
            "/status": self._show_status,
            "/doctor": self._show_doctor,
            "/providers": self._show_providers,
            "/models": self._show_models,
            "/model": self._show_models,
            "/provider": self._show_provider,
            "/permissions": self._show_permissions,
            "/config": self._show_config,
            "/plan": self._show_plan,
            "/run": self._show_run,
            "/why": self._show_why,
            "/review": self._show_review,
            "/abort": self._show_abort,
            "/route": self._show_route,
            "/vision": self._show_vision,
            "/shell": self._show_shell,
            "/quit": self._quit_from_command,
            "/exit": self._quit_from_command,
        }
        return handlers.get(command), arg

    async def _show_help(self, arg: str = "") -> None:
        help_output = build_help_output(arg or None)
        lines = [help_output.summary, ""]
        for group in help_output.command_groups:
            lines.append(f"[bold #dd9460]{group.name}[/]")
            lines.extend(f"  {cmd}" for cmd in group.commands)
            lines.append("")
        if help_output.examples:
            lines.append("[bold #dd9460]Examples[/]")
            lines.extend(f"  {example}" for example in help_output.examples)
            lines.append("")
        if help_output.keyboard_shortcuts:
            lines.append("[bold #dd9460]Keyboard[/]")
            lines.extend(f"  {s.key}  {s.action}" for s in help_output.keyboard_shortcuts)
        await self._mount_event(OutputPanel(help_output.title, "\n".join(lines).rstrip()))

    async def _show_status(self, _: str = "") -> None:
        profile = self._active_profile()
        status = "not checked"
        if self.doctor_result is not None:
            status = self.doctor_result.overall

        lines = [
            f"Version: [bold #e8ecef]{__version__}[/]",
            f"Config: [#7c8893]{self.config_path}[/]",
            f"Provider: {self._format_profile(profile)}",
            f"Chat model: [bold #e8ecef]{self._chat_model_display()}[/]",
            f"Doctor: {self._format_status(status)}",
            f"Route adapter: [bold #e8ecef]{self.config.route_adapter}[/]",
        ]
        await self._mount_event(OutputPanel("Status", "\n".join(lines)))

    async def _show_doctor(self, _: str = "") -> None:
        self.doctor_result = await asyncio.to_thread(run_doctor, self.config_path)
        self.query_one(WelcomePanel).update_doctor_result(self.doctor_result)
        summary = [
            f"Overall: {self._format_status(self.doctor_result.overall)}",
            "",
        ]
        summary.extend(format_doctor_item(item) for item in self.doctor_result.items)
        await self._mount_event(OutputPanel("Doctor", "\n".join(summary)))

    async def _show_providers(self, _: str = "") -> None:
        if not self.config.provider_profiles:
            await self._mount_event(TimelineItem("warn", "No provider profiles are configured."))
            return

        rows = []
        for name, profile in self.config.provider_profiles.items():
            active = "*" if name == self.config.active_provider else " "
            rows.append(
                f"{active} [bold #e8ecef]{name}[/] · {profile.provider} · "
                f"{profile.base_url or 'provider default'} · {profile.api_key_env or 'no key env'}"
            )
        await self._mount_event(OutputPanel("Provider profiles", "\n".join(rows)))

    async def _show_models(self, _: str = "") -> None:
        if self.config.model_bindings is None:
            await self._mount_event(TimelineItem("warn", "No model bindings are configured."))
            return

        rows = [
            f"{name}: [bold #e8ecef]{value}[/]"
            for name, value in self.config.model_bindings.model_dump().items()
        ]
        await self._mount_event(OutputPanel("Model bindings", "\n".join(rows)))

    async def _show_config(self, _: str = "") -> None:
        locations_path = self.config.resolved_locations_path(self.config_path)
        await self._mount_event(
            OutputPanel(
                "Config",
                "\n".join(
                    [
                        f"File: [#7c8893]{self.config_path}[/]",
                        f"Locations: [#7c8893]{locations_path}[/]",
                        f"Created by setup: [bold #e8ecef]{self.config.created_by_setup}[/]",
                    ]
                ),
            )
        )

    async def _show_provider(self, _: str = "") -> None:
        profile = self._active_profile()
        await self._mount_event(OutputPanel("Provider", self._format_profile(profile)))

    async def _show_permissions(self, _: str = "") -> None:
        lines = [f"[bold #e8ecef]{cmd}[/] requires approval" for cmd in APPROVAL_REQUIRED_COMMANDS]
        lines.append("")
        lines.append("Tool risk registry:")
        for name, info in sorted(TOOL_RISK_REGISTRY.items()):
            approval = "needs approval" if info.needs_approval else "no approval"
            lines.append(f"  {name}: risk={info.risk_level} scope={info.effect_scope} ({approval})")
        await self._mount_event(OutputPanel("Permissions", "\n".join(lines)))

    # -- Planning / running ------------------------------------------------

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
            lines.append(f"Current step: [bold #e8ecef]{active_step.title}[/]")
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

    async def _show_ros_topics(self, _: str = "") -> None:
        output = await ros_topics(self.config)
        if not output.topics:
            await self._mount_event(TimelineItem("warn", "No topics found."))
            return
        rows = [f"{item.name}  [#7c8893]({item.kind_hint})[/]" for item in output.topics]
        await self._mount_event(OutputPanel("ROS2 topics", "\n".join(rows)))

    async def _show_ros_topic_info(self, arg: str) -> None:
        if not arg:
            await self._mount_event(TimelineItem("warn", "Usage: /ros topic-info <topic>"))
            return

        output = await ros_topic_info(self.config, arg)
        if not output.message_type:
            await self._mount_event(TimelineItem("warn", output.summary))
            return

        lines = [
            f"Message type: [bold #e8ecef]{output.message_type}[/]",
            f"Publishers ({output.publisher_count}): {', '.join(output.publishers) or '—'}",
            f"Subscribers ({output.subscriber_count}): {', '.join(output.subscribers) or '—'}",
        ]
        await self._mount_event(OutputPanel(f"Topic info: {arg}", "\n".join(lines)))

    async def _show_ros_echo(self, arg: str) -> None:
        parts = arg.split()
        if not parts:
            await self._mount_event(TimelineItem("warn", "Usage: /ros echo <topic> [count]"))
            return
        topic = parts[0]
        limit = 1
        if len(parts) > 1 and parts[1].isdigit():
            limit = max(1, int(parts[1]))

        output = await ros_echo(self.config, topic, limit=limit)
        if not output.messages:
            await self._mount_event(TimelineItem("warn", output.summary))
            return
        rendered = "\n\n".join(
            json.dumps(msg, ensure_ascii=False, indent=2) for msg in output.messages
        )
        await self._mount_event(OutputPanel(f"Echo: {topic}", rendered))

    async def _show_ros_schema(self, arg: str) -> None:
        if not arg:
            await self._mount_event(TimelineItem("warn", "Usage: /ros schema <topic>"))
            return

        output = await ros_schema(self.config, arg)
        lines = [f"Message type: [bold #e8ecef]{output.message_type}[/]", ""]
        for field in output.field_summary:
            lines.append(
                f"[bold #e8ecef]{field.field_name}[/] ({field.field_type}): {field.description}"
            )
        await self._mount_event(OutputPanel(f"Schema: {arg}", "\n".join(lines)))

    async def _show_ros_pub(self, arg: str) -> None:
        parts = arg.split(maxsplit=1)
        if len(parts) != 2:
            await self._mount_event(TimelineItem("warn", "Usage: /ros pub <topic> <json payload>"))
            return

        topic, payload_json = parts
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            await self._mount_event(TimelineItem("error", f"Invalid JSON payload: {exc}"))
            return

        validation = await ros_pub_validate(topic, payload)
        if not validation.ok:
            message = validation.error.message if validation.error else "Validation failed."
            await self._mount_event(TimelineItem("error", message))
            return

        ctx = self._new_run_context(f"/ros pub {arg}")
        tool_call = ToolCallRecord(
            tool_name="ros_pub_execute_tool",
            category=ToolCallCategory.ROS2,
            input_summary=f"publish to {topic}",
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
        )
        self.run_store.add_tool_call(ctx.run, tool_call)
        if "ros_pub" in self._auto_approved:
            await self._execute_direct(
                {
                    "kind": "ros_pub",
                    "ctx": ctx,
                    "topic": topic,
                    "message_type": validation.message_type,
                    "payload": payload,
                }
            )
            return
        approval = ApprovalRequest(
            run_id=ctx.run.run_id,
            tool_call_id=tool_call.tool_call_id,
            title=f"Publish to {topic}",
            summary=f"Send a {validation.message_type} message to {topic}.",
            raw_action=f'ros2 topic pub --once {topic} {validation.message_type} "{payload}"',
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
            justification="Requested via /ros pub.",
        )
        self.run_store.add_interruption(ctx.run, approval)
        self.run_store.set_status(ctx.run, RunStatus.AWAITING_APPROVAL)

        self._pending_direct_approvals[approval.tool_call_id] = {
            "kind": "ros_pub",
            "ctx": ctx,
            "topic": topic,
            "message_type": validation.message_type,
            "payload": payload,
        }
        await self._mount_event(ApprovalCard(approval))
        self._scroll_to_bottom()

    async def _show_ros_drive(self, arg: str) -> None:
        # /ros drive <topic> <json payload> [seconds]
        parts = arg.split()
        if len(parts) < 2:
            await self._mount_event(
                TimelineItem("warn", "Usage: /ros drive <topic> <json payload> [seconds]")
            )
            return
        duration = 1.0
        if len(parts) >= 3 and _is_number(parts[-1]):
            duration = float(parts[-1])
            payload_json = " ".join(parts[1:-1])
        else:
            payload_json = " ".join(parts[1:])
        topic = parts[0]
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            await self._mount_event(TimelineItem("error", f"Invalid JSON payload: {exc}"))
            return

        validation = await ros_pub_validate(topic, payload)
        if not validation.ok:
            message = validation.error.message if validation.error else "Validation failed."
            await self._mount_event(TimelineItem("error", message))
            return

        ctx = self._new_run_context(f"/ros drive {arg}")
        tool_call = ToolCallRecord(
            tool_name="ros_drive_execute_tool",
            category=ToolCallCategory.ROS2,
            input_summary=f"drive {topic} for {duration}s",
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
        )
        self.run_store.add_tool_call(ctx.run, tool_call)
        pending = {
            "kind": "drive",
            "ctx": ctx,
            "topic": topic,
            "message_type": validation.message_type,
            "payload": payload,
            "duration": duration,
        }
        if "drive" in self._auto_approved:
            await self._execute_direct(pending)
            return
        approval = ApprovalRequest(
            run_id=ctx.run.run_id,
            tool_call_id=tool_call.tool_call_id,
            tool_name="ros_drive_execute_tool",
            title=f"Drive {topic} for {duration}s",
            summary=f"Publish a {validation.message_type} to {topic} for {duration}s, then stop.",
            raw_action=f"ros2 topic pub --rate 10 {topic} … for {duration}s, then zero-stop",
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
            justification="Requested via /ros drive.",
        )
        self.run_store.add_interruption(ctx.run, approval)
        self.run_store.set_status(ctx.run, RunStatus.AWAITING_APPROVAL)
        self._pending_direct_approvals[approval.tool_call_id] = pending
        await self._mount_event(ApprovalCard(approval))
        self._scroll_to_bottom()

    # -- Route / locations ----------------------------------------------------

    def _locations_path(self) -> Path | None:
        return self.config.resolved_locations_path(self.config_path)

    def _load_locations(self) -> list[Location]:
        path = self._locations_path()
        if path is None:
            return []
        try:
            ensure_locations_file(path)
            return load_locations(path)
        except LocationsFileError:
            return []

    async def _show_route(self, arg: str) -> None:
        if not arg:
            await self._mount_event(
                TimelineItem("warn", "Usage: /route <natural language request>")
            )
            return

        locations = self._load_locations()
        output = await route_preview(self.config, locations, arg)
        if not output.outgoing_action:
            await self._mount_event(TimelineItem("warn", output.route_preview))
            return

        ctx = self._new_run_context(f"/route {arg}")
        tool_call = ToolCallRecord(
            tool_name="route_execute_tool",
            category=ToolCallCategory.ROUTE,
            input_summary=output.route_preview,
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
        )
        self.run_store.add_tool_call(ctx.run, tool_call)
        if "route" in self._auto_approved:
            await self._execute_direct(
                {"kind": "route", "ctx": ctx, "outgoing_action": output.outgoing_action}
            )
            return
        approval = ApprovalRequest(
            run_id=ctx.run.run_id,
            tool_call_id=tool_call.tool_call_id,
            title="Send navigation route",
            summary=output.route_preview,
            raw_action=str(output.outgoing_action),
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
            justification="Requested via /route.",
        )
        self.run_store.add_interruption(ctx.run, approval)
        self.run_store.set_status(ctx.run, RunStatus.AWAITING_APPROVAL)

        self._pending_direct_approvals[approval.tool_call_id] = {
            "kind": "route",
            "ctx": ctx,
            "outgoing_action": output.outgoing_action,
        }
        await self._mount_event(ApprovalCard(approval))
        self._scroll_to_bottom()

    async def _show_vision(self, arg: str) -> None:
        # Accept both "/vision image <path>" and "/vision <path>".
        parts = arg.split(maxsplit=1)
        if parts and parts[0] == "image":
            path = parts[1].strip() if len(parts) > 1 else ""
        else:
            path = arg.strip()
        if not path:
            await self._mount_event(TimelineItem("warn", "Usage: /vision image <path>"))
            return

        try:
            output = await analyze_image(self.config, path)
        except VisionError as exc:
            await self._mount_event(TimelineItem("error", str(exc)))
            return

        lines = [output.summary]
        if output.objects:
            lines.append(f"[bold #e8ecef]Objects:[/] {', '.join(output.objects)}")
        if output.anomalies:
            lines.append(f"[bold #e8ecef]Anomalies:[/] {', '.join(output.anomalies)}")
        if output.next_action_suggestions:
            lines.append(
                "[bold #e8ecef]Suggested next:[/] " + "; ".join(output.next_action_suggestions)
            )
        await self._mount_event(OutputPanel(f"Vision: {output.source}", "\n".join(lines)))

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

    async def _show_loc_list(self, _: str = "") -> None:
        locations = self._load_locations()
        if not locations:
            await self._mount_event(
                TimelineItem("warn", "No locations configured. Add entries to locations.toml.")
            )
            return
        rows = [
            f"[bold #e8ecef]{loc.name}[/] · {', '.join(loc.aliases) or 'no aliases'}"
            for loc in locations
        ]
        await self._mount_event(OutputPanel("Locations", "\n".join(rows)))

    async def _show_loc_show(self, arg: str) -> None:
        if not arg:
            await self._mount_event(TimelineItem("warn", "Usage: /loc show <name>"))
            return

        locations = self._load_locations()
        try:
            location = find_location(locations, arg)
        except LocationNotFoundError as exc:
            if exc.candidates:
                names = ", ".join(loc.name for loc in exc.candidates)
                await self._mount_event(
                    TimelineItem("warn", f"Location '{arg}' not found. Did you mean: {names}?")
                )
            else:
                await self._mount_event(TimelineItem("warn", f"Location '{arg}' not found."))
            return

        lines = [
            f"Name: [bold #e8ecef]{location.name}[/]",
            f"Aliases: {', '.join(location.aliases) or '(none)'}",
            f"Frame: {location.frame_id}",
            f"Pose: x={location.pose.x}, y={location.pose.y}, yaw={location.pose.yaw}",
            f"Tags: {', '.join(location.tags) or '(none)'}",
        ]
        if location.description:
            lines.append(f"Description: {location.description}")
        await self._mount_event(OutputPanel(f"Location: {location.name}", "\n".join(lines)))

    # -- Approval decisions ---------------------------------------------------

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

        await self._execute_direct(pending)

    async def _execute_direct(self, pending: dict) -> None:
        """Run an approved (or auto-approved) direct command and render its result."""
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
            output = await route_execute(self.config, pending["outgoing_action"])
            self.run_store.finish(
                ctx.run, status=RunStatus.COMPLETED, final_output=output.route_preview
            )
            await self._mount_event(TimelineItem("success", output.route_preview))
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
                body += f"\n[bold #e8a1a1]stderr:[/]\n{shell_output.stderr_summary}"
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
        return f"[#7c8893]⏵⏵ {provider} · {model} · {_short_cwd()}[/]"

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
                f"[#dd9460]{frame}[/] {self._spinner_label}… "
                f"[#7c8893]({elapsed}s · esc to interrupt)[/]"
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
            return "[#e06c75]missing[/]"
        return f"[bold #e8ecef]{profile.name}[/] · {profile.provider}"

    def _format_status(self, value: str) -> str:
        return f"[bold {status_color(value)}]{value}[/]"


class WelcomePanel(Container):
    """Orange hero card shown at the top of the transcript."""

    def __init__(
        self,
        *,
        version: str,
        provider_name: str,
        provider_kind: str,
        model_name: str,
        config_path: Path,
        doctor_result: DoctorResult | None,
    ) -> None:
        super().__init__(id="welcome")
        self.version = version
        self.provider_name = provider_name
        self.provider_kind = provider_kind
        self.model_name = model_name
        self.config_path = config_path
        self.doctor_result = doctor_result

    def compose(self) -> ComposeResult:
        self.border_title = f"JenAI v{self.version}"
        with Horizontal(classes="welcome-row"):
            with Vertical(id="welcome-left"):
                yield Static("Robot workflow console", classes="heading")
                yield Static(pixel_mark(), id="pixel-mark")
                yield Static(self._provider_meta(), classes="meta")
            with Vertical(id="welcome-right"):
                yield Static("Ready for ROS2 work", classes="section-title")
                yield Static("Plan, inspect, and route robot tasks from one terminal.")
                yield Static("────────────────────────────────", classes="rule")
                yield Static("Current workspace", classes="section-title")
                yield Static(str(Path.cwd()), classes="meta")
                yield Static("Doctor status", classes="section-title")
                yield Static(self._doctor_summary(), id="welcome-doctor-status")

    def update_doctor_result(self, doctor_result: DoctorResult | None) -> None:
        self.doctor_result = doctor_result
        self.query_one("#welcome-doctor-status", Static).update(self._doctor_summary())

    def _provider_meta(self) -> str:
        return (
            f"{self.model_name} · {self.provider_kind}\n"
            f"{self.provider_name} · {self.config_path.parent}"
        )

    def _doctor_summary(self) -> Text:
        if self.doctor_result is None:
            return Text("Not checked", style=MUTED)

        text = Text()
        status = DoctorStatus(self.doctor_result.overall)
        text.append(status.value, style=f"bold {status_color(status)}")

        fails = sum(item.status == DoctorStatus.FAIL for item in self.doctor_result.items)
        warns = sum(item.status == DoctorStatus.WARN for item in self.doctor_result.items)
        text.append(f" · {fails} fail · {warns} warn", style=MUTED)
        return text


# Claude Code-style markers: a filled bullet for each transcript entry and an
# elbow connector for the indented result/detail lines beneath it.
BULLET = "⏺"
ELBOW = "⎿"

_MARKER_COLOR = {
    "command": BLUE,
    "success": GREEN,
    "warn": ACCENT,
    "error": ERROR,
    "muted": MUTED,
    "assistant": ACCENT,
}


def _bullet_markup(variant: str, body: str) -> str:
    color = _MARKER_COLOR.get(variant, ACCENT)
    return f"[{color}]{BULLET}[/] {body}"


def _detail_markup(lines: list[str]) -> str:
    """Render detail lines under a bullet as Claude Code elbow-indented text."""
    out: list[str] = []
    for i, line in enumerate(lines):
        prefix = f"  [{MUTED}]{ELBOW}[/] " if i == 0 else "     "
        out.append(f"{prefix}[{MUTED}]{line}[/]")
    return "\n".join(out)


class PromptPill(Static):
    """Echo of the user's submitted line, shown as a muted `>` prompt."""

    def __init__(self, text: str) -> None:
        super().__init__(f"[{MUTED}]>[/] [#c8cdd2]{text}[/]", classes="prompt-line")


class TimelineItem(Static):
    """A single Claude Code-style bullet line (⏺ marker + body markup)."""

    def __init__(self, variant: str, body: str) -> None:
        super().__init__(_bullet_markup(variant, body), classes="bullet-line")
        self.variant = variant
        self.body = body


class OutputPanel(Static):
    """A bullet with a title line and elbow-indented body lines (no box)."""

    def __init__(self, title: str, body: str, *, variant: str = "assistant") -> None:
        detail = _detail_markup(body.split("\n")) if body else ""
        markup = _bullet_markup(variant, f"[bold #e8ecef]{title}[/]")
        if detail:
            markup = f"{markup}\n{detail}"
        super().__init__(markup, classes="bullet-line")
        self.title = title
        self.body = body


class CommandPalette(Static):
    # Rows shown at once; the window scrolls to follow the selection so every
    # matching command is reachable without a hard cap.
    WINDOW = 12

    def update_matches(
        self,
        matches: list[SlashCommand],
        selected_index: int,
    ) -> None:
        if not matches:
            self.update("[#7c8893]No matching commands[/]")
            return

        total = len(matches)
        # Centre the window on the selection, then clamp so it never runs past
        # either end of the list (keeps the selected row visible while scrolling).
        if total <= self.WINDOW:
            start = 0
        else:
            start = min(max(selected_index - self.WINDOW // 2, 0), total - self.WINDOW)
        end = min(start + self.WINDOW, total)

        text = Text()
        text.append(f"Commands  ({selected_index + 1}/{total})\n", style=f"bold {ACCENT}")
        if start > 0:
            text.append(f"  ↑ {start} more\n", style=MUTED)
        for index in range(start, end):
            command = matches[index]
            selected = index == selected_index
            arrow_style = GREEN if selected else MUTED
            line_style = "bold #e8ecef" if selected else "#c8cdd2"
            text.append("❯ " if selected else "  ", style=arrow_style)
            text.append(command.name.ljust(16), style=line_style)
            text.append(command.description, style=MUTED)
            text.append("\n")
        if end < total:
            text.append(f"  ↓ {total - end} more", style=MUTED)
        text.rstrip()
        self.update(text)


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _short_cwd() -> str:
    """Home-relative, abbreviated cwd for the status line (e.g. ~/JenAI)."""
    cwd = Path.cwd()
    try:
        return "~/" + str(cwd.relative_to(Path.home()))
    except ValueError:
        return str(cwd)



def pixel_mark() -> Text:
    colors = {
        "body": "#d98c69",
        "belly": "#e8a987",
        "dark": "#ad6248",
        "black": "#34241d",
        "white": "#fdf5ef",
        "cheek": "#e89a9a",
        "collar": "#5fb1c0",
        "tag": "#f0c84e",
    }
    cells: dict[tuple[int, int], str] = {}

    def fill(x0: int, y0: int, x1: int, y1: int, color: str) -> None:
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                cells[(x, y)] = color

    def put(x: int, y: int, color: str) -> None:
        cells[(x, y)] = color

    def delete(x: int, y: int) -> None:
        cells.pop((x, y), None)

    fill(9, 2, 11, 9, colors["dark"])
    put(10, 10, colors["dark"])
    fill(11, 1, 18, 7, colors["body"])
    delete(11, 1)
    delete(18, 1)
    fill(16, 5, 20, 7, colors["body"])
    delete(20, 7)
    put(20, 5, colors["black"])
    put(20, 6, colors["black"])
    put(19, 6, colors["black"])
    put(18, 7, colors["black"])
    fill(14, 3, 15, 4, colors["black"])
    put(15, 3, colors["white"])
    put(17, 6, colors["cheek"])
    fill(-1, 7, 13, 10, colors["body"])
    delete(-1, 7)
    fill(0, 10, 12, 10, colors["belly"])
    put(-2, 6, colors["body"])
    put(-3, 5, colors["body"])
    put(-3, 4, colors["body"])
    put(-2, 4, colors["body"])
    fill(0, 11, 1, 13, colors["body"])
    fill(3, 11, 4, 13, colors["body"])
    fill(10, 11, 11, 13, colors["body"])
    fill(13, 11, 14, 13, colors["body"])
    # Collar/tag is drawn last: the body fills above cover this region.
    fill(11, 7, 12, 9, colors["collar"])
    put(12, 10, colors["tag"])

    min_x = min(x for x, _ in cells)
    max_x = max(x for x, _ in cells)
    min_y = min(y for _, y in cells)
    max_y = max(y for _, y in cells)

    text = Text()
    for y in range(min_y, max_y + 1, 2):
        for x in range(min_x, max_x + 1):
            top = cells.get((x, y))
            bottom = cells.get((x, y + 1))
            if top and bottom:
                text.append("█" if top == bottom else "▀", style=f"{top} on {bottom}")
            elif top:
                text.append("▀", style=top)
            elif bottom:
                text.append("▄", style=bottom)
            else:
                text.append(" ")
        if y + 1 < max_y:
            text.append("\n")
    return text

def status_color(status: DoctorStatus | str) -> str:
    try:
        status = DoctorStatus(status)
    except ValueError:
        return MUTED
    return {
        DoctorStatus.PASS: GREEN,
        DoctorStatus.WARN: ACCENT,
        DoctorStatus.FAIL: ERROR,
    }.get(status, MUTED)


def format_doctor_item(item: DoctorCheckItem) -> str:
    fix = f"\n[#7c8893]  fix:[/] {item.fix_suggestion}" if item.fix_suggestion else ""
    return (
        f"[bold {status_color(item.status)}]{item.status}[/] "
        f"{item.section}.{item.check_name}: {item.message}{fix}"
    )
