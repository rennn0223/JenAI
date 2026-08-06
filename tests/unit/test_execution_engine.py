from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from jenai.schemas.models import TaskOutcome
from jenai.workflows.execution_engine import (
    ActiveExecuteUnsettledDiagnostic,
    DispatchContext,
    ExecutionEngine,
    ExecutionReport,
    ScriptedAtomicStepAdapter,
    StepDisposition,
    StepEvidence,
    StepEvidenceKind,
    StepResult,
    StopResult,
)
from jenai.workflows.patrol_mission import (
    BoundLocation,
    ExecutionPlan,
    ExecutionStep,
    PatrolMissionPolicy,
    PatrolMissionSpec,
    compile_patrol_mission,
)


def _plan(*, retry_count: int = 1) -> ExecutionPlan:
    mission = PatrolMissionSpec(
        mission_id="mission-1",
        site_id="warehouse",
        site_version="7",
        site_profile_digest="a" * 64,
        robot_id="robot-1",
        vehicle_profile_digest="b" * 64,
        locations_sha256="c" * 64,
        ordered_locations=(
            BoundLocation(location_id="loc-a", location_name="A"),
            BoundLocation(location_id="loc-b", location_name="B"),
            BoundLocation(location_id="loc-c", location_name="C"),
        ),
        home_location=BoundLocation(location_id="loc-dock", location_name="Dock"),
        policy=PatrolMissionPolicy(retry_count=retry_count),
    )
    return compile_patrol_mission(mission)


def _result(disposition: StepDisposition, summary: str) -> StepResult:
    kwargs: dict[str, object] = {}
    if disposition is StepDisposition.SUCCEEDED:
        kwargs = {
            "evidence": (
                StepEvidence(kind=StepEvidenceKind.NAVIGATION_TERMINAL, evidence_id="nav-result"),
                StepEvidence(kind=StepEvidenceKind.ENDPOINT_POSE, evidence_id="fresh-pose"),
            ),
            "position_error_m": 0.05,
        }
    return StepResult(disposition=disposition, summary=summary, **kwargs)


class _BlockingAtomicStepAdapter:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.execute_calls: list[tuple[ExecutionStep, DispatchContext]] = []
        self.stop_calls: list[DispatchContext] = []

    async def execute(
        self,
        step: ExecutionStep,
        dispatch_context: DispatchContext,
    ) -> StepResult:
        self.execute_calls.append((step, dispatch_context))
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            await self.release.wait()
        return _result(StepDisposition.SUCCEEDED, "late success")

    async def stop(self, active_dispatch: DispatchContext) -> StopResult:
        assert active_dispatch.cancellation.cancelled is True
        self.stop_calls.append(active_dispatch)
        self.release.set()
        return StopResult(summary="cancel acknowledged", cancel_acknowledged=True)


class _HungExecuteAdapter:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.stop_calls: list[DispatchContext] = []

    async def execute(
        self,
        step: ExecutionStep,
        dispatch_context: DispatchContext,
    ) -> StepResult:
        del step, dispatch_context
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            await self.release.wait()
        return _result(StepDisposition.SUCCEEDED, "late success")

    async def stop(self, active_dispatch: DispatchContext) -> StopResult:
        self.stop_calls.append(active_dispatch)
        return StopResult(summary="cancel acknowledged", cancel_acknowledged=True)


def test_all_steps_succeed_in_the_exact_approved_order() -> None:
    plan = _plan()
    adapter = ScriptedAtomicStepAdapter(
        [
            _result(StepDisposition.SUCCEEDED, f"arrived at {step.location_name}")
            for step in plan.steps
        ]
    )
    engine = ExecutionEngine(plan, adapter)

    report = asyncio.run(engine.run())

    assert report.outcome is TaskOutcome.SUCCEEDED
    assert [record.step.location_name for record in report.step_records] == [
        "A",
        "B",
        "C",
        "Dock",
    ]
    assert [call.step.location_name for call in adapter.execute_calls] == [
        "A",
        "B",
        "C",
        "Dock",
    ]
    assert all(len(record.attempts) == 1 for record in report.step_records)


@pytest.mark.parametrize(
    "payload",
    (
        {"position_error_m": 0.05, "evidence": ()},
        {
            "position_error_m": None,
            "evidence": (
                {"kind": "navigation_terminal", "evidence_id": "nav-result"},
                {"kind": "endpoint_pose", "evidence_id": "fresh-pose"},
            ),
        },
        {
            "position_error_m": 0.05,
            "evidence": ({"kind": "navigation_terminal", "evidence_id": "nav-result"},),
        },
    ),
)
def test_success_requires_terminal_and_endpoint_evidence_plus_pose_error(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        StepResult(disposition=StepDisposition.SUCCEEDED, summary="arrived", **payload)


def test_engine_converts_adapter_success_beyond_step_tolerance_to_endpoint_mismatch() -> None:
    plan = _plan()
    results = [_result(StepDisposition.SUCCEEDED, "arrived") for _ in plan.steps]
    results[-1] = results[-1].model_copy(update={"position_error_m": 0.16})

    report = asyncio.run(ExecutionEngine(plan, ScriptedAtomicStepAdapter(results)).run())

    assert report.outcome is TaskOutcome.ENDPOINT_MISMATCH
    assert report.step_records[-1].attempts[-1].result.disposition is (
        StepDisposition.ENDPOINT_MISMATCH
    )


def test_waypoint_local_failure_retries_once_then_continues_after_success() -> None:
    plan = _plan()
    adapter = ScriptedAtomicStepAdapter(
        [
            _result(StepDisposition.WAYPOINT_LOCAL_FAILURE, "A temporarily blocked"),
            _result(StepDisposition.SUCCEEDED, "arrived at A"),
            *(
                _result(StepDisposition.SUCCEEDED, f"arrived at {step.location_name}")
                for step in plan.steps[1:]
            ),
        ]
    )

    report = asyncio.run(ExecutionEngine(plan, adapter).run())

    assert report.outcome is TaskOutcome.SUCCEEDED
    assert [call.step.location_name for call in adapter.execute_calls] == [
        "A",
        "A",
        "B",
        "C",
        "Dock",
    ]
    assert [attempt.attempt for attempt in report.step_records[0].attempts] == [1, 2]
    assert report.step_records[0].skipped is False


def test_exhausted_waypoint_local_failure_is_skipped_and_mission_is_partial() -> None:
    plan = _plan()
    adapter = ScriptedAtomicStepAdapter(
        [
            _result(StepDisposition.SUCCEEDED, "arrived at A"),
            _result(StepDisposition.WAYPOINT_LOCAL_FAILURE, "B blocked"),
            _result(StepDisposition.WAYPOINT_LOCAL_FAILURE, "B still blocked"),
            _result(StepDisposition.SUCCEEDED, "arrived at C"),
            _result(StepDisposition.SUCCEEDED, "returned to Dock"),
        ]
    )

    report = asyncio.run(ExecutionEngine(plan, adapter).run())

    assert report.outcome is TaskOutcome.PARTIAL
    assert [record.step.location_name for record in report.step_records] == [
        "A",
        "B",
        "C",
        "Dock",
    ]
    assert report.step_records[1].skipped is True
    assert [attempt.attempt for attempt in report.step_records[1].attempts] == [1, 2]


def test_navigation_system_failure_aborts_without_dispatching_later_steps() -> None:
    plan = _plan()
    adapter = ScriptedAtomicStepAdapter(
        [
            _result(StepDisposition.SUCCEEDED, "arrived at A"),
            _result(StepDisposition.NAVIGATION_SYSTEM_FAILURE, "localization unavailable"),
        ]
    )

    report = asyncio.run(ExecutionEngine(plan, adapter).run())

    assert report.outcome is TaskOutcome.FAILED
    assert [call.step.location_name for call in adapter.execute_calls] == ["A", "B"]
    assert [record.step.location_name for record in report.step_records] == ["A", "B"]


def test_return_home_local_failure_cannot_be_skipped() -> None:
    plan = _plan()
    adapter = ScriptedAtomicStepAdapter(
        [
            *(
                _result(StepDisposition.SUCCEEDED, f"arrived at {step.location_name}")
                for step in plan.steps[:-1]
            ),
            _result(StepDisposition.WAYPOINT_LOCAL_FAILURE, "Dock blocked"),
            _result(StepDisposition.WAYPOINT_LOCAL_FAILURE, "Dock still blocked"),
        ]
    )

    report = asyncio.run(ExecutionEngine(plan, adapter).run())

    assert report.outcome is TaskOutcome.FAILED
    assert report.step_records[-1].step.location_name == "Dock"
    assert report.step_records[-1].skipped is False


def test_return_home_endpoint_mismatch_is_terminal_and_not_success() -> None:
    plan = _plan()
    adapter = ScriptedAtomicStepAdapter(
        [
            *(
                _result(StepDisposition.SUCCEEDED, f"arrived at {step.location_name}")
                for step in plan.steps[:-1]
            ),
            StepResult(
                disposition=StepDisposition.ENDPOINT_MISMATCH,
                summary="Dock pose outside tolerance",
                position_error_m=0.21,
            ),
        ]
    )

    report = asyncio.run(ExecutionEngine(plan, adapter).run())

    assert report.outcome is TaskOutcome.ENDPOINT_MISMATCH
    assert report.step_records[-1].attempts[-1].result.position_error_m == 0.21


def test_stop_before_first_step_dispatches_nothing_and_is_idempotent() -> None:
    async def scenario() -> tuple[
        StopResult, StopResult, ExecutionReport, ScriptedAtomicStepAdapter
    ]:
        adapter = ScriptedAtomicStepAdapter(
            [_result(StepDisposition.SUCCEEDED, "must not execute")]
        )
        engine = ExecutionEngine(_plan(), adapter)
        first = await engine.stop()
        second = await engine.stop()
        report = await engine.run()
        return first, second, report, adapter

    first, second, report, adapter = asyncio.run(scenario())

    assert first == second
    assert report.outcome is TaskOutcome.CANCELLED
    assert report.step_records == ()
    assert adapter.execute_calls == []
    assert adapter.stop_calls == []


def test_active_stop_invalidates_token_before_adapter_stop_and_late_success_is_diagnostic() -> None:
    async def scenario() -> tuple[
        StopResult, StopResult, ExecutionReport, _BlockingAtomicStepAdapter
    ]:
        adapter = _BlockingAtomicStepAdapter()
        engine = ExecutionEngine(_plan(), adapter)
        run_task = asyncio.create_task(engine.run())
        await adapter.started.wait()
        first = await engine.stop()
        second = await engine.stop()
        report = await run_task
        return first, second, report, adapter

    first, second, report, adapter = asyncio.run(scenario())

    assert first == second
    assert len(adapter.stop_calls) == 1
    context = adapter.stop_calls[0]
    assert context.cancellation.cancelled is True
    assert not hasattr(context, "invalidate")
    with pytest.raises(FrozenInstanceError):
        context.attempt = 99
    assert report.outcome is TaskOutcome.CANCELLED
    assert report.stop_result == first
    assert len(report.step_records) == 1
    assert report.step_records[0].attempts[-1].result.disposition is (StepDisposition.CANCELLED)
    assert len(report.diagnostics) == 1
    assert report.diagnostics[0].kind == "late_step_result_after_stop"
    assert report.diagnostics[0].disposition is StepDisposition.SUCCEEDED


def test_engine_detaches_the_approved_plan_from_external_mutation() -> None:
    plan = _plan()
    adapter = ScriptedAtomicStepAdapter(
        [_result(StepDisposition.SUCCEEDED, "arrived") for _ in plan.steps]
    )
    engine = ExecutionEngine(plan, adapter)

    object.__setattr__(plan.steps[0], "location_name", "Tampered")
    report = asyncio.run(engine.run())

    assert [call.step.location_name for call in adapter.execute_calls] == [
        "A",
        "B",
        "C",
        "Dock",
    ]
    assert [record.step_index for record in report.step_records] == [0, 1, 2, 3]
    assert report.outcome is TaskOutcome.SUCCEEDED


def test_stop_returns_a_report_even_when_active_execute_does_not_return() -> None:
    async def scenario() -> tuple[ExecutionReport, StopResult, _HungExecuteAdapter]:
        adapter = _HungExecuteAdapter()
        engine = ExecutionEngine(
            _plan(),
            adapter,
            execute_settlement_timeout_s=0.01,
        )
        run_task = asyncio.create_task(engine.run())
        await adapter.started.wait()
        stop_result = await engine.stop()
        try:
            report = await asyncio.wait_for(asyncio.shield(run_task), timeout=0.1)
        finally:
            adapter.release.set()
            if not run_task.done():
                await run_task
        return report, stop_result, adapter

    report, stop_result, adapter = asyncio.run(scenario())

    assert report.outcome is TaskOutcome.CANCELLED
    assert report.stop_result == stop_result
    assert len(adapter.stop_calls) == 1
    assert len(report.step_records) == 1
    assert report.step_records[0].attempts[-1].result.disposition is StepDisposition.CANCELLED
    assert len(report.diagnostics) == 1
    diagnostic = report.diagnostics[0]
    assert isinstance(diagnostic, ActiveExecuteUnsettledDiagnostic)
    assert diagnostic.limitation == "adapter_execute_task_did_not_settle"
    assert diagnostic.timeout_s == 0.01


class _HungStopAdapter(_HungExecuteAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.stop_started = asyncio.Event()
        self.stop_release = asyncio.Event()

    async def stop(self, active_dispatch: DispatchContext) -> StopResult:
        self.stop_calls.append(active_dispatch)
        self.stop_started.set()
        try:
            await self.stop_release.wait()
        except asyncio.CancelledError:
            await self.stop_release.wait()
        return StopResult(summary="late STOP acknowledgement", cancel_acknowledged=True)


class _RetryThenHangAdapter(_HungExecuteAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.execute_count = 0

    async def execute(
        self,
        step: ExecutionStep,
        dispatch_context: DispatchContext,
    ) -> StepResult:
        del step, dispatch_context
        self.execute_count += 1
        if self.execute_count == 1:
            return _result(StepDisposition.WAYPOINT_LOCAL_FAILURE, "retry me")
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            await self.release.wait()
        return _result(StepDisposition.SUCCEEDED, "late success")


def test_malformed_completion_evidence_is_rejected() -> None:
    with pytest.raises(ValidationError):
        StepResult(
            disposition=StepDisposition.SUCCEEDED,
            summary="arrived",
            position_error_m=0.05,
            evidence=(
                {"kind": "untrusted_pose", "evidence_id": "bad"},
                {"kind": "navigation_terminal", "evidence_id": "nav-result"},
            ),
        )


def test_stop_is_bounded_when_adapter_stop_does_not_return() -> None:
    async def scenario() -> tuple[ExecutionReport, StopResult, _HungStopAdapter]:
        adapter = _HungStopAdapter()
        engine = ExecutionEngine(
            _plan(),
            adapter,
            stop_timeout_s=0.01,
            execute_settlement_timeout_s=0.01,
        )
        run_task = asyncio.create_task(engine.run())
        await adapter.started.wait()
        stop_result = await engine.stop()
        report = await asyncio.wait_for(asyncio.shield(run_task), timeout=0.1)
        adapter.release.set()
        adapter.stop_release.set()
        await asyncio.sleep(0)
        return report, stop_result, adapter

    report, stop_result, adapter = asyncio.run(scenario())

    assert stop_result.timed_out is True
    assert stop_result.cancel_acknowledged is False
    assert report.stop_result == stop_result
    assert report.outcome is TaskOutcome.CANCELLED
    assert len(adapter.stop_calls) == 1


def test_cancelling_one_stop_waiter_does_not_cancel_shared_stop() -> None:
    async def scenario() -> tuple[StopResult, ExecutionReport, _HungStopAdapter]:
        adapter = _HungStopAdapter()
        engine = ExecutionEngine(_plan(), adapter)
        run_task = asyncio.create_task(engine.run())
        await adapter.started.wait()
        waiter = asyncio.create_task(engine.stop())
        await adapter.stop_started.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        adapter.stop_release.set()
        adapter.release.set()
        stop_result = await engine.stop()
        report = await run_task
        return stop_result, report, adapter

    stop_result, report, adapter = asyncio.run(scenario())

    assert stop_result.cancel_acknowledged is True
    assert stop_result.timed_out is False
    assert report.stop_result == stop_result
    assert len(adapter.stop_calls) == 1


def test_cancelling_run_performs_shielded_stop_before_propagating() -> None:
    async def scenario() -> _HungExecuteAdapter:
        adapter = _HungExecuteAdapter()
        engine = ExecutionEngine(
            _plan(),
            adapter,
            execute_settlement_timeout_s=0.01,
        )
        run_task = asyncio.create_task(engine.run())
        await adapter.started.wait()
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run_task, timeout=0.1)
        adapter.release.set()
        await asyncio.sleep(0)
        return adapter

    adapter = asyncio.run(scenario())

    assert len(adapter.stop_calls) == 1
    assert adapter.stop_calls[0].cancellation.cancelled is True


def test_stop_preserves_prior_retry_attempt_and_active_attempt() -> None:
    async def scenario() -> tuple[ExecutionReport, _RetryThenHangAdapter]:
        adapter = _RetryThenHangAdapter()
        engine = ExecutionEngine(
            _plan(),
            adapter,
            execute_settlement_timeout_s=0.01,
        )
        run_task = asyncio.create_task(engine.run())
        await adapter.started.wait()
        await engine.stop()
        report = await run_task
        adapter.release.set()
        await asyncio.sleep(0)
        return report, adapter

    report, adapter = asyncio.run(scenario())

    assert report.outcome is TaskOutcome.CANCELLED
    assert adapter.execute_count == 2
    assert len(report.step_records) == 1
    assert [attempt.result.disposition for attempt in report.step_records[0].attempts] == [
        StepDisposition.WAYPOINT_LOCAL_FAILURE,
        StepDisposition.CANCELLED,
    ]


class _PreDispatchStopAdapter:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.stop_calls: list[DispatchContext] = []

    async def execute(
        self,
        step: ExecutionStep,
        dispatch_context: DispatchContext,
    ) -> StepResult:
        del step, dispatch_context
        self.execute_calls += 1
        raise AssertionError("execute must not be called after the dispatch fence is invalidated")

    async def stop(self, active_dispatch: DispatchContext) -> StopResult:
        self.stop_calls.append(active_dispatch)
        return StopResult(summary="pre-dispatch STOP acknowledged", cancel_acknowledged=True)


def test_stop_after_fence_creation_prevents_adapter_execute_from_being_called() -> None:
    async def scenario() -> tuple[ExecutionReport, _PreDispatchStopAdapter]:
        adapter = _PreDispatchStopAdapter()
        engine = ExecutionEngine(_plan(), adapter)
        run_task = asyncio.create_task(engine.run())
        stop_task = asyncio.create_task(engine.stop())
        report, _ = await asyncio.gather(run_task, stop_task)
        return report, adapter

    report, adapter = asyncio.run(scenario())

    assert report.outcome is TaskOutcome.CANCELLED
    assert adapter.execute_calls == 0
    assert len(adapter.stop_calls) == 1


class _CancellationFailureAdapter:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.never = asyncio.Event()

    async def execute(
        self,
        step: ExecutionStep,
        dispatch_context: DispatchContext,
    ) -> StepResult:
        del step, dispatch_context
        self.started.set()
        try:
            await self.never.wait()
        except asyncio.CancelledError as exc:
            raise RuntimeError("adapter cancellation cleanup failed") from exc

    async def stop(self, active_dispatch: DispatchContext) -> StopResult:
        del active_dispatch
        return StopResult(summary="STOP acknowledged", cancel_acknowledged=True)


def test_stop_consumes_execute_exception_during_bounded_settlement() -> None:
    async def scenario() -> ExecutionReport:
        adapter = _CancellationFailureAdapter()
        engine = ExecutionEngine(_plan(), adapter)
        run_task = asyncio.create_task(engine.run())
        await adapter.started.wait()
        await engine.stop()
        return await run_task

    report = asyncio.run(scenario())

    assert report.outcome is TaskOutcome.CANCELLED
    assert len(report.diagnostics) == 1
    diagnostic = report.diagnostics[0]
    assert diagnostic.kind == "late_step_result_after_stop"
    assert diagnostic.disposition is StepDisposition.NAVIGATION_SYSTEM_FAILURE
