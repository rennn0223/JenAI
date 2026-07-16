"""Info/config slash-command handlers for the JenAI TUI.

Mixin for JenAITuiApp: read-only commands about the session — help, status,
doctor, provider/model management. Rendering primitives (self._mount_event,
OutputPanel…) and app state (self.config…) come from the host class.
"""

from __future__ import annotations

import asyncio

from jenai import __version__
from jenai.config import save_config
from jenai.doctor import run_doctor
from jenai.providers import ProviderChatError, chat_model_name, list_provider_models
from jenai.tools.registry import TOOL_RISK_REGISTRY
from jenai.tui.help_content import build_help_output
from jenai.tui.panels import (
    ACCENT,
    ERROR,
    GREEN,
    MUTED,
    OutputPanel,
    TimelineItem,
    WelcomePanel,
    format_doctor_item,
)

APPROVAL_REQUIRED_COMMANDS = ("/ros pub", "/route", "/shell", "/run")

MODEL_BINDING_NAMES = ("chat", "plan", "vision", "route", "default")


class InfoCommandsMixin:
    async def _show_help(self, arg: str = "") -> None:
        help_output = build_help_output(arg or None)
        lines = [help_output.summary, ""]
        for group in help_output.command_groups:
            lines.append(f"[bold #d97757]{group.name}[/]")
            lines.extend(f"  {cmd}" for cmd in group.commands)
            lines.append("")
        if help_output.examples:
            lines.append("[bold #d97757]Examples[/]")
            lines.extend(f"  {example}" for example in help_output.examples)
            lines.append("")
        if help_output.keyboard_shortcuts:
            lines.append("[bold #d97757]Keyboard[/]")
            lines.extend(f"  {s.key}  {s.action}" for s in help_output.keyboard_shortcuts)
        await self._mount_event(OutputPanel(help_output.title, "\n".join(lines).rstrip()))

    async def _show_status(self, _: str = "") -> None:
        profile = self._active_profile()
        status = "not checked"
        if self.doctor_result is not None:
            status = self.doctor_result.overall

        lines = [
            f"Version: [bold #f2ede1]{__version__}[/]",
            f"Config: [#9c9689]{self.config_path}[/]",
            f"Provider: {self._format_profile(profile)}",
            f"Chat model: [bold #f2ede1]{self._chat_model_display()}[/]",
            f"Doctor: {self._format_status(status)}",
            f"Route adapter: [bold #f2ede1]{self.config.route_adapter}[/]",
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
        for idx, (name, profile) in enumerate(self.config.provider_profiles.items(), start=1):
            active = f"[bold {GREEN}]→[/]" if name == self.config.active_provider else " "
            rows.append(
                f" {active} [{MUTED}]{idx}[/] [bold #f2ede1]{name}[/] · {profile.provider} · "
                f"{profile.base_url or 'provider default'} · {profile.api_key_env or 'no key env'}"
            )
        rows.append("")
        rows.append(f"[{MUTED}]Switch with /provider <name|number>.[/]")
        await self._mount_event(OutputPanel("Provider profiles", "\n".join(rows)))

    async def _show_models(self, _: str = "") -> None:
        if self.config.model_bindings is None:
            await self._mount_event(TimelineItem("warn", "No model bindings are configured."))
            return

        rows = [
            f"{name}: [bold #f2ede1]{value}[/]"
            for name, value in self.config.model_bindings.model_dump().items()
        ]
        rows.append("")
        rows.append(f"[{MUTED}]Switch with /model <name> — /model lists what the provider has.[/]")
        await self._mount_event(OutputPanel("Model bindings", "\n".join(rows)))

    async def _show_model(self, arg: str = "") -> None:
        if not arg:
            # Bare "/model" opens the arrow-navigable picker (Enter selects); the
            # numbered text list stays reachable as the fallback when the endpoint
            # can't be listed, so scripts/pipes still get a readable answer.
            await self._open_model_picker()
            return

        first, _, rest = arg.partition(" ")
        rest = rest.strip()
        if first in (*MODEL_BINDING_NAMES, "all") and rest:
            targets = MODEL_BINDING_NAMES if first == "all" else (first,)
            spec = rest
        else:
            # Bare "/model <name>" switches the conversation model: chat + the
            # default fallback, leaving specialised bindings (vision…) alone.
            targets = ("chat", "default")
            spec = arg

        model = await self._resolve_model_spec(spec)
        if model is None:
            return
        await self._apply_model_choice(model, targets)

    async def _open_model_picker(self) -> None:
        """Fetch the endpoint's models and mount the interactive picker; fall
        back to the numbered text list if the endpoint can't be queried."""
        try:
            self._available_models = await list_provider_models(self.config)
        except ProviderChatError:
            await self._list_provider_models()  # shows the honest error inline
            return
        if not self._available_models:
            await self._list_provider_models()
            return
        from jenai.tui.widgets import ModelPicker

        await self._mount_event(
            ModelPicker(self._available_models, self._chat_model_display())
        )

    async def _apply_model_choice(self, model: str, targets: tuple[str, ...]) -> None:
        """Write the chosen model into the given bindings and persist. Shared by
        `/model <name|number>` and the interactive picker so both save the same."""
        bindings = self.config.model_bindings
        if bindings is None:
            await self._mount_event(
                TimelineItem("warn", "No model bindings are configured — run setup first.")
            )
            return

        for name in targets:
            setattr(bindings, name, model)
        await asyncio.to_thread(save_config, self.config, self.config_path)
        self._refresh_model_display()
        scope = "all bindings" if targets is MODEL_BINDING_NAMES else " + ".join(targets)
        await self._mount_event(
            TimelineItem(
                "success",
                f"Model switched to [bold #f2ede1]{model}[/] ({scope}) · saved to config",
            )
        )

    async def _list_provider_models(self) -> None:
        lines: list[str] = []
        if self.config.model_bindings is not None:
            lines.append(f"[bold {ACCENT}]Bindings[/]")
            lines.extend(
                f"  {name}: [bold #f2ede1]{value}[/]"
                for name, value in self.config.model_bindings.model_dump().items()
            )
            lines.append("")

        profile = self._active_profile()
        endpoint = (profile.base_url if profile else None) or "provider default endpoint"
        try:
            self._available_models = await list_provider_models(self.config)
        except ProviderChatError as exc:
            lines.append(f"[{ERROR}]Could not list models from {endpoint}: {exc}[/]")
            await self._mount_event(OutputPanel("Model", "\n".join(lines).rstrip()))
            return

        current = {chat_model_name(self.config), self._chat_model_display()}
        lines.append(f"[bold {ACCENT}]Available[/] [{MUTED}]· {endpoint}[/]")
        if not self._available_models:
            lines.append(f"  [{MUTED}]The endpoint reported no models.[/]")
        for idx, model_id in enumerate(self._available_models, start=1):
            if model_id in current:
                lines.append(
                    f"  [bold {GREEN}]→[/] [{MUTED}]{idx:>2}[/] [bold #f2ede1]{model_id}[/]"
                )
            else:
                lines.append(f"    [{MUTED}]{idx:>2}[/] {model_id}")
        lines.append("")
        lines.append(
            f"[{MUTED}]Switch: /model <name|number> · one binding: /model vision <name> · "
            "everything: /model all <name>[/]"
        )
        await self._mount_event(OutputPanel("Model", "\n".join(lines)))

    async def _resolve_model_spec(self, spec: str) -> str | None:
        if spec.startswith("<"):
            await self._mount_event(
                TimelineItem(
                    "warn",
                    "That looks like the usage placeholder — give a real model, e.g. "
                    "[bold #f2ede1]/model qwen3.6:35b[/]. Run [bold #f2ede1]/model[/] to list.",
                )
            )
            return None
        if not spec.isdigit():
            return spec
        if not self._available_models:
            try:
                self._available_models = await list_provider_models(self.config)
            except ProviderChatError as exc:
                await self._mount_event(
                    TimelineItem("warn", f"Could not list provider models: {exc}")
                )
                return None
        index = int(spec)
        if not 1 <= index <= len(self._available_models):
            await self._mount_event(
                TimelineItem(
                    "warn",
                    f"No model #{index} — run /model to see the numbered list.",
                )
            )
            return None
        return self._available_models[index - 1]

    # -- ROS bridge (live nav / pose / camera) --------------------------------

    async def _show_provider(self, arg: str = "") -> None:
        if not arg:
            profile = self._active_profile()
            body = (
                f"{self._format_profile(profile)}\n\n"
                f"[{MUTED}]Switch with /provider <name|number> · profiles: /providers[/]"
            )
            await self._mount_event(OutputPanel("Provider", body))
            return

        profiles = self.config.provider_profiles
        name = arg.strip()
        if name.startswith("<"):
            await self._mount_event(
                TimelineItem(
                    "warn",
                    "That looks like the usage placeholder — give a profile name, "
                    "e.g. [bold #f2ede1]/provider local[/]. Run [bold #f2ede1]/providers[/] "
                    "to list.",
                )
            )
            return
        if name.isdigit():
            index = int(name)
            names = list(profiles)
            if not 1 <= index <= len(names):
                await self._mount_event(
                    TimelineItem("warn", f"No provider #{index} — run /providers to list.")
                )
                return
            name = names[index - 1]
        if name not in profiles:
            known = ", ".join(profiles) or "none configured"
            await self._mount_event(
                TimelineItem("warn", f"Unknown provider '{name}'. Profiles: {known}.")
            )
            return

        self.config.active_provider = name
        # Model ids are endpoint-specific; stale numbers must not leak across.
        self._available_models = []
        await asyncio.to_thread(save_config, self.config, self.config_path)
        self._refresh_model_display()
        profile = profiles[name]
        await self._mount_event(
            TimelineItem(
                "success",
                f"Provider switched to [bold #f2ede1]{name}[/] ({profile.provider} · "
                f"{profile.base_url or 'provider default'}) · saved to config\n"
                f"Run [bold #f2ede1]/model[/] to pick one of its models.",
            )
        )

    async def _show_permissions(self, _: str = "") -> None:
        lines = [f"[bold #f2ede1]{cmd}[/] requires approval" for cmd in APPROVAL_REQUIRED_COMMANDS]
        lines.append("")
        lines.append("Tool risk registry:")
        for name, info in sorted(TOOL_RISK_REGISTRY.items()):
            approval = "needs approval" if info.needs_approval else "no approval"
            lines.append(f"  {name}: risk={info.risk_level} scope={info.effect_scope} ({approval})")
        await self._mount_event(OutputPanel("Permissions", "\n".join(lines)))

    # -- Planning / running ------------------------------------------------

    async def _show_config(self, _: str = "") -> None:
        locations_path = self.config.resolved_locations_path(self.config_path)
        await self._mount_event(
            OutputPanel(
                "Config",
                "\n".join(
                    [
                        f"File: [#9c9689]{self.config_path}[/]",
                        f"Locations: [#9c9689]{locations_path}[/]",
                        f"Created by setup: [bold #f2ede1]{self.config.created_by_setup}[/]",
                    ]
                ),
            )
        )
