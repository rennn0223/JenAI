from __future__ import annotations

import asyncio

from jenai.schemas.models import TaskOutcome
from jenai.workflows.execution_engine import (
    DispatchToken,
    ExecutionEngine,
    ExecutionReport,
    ScriptedAtomicStepAdapter,
    StepDisposition,
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
    return StepResult(disposition=disposition, summary=summary)


class _BlockingAtomicStepAdapter:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.execute_calls: list[tuple[ExecutionStep, DispatchToken]] = []
        self.stop_calls: list[DispatchToken] = []

    async def execute(
        self,
        step: ExecutionStep,
        dispatch_token: DispatchToken,
    ) -> StepResult:
        self.execute_calls.append((step, dispatch_token))
        self.started.set()
        await self.release.wait()
        return _result(StepDisposition.SUCCEEDED, "late success")

    async def stop(self, active_dispatch: DispatchToken) -> StopResult:
        assert active_dispatch.valid is False
        self.stop_calls.append(active_dispatch)
        self.release.set()
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
    assert adapter.stop_calls[0].valid is False
    assert report.outcome is TaskOutcome.CANCELLED
    assert report.step_records == ()
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
