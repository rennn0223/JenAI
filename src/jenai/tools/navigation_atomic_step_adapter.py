"""Production execution adapter between Golden Path steps and Capability Executor."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from jenai.config.models import AppConfig
from jenai.runtime import (
    AuthorityContext,
    CapabilityExecutionRegistration,
    CapabilityExecutionReport,
    CapabilityExecutor,
    EvidenceTimestampStatus,
    ExecutionContext,
    ExecutionDisposition,
    ExecutorEventSink,
    ExecutorEvidence,
    ExecutorStopResult,
    InMemoryCapabilityExecutor,
    ObservationContext,
    ObservationSnapshot,
    PreparedCapabilityStep,
    SnapshotRequest,
    SourceAssurance,
    StopContext,
    StopTrigger,
    TransportSecurity,
    TypedCapabilityStep,
)
from jenai.schemas import RouteOutput
from jenai.site_assets import resolve_site_location, validate_site_assets
from jenai.tools.safety import HaltReceipt, NavigationCancelStatus
from jenai.workflows.execution_engine import (
    AtomicStepAdapter,
    DispatchContext,
    StepDisposition,
    StepEvidence,
    StepEvidenceKind,
    StepResult,
    StopResult,
)
from jenai.workflows.patrol_mission import ExecutionStep, PatrolMissionSpec


class EffectfulMissionBlockedError(RuntimeError):
    """A STOP boundary requires fresh robot/Nav2 confirmation before more effects."""


def _required_cancellation_check(
    is_cancelled: Callable[[], bool] | None,
) -> Callable[[], bool]:
    if is_cancelled is None:
        raise RuntimeError("navigation execution requires an explicit cancellation check")
    return is_cancelled


class _NavigationGatewayPort(Protocol):
    async def execute(
        self,
        outgoing_action: dict[str, object],
        *,
        run_id: str | None = None,
        session_id: str | None = None,
        endpoint_retry_limit: int | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> RouteOutput: ...

    async def stop(self) -> HaltReceipt: ...


class _NavigationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    location_id: str
    location_name: str
    position_tolerance_m: float = Field(gt=0, allow_inf_nan=False)
    require_yaw: bool
    site_id: str
    site_version: str
    site_profile_digest: str
    vehicle_profile_digest: str
    locations_sha256: str
    goal: dict[str, object] | None = None


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_navigation_capability_executor(
    *,
    config: AppConfig,
    config_path: Path,
    gateway: _NavigationGatewayPort,
) -> InMemoryCapabilityExecutor:
    """Build the existing in-process Capability Executor for one Navigation Gateway."""

    detached_config = AppConfig.model_validate(config.model_dump(mode="json"))

    async def snapshot(
        request: SnapshotRequest,
        _context: ObservationContext,
    ) -> ObservationSnapshot:
        return ObservationSnapshot(
            robot_id=request.robot_id,
            evidence=(),
            limitations=("navigation capability executor has no standalone snapshot",),
        )

    async def prepare(
        step: TypedCapabilityStep,
        _context: ExecutionContext,
    ) -> TypedCapabilityStep:
        request = _NavigationInput.model_validate(step.model_dump(mode="json")["input"])
        if request.goal is not None:
            raise ValueError("untrusted navigation input must not provide a goal")
        if (
            request.site_id != detached_config.site.site_id
            or request.site_version != detached_config.site.version
            or request.locations_sha256 != detached_config.site.locations_sha256
            or request.site_profile_digest
            != _canonical_digest(detached_config.site.model_dump(mode="json"))
            or request.vehicle_profile_digest
            != _canonical_digest(detached_config.vehicle.model_dump(mode="json"))
        ):
            raise ValueError("navigation input does not match the active reviewed profiles")
        locations = validate_site_assets(detached_config, config_path)
        location = resolve_site_location(locations, request.location_id)
        if location.name != request.location_name:
            raise ValueError("navigation location identity does not match the reviewed Site")
        canonical = request.model_copy(update={"goal": location.model_dump(mode="json")})
        return TypedCapabilityStep(
            capability_id=step.capability_id,
            input_schema_version=step.input_schema_version,
            input=canonical.model_dump(mode="json"),
        )

    async def execute(
        prepared: PreparedCapabilityStep,
        _events: ExecutorEventSink,
        is_cancelled: Callable[[], bool] | None,
    ) -> CapabilityExecutionReport:
        request = _NavigationInput.model_validate(prepared.step.model_dump(mode="json")["input"])
        if request.goal is None:
            raise ValueError("prepared navigation input has no bound goal")
        is_cancelled = _required_cancellation_check(is_cancelled)
        if is_cancelled():
            return CapabilityExecutionReport(
                disposition=ExecutionDisposition.CANCELLED,
                summary="Navigation dispatch cancelled before Gateway execution.",
            )
        output = await gateway.execute(
            {"capability_id": "navigate", "goal": request.goal},
            run_id=prepared.context.task_id,
            endpoint_retry_limit=0,
            session_id=prepared.context.authority.boot_id,
            is_cancelled=is_cancelled,
        )
        return _capability_report(output)

    async def stop(
        _context: StopContext,
        _events: ExecutorEventSink,
    ) -> ExecutorStopResult:
        receipt = await gateway.stop()
        status = receipt.navigation_cancel_status
        cancel_requested = status is not NavigationCancelStatus.NOT_ACTIVE
        cancel_acknowledged = (
            status is NavigationCancelStatus.ACKNOWLEDGED if cancel_requested else None
        )
        return ExecutorStopResult(
            request_accepted=True,
            cancel_requested=cancel_requested,
            cancel_acknowledged=cancel_acknowledged,
            zero_velocity_command_published=receipt.zero_velocity_command_published,
        )

    return InMemoryCapabilityExecutor(
        snapshot_handler=snapshot,
        registrations=(
            CapabilityExecutionRegistration(
                capability_id="navigate",
                input_schema_version="1",
                prepare=prepare,
                execute=execute,
            ),
        ),
        stop_handler=stop,
    )


def _capability_report(output: RouteOutput) -> CapabilityExecutionReport:
    attempt = output.navigation_attempts[-1] if output.navigation_attempts else None
    if (
        attempt is not None
        and attempt.terminal_observed
        and attempt.terminal_status == "succeeded"
        and attempt.endpoint_pose_observed
        and attempt.position_error_m is not None
    ):
        evidence = (
            ExecutorEvidence(
                kind="navigation_terminal",
                source="navigation_gateway",
                source_timestamp_status=EvidenceTimestampStatus.UNAVAILABLE,
                transport_security=TransportSecurity.UNKNOWN,
                source_assurance=SourceAssurance.RUNTIME_OBSERVED,
                payload_schema_version="1",
                payload={
                    "evidence_id": f"nav2-terminal:{attempt.tag}",
                    "status": attempt.terminal_status,
                },
            ),
            ExecutorEvidence(
                kind="endpoint_pose",
                source="navigation_gateway",
                source_timestamp_status=EvidenceTimestampStatus.UNAVAILABLE,
                transport_security=TransportSecurity.UNKNOWN,
                source_assurance=SourceAssurance.RUNTIME_OBSERVED,
                payload_schema_version="1",
                payload={
                    "evidence_id": f"endpoint-pose:{attempt.tag}",
                    "position_error_m": attempt.position_error_m,
                },
            ),
        )
        return CapabilityExecutionReport(
            disposition=ExecutionDisposition.COMPLETED,
            summary=output.route_preview,
            evidence=evidence,
        )
    terminal_status = attempt.terminal_status if attempt is not None else None
    if output.execution_status == "cancelled" or terminal_status == "canceled":
        return CapabilityExecutionReport(
            disposition=ExecutionDisposition.CANCELLED,
            summary=output.route_preview or "Navigation canceled.",
        )

    failure_scope = (
        "waypoint_local" if output.failure_scope == "waypoint_local" else "navigation_system"
    )
    failure_evidence = ExecutorEvidence(
        kind="navigation_failure",
        source="navigation_gateway",
        source_timestamp_status=EvidenceTimestampStatus.UNAVAILABLE,
        transport_security=TransportSecurity.UNKNOWN,
        source_assurance=SourceAssurance.RUNTIME_OBSERVED,
        payload_schema_version="1",
        payload={
            "scope": failure_scope,
            "terminal_status": terminal_status,
        },
    )
    return CapabilityExecutionReport(
        disposition=ExecutionDisposition.FAILED,
        summary=output.route_preview or "Navigation did not produce verified completion Evidence.",
        evidence=(failure_evidence,),
    )


class NavigationAtomicStepAdapter(AtomicStepAdapter):
    """Translate one bound plan step without owning mission policy or progress."""

    def __init__(
        self,
        *,
        executor: CapabilityExecutor,
        authority: AuthorityContext,
        mission: PatrolMissionSpec,
        fencing_token: int,
        events: ExecutorEventSink,
    ) -> None:
        if authority.safety_epoch < 1:
            raise ValueError(
                "NavigationAtomicStepAdapter requires a STOP-capable authority context"
            )
        self._executor = executor
        self._authority = AuthorityContext.model_validate(authority.model_dump(mode="json"))
        self._mission = PatrolMissionSpec.model_validate(mission.model_dump(mode="json"))
        self._fencing_token = fencing_token
        self._events = events
        self._reconfirmation_required = False

    async def execute(
        self,
        step: ExecutionStep,
        dispatch_context: DispatchContext,
    ) -> StepResult:
        if self._reconfirmation_required:
            raise EffectfulMissionBlockedError(
                "effectful Mission blocked until robot/Nav2 state is reconfirmed"
            )
        context = ExecutionContext(
            authority=self._authority,
            fencing_token=self._fencing_token,
            robot_id=self._mission.robot_id,
            task_id=self._mission.mission_id,
            command_id=dispatch_context.dispatch_id,
        )
        typed_step = TypedCapabilityStep(
            capability_id="navigate",
            input_schema_version="1",
            input={
                "kind": step.kind,
                "location_id": step.location_id,
                "location_name": step.location_name,
                "position_tolerance_m": step.position_tolerance_m,
                "require_yaw": step.require_yaw,
                "site_id": self._mission.site_id,
                "site_version": self._mission.site_version,
                "site_profile_digest": self._mission.site_profile_digest,
                "vehicle_profile_digest": self._mission.vehicle_profile_digest,
                "locations_sha256": self._mission.locations_sha256,
            },
        )
        prepared = await self._executor.prepare(typed_step, context)
        if dispatch_context.cancellation.cancelled:
            return StepResult(
                disposition=StepDisposition.CANCELLED,
                summary="Navigation dispatch cancelled before Capability execution.",
            )
        report = await self._executor.execute(
            prepared,
            context,
            self._events,
            is_cancelled=lambda: dispatch_context.cancellation.cancelled,
        )
        return _step_result(report)

    async def stop(self, active_dispatch: DispatchContext) -> StopResult:
        self._reconfirmation_required = True
        result = await self._executor.stop(
            StopContext(
                authority=self._authority,
                robot_id=self._mission.robot_id,
                stop_id=active_dispatch.dispatch_id,
                trigger=StopTrigger.OPERATOR,
            ),
            self._events,
        )
        acknowledged = result.cancel_acknowledged if result.cancel_requested else True
        return StopResult(
            summary=(
                "STOP delivered; fresh robot/Nav2 confirmation is required before "
                "another effectful Mission."
            ),
            cancel_acknowledged=acknowledged,
        )

    async def reconfirm_robot_state(self) -> bool:
        """Clear STOP fencing only after a bounded halt observes no active Nav2 goal."""

        result = await self._executor.stop(
            StopContext(
                authority=self._authority,
                robot_id=self._mission.robot_id,
                stop_id=f"reconfirm-{uuid4().hex}",
                trigger=StopTrigger.POLICY,
            ),
            self._events,
        )
        confirmed = (
            result.request_accepted
            and not result.cancel_requested
            and result.zero_velocity_command_published is True
        )
        if confirmed:
            self._reconfirmation_required = False
        return confirmed


def _evidence_payload(
    evidence: tuple[ExecutorEvidence, ...],
    kind: str,
) -> ExecutorEvidence | None:
    return next((item for item in evidence if item.kind == kind), None)


def _step_result(report: CapabilityExecutionReport) -> StepResult:
    evidence = report.evidence
    if report.disposition == ExecutionDisposition.CANCELLED:
        return StepResult(
            disposition=StepDisposition.CANCELLED,
            summary=report.summary,
        )
    if report.disposition == ExecutionDisposition.FAILED:
        failure = _evidence_payload(evidence, "navigation_failure")
        scope = failure.payload.get("scope") if failure is not None else None
        return StepResult(
            disposition=(
                StepDisposition.WAYPOINT_LOCAL_FAILURE
                if scope == "waypoint_local"
                else StepDisposition.NAVIGATION_SYSTEM_FAILURE
            ),
            summary=report.summary,
        )
    terminal = _evidence_payload(evidence, "navigation_terminal")
    endpoint = _evidence_payload(evidence, "endpoint_pose")
    if terminal is None or endpoint is None:
        return StepResult(
            disposition=StepDisposition.NAVIGATION_SYSTEM_FAILURE,
            summary="Navigation completion Evidence was unavailable; success was not accepted.",
        )
    terminal_id = terminal.payload.get("evidence_id")
    endpoint_id = endpoint.payload.get("evidence_id")
    position_error = endpoint.payload.get("position_error_m")
    terminal_status = terminal.payload.get("status")
    if (
        terminal_status != "succeeded"
        or not isinstance(terminal_id, str)
        or not terminal_id.strip()
        or not isinstance(endpoint_id, str)
        or not endpoint_id.strip()
        or isinstance(position_error, bool)
        or not isinstance(position_error, (int, float))
    ):
        return StepResult(
            disposition=StepDisposition.NAVIGATION_SYSTEM_FAILURE,
            summary="Navigation completion Evidence was malformed; success was not accepted.",
        )
    return StepResult(
        disposition=StepDisposition.SUCCEEDED,
        summary=report.summary,
        evidence=(
            StepEvidence(
                kind=StepEvidenceKind.NAVIGATION_TERMINAL,
                evidence_id=terminal_id,
            ),
            StepEvidence(
                kind=StepEvidenceKind.ENDPOINT_POSE,
                evidence_id=endpoint_id,
            ),
        ),
        position_error_m=float(position_error),
    )
