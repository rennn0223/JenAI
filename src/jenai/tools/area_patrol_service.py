"""Product-surface-neutral service for deterministic semantic area patrol."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from jenai.bridge import BridgeError, RosBridgeClient
from jenai.config.models import AppConfig
from jenai.json_types import JsonObject
from jenai.schemas import (
    EffectScope,
    Location,
    RiskLevel,
    RouteOutput,
    RunRecord,
    TaskOutcome,
    ToolCallCategory,
    ToolCallRecord,
    ToolCallStatus,
)
from jenai.site_assets import SiteAssetError, load_site_patrol_areas, resolve_site_location
from jenai.state.reports import area_patrol_report_payload, save_area_patrol_log
from jenai.state.runs import RunStore
from jenai.tools.nav_live import NavProgress
from jenai.tools.navigation_gateway import NavigationGateway
from jenai.tools.registry import ToolRiskInfo
from jenai.tools.vision_core import capture_and_analyze
from jenai.workflows.area_patrol import (
    AreaPatrolReport,
    AreaPatrolRequest,
    AreaPatrolWorkflow,
    InspectionPoint,
    InspectionResult,
    InspectionVerdict,
    NavigationResult,
    PatrolMissionStatus,
    StepVerdict,
)

ToolOutput = JsonObject
StatusObserver = Callable[[str], None]
logger = logging.getLogger(__name__)


class AreaPatrolRunContext(Protocol):
    """Minimal application context required by the shared patrol capability."""

    config: AppConfig
    config_path: Path
    run: RunRecord
    run_store: RunStore


_WHOLE_SITE_TARGET_ALIASES = frozenset(
    {
        "",
        "all",
        "all required areas",
        "configured site",
        "current site",
        "current site profile",
        "every required area",
        "site",
        "site profile",
        "whole site",
        "全部",
        "全部必巡區域",
        "所有必巡區域",
        "目前場域",
        "目前 site profile",
        "整個場域",
    }
)

AREA_PATROL_RISK_INFO = ToolRiskInfo(
    risk_level=RiskLevel.P1,
    effect_scope=EffectScope.SIM_CONTROL,
    needs_approval=True,
    description=(
        "Cover configured semantic patrol areas, preserve inspection evidence, "
        "and return to the configured home location."
    ),
)


def _normalize_patrol_target(target: str) -> str:
    """Map bounded whole-site aliases from untrusted model output to ``all``."""

    normalized = " ".join(target.casefold().split())
    return "all" if normalized in _WHOLE_SITE_TARGET_ALIASES else target.strip()


def _record_call(
    run_ctx: AreaPatrolRunContext,
    input_summary: str,
) -> ToolCallRecord:
    call = ToolCallRecord(
        tool_name="area_patrol_workflow_tool",
        category=ToolCallCategory.ROUTE,
        input_summary=input_summary,
        status=ToolCallStatus.RUNNING,
        risk_level=AREA_PATROL_RISK_INFO.risk_level,
        effect_scope=AREA_PATROL_RISK_INFO.effect_scope,
    )
    run_ctx.run_store.add_tool_call(run_ctx.run, call)
    return call


def _finish_call(
    run_ctx: AreaPatrolRunContext,
    call: ToolCallRecord,
    *,
    ok: bool,
    summary: str,
) -> None:
    run_ctx.run_store.update_tool_call(
        run_ctx.run,
        call.tool_call_id,
        status=ToolCallStatus.SUCCEEDED if ok else ToolCallStatus.FAILED,
        output_summary=summary,
    )


def _step_verdict(output: RouteOutput) -> StepVerdict:
    status = output.execution_status.strip().lower()
    if status == "succeeded":
        return StepVerdict.SUCCEEDED
    if status in {"blocked", "referred"}:
        return StepVerdict.BLOCKED
    if status == "unavailable":
        return StepVerdict.RETRYABLE_FAILURE
    if status in {"cancelled", "canceled", "aborted"}:
        return StepVerdict.CANCELLED
    if status in {"failed", "endpoint_mismatch"}:
        return StepVerdict.RETRYABLE_FAILURE
    return StepVerdict.FAILED


class AgentAreaPatrolRuntime:
    """Translate typed patrol steps into Site Profile, Nav2, and camera calls."""

    def __init__(
        self,
        run_ctx: AreaPatrolRunContext,
        locations: list[Location],
        *,
        on_status: StatusObserver | None = None,
    ) -> None:
        self._run_ctx = run_ctx
        self._locations = locations
        self._on_status = on_status
        self._camera_bridge: RosBridgeClient | None = None
        self._inspection_sequence = 0
        self._last_nav_progress_at = 0.0
        self._navigation_gateway: NavigationGateway | None = NavigationGateway(
            run_ctx.config,
            config_path=run_ctx.config_path,
            audit_store=run_ctx.run_store.audit_store,
        )

    async def navigate(self, point: InspectionPoint) -> NavigationResult:
        return await self._navigate_location(point.location, phase="Navigating to")

    async def return_home(self, location: str) -> NavigationResult:
        return await self._navigate_location(location, phase="Returning home to")

    async def inspect(self, point: InspectionPoint) -> InspectionResult:
        self._inspection_sequence += 1
        self._set_status(f"Inspecting {point.location} · preserving camera evidence")
        evidence_path = self._evidence_path(point.location, self._inspection_sequence)
        try:
            bridge = await self._get_camera_bridge()
            output = await capture_and_analyze(
                self._run_ctx.config,
                bridge,
                self._run_ctx.config.vehicle.camera_topic,
                task_context=(
                    f"Semantic patrol inspection at {point.location}. "
                    f"Operator request: {self._run_ctx.run.user_input}"
                ),
                preserve_to=evidence_path,
            )
        except Exception as exc:  # noqa: BLE001 - converted to an honest workflow result
            return InspectionResult(
                verdict=InspectionVerdict.REQUIRES_REVIEW,
                detail=f"camera evidence unavailable: {exc}",
            )

        evidence = (output.source,)
        if output.anomalies:
            return InspectionResult(
                verdict=InspectionVerdict.REQUIRES_REVIEW,
                detail=f"reported anomalies: {', '.join(output.anomalies)}",
                evidence=evidence,
            )
        if output.analysis_status == "unavailable":
            return InspectionResult(
                verdict=InspectionVerdict.REQUIRES_REVIEW,
                detail=output.summary,
                evidence=evidence,
            )
        return InspectionResult(
            verdict=InspectionVerdict.VERIFIED,
            detail=output.summary or "image observation preserved",
            evidence=evidence,
        )

    async def close(self) -> None:
        """Release workflow-owned bridges; repeated calls are safe."""

        gateway, self._navigation_gateway = self._navigation_gateway, None
        if gateway is not None:
            await gateway.close()

        bridge, self._camera_bridge = self._camera_bridge, None
        if bridge is not None:
            with contextlib.suppress(BridgeError):
                await bridge.stop()

    async def _navigate_location(self, reference: str, *, phase: str) -> NavigationResult:
        self._last_nav_progress_at = 0.0
        self._set_status(f"{phase} {reference}")
        try:
            location = resolve_site_location(self._locations, reference)
            gateway = self._navigation_gateway
            if gateway is None:
                raise RuntimeError("navigation gateway is closed")
            output = await gateway.execute(
                {
                    "goal": location.model_dump(mode="json"),
                    "capability_id": "area_patrol",
                },
                run_id=self._run_ctx.run.run_id,
                session_id=self._run_ctx.run.session_id,
                on_progress=lambda progress: self._navigation_progress(reference, progress),
            )
        except Exception as exc:  # noqa: BLE001 - adapter failures become typed results
            return NavigationResult(
                StepVerdict.FAILED,
                f"navigation adapter error: {exc}",
            )
        verdict = _step_verdict(output)
        result_label = "Reached" if verdict is StepVerdict.SUCCEEDED else "Navigation ended at"
        self._set_status(f"{result_label} {reference} · {verdict.value}")
        return NavigationResult(verdict, output.route_preview)

    def _navigation_progress(self, reference: str, progress: NavProgress) -> None:
        now = time.monotonic()
        if now - self._last_nav_progress_at < 5.0:
            return
        self._last_nav_progress_at = now
        distance = float(progress.distance_remaining)
        if not math.isfinite(distance) or (progress.recoveries > 0 and distance <= 0.05):
            distance_text = "distance unavailable while recovering"
        else:
            distance_text = f"{max(distance, 0.0):.1f} m remaining"
        recovery_text = f" · {progress.recoveries} recoveries" if progress.recoveries else ""
        self._set_status(
            f"Navigating to {reference} · {distance_text} · {progress.elapsed:.0f}s{recovery_text}"
        )

    def _set_status(self, message: str) -> None:
        if self._on_status is None:
            return
        try:
            self._on_status(message)
        except Exception:
            logger.warning("Area patrol status observer failed", exc_info=True)

    async def _get_camera_bridge(self) -> RosBridgeClient:
        if self._camera_bridge is None:
            bridge = RosBridgeClient()
            await bridge.start()
            self._camera_bridge = bridge
        return self._camera_bridge

    def _evidence_path(self, location: str, sequence: int) -> Path:
        mission_digest = hashlib.sha256(self._run_ctx.run.run_id.encode("utf-8")).hexdigest()[:12]
        location_digest = hashlib.sha256(location.encode("utf-8")).hexdigest()[:12]
        return (
            self._run_ctx.config_path.parent
            / "reports"
            / f"evidence-{mission_digest}-{sequence:04d}-{location_digest}.png"
        )


def _report_outcome(status: PatrolMissionStatus) -> TaskOutcome:
    if status is PatrolMissionStatus.SUCCESS:
        return TaskOutcome.SUCCEEDED
    if status is PatrolMissionStatus.ABORTED:
        return TaskOutcome.CANCELLED
    if status is PatrolMissionStatus.FAILED:
        return TaskOutcome.FAILED
    return TaskOutcome.PARTIAL


def _report_output(report: AreaPatrolReport) -> ToolOutput:
    return area_patrol_report_payload(report)


async def run_area_patrol_capability(
    run_ctx: AreaPatrolRunContext,
    target: str = "all",
    max_navigation_retries: int = 1,
    return_home: bool = True,
) -> ToolOutput:
    """Run the shared semantic-patrol capability for any approved product surface."""

    call = _record_call(
        run_ctx,
        f"target={target or 'all'}, retries={max_navigation_retries}, return_home={return_home}",
    )
    try:
        if not 0 <= max_navigation_retries <= 5:
            raise ValueError("max_navigation_retries must be between 0 and 5")
        target = _normalize_patrol_target(target)
        areas = load_site_patrol_areas(
            run_ctx.config,
            run_ctx.config_path,
            target=target,
        )
        locations_path = run_ctx.config.resolved_locations_path(run_ctx.config_path)
        if locations_path is None:
            raise SiteAssetError("The active Site Profile has no locations file.")
        from jenai.adapters.locations import load_locations

        locations = load_locations(locations_path)
        request = AreaPatrolRequest(
            mission_id=run_ctx.run.run_id,
            target=target.strip() or "all",
            home_location=run_ctx.config.site.home_location,
            max_navigation_retries=max_navigation_retries,
            return_home=return_home,
        )
    except (OSError, SiteAssetError, ValueError) as exc:
        message = f"Area patrol configuration is invalid: {exc}"
        run_ctx.run.outcome = TaskOutcome.FAILED
        _finish_call(run_ctx, call, ok=False, summary=message)
        return {"execution_status": "failed", "summary": message}

    def _publish_status(message: str) -> None:
        run_ctx.run_store.update_tool_call(
            run_ctx.run,
            call.tool_call_id,
            status=ToolCallStatus.RUNNING,
            output_summary=message,
        )

    runtime = AgentAreaPatrolRuntime(run_ctx, locations, on_status=_publish_status)
    try:
        report = await AreaPatrolWorkflow(runtime).run(request, areas)
    finally:
        await runtime.close()

    outcome = _report_outcome(report.status)
    report_path: Path | None = None
    report_error: str | None = None
    try:
        report_path = save_area_patrol_log(report, run_ctx.config_path)
    except OSError as exc:
        report_error = f"Area patrol report could not be saved: {exc}"
        if outcome is TaskOutcome.SUCCEEDED:
            outcome = TaskOutcome.PARTIAL
    run_ctx.run.outcome = outcome
    summary = (
        f"Area patrol {report.status.value}: required observation coverage "
        f"{report.coverage_ratio:.1%}; returned_home={report.returned_home}."
    )
    if report_error is not None:
        summary = f"{summary} {report_error}"
    _finish_call(
        run_ctx,
        call,
        ok=(
            report.status not in {PatrolMissionStatus.FAILED, PatrolMissionStatus.ABORTED}
            and report_error is None
        ),
        summary=summary,
    )
    output = _report_output(report)
    output["outcome"] = outcome.value
    output["summary"] = summary
    output["report_path"] = str(report_path) if report_path is not None else None
    output["report_saved"] = report_path is not None
    return output
