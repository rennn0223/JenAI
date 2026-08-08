"""The only application-level gateway allowed to send navigation goals."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from typing import Any

from jenai.bridge import BridgeError, MapIdentityInfo, RosBridgeClient
from jenai.capabilities import has_registered_capability
from jenai.config.models import AppConfig
from jenai.schemas import GateReport, RouteOutput
from jenai.site_assets import SiteAssetError, bind_navigation_action
from jenai.state.audit import AuditStore
from jenai.tools.nav_live import NavProgress, navigate_with_fallback
from jenai.tools.safety import HaltReceipt, arm_watchdog, halt_robot_with_receipt

BridgeProvider = Callable[[], Awaitable[RosBridgeClient]]
logger = logging.getLogger(__name__)

_NAVIGATION_CAPABILITY_IDS = (
    "navigate",
    "explore_known_locations",
    "patrol_photo",
    "area_patrol",
    "dock_approach",
)


def _blocked_capability(outgoing_action: dict[str, Any], capability_id: str) -> RouteOutput:
    message = (
        f"Navigation capability '{capability_id}' is not registered for this robot "
        "profile. Navigation was blocked."
    )
    return RouteOutput(
        input_text="",
        route_preview=message,
        outgoing_action=outgoing_action,
        approval_status="approved",
        execution_status="blocked",
    )


def _site_verdict(
    outgoing_action: dict[str, Any],
    message: str,
    *,
    status: str = "blocked",
) -> RouteOutput:
    return RouteOutput(
        input_text="",
        route_preview=message,
        outgoing_action=outgoing_action,
        approval_status="approved",
        execution_status=status,
    )


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
        config_path: Path | None = None,
        audit_store: AuditStore | None = None,
    ) -> None:
        self._config = config
        self._config_path = config_path
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
        """Fail closed before motion when the active site or map is invalid."""
        block = self._configured_site_block(outgoing_action, run_id=run_id, session_id=session_id)
        if block is not None:
            return block

        expected_digest = self._config.site.map_sha256
        if expected_digest is None:  # Defensive if an unchecked config mutates mid-call.
            return _site_verdict(outgoing_action, "Active site has no map identity.")
        return await self._verify_live_map(
            outgoing_action,
            expected_digest,
            run_id=run_id,
            session_id=session_id,
        )

    def _configured_site_block(
        self,
        outgoing_action: dict[str, Any],
        *,
        run_id: str | None,
        session_id: str | None,
    ) -> RouteOutput | None:
        site = self._config.site
        if not site.active:
            message = (
                "No validated Site Profile is active. Navigation was blocked; "
                "validate and explicitly activate the site before using saved coordinates."
            )
            self._audit_site_map(
                "blocked", site.map_sha256, None, run_id=run_id, session_id=session_id
            )
            return _site_verdict(outgoing_action, message)

        if not site.execution_ready:
            message = (
                f"Active site '{site.display_name}' is not execution-ready. "
                "Navigation was blocked; run 'JenAI site validate --repair' "
                "with the validated map active."
            )
            self._audit_site_assets("blocked", message, run_id=run_id, session_id=session_id)
            return _site_verdict(outgoing_action, message)

        if site.map_sha256 is None:  # Defensive against unchecked model construction.
            return _site_verdict(
                outgoing_action,
                f"Active site '{site.display_name}' has no map identity.",
            )
        return None

    async def _verify_live_map(
        self,
        outgoing_action: dict[str, Any],
        expected_digest: str,
        *,
        run_id: str | None,
        session_id: str | None,
    ) -> RouteOutput | None:
        try:
            identity = await (await self._get_bridge()).map_identity(timeout=3.0)
        except BridgeError as exc:
            message = (
                f"Could not verify the active map for site "
                f"'{self._config.site.display_name}': {exc}. "
                "Navigation is temporarily unavailable."
            )
            self._audit_site_map(
                "unavailable",
                expected_digest,
                None,
                run_id=run_id,
                session_id=session_id,
            )
            return _site_verdict(outgoing_action, message, status="unavailable")

        mismatch_message = self._map_mismatch_message(identity, expected_digest)
        if mismatch_message is None:
            self._audit_site_map(
                "pass",
                expected_digest,
                identity.digest,
                run_id=run_id,
                session_id=session_id,
            )
            return None

        self._audit_site_map(
            "blocked",
            expected_digest,
            identity.digest,
            run_id=run_id,
            session_id=session_id,
        )
        return _site_verdict(outgoing_action, mismatch_message)

    def _map_mismatch_message(self, identity: MapIdentityInfo, expected_digest: str) -> str | None:
        site = self._config.site
        if identity.frame_id != site.map_frame:
            return (
                f"Site '{site.display_name}' expects map frame '{site.map_frame}', "
                f"but ROS reported '{identity.frame_id}'. Navigation was blocked."
            )
        if identity.digest != expected_digest:
            return (
                f"Map identity mismatch for site '{site.display_name}': expected "
                f"{expected_digest[:12]}, observed {identity.digest[:12]}. "
                "Navigation was blocked; validate and activate the correct Site Profile."
            )
        return None

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

    def _audit_site_assets(
        self,
        status: str,
        message: str,
        *,
        run_id: str | None,
        session_id: str | None,
    ) -> None:
        if self._audit_store is None:
            return
        try:
            self._audit_store.record(
                "site_asset_verdict",
                run_id=run_id,
                session_id=session_id,
                status=status,
                summary=message,
                details={"expected_locations_sha256": self._config.site.locations_sha256},
            )
        except Exception:
            logger.warning("Site asset verdict audit failed", exc_info=True)

    def _observe_gate_report(
        self,
        report: GateReport,
        *,
        observer: Callable[[GateReport], None] | None,
        run_id: str | None,
        session_id: str | None,
    ) -> None:
        """Forward and persist immutable gate evidence without altering its verdict."""
        if observer is not None:
            try:
                observer(report)
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

    async def execute(
        self,
        outgoing_action: dict[str, Any],
        *,
        on_progress: Callable[[NavProgress], None] | None = None,
        on_gate: Callable[[str], None] | None = None,
        on_gate_report: Callable[[GateReport], None] | None = None,
        run_id: str | None = None,
        endpoint_retry_limit: int | None = None,
        session_id: str | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> RouteOutput:
        audit_gate = partial(
            self._observe_gate_report,
            observer=on_gate_report,
            run_id=run_id,
            session_id=session_id,
        )
        requested_capability = str(outgoing_action.get("capability_id") or "navigate")
        if not has_registered_capability(self._config, *_NAVIGATION_CAPABILITY_IDS):
            return _blocked_capability(outgoing_action, requested_capability)

        site_block = await self._verify_active_site(
            outgoing_action,
            run_id=run_id,
            session_id=session_id,
        )
        if is_cancelled is not None and is_cancelled():
            return RouteOutput(
                input_text="",
                route_preview="Navigation dispatch cancelled after Site preflight.",
                outgoing_action=outgoing_action,
                approval_status="approved",
                execution_status="cancelled",
            )
        if site_block is not None:
            return site_block
        try:
            if self._config_path is None:
                raise SiteAssetError(
                    "The configuration path is unavailable, so relative Site Profile assets "
                    "cannot be verified."
                )
            outgoing_action = bind_navigation_action(
                self._config,
                self._config_path,
                outgoing_action,
            )
        except SiteAssetError as exc:
            message = f"{exc} Navigation was blocked."
            self._audit_site_assets("blocked", message, run_id=run_id, session_id=session_id)
            return RouteOutput(
                input_text="",
                route_preview=message,
                outgoing_action=outgoing_action,
                approval_status="approved",
                execution_status="blocked",
            )
        capability_id = str(outgoing_action.get("capability_id") or "navigate")
        if capability_id not in _NAVIGATION_CAPABILITY_IDS or not has_registered_capability(
            self._config, capability_id
        ):
            return _blocked_capability(outgoing_action, capability_id)
        if is_cancelled is not None and is_cancelled():
            return RouteOutput(
                input_text="",
                route_preview="Navigation dispatch cancelled after action binding.",
                outgoing_action=outgoing_action,
                approval_status="approved",
                execution_status="cancelled",
            )

        self._audit_site_assets(
            "pass", "Site assets verified.", run_id=run_id, session_id=session_id
        )
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
            on_gate_report=audit_gate,
            endpoint_retry_limit=endpoint_retry_limit,
            is_cancelled=is_cancelled,
        )

    async def stop(self) -> HaltReceipt:
        """Cancel active navigation and publish zero velocity through the shared halt seam."""

        return await halt_robot_with_receipt(self._config, await self._get_bridge())

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
    config_path: Path | None = None,
    on_progress: Callable[[NavProgress], None] | None = None,
    on_gate: Callable[[str], None] | None = None,
    on_gate_report: Callable[[GateReport], None] | None = None,
    audit_store: AuditStore | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
) -> RouteOutput:
    """Execute through a one-shot, always-cleaned-up NavigationGateway."""
    gateway = NavigationGateway(config, config_path=config_path, audit_store=audit_store)
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
