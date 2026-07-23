"""Static contract shared by the TUI mixins.

The concrete :class:`JenAITuiApp` is assembled with multiple inheritance.
Python resolves that composition at runtime, but a type checker cannot infer
which sibling mixin supplies a method or field.  This base class records those
cross-mixin dependencies without adding runtime methods: declarations live
behind ``TYPE_CHECKING`` and therefore cannot shadow the real implementation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import Static

from jenai.agent.context import JenAIRunContext
from jenai.bridge import RosBridgeClient
from jenai.config.models import AppConfig, ProviderProfile
from jenai.schemas import DoctorResult, Location, RouteOutput, RunRecord, ToolCallStatus
from jenai.state import RunStore
from jenai.tools.perception import PerceptionLoop

if TYPE_CHECKING:
    from jenai.tui.direct_execution import PendingCommand


class TuiHostContract:
    """Compile-time-only interface required by command mixins."""

    if TYPE_CHECKING:
        config: AppConfig
        config_path: Path
        doctor_result: DoctorResult | None
        run_store: RunStore
        is_running: bool
        _doctor_is_full: bool
        _pending_direct_approvals: dict[str, PendingCommand]
        _pending_approvals: dict[str, dict[str, Any]]
        _auto_approved: set[str]
        _active_task: asyncio.Task[None] | None
        _perception: PerceptionLoop | None
        _bridge: RosBridgeClient | None
        _spinner_timer: Timer | None
        _spinner_label: str

        async def _mount_event(self, widget: Static | Horizontal | Vertical) -> None: ...

        def _scroll_to_bottom(self) -> None: ...

        def _new_run_context(self, user_input: str) -> JenAIRunContext: ...

        def _current_run(self) -> RunRecord | None: ...

        def _locations_path(self) -> Path | None: ...

        def _load_locations(self) -> list[Location]: ...

        async def _get_bridge(self) -> RosBridgeClient: ...

        async def _execute_route_action(self, outgoing_action: dict[str, Any]) -> RouteOutput: ...

        async def _execute_direct(self, pending: PendingCommand) -> None: ...

        def _finish_direct_tool(
            self,
            pending: PendingCommand,
            *,
            ok: bool,
            summary: str,
            status: ToolCallStatus | None = None,
        ) -> None: ...

        def _start_next_queued(self) -> None: ...

        def _start_spinner(self, label: str) -> None: ...

        def _stop_spinner(self) -> None: ...

        async def _run_with_agent_progress(
            self,
            ctx: JenAIRunContext,
            awaitable: Coroutine[Any, Any, RunRecord],
        ) -> RunRecord: ...

        async def _render_run_update(
            self,
            ctx: JenAIRunContext,
            run: RunRecord,
            *,
            agent: Any | None = None,
        ) -> None: ...

        def _active_profile(self) -> ProviderProfile | None: ...

        def _format_status(self, value: str) -> str: ...

        def _format_profile(self, profile: ProviderProfile | None) -> str: ...

        def _chat_model_display(self) -> str: ...

        def _refresh_model_display(self) -> None: ...
