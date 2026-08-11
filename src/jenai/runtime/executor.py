"""Capability Executor seam and its deterministic in-memory adapter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from jenai.runtime.models import (
    CapabilityExecutionReport,
    ExecutionContext,
    ExecutorEvent,
    ExecutorStopResult,
    ObservationContext,
    ObservationSnapshot,
    PreparedCapabilityStep,
    SnapshotRequest,
    StopContext,
    TypedCapabilityStep,
    capability_binding_sha256,
)


class CapabilityUnavailableError(RuntimeError):
    """The requested platform-neutral capability has no registered handler."""


class UnsupportedCapabilitySchemaError(RuntimeError):
    """The capability exists, but the requested input schema version does not."""


class CapabilityPreparationError(RuntimeError):
    """Effect-free schema validation or canonical preparation failed."""


class ExecutionContextMismatchError(RuntimeError):
    """Prepared work does not match the caller-supplied execution context."""


class ExecutorEventSink(Protocol):
    async def publish(self, event: ExecutorEvent) -> None:
        """Accept one typed progress or evidence fact."""


class _DetachedEventSink:
    """Revalidate events before they cross from a handler to the Authority sink."""

    def __init__(self, delegate: ExecutorEventSink) -> None:
        self._delegate = delegate

    async def publish(self, event: ExecutorEvent) -> None:
        detached = ExecutorEvent.model_validate(event.model_dump(mode="json"))
        await self._delegate.publish(detached)


class CapabilityExecutor(Protocol):
    """Internal Runtime port; policy, approval, and outcomes remain Authority-owned."""

    async def snapshot(
        self,
        request: SnapshotRequest,
        context: ObservationContext,
    ) -> ObservationSnapshot: ...

    async def prepare(
        self,
        step: TypedCapabilityStep,
        context: ExecutionContext,
    ) -> PreparedCapabilityStep: ...

    async def execute(
        self,
        prepared: PreparedCapabilityStep,
        context: ExecutionContext,
        events: ExecutorEventSink,
        *,
        is_cancelled: CancellationCheck | None = None,
    ) -> CapabilityExecutionReport: ...

    async def stop(
        self,
        context: StopContext,
        events: ExecutorEventSink,
    ) -> ExecutorStopResult: ...


SnapshotHandler = Callable[[SnapshotRequest, ObservationContext], Awaitable[ObservationSnapshot]]
PreparationHandler = Callable[
    [TypedCapabilityStep, ExecutionContext], Awaitable[TypedCapabilityStep]
]
CancellationCheck = Callable[[], bool]
ExecutionHandler = Callable[
    [PreparedCapabilityStep, ExecutorEventSink, CancellationCheck | None],
    Awaitable[CapabilityExecutionReport],
]
StopHandler = Callable[[StopContext, ExecutorEventSink], Awaitable[ExecutorStopResult]]


@dataclass(frozen=True, slots=True)
class CapabilityExecutionRegistration:
    """One supported input schema and its effect-free prepare/effect handlers."""

    capability_id: str
    input_schema_version: str
    prepare: PreparationHandler
    execute: ExecutionHandler

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise ValueError("capability_id must not be blank")
        if not self.input_schema_version.strip():
            raise ValueError("input_schema_version must not be blank")


class InMemoryCapabilityExecutor:
    """Deterministic adapter used before HTTP or robot integration exists."""

    def __init__(
        self,
        *,
        snapshot_handler: SnapshotHandler,
        registrations: Iterable[CapabilityExecutionRegistration],
        stop_handler: StopHandler,
    ) -> None:
        self._snapshot_handler = snapshot_handler
        self._registrations: dict[tuple[str, str], CapabilityExecutionRegistration] = {}
        self._capability_ids: set[str] = set()
        for registration in registrations:
            key = (registration.capability_id, registration.input_schema_version)
            if key in self._registrations:
                raise ValueError(f"duplicate capability execution registration: {key!r}")
            self._registrations[key] = registration
            self._capability_ids.add(registration.capability_id)
        self._stop_handler = stop_handler

    async def snapshot(
        self,
        request: SnapshotRequest,
        context: ObservationContext,
    ) -> ObservationSnapshot:
        candidate = await self._snapshot_handler(request, context)
        snapshot = ObservationSnapshot.model_validate(candidate.model_dump(mode="json"))
        if snapshot.robot_id != request.robot_id:
            raise ExecutionContextMismatchError(
                "snapshot robot identity does not match the request"
            )
        return snapshot

    async def prepare(
        self,
        step: TypedCapabilityStep,
        context: ExecutionContext,
    ) -> PreparedCapabilityStep:
        detached_step = TypedCapabilityStep.model_validate(step.model_dump(mode="json"))
        detached_context = ExecutionContext.model_validate(context.model_dump(mode="json"))
        registration = self._registration_for(
            detached_step.capability_id,
            detached_step.input_schema_version,
        )
        try:
            candidate = await registration.prepare(detached_step, detached_context)
            canonical_step = TypedCapabilityStep.model_validate(candidate.model_dump(mode="json"))
        except CapabilityPreparationError:
            raise
        except (TypeError, ValueError) as exc:
            raise CapabilityPreparationError(
                f"capability input failed preparation: {detached_step.capability_id} "
                f"schema {detached_step.input_schema_version}"
            ) from exc
        if (
            canonical_step.capability_id != registration.capability_id
            or canonical_step.input_schema_version != registration.input_schema_version
        ):
            raise CapabilityPreparationError(
                "preparation handler changed capability identity or schema version"
            )
        return PreparedCapabilityStep(
            step=canonical_step,
            context=detached_context,
            binding_sha256=capability_binding_sha256(canonical_step, detached_context),
        )

    async def execute(
        self,
        prepared: PreparedCapabilityStep,
        context: ExecutionContext,
        events: ExecutorEventSink,
        *,
        is_cancelled: CancellationCheck | None = None,
    ) -> CapabilityExecutionReport:
        detached_context = ExecutionContext.model_validate(context.model_dump(mode="json"))
        detached_prepared = PreparedCapabilityStep.model_validate(prepared.model_dump(mode="json"))
        expected = capability_binding_sha256(detached_prepared.step, detached_context)
        if (
            detached_prepared.context != detached_context
            or detached_prepared.binding_sha256 != expected
        ):
            raise ExecutionContextMismatchError(
                "prepared capability does not match the supplied execution context or binding"
            )
        registration = self._registration_for(
            detached_prepared.step.capability_id,
            detached_prepared.step.input_schema_version,
        )
        candidate = await registration.execute(
            detached_prepared,
            _DetachedEventSink(events),
            is_cancelled,
        )
        return CapabilityExecutionReport.model_validate(candidate.model_dump(mode="json"))

    async def stop(
        self,
        context: StopContext,
        events: ExecutorEventSink,
    ) -> ExecutorStopResult:
        detached_context = StopContext.model_validate(context.model_dump(mode="json"))
        candidate = await self._stop_handler(
            detached_context,
            _DetachedEventSink(events),
        )
        return ExecutorStopResult.model_validate(candidate.model_dump(mode="json"))

    def _registration_for(
        self,
        capability_id: str,
        input_schema_version: str,
    ) -> CapabilityExecutionRegistration:
        try:
            return self._registrations[(capability_id, input_schema_version)]
        except KeyError as exc:
            if capability_id in self._capability_ids:
                raise UnsupportedCapabilitySchemaError(
                    "capability input schema is not registered in this executor: "
                    f"{capability_id} version {input_schema_version}"
                ) from exc
            raise CapabilityUnavailableError(
                f"capability is not registered in this executor: {capability_id}"
            ) from exc
