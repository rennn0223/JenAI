from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import NamedTuple

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
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
from jenai.tools.ros2_core import ros_pub_execute, ros_pub_validate, ros_schema, ros_topics
from jenai.tools.route_core import route_execute, route_preview
from jenai.tui.help_content import build_help_output
from jenai.tui.widgets import ApprovalCard, ErrorBlock, PlanBlock, ToolBlock

APPROVAL_REQUIRED_COMMANDS = ("/ros pub", "/route", "/run")

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
    SlashCommand("/ros schema", "Summarize a ROS2 topic's message schema", "/ros schema <topic>"),
    SlashCommand(
        "/ros pub", "Publish to a ROS2 topic (needs approval)", "/ros pub <topic> <payload>"
    ),
    SlashCommand(
        "/route", "Resolve and send a navigation route (needs approval)", "/route <text>"
    ),
    SlashCommand("/loc list", "List known locations"),
    SlashCommand("/loc show", "Show a location's details", "/loc show <name>"),
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

    #header {
        height: 3;
        padding: 0 3;
        background: #1e242b;
        border-bottom: heavy #303944;
        content-align: left middle;
    }

    #header-left {
        color: #e8ecef;
        text-style: bold;
        width: 1fr;
    }

    #header-right {
        color: #7c8893;
        text-align: right;
        width: auto;
    }

    #body {
        height: 1fr;
        padding: 1 3 0 3;
        scrollbar-background: #1e242b;
        scrollbar-color: #c8765a;
        scrollbar-color-hover: #dd9460;
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

    .hint {
        color: #7c8893;
        margin-bottom: 1;
        text-style: italic;
    }

    .prompt-pill {
        background: #2b323b;
        color: #e8ecef;
        border: round #3a424c;
        padding: 0 2;
        margin-bottom: 1;
        width: auto;
    }

    #events {
        height: auto;
        margin-bottom: 1;
    }

    .timeline-item {
        height: auto;
        margin-bottom: 1;
    }

    .dot {
        width: 3;
        text-style: bold;
        color: #e8ecef;
    }

    .dot-command {
        color: #8e9bf0;
    }

    .dot-success {
        color: #6fbf73;
    }

    .dot-warn {
        color: #dd9460;
    }

    .dot-error {
        color: #e06c75;
    }

    .dot-muted {
        color: #9aa3ad;
    }

    .timeline-copy {
        width: 1fr;
        color: #c8cdd2;
    }

    .output-panel {
        background: #232a33;
        border: round #3a424c;
        border-title-color: #8e9bf0;
        padding: 1 2;
        margin-bottom: 1;
        height: auto;
    }

    .panel-title {
        color: #8e9bf0;
        text-style: bold;
        margin-bottom: 1;
    }

    .panel-copy {
        color: #c8cdd2;
    }

    .approval-card {
        background: #2b241f;
        border: heavy #dd9460;
        padding: 1 2;
        margin-bottom: 1;
        height: auto;
    }

    .approval-title {
        color: #dd9460;
        text-style: bold;
        margin-bottom: 1;
    }

    .approval-summary {
        color: #e8ecef;
        margin-bottom: 1;
    }

    .approval-meta {
        color: #7c8893;
        margin-bottom: 1;
    }

    .approval-justification {
        color: #c8cdd2;
        margin-bottom: 1;
    }

    .approval-footer {
        color: #7c8893;
    }

    #composer-wrap {
        height: auto;
        padding: 1 3 1 3;
        background: #1e242b;
        border-top: solid #262d35;
    }

    #palette {
        height: auto;
        max-height: 10;
        margin-bottom: 1;
        padding: 1 2;
        background: #232a33;
        border: round #3a424c;
    }

    #composer {
        height: 3;
        background: #232a33;
        color: #e8ecef;
        border: round #3a424c;
        padding: 0 1;
    }

    #composer:focus {
        border: round #dd9460;
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

    def compose(self) -> ComposeResult:
        profile = self._active_profile()
        model_name = self._chat_model_display()

        with Container(id="stage"):
            with Vertical(id="window"):
                yield CliHeader()
                with ScrollableContainer(id="body"):
                    yield WelcomePanel(
                        version=__version__,
                        provider_name=profile.name if profile else "provider missing",
                        provider_kind=profile.provider if profile else "unknown",
                        model_name=model_name,
                        config_path=self.config_path,
                        doctor_result=self.doctor_result,
                    )
                    yield Static(
                        "/help · /plan · /run · /ros topics · /route · /loc list · /status",
                        classes="hint",
                    )
                    with Vertical(id="events"):
                        yield TimelineItem(
                            "success",
                            "JenAI shell is ready. Type [bold #e8ecef]/help[/] to see "
                            "available commands.",
                        )
                        yield TimelineItem(
                            "muted",
                            "Provider chat, planning/execution, ROS2 tools, and route/location "
                            "commands are all live. WebUI is still a later module.",
                        )
                with Container(id="composer-wrap"):
                    yield CommandPalette(id="palette")
                    yield Input(
                        placeholder="Ask JenAI or type /help",
                        id="composer",
                    )

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
        if value:
            await self.handle_user_text(value)

    def on_key(self, event) -> None:
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
        if value.startswith("/"):
            await self._handle_command(value)
        else:
            await self._mount_event(TimelineItem("muted", "Sending to provider..."))
            self._scroll_to_bottom()
            try:
                response = await ask_provider(self.config, value)
            except ProviderChatError as exc:
                await self._mount_event(TimelineItem("error", str(exc)))
                self._scroll_to_bottom()
                return

            await self._mount_event(
                TimelineItem(
                    "command",
                    f"[#7c8893]{response.provider} · {response.model}[/]\n{response.content}",
                )
            )
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
                "schema": self._show_ros_schema,
                "pub": self._show_ros_pub,
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
                self._pending_approvals[run.run_id] = {
                    "agent": agent,
                    "ctx": ctx,
                    "decisions": {},
                    "expected": {a.tool_call_id for a in pending_approvals},
                }
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
        await self._mount_event(TimelineItem("muted", "Planning..."))
        self._scroll_to_bottom()
        run = await run_plan(ctx, arg)
        await self._render_run_update(ctx, run)

    async def _show_run(self, arg: str) -> None:
        if not arg:
            await self._mount_event(TimelineItem("warn", "Usage: /run <task description>"))
            return

        ctx = self._new_run_context(arg)
        agent = build_run_agent(self.config)
        await self._mount_event(TimelineItem("muted", "Running..."))
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
        await self._mount_event(TimelineItem("muted", "Reviewing plan..."))
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
        if message.tool_call_id in self._pending_direct_approvals:
            await self._resolve_direct_approval(message.tool_call_id, message.approved)
            return

        run_id = self._find_run_id_for_call(message.tool_call_id)
        if run_id is not None:
            await self._resolve_agent_approval(run_id, message.tool_call_id, message.approved)

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

        self.run_store.set_status(ctx.run, RunStatus.RUNNING)
        if pending["kind"] == "ros_pub":
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
        self._scroll_to_bottom()

    async def _resolve_agent_approval(self, run_id: str, tool_call_id: str, approved: bool) -> None:
        pending = self._pending_approvals[run_id]
        pending["decisions"][tool_call_id] = approved
        await self._remove_approval_card(tool_call_id)

        if set(pending["decisions"]) < pending["expected"]:
            return

        del self._pending_approvals[run_id]
        await self._mount_event(TimelineItem("muted", "Resuming..."))
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


class CliHeader(Horizontal):
    def __init__(self) -> None:
        super().__init__(id="header")

    def compose(self) -> ComposeResult:
        yield Static(f"[#dd9460]JenAI[/] [#7c8893]v{__version__}[/]", id="header-left")
        yield Static("/help · ctrl+c quit", id="header-right")


class WelcomePanel(Container):
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


class PromptPill(Static):
    def __init__(self, text: str) -> None:
        super().__init__(f"[#6b7681]›[/] {text}", classes="prompt-pill")


class TimelineItem(Horizontal):
    def __init__(self, variant: str, body: str) -> None:
        super().__init__(classes="timeline-item")
        self.variant = variant
        self.body = body

    def compose(self) -> ComposeResult:
        dot_class = {
            "command": "dot dot-command",
            "success": "dot dot-success",
            "warn": "dot dot-warn",
            "error": "dot dot-error",
            "muted": "dot dot-muted",
        }.get(self.variant, "dot")
        yield Static("●", classes=dot_class)
        yield Static(self.body, classes="timeline-copy")


class OutputPanel(Vertical):
    def __init__(self, title: str, body: str) -> None:
        super().__init__(classes="output-panel")
        self.title = title
        self.body = body

    def compose(self) -> ComposeResult:
        yield Static(self.title, classes="panel-title")
        yield Static(self.body, classes="panel-copy")


class CommandPalette(Static):
    def update_matches(
        self,
        matches: list[SlashCommand],
        selected_index: int,
    ) -> None:
        if not matches:
            self.update("[#7c8893]No matching commands[/]")
            return

        text = Text()
        text.append("Commands\n", style=f"bold {ACCENT}")
        for index, command in enumerate(matches[:8]):
            selected = index == selected_index
            arrow_style = GREEN if selected else MUTED
            line_style = "bold #e8ecef" if selected else "#c8cdd2"
            text.append("› " if selected else "  ", style=arrow_style)
            text.append(command.name.ljust(12), style=line_style)
            text.append(command.description, style=MUTED)
            if index != min(len(matches), 8) - 1:
                text.append("\n")
        self.update(text)


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
