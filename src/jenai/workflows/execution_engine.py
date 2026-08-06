"""Deterministic execution of one approved Golden Path plan."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable
from enum import StrEnum
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    evidence: tuple[str, ...] = ()
    cancel_acknowledged: bool | None = None
    position_error_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    _normalize_summary = field_validator("summary")(_required_text)

    @field_validator("evidence")
    @classmethod
    def normalize_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_required_text(item) for item in value)


class StopResult(EngineModel):
    summary: str
    cancel_acknowledged: bool | None = None

    _normalize_summary = field_validator("summary")(_required_text)


class DispatchToken:
    """Engine-owned cancellation fence shared with one atomic dispatch."""

    def __init__(self, *, step_index: int, attempt: int) -> None:
        self.dispatch_id = uuid4().hex
        self.step_index = step_index
        self.attempt = attempt
        self._valid = True

    @property
    def valid(self) -> bool:
        return self._valid

    def invalidate(self) -> None:
        self._valid = False


class AtomicStepAdapter(Protocol):
    """Narrow effect seam; mission policy remains in ExecutionEngine."""

    async def execute(
        self,
        step: ExecutionStep,
        dispatch_token: DispatchToken,
    ) -> StepResult: ...

    async def stop(self, active_dispatch: DispatchToken) -> StopResult: ...


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


class ExecutionReport(EngineModel):
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome: TaskOutcome
    step_records: tuple[StepRecord, ...]
    diagnostics: tuple[EngineDiagnostic, ...] = ()


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
        self.stop_calls: list[DispatchToken] = []

    async def execute(
        self,
        step: ExecutionStep,
        dispatch_token: DispatchToken,
    ) -> StepResult:
        self.execute_calls.append(
            ExecutionCall(
                step=step,
                dispatch_id=dispatch_token.dispatch_id,
                step_index=dispatch_token.step_index,
                attempt=dispatch_token.attempt,
            )
        )
        if not self._results:
            raise AssertionError("no scripted atomic-step result remains")
        result = self._results.popleft()
        return StepResult.model_validate(result.model_dump(mode="json"))

    async def stop(self, active_dispatch: DispatchToken) -> StopResult:
        self.stop_calls.append(active_dispatch)
        return StopResult(summary="fake adapter stopped", cancel_acknowledged=True)


class ExecutionEngine:
    """Single mutable owner of progress for one approved ExecutionPlan."""

    def __init__(self, plan: ExecutionPlan, adapter: AtomicStepAdapter) -> None:
        self._plan = ExecutionPlan.model_validate(plan.model_dump(mode="json"))
        self._adapter = adapter
        self._lock = asyncio.Lock()
        self._run_started = False
        self._stop_requested = False
        self._active_token: DispatchToken | None = None
        self._terminal_outcome: TaskOutcome | None = None
        self._stop_task: asyncio.Task[StopResult] | None = None
        self._stop_result: StopResult | None = None
        self._records: list[StepRecord] = []
        self._diagnostics: list[EngineDiagnostic] = []
        self._completed_step_indices: list[int] = []
        self._skipped_step_indices: list[int] = []
        self._current_step_index = 0
        self._attempt_count = 0

    async def run(self) -> ExecutionReport:
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
                retry, terminal_report = await self._accept_attempt(
                    step=step,
                    step_index=step_index,
                    attempt=attempt,
                    token=dispatch,
                    result=result,
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
    ) -> DispatchToken | ExecutionReport:
        async with self._lock:
            if self._stop_requested:
                return self._report_locked()
            token = DispatchToken(step_index=step_index, attempt=attempt)
            self._active_token = token
            self._current_step_index = step_index
            self._attempt_count = attempt
            return token

    async def _observe_step(
        self,
        step: ExecutionStep,
        token: DispatchToken,
    ) -> StepResult:
        try:
            observed = await self._adapter.execute(step, token)
            return StepResult.model_validate(observed.model_dump(mode="json"))
        except Exception as exc:
            return StepResult(
                disposition=StepDisposition.NAVIGATION_SYSTEM_FAILURE,
                summary=f"atomic adapter raised {type(exc).__name__}",
            )

    async def _accept_attempt(
        self,
        *,
        step: ExecutionStep,
        step_index: int,
        attempt: int,
        token: DispatchToken,
        result: StepResult,
        attempts: list[StepAttempt],
    ) -> tuple[bool, ExecutionReport | None]:
        async with self._lock:
            if self._active_token is token:
                self._active_token = None
            if self._stop_requested or not token.valid:
                self._record_late_result(step_index, attempt, result)
                return False, self._report_locked()

            attempts.append(StepAttempt(attempt=attempt, result=result))
            if self._should_retry(result, attempt):
                return True, None

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
                return False, self._report_locked()
            return False, None

    def _should_retry(self, result: StepResult, attempt: int) -> bool:
        return (
            result.disposition is StepDisposition.WAYPOINT_LOCAL_FAILURE
            and attempt <= self._plan.mission.policy.retry_count
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
                token = self._active_token
                if token is None:
                    self._stop_result = StopResult(
                        summary="stopped before active dispatch",
                        cancel_acknowledged=None,
                    )
                    return self._stop_result
                token.invalidate()
                stop_task = asyncio.create_task(self._stop_active(token))
                self._stop_task = stop_task

        if stop_task is None:
            raise RuntimeError("STOP coordination lost its task")
        result = await stop_task
        async with self._lock:
            if self._stop_result is None:
                self._stop_result = result
            return self._stop_result

    async def _stop_active(self, token: DispatchToken) -> StopResult:
        try:
            observed = await self._adapter.stop(token)
            return StopResult.model_validate(observed.model_dump(mode="json"))
        except Exception as exc:
            return StopResult(
                summary=f"adapter STOP raised {type(exc).__name__}",
                cancel_acknowledged=False,
            )

    def _report_locked(self) -> ExecutionReport:
        if self._terminal_outcome is None:
            raise RuntimeError("execution has no terminal outcome")
        return ExecutionReport(
            plan_digest=self._plan.plan_digest,
            outcome=self._terminal_outcome,
            step_records=tuple(self._records),
            diagnostics=tuple(self._diagnostics),
        )
