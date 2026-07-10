"""The only application-level gateway allowed to send navigation goals."""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable

from jenai.bridge import BridgeError, RosBridgeClient
from jenai.config.models import AppConfig
from jenai.schemas import RouteOutput
from jenai.tools.nav_live import NavProgress, navigate_with_fallback
from jenai.tools.safety import arm_watchdog

BridgeProvider = Callable[[], Awaitable[RosBridgeClient]]


class NavigationGateway:
    """Apply navigation policy before any goal can reach ROS.

    Surfaces may inject a long-lived bridge provider. Callers without one get a
    one-shot bridge that is watchdog-armed before startup and closed after use.
    """

    def __init__(self, config: AppConfig, *, get_bridge: BridgeProvider | None = None) -> None:
        self._config = config
        self._external_get_bridge = get_bridge
        self._owned_bridge: RosBridgeClient | None = None
        self._armed_bridge: RosBridgeClient | None = None

    async def _get_bridge(self) -> RosBridgeClient:
        if self._external_get_bridge is not None:
            bridge = await self._external_get_bridge()
        else:
            if self._owned_bridge is None:
                self._owned_bridge = RosBridgeClient()
            bridge = self._owned_bridge

        if self._armed_bridge is not bridge:
            await arm_watchdog(self._config, bridge)
            self._armed_bridge = bridge
        if not bridge.running:
            await bridge.start()
        return bridge

    async def execute(
        self,
        outgoing_action: dict,
        *,
        on_progress: Callable[[NavProgress], None] | None = None,
        on_gate: Callable[[str], None] | None = None,
    ) -> RouteOutput:
        return await navigate_with_fallback(
            self._config,
            self._get_bridge,
            outgoing_action,
            on_progress=on_progress,
            on_gate=on_gate,
        )

    async def close(self) -> None:
        if self._owned_bridge is None:
            return
        bridge, self._owned_bridge = self._owned_bridge, None
        self._armed_bridge = None
        with contextlib.suppress(BridgeError):
            await bridge.stop()


async def execute_navigation(
    config: AppConfig,
    outgoing_action: dict,
    *,
    on_progress: Callable[[NavProgress], None] | None = None,
    on_gate: Callable[[str], None] | None = None,
) -> RouteOutput:
    """Execute through a one-shot, always-cleaned-up NavigationGateway."""
    gateway = NavigationGateway(config)
    try:
        return await gateway.execute(
            outgoing_action, on_progress=on_progress, on_gate=on_gate
        )
    finally:
        await gateway.close()
