"""The only application-level gateway allowed to send navigation goals."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from jenai.bridge import BridgeError, RosBridgeClient
from jenai.config.models import AppConfig
from jenai.schemas import GateReport, RouteOutput
from jenai.state.audit import AuditStore
from jenai.tools.nav_live import NavProgress, navigate_with_fallback
from jenai.tools.safety import arm_watchdog

BridgeProvider = Callable[[], Awaitable[RosBridgeClient]]
logger = logging.getLogger(__name__)


class NavigationGateway:
    """Apply navigation policy before any goal can reach ROS.

    Surfaces may inject a long-lived bridge provider. Callers without one get a
    one-shot bridge that is watchdog-armed before startup and closed after use.
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        get_bridge: BridgeProvider | None = None,
        audit_store: AuditStore | None = None,
    ) -> None:
        self._config = config
        self._external_get_bridge = get_bridge
        self._audit_store = audit_store
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

    async def _verify_active_site(
        self,
        outgoing_action: dict[str, Any],
        *,
        run_id: str | None,
        session_id: str | None,
    ) -> RouteOutput | None:
        """Fail closed before motion when the active site's map is not present."""
        site = self._config.site
        if not site.active:
            message = (
                "No validated Site Profile is active. Navigation was blocked; "
                "validate and explicitly activate the site before using saved coordinates."
            )
            self._audit_site_map(
                "blocked",
                site.map_sha256,
                None,
                run_id=run_id,
                session_id=session_id,
            )
            return RouteOutput(
                input_text="",
                route_preview=message,
                outgoing_action=outgoing_action,
                approval_status="approved",
                execution_status="blocked",
            )
        expected_digest = site.map_sha256
        if expected_digest is None:  # Defensive against unchecked model construction.
            return RouteOutput(
                input_text="",
                route_preview=f"Active site '{site.display_name}' has no map identity.",
                outgoing_action=outgoing_action,
                approval_status="approved",
                execution_status="blocked",
            )

        observed_digest: str | None = None
        try:
            identity = await (await self._get_bridge()).map_identity(timeout=3.0)
            observed_digest = identity.digest
            if identity.frame_id != site.map_frame:
                message = (
                    f"Site '{site.display_name}' expects map frame '{site.map_frame}', "
                    f"but ROS reported '{identity.frame_id}'. Navigation was blocked."
                )
            elif observed_digest != expected_digest:
                message = (
                    f"Map identity mismatch for site '{site.display_name}': expected "
                    f"{expected_digest[:12]}, observed {observed_digest[:12]}. "
                    "Navigation was blocked; validate and activate the correct Site Profile."
                )
            else:
                self._audit_site_map(
                    "pass",
                    expected_digest,
                    observed_digest,
                    run_id=run_id,
                    session_id=session_id,
                )
                return None
        except BridgeError as exc:
            message = (
                f"Could not verify the active map for site '{site.display_name}': {exc}. "
                "Navigation was blocked."
            )

        self._audit_site_map(
            "blocked",
            expected_digest,
            observed_digest,
            run_id=run_id,
            session_id=session_id,
        )
        return RouteOutput(
            input_text="",
            route_preview=message,
            outgoing_action=outgoing_action,
            approval_status="approved",
            execution_status="blocked",
        )

    def _audit_site_map(
        self,
        status: str,
        expected: str | None,
        observed: str | None,
        *,
        run_id: str | None,
        session_id: str | None,
    ) -> None:
        if self._audit_store is None:
            return
        try:
            self._audit_store.record(
                "site_map_verdict",
                run_id=run_id,
                session_id=session_id,
                status=status,
                details={"expected_sha256": expected, "observed_sha256": observed},
            )
        except Exception:
            logger.warning("Site map verdict audit failed", exc_info=True)

    async def execute(
        self,
        outgoing_action: dict[str, Any],
        *,
        on_progress: Callable[[NavProgress], None] | None = None,
        on_gate: Callable[[str], None] | None = None,
        on_gate_report: Callable[[GateReport], None] | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> RouteOutput:
        def _audit_gate(report: GateReport) -> None:
            # Acceptance/HIL callers need the exact three-valued verdict in
            # their immutable artifact; UI callers can continue using only the
            # human-readable progress callback. Observation never changes the
            # gate decision.
            if on_gate_report is not None:
                try:
                    on_gate_report(report)
                except Exception:
                    logger.warning("Gate evidence observer failed", exc_info=True)
            if self._audit_store is None:
                return
            try:
                self._audit_store.record(
                    "gate_verdict",
                    run_id=run_id,
                    session_id=session_id,
                    status=report.verdict,
                    summary=report.reason or None,
                    details={
                        "elapsed_s": report.twin_elapsed_s,
                        "criteria": [
                            {
                                "id": criterion.criterion_id,
                                "status": criterion.status,
                            }
                            for criterion in report.criteria
                        ],
                    },
                )
            except Exception:
                logger.warning("Gate verdict audit failed", exc_info=True)

        site_block = await self._verify_active_site(
            outgoing_action,
            run_id=run_id,
            session_id=session_id,
        )
        if site_block is not None:
            return site_block
        if self._config.route_adapter == "odom":
            return RouteOutput(
                input_text="",
                route_preview=(
                    "The legacy odom direct-drive fallback is not available through the "
                    "high-level Navigation Gateway. Configure Nav2 or a registered external "
                    "robot controller so JenAI decides the goal without replacing low-level "
                    "motion control."
                ),
                outgoing_action=outgoing_action,
                approval_status="approved",
                execution_status="blocked",
            )

        return await navigate_with_fallback(
            self._config,
            self._get_bridge,
            outgoing_action,
            on_progress=on_progress,
            on_gate=on_gate,
            on_gate_report=_audit_gate,
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
    outgoing_action: dict[str, Any],
    *,
    on_progress: Callable[[NavProgress], None] | None = None,
    on_gate: Callable[[str], None] | None = None,
    on_gate_report: Callable[[GateReport], None] | None = None,
    audit_store: AuditStore | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
) -> RouteOutput:
    """Execute through a one-shot, always-cleaned-up NavigationGateway."""
    gateway = NavigationGateway(config, audit_store=audit_store)
    try:
        return await gateway.execute(
            outgoing_action,
            on_progress=on_progress,
            on_gate=on_gate,
            on_gate_report=on_gate_report,
            run_id=run_id,
            session_id=session_id,
        )
    finally:
        await gateway.close()
