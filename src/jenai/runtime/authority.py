"""Single-owner in-memory Robot Runtime Authority."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Coroutine, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import Field, field_validator

from jenai.runtime.executor import (
    CapabilityExecutor,
    CapabilityUnavailableError,
    ExecutorEventSink,
)
from jenai.runtime.immutable_json import FrozenJsonObject, ImmutableJsonObject
from jenai.runtime.models import (
    AuthorityContext,
    CancelContext,
    CapabilityExecutionReport,
    ExecutionContext,
    ExecutorCancelResult,
    ExecutorEvent,
    ExecutorStopResult,
    RuntimeModel,
    StopContext,
    StopTrigger,
    TypedCapabilityStep,
)
from jenai.schemas import TaskOutcome


class RuntimeAuthorityError(RuntimeError):
    """Base error for fail-closed Authority admission and lifecycle operations."""


class StaleSafetyEpochError(RuntimeAuthorityError):
    """The request was created under an older safety epoch."""


class LeaseBusyError(RuntimeAuthorityError):
    """Another effectful Task already owns the robot command lease."""


class ApprovalNotPendingError(RuntimeAuthorityError):
    """The Approval cannot be resolved in its current state."""


class ApprovalDigestMismatchError(RuntimeAuthorityError):
    """The exact action no longer matches the action held for Approval."""


class TaskStatus(StrEnum):
    AWAITING_APPROVAL = "awaiting_approval"
    ACCEPTED = "accepted"
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class SubmitTask(RuntimeModel):
    """Product-facing typed high-level Task request (MissionSpec input)."""

    robot_id: str
    capability_id: str
    input_schema_version: str
    input: ImmutableJsonObject
    expected_safety_epoch: int = Field(ge=0)

    @field_validator("robot_id", "capability_id", "input_schema_version")
    @classmethod
    def required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped


class ResolveApproval(RuntimeModel):
    task_id: str
    approval_id: str
    decision: ApprovalDecision
    expected_safety_epoch: int = Field(ge=0)


class CancelTask(RuntimeModel):
    command_id: str
    reason: str
    expected_safety_epoch: int = Field(ge=0)

    @field_validator("command_id", "reason")
    @classmethod
    def required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped


class StopRequest(RuntimeModel):
    robot_id: str
    idempotency_key: str

    @field_validator("robot_id", "idempotency_key")
    @classmethod
    def required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped


class StopView(RuntimeModel):
    stop_id: str
    safety_epoch: int = Field(ge=1)
    accepted_sequence: int = Field(ge=1)
    preempted_task_id: str | None = None
    replayed: bool
    request_accepted: bool
    cancel_requested: bool
    cancel_acknowledged: bool | None = None
    terminal: bool
    limitations: tuple[str, ...] = ()


class ApprovalView(RuntimeModel):
    approval_id: str
    task_id: str
    command_id: str
    capability_id: str
    safety_epoch: int = Field(ge=0)
    expires_at: datetime


class RuntimeTaskReceipt(RuntimeModel):
    """Immutable in-memory Receipt owned by the Authority."""

    task_id: str
    command_id: str
    outcome: TaskOutcome
    finished_at: datetime
    terminal_sequence: int = Field(ge=1)
    terminal_data: ImmutableJsonObject = Field(default_factory=lambda: FrozenJsonObject({}))


class TaskView(RuntimeModel):
    """Detached MissionRun projection of one accepted Task and Workflow Instance."""

    task_id: str
    command_id: str
    robot_id: str
    capability_id: str
    status: TaskStatus
    safety_epoch: int = Field(ge=0)
    latest_event_sequence: int = Field(ge=0)
    current_step: int | None = Field(default=None, ge=0)
    pending_approval: ApprovalView | None = None
    outcome: TaskOutcome | None = None
    receipt: RuntimeTaskReceipt | None = None


class CancelView(RuntimeModel):
    task: TaskView
    request_accepted: bool
    cancel_requested: bool
    cancel_acknowledged: bool | None = None
    limitations: tuple[str, ...] = ()


class RuntimeEvent(RuntimeModel):
    """Authority-sequenced immutable task fact."""

    sequence: int = Field(ge=1)
    kind: str
    occurred_at: datetime
    task_id: str | None = None
    command_id: str | None = None
    data: ImmutableJsonObject = Field(default_factory=lambda: FrozenJsonObject({}))


class RuntimeSnapshot(RuntimeModel):
    """Atomic detached projection whose head matches the included Event journal."""

    safety_epoch: int = Field(ge=0)
    tasks: tuple[TaskView, ...]
    events: tuple[RuntimeEvent, ...]
    head_sequence: int = Field(ge=0)
    active_lease_task_id: str | None = None
    effectful_admission_blocked_reason: str | None = None


BuildSteps = Callable[[SubmitTask], tuple[TypedCapabilityStep, ...]]
CompletionEvaluator = Callable[[tuple[CapabilityExecutionReport, ...]], TaskOutcome | str]
_ResultT = TypeVar("_ResultT")


@dataclass(frozen=True, slots=True)
class RuntimeTaskRegistration:
    """Authority-owned deterministic Workflow definition for one typed Task schema."""

    capability_id: str
    input_schema_version: str
    workflow_definition_version: str
    effectful: bool
    requires_approval: bool
    approval_ttl: timedelta
    build_steps: BuildSteps
    evaluate_completion: CompletionEvaluator

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise ValueError("capability_id must not be blank")
        if not self.input_schema_version.strip():
            raise ValueError("input_schema_version must not be blank")
        if not self.workflow_definition_version.strip():
            raise ValueError("workflow_definition_version must not be blank")
        if self.approval_ttl <= timedelta(0):
            raise ValueError("approval_ttl must be positive")


@dataclass(frozen=True, slots=True)
class _ApprovalRecord:
    view: ApprovalView
    action_sha256: str


@dataclass(slots=True)
class _TaskRecord:
    request: SubmitTask
    registration: RuntimeTaskRegistration
    task_id: str
    command_id: str
    steps: tuple[TypedCapabilityStep, ...]
    action_sha256: str
    status: TaskStatus
    safety_epoch: int
    fencing_token: int
    latest_event_sequence: int = 0
    approval: _ApprovalRecord | None = None
    current_step: int | None = None
    outcome: TaskOutcome | None = None
    receipt: RuntimeTaskReceipt | None = None


@dataclass(slots=True)
class _StopOperation:
    idempotency_keys: set[str]
    future: asyncio.Future[StopView]
    context: StopContext
    stop_id: str
    accepted_sequence: int
    preempted_task_id: str | None
    active_record: _TaskRecord | None
    active_effect_or_uncertainty: bool
    accept_events: bool = True


@dataclass(slots=True)
class _CancelOperation:
    task_id: str
    future: asyncio.Future[CancelView]
    context: CancelContext
    call_executor: bool
    effectful_cleanup: bool
    accept_events: bool = True


def _default_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class _AuthorityEventSink(ExecutorEventSink):
    def __init__(
        self,
        authority: InMemoryRuntimeAuthority,
        task_id: str,
        context: ExecutionContext,
    ) -> None:
        self._authority = authority
        self._task_id = task_id
        self._context = context

    async def publish(self, event: ExecutorEvent) -> None:
        await self._authority._publish_executor_event(
            self._task_id,
            self._context,
            event,
        )


class _RuntimeStopEventSink(ExecutorEventSink):
    def __init__(self, authority: InMemoryRuntimeAuthority, context: StopContext) -> None:
        self._authority = authority
        self._context = context

    async def publish(self, event: ExecutorEvent) -> None:
        await self._authority._publish_stop_event(self._context, event)


class _RuntimeCancelEventSink(ExecutorEventSink):
    def __init__(self, authority: InMemoryRuntimeAuthority, context: CancelContext) -> None:
        self._authority = authority
        self._context = context

    async def publish(self, event: ExecutorEvent) -> None:
        await self._authority._publish_cancel_event(self._context, event)


class InMemoryRuntimeAuthority:
    """Own Task, Approval, lease, Event, Outcome, and Receipt truth in memory."""

    def __init__(
        self,
        *,
        runtime_id: str,
        boot_id: str,
        robot_id: str,
        authority_generation: int,
        initial_safety_epoch: int,
        executor: CapabilityExecutor,
        registrations: Iterable[RuntimeTaskRegistration],
        now: Callable[[], datetime] | None = None,
        stop_timeout: timedelta = timedelta(seconds=5),
        cancel_timeout: timedelta = timedelta(seconds=5),
        new_id: Callable[[str], str] | None = None,
    ) -> None:
        if authority_generation < 1:
            raise ValueError("authority_generation must be positive")
        if initial_safety_epoch < 0:
            raise ValueError("initial_safety_epoch must not be negative")
        if stop_timeout <= timedelta(0):
            raise ValueError("stop_timeout must be positive")
        if cancel_timeout <= timedelta(0):
            raise ValueError("cancel_timeout must be positive")
        self._runtime_id = runtime_id
        self._boot_id = boot_id
        self._robot_id = robot_id
        self._authority_generation = authority_generation
        self._safety_epoch = initial_safety_epoch
        self._executor = executor
        self._registrations: dict[tuple[str, str], RuntimeTaskRegistration] = {}
        for registration in registrations:
            key = (registration.capability_id, registration.input_schema_version)
            if key in self._registrations:
                raise ValueError(f"duplicate Runtime Task registration: {key!r}")
            self._registrations[key] = registration
        self._now = now or (lambda: datetime.now(UTC))
        self._new_id = new_id or _default_id
        self._tasks: dict[str, _TaskRecord] = {}
        self._stop_timeout_s = stop_timeout.total_seconds()
        self._cancel_timeout_s = cancel_timeout.total_seconds()
        self._events: list[RuntimeEvent] = []
        self._active_lease_task_id: str | None = None
        self._effectful_admission_blocked_reason: str | None = None
        self._next_fencing_token = 1
        self._lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._execution_tasks: dict[str, asyncio.Task[None]] = {}
        self._active_stop: _StopOperation | None = None
        self._stop_results: dict[str, StopView] = {}
        self._stop_inflight: dict[str, asyncio.Future[StopView]] = {}
        self._cancel_results: dict[str, CancelView] = {}
        self._cancel_inflight: dict[str, asyncio.Future[CancelView]] = {}
        self._cancel_operations: dict[str, _CancelOperation] = {}

    async def submit(self, request: SubmitTask) -> TaskView:
        detached = SubmitTask.model_validate(request.model_dump(mode="json"))
        async with self._lock:
            if detached.robot_id != self._robot_id:
                raise RuntimeAuthorityError("Task robot does not match this Runtime Authority")
            if detached.expected_safety_epoch != self._safety_epoch:
                raise StaleSafetyEpochError("Task expected safety epoch is stale")
            self._expire_approvals()
            registration = self._registration_for(detached)
            if registration.effectful and (
                self._effectful_admission_blocked_reason is not None
                or (not registration.requires_approval and self._active_lease_task_id is not None)
            ):
                raise LeaseBusyError(self._effectful_admission_error())
            steps = self._build_steps(detached, registration)
            task_id = self._new_id("task")
            command_id = self._new_id("command")
            action_sha256 = self._action_sha256(detached, registration, steps)
            status = (
                TaskStatus.AWAITING_APPROVAL
                if registration.requires_approval
                else TaskStatus.ACCEPTED
            )
            record = _TaskRecord(
                request=detached,
                registration=registration,
                task_id=task_id,
                command_id=command_id,
                steps=steps,
                action_sha256=action_sha256,
                status=status,
                safety_epoch=self._safety_epoch,
                fencing_token=self._next_fencing_token,
            )
            self._next_fencing_token += 1
            if registration.effectful and not registration.requires_approval:
                self._active_lease_task_id = task_id
            self._tasks[task_id] = record
            self._append_event(record, "TaskAccepted")
            if registration.requires_approval:
                view = ApprovalView(
                    approval_id=self._new_id("approval"),
                    task_id=task_id,
                    command_id=command_id,
                    capability_id=detached.capability_id,
                    safety_epoch=self._safety_epoch,
                    expires_at=self._now() + registration.approval_ttl,
                )
                record.approval = _ApprovalRecord(view=view, action_sha256=action_sha256)
                self._append_event(record, "ApprovalRequired")
                return self._view(record)
            self._start_background_execution(record)
            return self._view(record)

    async def resolve_approval(self, request: ResolveApproval) -> TaskView:
        detached = ResolveApproval.model_validate(request.model_dump(mode="json"))
        async with self._lock:
            self._expire_approvals()
            record = self._task_for(detached.task_id)
            approval = record.approval
            if (
                record.status != TaskStatus.AWAITING_APPROVAL
                or approval is None
                or approval.view.approval_id != detached.approval_id
            ):
                raise ApprovalNotPendingError("Approval is no longer pending")
            if detached.expected_safety_epoch != self._safety_epoch:
                raise StaleSafetyEpochError("Approval expected safety epoch is stale")
            current_steps = self._build_steps(record.request, record.registration)
            current_digest = self._action_sha256(
                record.request,
                record.registration,
                current_steps,
            )
            if approval.action_sha256 != current_digest or record.action_sha256 != current_digest:
                record.approval = None
                self._append_event(
                    record,
                    "ApprovalInvalidated",
                    data={"reason": "action_digest_mismatch"},
                )
                self._finish_task(record, TaskOutcome.BLOCKED, "TaskFinished")
                raise ApprovalDigestMismatchError("Approval exact action binding changed")
            if detached.decision == ApprovalDecision.REJECT:
                record.approval = None
                self._append_event(record, "ApprovalResolved", data={"decision": "reject"})
                self._finish_task(record, TaskOutcome.BLOCKED, "TaskFinished")
                return self._view(record)
            if record.registration.effectful and self._effectful_admission_is_blocked():
                raise LeaseBusyError(self._effectful_admission_error())
            record.approval = None
            self._append_event(record, "ApprovalResolved", data={"decision": "approve"})
            if record.registration.effectful:
                self._active_lease_task_id = record.task_id
            record.status = TaskStatus.ACCEPTED
            self._start_background_execution(record)
            return self._view(record)

    async def cancel(self, request: CancelTask) -> CancelView:
        detached = CancelTask.model_validate(request.model_dump(mode="json"))
        stop_future: asyncio.Future[StopView] | None = None
        cancel_future: asyncio.Future[CancelView] | None = None
        async with self._lock:
            if detached.expected_safety_epoch != self._safety_epoch:
                raise StaleSafetyEpochError("Cancel expected safety epoch is stale")
            record = self._task_for_command(detached.command_id)
            if self._active_stop is not None and record.outcome is None:
                stop_future = self._active_stop.future
            else:
                completed = self._cancel_results.get(record.task_id)
                if completed is not None:
                    return completed
                cancel_future = self._cancel_inflight.get(record.task_id)
                if cancel_future is None:
                    operation = self._begin_cancel(record, detached.reason)
                    cancel_future = operation.future
                    self._track_background(self._complete_cancel(operation))
        if stop_future is not None:
            stop_result = await asyncio.shield(stop_future)
            async with self._lock:
                record = self._task_for_command(detached.command_id)
                return CancelView(
                    task=self._view(record),
                    request_accepted=stop_result.request_accepted,
                    cancel_requested=stop_result.cancel_requested,
                    cancel_acknowledged=stop_result.cancel_acknowledged,
                    limitations=("task_cancel_superseded_by_robot_stop", *stop_result.limitations),
                )
        if cancel_future is None:
            raise RuntimeAuthorityError("cancel operation was not created")
        return await asyncio.shield(cancel_future)

    async def stop(self, request: StopRequest) -> StopView:
        detached = StopRequest.model_validate(request.model_dump(mode="json"))
        if detached.robot_id != self._robot_id:
            raise RuntimeAuthorityError("STOP robot does not match this Runtime Authority")
        async with self._lock:
            completed = self._stop_results.get(detached.idempotency_key)
            if completed is not None:
                return completed.model_copy(update={"replayed": True})
            wait_for = self._stop_inflight.get(detached.idempotency_key)
            replayed = wait_for is not None
            if wait_for is None and self._active_stop is not None:
                operation = self._active_stop
                operation.idempotency_keys.add(detached.idempotency_key)
                self._stop_inflight[detached.idempotency_key] = operation.future
                wait_for = operation.future
                replayed = True
            elif wait_for is None:
                operation = self._begin_stop(detached)
                wait_for = operation.future
                self._track_background(self._complete_stop(operation))
        result = await asyncio.shield(wait_for)
        return result.model_copy(update={"replayed": True}) if replayed else result

    def _begin_cancel(self, record: _TaskRecord, reason: str) -> _CancelOperation:
        future: asyncio.Future[CancelView] = asyncio.get_running_loop().create_future()
        self._cancel_inflight[record.task_id] = future
        call_executor = record.status in {TaskStatus.ACCEPTED, TaskStatus.RUNNING}
        if call_executor and record.registration.effectful:
            self._effectful_admission_blocked_reason = f"task_cancel:{record.task_id}"
        self._cancel_execution(record.task_id)
        if record.approval is not None:
            record.approval = None
            self._append_event(record, "ApprovalInvalidated", data={"reason": "task_cancel"})
        if self._active_lease_task_id == record.task_id:
            self._active_lease_task_id = None
        if record.outcome is None:
            record.status = TaskStatus.STOPPING
            self._append_event(record, "TaskProgressed", data={"phase": "cancel_requested"})
        context = CancelContext(
            authority=self._authority_context(),
            robot_id=self._robot_id,
            task_id=record.task_id,
            command_id=record.command_id,
            reason=reason,
        )
        operation = _CancelOperation(
            task_id=record.task_id,
            future=future,
            context=context,
            call_executor=call_executor and record.outcome is None,
            effectful_cleanup=(
                call_executor and record.registration.effectful and record.outcome is None
            ),
        )
        self._cancel_operations[record.task_id] = operation
        return operation

    async def _complete_cancel(self, operation: _CancelOperation) -> None:
        if operation.call_executor:
            result = await self._request_executor_cancel(operation.context)
        else:
            result = ExecutorCancelResult(
                request_accepted=True,
                cancel_requested=False,
                limitations=("no_active_effect_to_cancel",),
            )
        async with self._lock:
            record = self._task_for(operation.task_id)
            cleanup_confirmed = self._cleanup_confirmed(
                result.request_accepted,
                result.cancel_requested,
                result.cancel_acknowledged,
                active_effect=operation.call_executor,
            )
            fence = f"task_cancel:{record.task_id}"
            if self._effectful_admission_blocked_reason == fence:
                self._effectful_admission_blocked_reason = (
                    None if cleanup_confirmed else f"task_cancel_unverified:{record.task_id}"
                )
            if record.outcome is None:
                self._finish_task(
                    record,
                    TaskOutcome.CANCELLED if cleanup_confirmed else TaskOutcome.UNAVAILABLE,
                    "TaskFinished",
                    data={
                        "request_accepted": result.request_accepted,
                        "cancel_requested": result.cancel_requested,
                        "cancel_acknowledged": result.cancel_acknowledged,
                        "limitations": result.limitations,
                    },
                )
            view = CancelView(
                task=self._view(record),
                request_accepted=result.request_accepted,
                cancel_requested=result.cancel_requested,
                cancel_acknowledged=result.cancel_acknowledged,
                limitations=result.limitations,
            )
            self._cancel_results[record.task_id] = view
            self._cancel_inflight.pop(record.task_id, None)
            self._cancel_operations.pop(record.task_id, None)
            if not operation.future.done():
                operation.future.set_result(view)

    async def _request_executor_cancel(self, context: CancelContext) -> ExecutorCancelResult:
        try:
            completed, candidate = await self._bounded_call(
                self._executor.cancel(context, _RuntimeCancelEventSink(self, context)),
                self._cancel_timeout_s,
            )
            if not completed or candidate is None:
                operation = self._cancel_operations.get(context.task_id)
                if operation is not None and operation.context == context:
                    operation.accept_events = False
                return ExecutorCancelResult(
                    request_accepted=False,
                    cancel_requested=True,
                    limitations=("executor_cancel_timeout",),
                )
            return ExecutorCancelResult.model_validate(candidate.model_dump(mode="json"))
        except Exception as exc:
            return ExecutorCancelResult(
                request_accepted=False,
                cancel_requested=True,
                limitations=(f"executor_cancel_failed:{type(exc).__name__}",),
            )

    def _begin_stop(self, request: StopRequest) -> _StopOperation:
        future: asyncio.Future[StopView] = asyncio.get_running_loop().create_future()
        self._safety_epoch += 1
        preexisting_cleanup_uncertainty = self._effectful_admission_blocked_reason is not None
        effectful_cancel_inflight = any(
            operation.effectful_cleanup for operation in self._cancel_operations.values()
        )
        active_record = (
            self._tasks.get(self._active_lease_task_id)
            if self._active_lease_task_id is not None
            else None
        )
        stop_id = self._new_id("stop")
        self._effectful_admission_blocked_reason = f"stop:{stop_id}"
        for cancel_operation in self._cancel_operations.values():
            cancel_operation.accept_events = False
        accepted_sequence = self._append_runtime_event(
            "SafetyEpochAdvanced",
            record=active_record,
            data={
                "safety_epoch": self._safety_epoch,
                "reason": "operator",
                "stop_id": stop_id,
            },
        )
        preempted_task_id = active_record.task_id if active_record is not None else None
        self._active_lease_task_id = None
        for record in self._tasks.values():
            if record.outcome is not None:
                continue
            self._cancel_execution(record.task_id)
            if record.approval is not None:
                record.approval = None
                self._append_event(record, "ApprovalInvalidated", data={"reason": "stop"})
            record.status = TaskStatus.STOPPING
        context = StopContext(
            authority=AuthorityContext(
                runtime_id=self._runtime_id,
                boot_id=self._boot_id,
                authority_generation=self._authority_generation,
                safety_epoch=self._safety_epoch,
            ),
            robot_id=self._robot_id,
            stop_id=stop_id,
            trigger=StopTrigger.OPERATOR,
        )
        operation = _StopOperation(
            idempotency_keys={request.idempotency_key},
            future=future,
            context=context,
            stop_id=stop_id,
            accepted_sequence=accepted_sequence,
            preempted_task_id=preempted_task_id,
            active_record=active_record,
            active_effect_or_uncertainty=(
                active_record is not None
                or effectful_cancel_inflight
                or preexisting_cleanup_uncertainty
            ),
        )
        self._active_stop = operation
        self._stop_inflight[request.idempotency_key] = future
        return operation

    async def _complete_stop(self, operation: _StopOperation) -> None:
        result = await self._request_executor_stop(operation.context)
        async with self._lock:
            self._finish_stop(operation, result)

    async def _request_executor_stop(self, context: StopContext) -> ExecutorStopResult:
        try:
            completed, executor_result = await self._bounded_call(
                self._executor.stop(context, _RuntimeStopEventSink(self, context)),
                self._stop_timeout_s,
            )
            if not completed or executor_result is None:
                if self._active_stop is not None and self._active_stop.context == context:
                    self._active_stop.accept_events = False
                return ExecutorStopResult(
                    request_accepted=False,
                    cancel_requested=True,
                    limitations=("executor_stop_timeout",),
                )
            return ExecutorStopResult.model_validate(executor_result.model_dump(mode="json"))
        except Exception as exc:
            return ExecutorStopResult(
                request_accepted=False,
                cancel_requested=False,
                limitations=(f"executor_stop_failed:{type(exc).__name__}",),
            )

    def _finish_stop(
        self,
        operation: _StopOperation,
        executor_result: ExecutorStopResult,
    ) -> None:
        cleanup_confirmed = self._cleanup_confirmed(
            executor_result.request_accepted,
            executor_result.cancel_requested,
            executor_result.cancel_acknowledged,
            active_effect=operation.active_effect_or_uncertainty,
        )
        fence = f"stop:{operation.stop_id}"
        if self._effectful_admission_blocked_reason == fence:
            self._effectful_admission_blocked_reason = (
                None if cleanup_confirmed else f"stop_unverified:{operation.stop_id}"
            )
        for record in self._tasks.values():
            if record.outcome is None and record.status == TaskStatus.STOPPING:
                self._finish_task(
                    record,
                    TaskOutcome.CANCELLED if cleanup_confirmed else TaskOutcome.UNAVAILABLE,
                    "TaskFinished",
                    data={
                        "stop_id": operation.stop_id,
                        "request_accepted": executor_result.request_accepted,
                        "cancel_requested": executor_result.cancel_requested,
                        "cancel_acknowledged": executor_result.cancel_acknowledged,
                        "limitations": executor_result.limitations,
                    },
                )
        self._append_runtime_event(
            "StopFinished",
            record=operation.active_record,
            data={
                "stop_id": operation.stop_id,
                "safety_epoch": operation.context.authority.safety_epoch,
                "request_accepted": executor_result.request_accepted,
                "cancel_requested": executor_result.cancel_requested,
                "cancel_acknowledged": executor_result.cancel_acknowledged,
                "limitations": executor_result.limitations,
                "cleanup_confirmed": cleanup_confirmed,
            },
        )
        result = StopView(
            stop_id=operation.stop_id,
            safety_epoch=operation.context.authority.safety_epoch,
            accepted_sequence=operation.accepted_sequence,
            preempted_task_id=operation.preempted_task_id,
            replayed=False,
            request_accepted=True,
            cancel_requested=executor_result.cancel_requested,
            cancel_acknowledged=executor_result.cancel_acknowledged,
            terminal=True,
            limitations=executor_result.limitations,
        )
        for key in operation.idempotency_keys:
            self._stop_results[key] = result
            self._stop_inflight.pop(key, None)
        if self._active_stop is operation:
            self._active_stop = None
        if not operation.future.done():
            operation.future.set_result(result)

    async def observe(self) -> RuntimeSnapshot:
        async with self._lock:
            self._expire_approvals()
            return RuntimeSnapshot(
                safety_epoch=self._safety_epoch,
                tasks=tuple(self._view(record) for record in self._tasks.values()),
                events=tuple(self._events),
                head_sequence=len(self._events),
                active_lease_task_id=self._active_lease_task_id,
                effectful_admission_blocked_reason=(self._effectful_admission_blocked_reason),
            )

    async def _run_workflow(self, task_id: str) -> None:
        reports: list[CapabilityExecutionReport] = []
        try:
            async with self._lock:
                record = self._task_for(task_id)
                if record.status != TaskStatus.ACCEPTED:
                    return
                record.status = TaskStatus.RUNNING
                self._append_event(record, "TaskStarted")
            for index, step in enumerate(record.steps):
                async with self._lock:
                    if record.status != TaskStatus.RUNNING:
                        return
                    record.current_step = index
                    context = self._execution_context(record)
                prepared = await self._executor.prepare(step, context)
                async with self._lock:
                    if not self._execution_is_current(record, context):
                        self._append_late_execution_event(
                            record,
                            "prepare_completed_after_revoke",
                        )
                        return
                report = await self._executor.execute(
                    prepared,
                    context,
                    _AuthorityEventSink(self, task_id, context),
                )
                reports.append(
                    CapabilityExecutionReport.model_validate(report.model_dump(mode="json"))
                )
                async with self._lock:
                    if not self._execution_is_current(record, context):
                        self._append_late_execution_event(
                            record,
                            "execute_completed_after_revoke",
                        )
                        return
            outcome = TaskOutcome(record.registration.evaluate_completion(tuple(reports)))
            async with self._lock:
                if not self._execution_is_current(record, self._execution_context(record)):
                    self._append_late_execution_event(record, "completion_after_revoke")
                    return
                self._finish_task(record, outcome, "TaskFinished")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with self._lock:
                record = self._task_for(task_id)
                if record.outcome is None and record.status != TaskStatus.STOPPING:
                    self._finish_task(
                        record,
                        (
                            TaskOutcome.UNAVAILABLE
                            if isinstance(exc, CapabilityUnavailableError)
                            else TaskOutcome.FAILED
                        ),
                        "TaskFinished",
                        data={"error_type": type(exc).__name__},
                    )

    async def _publish_executor_event(
        self,
        task_id: str,
        context: ExecutionContext,
        event: ExecutorEvent,
    ) -> None:
        detached = ExecutorEvent.model_validate(event.model_dump(mode="json"))
        async with self._lock:
            record = self._task_for(task_id)
            if not self._execution_is_current(record, context):
                self._append_late_execution_event(
                    record,
                    "executor_event_after_revoke",
                    executor_event_kind=detached.kind,
                )
                return
            self._append_event(
                record,
                "TaskProgressed",
                data={"executor_event_kind": detached.kind, "data": detached.data},
            )

    async def _publish_cancel_event(
        self,
        context: CancelContext,
        event: ExecutorEvent,
    ) -> None:
        detached = ExecutorEvent.model_validate(event.model_dump(mode="json"))
        async with self._lock:
            record = self._task_for(context.task_id)
            operation = self._cancel_operations.get(context.task_id)
            if (
                operation is None
                or operation.context != context
                or not operation.accept_events
                or context.authority != self._authority_context()
            ):
                self._append_late_execution_event(
                    record,
                    "cancel_event_after_operation",
                    executor_event_kind=detached.kind,
                )
                return
            self._append_event(
                record,
                "TaskProgressed",
                data={
                    "phase": "cancel_progress",
                    "executor_event_kind": detached.kind,
                    "data": detached.data,
                },
            )

    async def _publish_stop_event(
        self,
        context: StopContext,
        event: ExecutorEvent,
    ) -> None:
        detached = ExecutorEvent.model_validate(event.model_dump(mode="json"))
        async with self._lock:
            if (
                self._active_stop is None
                or self._active_stop.context != context
                or not self._active_stop.accept_events
                or context.authority != self._authority_context()
            ):
                self._append_runtime_event(
                    "StopProgressed",
                    data={
                        "stop_id": context.stop_id,
                        "safety_epoch": context.authority.safety_epoch,
                        "late": True,
                        "reason": "stop_event_after_operation",
                        "executor_event_kind": detached.kind,
                    },
                )
                return
            self._append_runtime_event(
                "StopProgressed",
                data={
                    "stop_id": context.stop_id,
                    "safety_epoch": context.authority.safety_epoch,
                    "executor_event_kind": detached.kind,
                    "data": detached.data,
                },
            )

    def _start_background_execution(self, record: _TaskRecord) -> None:
        task = asyncio.create_task(self._run_workflow(record.task_id))
        self._execution_tasks[record.task_id] = task
        self._background_tasks.add(task)

        def completed(done: asyncio.Task[None]) -> None:
            self._background_tasks.discard(done)
            if self._execution_tasks.get(record.task_id) is done:
                self._execution_tasks.pop(record.task_id, None)

        task.add_done_callback(completed)

    def _track_background(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _bounded_call(
        self,
        coroutine: Coroutine[Any, Any, _ResultT],
        timeout_s: float,
    ) -> tuple[bool, _ResultT | None]:
        task = asyncio.create_task(coroutine)
        done, _pending = await asyncio.wait({task}, timeout=timeout_s)
        if not done:
            task.cancel()
            self._quarantine_task(task)
            return False, None
        return True, task.result()

    def _quarantine_task(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.add(task)

        def consume_result(done: asyncio.Task[Any]) -> None:
            self._background_tasks.discard(done)
            if done.cancelled():
                return
            try:
                done.exception()
            except asyncio.CancelledError:
                return

        task.add_done_callback(consume_result)

    def _cancel_execution(self, task_id: str) -> None:
        task = self._execution_tasks.get(task_id)
        if task is not None and not task.done():
            task.cancel()

    def _authority_context(self) -> AuthorityContext:
        return AuthorityContext(
            runtime_id=self._runtime_id,
            boot_id=self._boot_id,
            authority_generation=self._authority_generation,
            safety_epoch=self._safety_epoch,
        )

    def _execution_context(self, record: _TaskRecord) -> ExecutionContext:
        return ExecutionContext(
            authority=self._authority_context(),
            fencing_token=record.fencing_token,
            robot_id=self._robot_id,
            task_id=record.task_id,
            command_id=record.command_id,
        )

    def _execution_is_current(self, record: _TaskRecord, context: ExecutionContext) -> bool:
        return (
            record.status == TaskStatus.RUNNING
            and context.authority == self._authority_context()
            and (not record.registration.effectful or self._active_lease_task_id == record.task_id)
            and context.fencing_token == record.fencing_token
        )

    def _effectful_admission_is_blocked(self) -> bool:
        return (
            self._active_lease_task_id is not None
            or self._effectful_admission_blocked_reason is not None
        )

    def _effectful_admission_error(self) -> str:
        if self._effectful_admission_blocked_reason is not None:
            return (
                "effectful admission is closed until prior cleanup is positively "
                f"reconciled: {self._effectful_admission_blocked_reason}"
            )
        return "another effectful Task owns the robot command lease"

    @staticmethod
    def _cleanup_confirmed(
        request_accepted: bool,
        cancel_requested: bool,
        cancel_acknowledged: bool | None,
        *,
        active_effect: bool,
    ) -> bool:
        if not request_accepted:
            return False
        if active_effect:
            return cancel_requested and cancel_acknowledged is True
        return not cancel_requested or cancel_acknowledged is True

    def _finish_task(
        self,
        record: _TaskRecord,
        outcome: TaskOutcome,
        event_kind: str,
        *,
        data: dict[str, object] | None = None,
    ) -> None:
        if record.outcome is not None:
            return
        record.outcome = outcome
        record.status = self._terminal_status(outcome)
        record.current_step = None
        record.approval = None
        if self._active_lease_task_id == record.task_id:
            self._active_lease_task_id = None
        terminal_data = {"outcome": outcome.value, **(data or {})}
        self._append_event(
            record,
            event_kind,
            data=terminal_data,
        )
        record.receipt = RuntimeTaskReceipt(
            task_id=record.task_id,
            command_id=record.command_id,
            outcome=outcome,
            finished_at=self._now(),
            terminal_sequence=record.latest_event_sequence,
            terminal_data=terminal_data,
        )

    def _expire_approvals(self) -> None:
        now = self._now()
        for record in self._tasks.values():
            approval = record.approval
            if approval is None or now < approval.view.expires_at:
                continue
            record.approval = None
            self._append_event(
                record,
                "ApprovalInvalidated",
                data={"reason": "expired"},
            )
            self._finish_task(record, TaskOutcome.BLOCKED, "TaskFinished")

    def _append_late_execution_event(
        self,
        record: _TaskRecord,
        reason: str,
        *,
        executor_event_kind: str | None = None,
    ) -> None:
        data: dict[str, object] = {"late": True, "reason": reason}
        if executor_event_kind is not None:
            data["executor_event_kind"] = executor_event_kind
        self._append_event(record, "TaskProgressed", data=data)

    def _append_event(
        self,
        record: _TaskRecord,
        kind: str,
        *,
        data: dict[str, object] | None = None,
    ) -> None:
        self._append_runtime_event(kind, record=record, data=data)

    def _append_runtime_event(
        self,
        kind: str,
        *,
        record: _TaskRecord | None = None,
        data: dict[str, object] | None = None,
    ) -> int:
        sequence = len(self._events) + 1
        event = RuntimeEvent(
            sequence=sequence,
            kind=kind,
            occurred_at=self._now(),
            task_id=record.task_id if record is not None else None,
            command_id=record.command_id if record is not None else None,
            data=data or {},
        )
        self._events.append(event)
        if record is not None:
            record.latest_event_sequence = sequence
        return sequence

    def _registration_for(self, request: SubmitTask) -> RuntimeTaskRegistration:
        try:
            return self._registrations[(request.capability_id, request.input_schema_version)]
        except KeyError as exc:
            raise RuntimeAuthorityError("Task capability schema is not registered") from exc

    @staticmethod
    def _build_steps(
        request: SubmitTask,
        registration: RuntimeTaskRegistration,
    ) -> tuple[TypedCapabilityStep, ...]:
        steps = tuple(
            TypedCapabilityStep.model_validate(step.model_dump(mode="json"))
            for step in registration.build_steps(request)
        )
        if not steps:
            raise RuntimeAuthorityError("Task Workflow must contain at least one step")
        return steps

    def _task_for(self, task_id: str) -> _TaskRecord:
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise RuntimeAuthorityError(f"unknown Task: {task_id}") from exc

    def _task_for_command(self, command_id: str) -> _TaskRecord:
        for record in self._tasks.values():
            if record.command_id == command_id:
                return record
        raise RuntimeAuthorityError(f"unknown command: {command_id}")

    @staticmethod
    def _action_sha256(
        request: SubmitTask,
        registration: RuntimeTaskRegistration,
        steps: tuple[TypedCapabilityStep, ...],
    ) -> str:
        payload = {
            "request": request.model_dump(mode="json"),
            "steps": [step.model_dump(mode="json") for step in steps],
            "workflow_definition_version": registration.workflow_definition_version,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _terminal_status(outcome: TaskOutcome) -> TaskStatus:
        if outcome in {
            TaskOutcome.SUCCEEDED,
            TaskOutcome.ARRIVED_UNVERIFIED,
            TaskOutcome.PARTIAL,
            TaskOutcome.ENDPOINT_MISMATCH,
        }:
            return TaskStatus.COMPLETED
        if outcome == TaskOutcome.BLOCKED:
            return TaskStatus.BLOCKED
        if outcome == TaskOutcome.CANCELLED:
            return TaskStatus.CANCELLED
        return TaskStatus.FAILED

    @staticmethod
    def _view(record: _TaskRecord) -> TaskView:
        return TaskView(
            task_id=record.task_id,
            command_id=record.command_id,
            robot_id=record.request.robot_id,
            capability_id=record.request.capability_id,
            status=record.status,
            safety_epoch=record.safety_epoch,
            latest_event_sequence=record.latest_event_sequence,
            current_step=record.current_step,
            pending_approval=record.approval.view if record.approval is not None else None,
            outcome=record.outcome,
            receipt=record.receipt,
        )
