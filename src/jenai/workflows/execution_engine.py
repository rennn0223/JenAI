"""Deterministic execution of one approved Golden Path plan."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jenai.schemas.models import TaskOutcome
from jenai.workflows.patrol_mission import ExecutionPlan, ExecutionStep, ReturnHomeStep


class EngineModel(BaseModel):
    """Strict immutable value crossing the execution boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _required_text(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("text must not be blank")
    return stripped


class StepEvidenceKind(StrEnum):
    NAVIGATION_TERMINAL = "navigation_terminal"
    ENDPOINT_POSE = "endpoint_pose"


class StepEvidence(EngineModel):
    kind: StepEvidenceKind
    evidence_id: str

    _normalize_evidence_id = field_validator("evidence_id")(_required_text)


class StepDisposition(StrEnum):
    """Atomic adapter observation; it does not decide mission policy."""

    SUCCEEDED = "succeeded"
    WAYPOINT_LOCAL_FAILURE = "waypoint_local_failure"
    NAVIGATION_SYSTEM_FAILURE = "navigation_system_failure"
    ENDPOINT_MISMATCH = "endpoint_mismatch"
    CANCELLED = "cancelled"


class StepResult(EngineModel):
    disposition: StepDisposition
    summary: str
    evidence: tuple[StepEvidence, ...] = ()
    cancel_acknowledged: bool | None = None
    position_error_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    _normalize_summary = field_validator("summary")(_required_text)

    @model_validator(mode="after")
    def successful_result_has_completion_evidence(self) -> StepResult:
        if self.disposition is not StepDisposition.SUCCEEDED:
            return self
        required = {
            StepEvidenceKind.NAVIGATION_TERMINAL,
            StepEvidenceKind.ENDPOINT_POSE,
        }
        observed = {item.kind for item in self.evidence}
        if self.position_error_m is None:
            raise ValueError("successful step requires a fresh endpoint pose error")
        if not required.issubset(observed):
            raise ValueError("successful step requires terminal and endpoint Evidence")
        return self


class StopResult(EngineModel):
    summary: str
    cancel_acknowledged: bool | None = None
    timed_out: bool = False

    _normalize_summary = field_validator("summary")(_required_text)


class CancellationView:
    """Live cancellation state without mutation authority."""

    __slots__ = ("__is_cancelled", "__wait_cancelled")

    def __init__(
        self,
        *,
        is_cancelled: Callable[[], bool],
        wait_cancelled: Callable[[], Awaitable[bool]],
    ) -> None:
        self.__is_cancelled = is_cancelled
        self.__wait_cancelled = wait_cancelled

    @property
    def cancelled(self) -> bool:
        return self.__is_cancelled()

    async def wait_cancelled(self) -> None:
        await self.__wait_cancelled()


@dataclass(frozen=True, slots=True)
class DispatchContext:
    """Immutable adapter view of one Engine-owned dispatch fence."""

    dispatch_id: str
    step_index: int
    attempt: int
    cancellation: CancellationView


class _DispatchFence:
    def __init__(self, *, step_index: int, attempt: int) -> None:
        self._cancelled = asyncio.Event()
        self.context = DispatchContext(
            dispatch_id=uuid4().hex,
            step_index=step_index,
            attempt=attempt,
            cancellation=CancellationView(
                is_cancelled=self._cancelled.is_set,
                wait_cancelled=self._cancelled.wait,
            ),
        )

    @property
    def valid(self) -> bool:
        return not self._cancelled.is_set()

    def invalidate(self) -> None:
        self._cancelled.set()


class AtomicStepAdapter(Protocol):
    """Narrow effect seam; mission policy remains in ExecutionEngine."""

    async def execute(
        self,
        step: ExecutionStep,
        dispatch_context: DispatchContext,
    ) -> StepResult: ...

    async def stop(self, active_dispatch: DispatchContext) -> StopResult: ...


class StepAttempt(EngineModel):
    attempt: int = Field(ge=1)
    result: StepResult


class StepRecord(EngineModel):
    step_index: int = Field(ge=0)
    step: ExecutionStep
    attempts: tuple[StepAttempt, ...]
    skipped: bool = False


class EngineDiagnostic(EngineModel):
    kind: Literal["late_step_result_after_stop"]
    step_index: int = Field(ge=0)
    attempt: int = Field(ge=1)
    disposition: StepDisposition
    summary: str


class ActiveExecuteUnsettledDiagnostic(EngineModel):
    kind: Literal["active_execute_unsettled_after_stop"] = "active_execute_unsettled_after_stop"
    step_index: int = Field(ge=0)
    attempt: int = Field(ge=1)
    timeout_s: float = Field(gt=0, allow_inf_nan=False)
    limitation: Literal["adapter_execute_task_did_not_settle"] = (
        "adapter_execute_task_did_not_settle"
    )


class ExecutionReport(EngineModel):
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome: TaskOutcome
    step_records: tuple[StepRecord, ...]
    diagnostics: tuple[EngineDiagnostic | ActiveExecuteUnsettledDiagnostic, ...] = ()
    stop_result: StopResult | None = None


class ExecutionCall(EngineModel):
    step: ExecutionStep
    dispatch_id: str
    step_index: int = Field(ge=0)
    attempt: int = Field(ge=1)


class ScriptedAtomicStepAdapter:
    """Deterministic fake that returns observations without choosing policy."""

    def __init__(self, results: Iterable[StepResult]) -> None:
        self._results = deque(
            StepResult.model_validate(result.model_dump(mode="json")) for result in results
        )
        self.execute_calls: list[ExecutionCall] = []
        self.stop_calls: list[DispatchContext] = []

    async def execute(
        self,
        step: ExecutionStep,
        dispatch_context: DispatchContext,
    ) -> StepResult:
        self.execute_calls.append(
            ExecutionCall(
                step=step,
                dispatch_id=dispatch_context.dispatch_id,
                step_index=dispatch_context.step_index,
                attempt=dispatch_context.attempt,
            )
        )
        if not self._results:
            raise AssertionError("no scripted atomic-step result remains")
        result = self._results.popleft()
        return StepResult.model_validate(result.model_dump(mode="json"))

    async def stop(self, active_dispatch: DispatchContext) -> StopResult:
        self.stop_calls.append(active_dispatch)
        return StopResult(summary="fake adapter stopped", cancel_acknowledged=True)


class ExecutionEngine:
    """Single mutable owner of progress for one approved ExecutionPlan."""

    def __init__(
        self,
        plan: ExecutionPlan,
        adapter: AtomicStepAdapter,
        *,
        stop_timeout_s: float = 1.0,
        execute_settlement_timeout_s: float = 1.0,
    ) -> None:
        if not isfinite(stop_timeout_s) or stop_timeout_s <= 0:
            raise ValueError("stop_timeout_s must be finite and greater than zero")
        if not isfinite(execute_settlement_timeout_s) or execute_settlement_timeout_s <= 0:
            raise ValueError("execute_settlement_timeout_s must be finite and greater than zero")
        self._plan = ExecutionPlan.model_validate(plan.model_dump(mode="json"))
        self._adapter = adapter
        self._stop_timeout_s = stop_timeout_s
        self._execute_settlement_timeout_s = execute_settlement_timeout_s
        self._lock = asyncio.Lock()
        self._run_started = False
        self._stop_requested = False
        self._active_fence: _DispatchFence | None = None
        self._active_execute_task: asyncio.Task[StepResult] | None = None
        self._unsettled_execute_tasks: set[asyncio.Task[StepResult]] = set()
        self._terminal_outcome: TaskOutcome | None = None
        self._stop_task: asyncio.Task[StopResult] | None = None
        self._stop_result: StopResult | None = None
        self._records: list[StepRecord] = []
        self._diagnostics: list[EngineDiagnostic | ActiveExecuteUnsettledDiagnostic] = []
        self._completed_step_indices: list[int] = []
        self._skipped_step_indices: list[int] = []
        self._current_step_index = 0
        self._attempt_count = 0

    async def run(self) -> ExecutionReport:
        try:
            return await self._run()
        except asyncio.CancelledError:
            await asyncio.shield(self.stop())
            await asyncio.shield(self._settle_cancelled_run())
            raise

    async def _run(self) -> ExecutionReport:
        early_report = await self._begin_run()
        if early_report is not None:
            return early_report

        for step_index, step in enumerate(self._plan.steps):
            attempts: list[StepAttempt] = []
            for attempt in range(1, self._plan.mission.policy.retry_count + 2):
                dispatch = await self._begin_attempt(step_index, attempt)
                if isinstance(dispatch, ExecutionReport):
                    return dispatch
                result = await self._observe_step(step, dispatch)
                if result is None:
                    return await self._finalize_stopped_attempt(
                        step=step,
                        step_index=step_index,
                        attempt=attempt,
                        attempts=attempts,
                    )
                retry, terminal_report, stopped = await self._accept_attempt(
                    step=step,
                    step_index=step_index,
                    attempt=attempt,
                    fence=dispatch,
                    result=result,
                    attempts=attempts,
                )
                if stopped:
                    return await self._finalize_stopped_attempt(
                        step=step,
                        step_index=step_index,
                        attempt=attempt,
                        attempts=attempts,
                    )
                if terminal_report is not None:
                    return terminal_report
                if not retry:
                    break

        return await self._finish_run()

    async def _begin_run(self) -> ExecutionReport | None:
        async with self._lock:
            if self._run_started:
                raise RuntimeError("an ExecutionEngine instance can run only once")
            self._run_started = True
            return self._report_locked() if self._stop_requested else None

    async def _begin_attempt(
        self,
        step_index: int,
        attempt: int,
    ) -> _DispatchFence | ExecutionReport:
        async with self._lock:
            if self._stop_requested:
                return self._report_locked()
            fence = _DispatchFence(step_index=step_index, attempt=attempt)
            self._active_fence = fence
            self._current_step_index = step_index
            self._attempt_count = attempt
            return fence

    async def _observe_step(
        self,
        step: ExecutionStep,
        fence: _DispatchFence,
    ) -> StepResult | None:
        execute_task = asyncio.create_task(self._call_adapter_execute(step, fence.context))
        async with self._lock:
            if self._active_fence is fence:
                self._active_execute_task = execute_task
        cancellation_wait = asyncio.create_task(fence.context.cancellation.wait_cancelled())
        try:
            done, _ = await asyncio.wait(
                {execute_task, cancellation_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not cancellation_wait.done():
                cancellation_wait.cancel()

        if fence.context.cancellation.cancelled:
            return None
        if execute_task not in done:
            raise RuntimeError("dispatch observation ended without a result")
        return execute_task.result()

    async def _call_adapter_execute(
        self,
        step: ExecutionStep,
        context: DispatchContext,
    ) -> StepResult:
        if context.cancellation.cancelled:
            raise asyncio.CancelledError
        try:
            observed = await self._adapter.execute(step, context)
            return StepResult.model_validate(observed.model_dump(mode="json"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return StepResult(
                disposition=StepDisposition.NAVIGATION_SYSTEM_FAILURE,
                summary=f"atomic adapter raised {type(exc).__name__}",
            )

    async def _finalize_stopped_attempt(
        self,
        *,
        step: ExecutionStep,
        step_index: int,
        attempt: int,
        attempts: list[StepAttempt],
    ) -> ExecutionReport:
        stop_result = await asyncio.shield(self.stop())
        async with self._lock:
            execute_task = self._active_execute_task
        late_result, settlement_diagnostic = await self._settle_active_execute_task(
            execute_task,
            step_index=step_index,
            attempt=attempt,
        )

        cancelled = StepResult(
            disposition=StepDisposition.CANCELLED,
            summary="active dispatch cancelled by STOP",
            cancel_acknowledged=stop_result.cancel_acknowledged,
        )
        async with self._lock:
            if late_result is not None:
                self._record_late_result(step_index, attempt, late_result)
            attempts.append(StepAttempt(attempt=attempt, result=cancelled))
            if settlement_diagnostic is not None:
                self._diagnostics.append(settlement_diagnostic)
            self._records.append(
                StepRecord(
                    step_index=step_index,
                    step=step,
                    attempts=tuple(attempts),
                )
            )
            self._active_fence = None
            self._active_execute_task = None
            self._stop_result = stop_result
            return self._report_locked()

    async def _settle_active_execute_task(
        self,
        execute_task: asyncio.Task[StepResult] | None,
        *,
        step_index: int,
        attempt: int,
    ) -> tuple[StepResult | None, ActiveExecuteUnsettledDiagnostic | None]:
        if execute_task is None:
            return None, None
        execute_task.cancel()
        done, _ = await asyncio.wait(
            {execute_task},
            timeout=self._execute_settlement_timeout_s,
        )
        if execute_task in done:
            return self._completed_task_result(execute_task), None

        self._unsettled_execute_tasks.add(execute_task)
        execute_task.add_done_callback(self._consume_unsettled_execute_task)
        return None, ActiveExecuteUnsettledDiagnostic(
            step_index=step_index,
            attempt=attempt,
            timeout_s=self._execute_settlement_timeout_s,
        )

    def _consume_unsettled_execute_task(
        self,
        execute_task: asyncio.Task[StepResult],
    ) -> None:
        self._unsettled_execute_tasks.discard(execute_task)
        self._completed_task_result(execute_task)

    async def _settle_cancelled_run(self) -> None:
        async with self._lock:
            execute_task = self._active_execute_task
            step_index = self._current_step_index
            attempt = self._attempt_count

        late_result, settlement_diagnostic = await self._settle_active_execute_task(
            execute_task,
            step_index=step_index,
            attempt=attempt,
        )
        async with self._lock:
            if late_result is not None:
                self._record_late_result(step_index, attempt, late_result)
            if settlement_diagnostic is not None:
                self._diagnostics.append(settlement_diagnostic)
            self._active_fence = None
            self._active_execute_task = None

    @staticmethod
    def _completed_task_result(
        execute_task: asyncio.Task[StepResult] | None,
    ) -> StepResult | None:
        if execute_task is None or not execute_task.done() or execute_task.cancelled():
            return None
        try:
            return execute_task.result()
        except Exception:
            return None

    async def _accept_attempt(
        self,
        *,
        step: ExecutionStep,
        step_index: int,
        attempt: int,
        fence: _DispatchFence,
        result: StepResult,
        attempts: list[StepAttempt],
    ) -> tuple[bool, ExecutionReport | None, bool]:
        async with self._lock:
            if self._stop_requested or not fence.valid:
                return False, None, True
            if self._active_fence is fence:
                self._active_fence = None
            self._active_execute_task = None

            result = self._apply_completion_truth(step, result)
            attempts.append(StepAttempt(attempt=attempt, result=result))
            if self._should_retry(result, attempt):
                return True, None, False

            skipped = (
                result.disposition is StepDisposition.WAYPOINT_LOCAL_FAILURE
                and not isinstance(step, ReturnHomeStep)
            )
            self._records.append(
                StepRecord(
                    step_index=step_index,
                    step=step,
                    attempts=tuple(attempts),
                    skipped=skipped,
                )
            )
            self._record_disposition_locked(step_index, result.disposition, skipped)
            if self._terminal_outcome is not None:
                return False, self._report_locked(), False
            return False, None, False

    def _should_retry(self, result: StepResult, attempt: int) -> bool:
        return (
            result.disposition is StepDisposition.WAYPOINT_LOCAL_FAILURE
            and attempt <= self._plan.mission.policy.retry_count
        )

    def _apply_completion_truth(
        self,
        step: ExecutionStep,
        result: StepResult,
    ) -> StepResult:
        if result.disposition is not StepDisposition.SUCCEEDED:
            return result
        if result.position_error_m is None:
            raise RuntimeError("validated success lost its endpoint pose error")
        if result.position_error_m <= step.position_tolerance_m:
            return result
        return StepResult(
            disposition=StepDisposition.ENDPOINT_MISMATCH,
            summary="endpoint pose exceeds the approved step tolerance",
            evidence=result.evidence,
            position_error_m=result.position_error_m,
        )

    def _record_late_result(
        self,
        step_index: int,
        attempt: int,
        result: StepResult,
    ) -> None:
        self._diagnostics.append(
            EngineDiagnostic(
                kind="late_step_result_after_stop",
                step_index=step_index,
                attempt=attempt,
                disposition=result.disposition,
                summary=result.summary,
            )
        )

    def _record_disposition_locked(
        self,
        step_index: int,
        disposition: StepDisposition,
        skipped: bool,
    ) -> None:
        if disposition is StepDisposition.SUCCEEDED:
            self._completed_step_indices.append(step_index)
            return
        if skipped:
            self._skipped_step_indices.append(step_index)
            return
        terminal_by_disposition = {
            StepDisposition.ENDPOINT_MISMATCH: TaskOutcome.ENDPOINT_MISMATCH,
            StepDisposition.CANCELLED: TaskOutcome.CANCELLED,
        }
        self._terminal_outcome = terminal_by_disposition.get(disposition, TaskOutcome.FAILED)

    async def _finish_run(self) -> ExecutionReport:
        async with self._lock:
            if self._terminal_outcome is None:
                self._terminal_outcome = (
                    TaskOutcome.PARTIAL if self._skipped_step_indices else TaskOutcome.SUCCEEDED
                )
            return self._report_locked()

    async def stop(self) -> StopResult:
        stop_task: asyncio.Task[StopResult] | None = None
        async with self._lock:
            if self._stop_result is not None:
                return self._stop_result
            if self._stop_task is not None:
                stop_task = self._stop_task
            elif self._terminal_outcome is not None:
                self._stop_result = StopResult(
                    summary="execution already terminal",
                    cancel_acknowledged=None,
                )
                return self._stop_result
            else:
                self._stop_requested = True
                self._terminal_outcome = TaskOutcome.CANCELLED
                fence = self._active_fence
                if fence is None:
                    self._stop_result = StopResult(
                        summary="stopped before active dispatch",
                        cancel_acknowledged=None,
                    )
                    return self._stop_result
                fence.invalidate()
                stop_task = asyncio.create_task(self._stop_active(fence.context))
                self._stop_task = stop_task

        if stop_task is None:
            raise RuntimeError("STOP coordination lost its task")
        result = await asyncio.shield(stop_task)
        async with self._lock:
            if self._stop_result is None:
                self._stop_result = result
            return self._stop_result

    async def _stop_active(self, context: DispatchContext) -> StopResult:
        stop_call = asyncio.create_task(self._call_adapter_stop(context))
        done, _ = await asyncio.wait({stop_call}, timeout=self._stop_timeout_s)
        if stop_call not in done:
            stop_call.cancel()
            stop_call.add_done_callback(self._consume_background_task_result)
            return StopResult(
                summary="adapter STOP timed out",
                cancel_acknowledged=False,
                timed_out=True,
            )
        return stop_call.result()

    async def _call_adapter_stop(self, context: DispatchContext) -> StopResult:
        try:
            observed = await self._adapter.stop(context)
            return StopResult.model_validate(observed.model_dump(mode="json"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return StopResult(
                summary=f"adapter STOP raised {type(exc).__name__}",
                cancel_acknowledged=False,
            )

    @staticmethod
    def _consume_background_task_result(task: asyncio.Task[StopResult]) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            return

    def _report_locked(self) -> ExecutionReport:
        if self._terminal_outcome is None:
            raise RuntimeError("execution has no terminal outcome")
        return ExecutionReport(
            plan_digest=self._plan.plan_digest,
            outcome=self._terminal_outcome,
            step_records=tuple(self._records),
            diagnostics=tuple(self._diagnostics),
            stop_result=self._stop_result,
        )
