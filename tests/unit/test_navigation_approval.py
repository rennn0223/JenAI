from __future__ import annotations

import asyncio

import pytest

from jenai.schemas import TaskOutcome
from jenai.workflows.execution_engine import (
    ExecutionEngine,
    ExecutionReport,
    ScriptedAtomicStepAdapter,
    StepDisposition,
    StepEvidence,
    StepEvidenceKind,
    StepResult,
)
from jenai.workflows.navigation_approval import (
    ApprovalChoice,
    NavigationApprovalMismatchError,
    NavigationApprovalScope,
)
from jenai.workflows.patrol_mission import (
    BoundLocation,
    NavigateMissionPolicy,
    NavigateMissionSpec,
    compile_single_navigation,
)


def _plan(target: str = "A"):
    mission = NavigateMissionSpec(
        mission_id=f"mission-{target}",
        site_id="site-1",
        site_version="1",
        site_profile_digest="1" * 64,
        robot_id="robot-1",
        vehicle_profile_digest="2" * 64,
        locations_sha256="3" * 64,
        target_location=BoundLocation(
            location_id=f"loc-{target.casefold()}",
            location_name=target,
        ),
        policy=NavigateMissionPolicy(),
    )
    return compile_single_navigation(mission)


def _successful_report(plan_digest: str) -> ExecutionReport:
    return ExecutionReport(
        plan_digest=plan_digest,
        outcome=TaskOutcome.SUCCEEDED,
        step_records=(),
    )


def test_yes_executes_exact_plan_once() -> None:
    scope = NavigationApprovalScope(session_id="session-1")
    plan = _plan()
    pending = scope.prepare(plan)
    calls: list[str] = []

    async def execute(received):
        calls.append(received.plan_digest)
        return _successful_report(received.plan_digest)

    result = asyncio.run(scope.resolve(pending, plan, ApprovalChoice.YES, execute))

    assert pending.requires_operator_input is True
    assert result.outcome is TaskOutcome.SUCCEEDED
    assert result.automatic is False
    assert calls == [plan.plan_digest]
    with pytest.raises(NavigationApprovalMismatchError, match="already consumed"):
        asyncio.run(scope.resolve(pending, plan, ApprovalChoice.YES, execute))


def test_no_blocks_without_engine_or_gateway_call() -> None:
    scope = NavigationApprovalScope(session_id="session-1")
    plan = _plan()
    pending = scope.prepare(plan)
    calls = 0

    async def execute(_received):
        nonlocal calls
        calls += 1
        return _successful_report(plan.plan_digest)

    result = asyncio.run(scope.resolve(pending, plan, ApprovalChoice.NO, execute))

    assert result.outcome is TaskOutcome.BLOCKED
    assert result.execution_report is None
    assert calls == 0


def test_auto_only_matches_same_digest_session_and_generation() -> None:
    scope = NavigationApprovalScope(session_id="session-1")
    plan_a = _plan("A")
    calls: list[str] = []

    async def execute(received):
        calls.append(received.plan_digest)
        return _successful_report(received.plan_digest)

    first = scope.prepare(plan_a)
    asyncio.run(scope.resolve(first, plan_a, ApprovalChoice.AUTO, execute))

    repeated = scope.prepare(plan_a)
    assert repeated.requires_operator_input is False
    repeated_result = asyncio.run(scope.resolve(repeated, plan_a, None, execute))
    assert repeated_result.automatic is True

    changed = scope.prepare(_plan("B"))
    assert changed.requires_operator_input is True

    scope.advance_generation("STOP")
    after_stop = scope.prepare(plan_a)
    assert after_stop.approval_generation == 2
    assert after_stop.requires_operator_input is True
    assert len(calls) == 2


def test_changed_plan_or_generation_cannot_use_stale_yes() -> None:
    scope = NavigationApprovalScope(session_id="session-1")
    plan_a = _plan("A")
    pending = scope.prepare(plan_a)

    async def execute(received):
        return _successful_report(received.plan_digest)

    with pytest.raises(NavigationApprovalMismatchError, match="plan"):
        asyncio.run(scope.resolve(pending, _plan("B"), ApprovalChoice.YES, execute))

    scope.advance_generation("runtime state unknown")
    with pytest.raises(NavigationApprovalMismatchError, match="generation"):
        asyncio.run(scope.resolve(pending, plan_a, ApprovalChoice.YES, execute))


def test_yes_runs_the_exact_one_step_plan_through_execution_engine() -> None:
    scope = NavigationApprovalScope(session_id="session-1")
    plan = _plan()
    pending = scope.prepare(plan)
    adapter = ScriptedAtomicStepAdapter(
        [
            StepResult(
                disposition=StepDisposition.SUCCEEDED,
                summary="arrived",
                evidence=(
                    StepEvidence(
                        kind=StepEvidenceKind.NAVIGATION_TERMINAL,
                        evidence_id="nav-result",
                    ),
                    StepEvidence(
                        kind=StepEvidenceKind.ENDPOINT_POSE,
                        evidence_id="fresh-pose",
                    ),
                ),
                position_error_m=0.04,
            )
        ]
    )

    async def execute(received):
        return await ExecutionEngine(received, adapter).run()

    result = asyncio.run(scope.resolve(pending, plan, ApprovalChoice.YES, execute))

    assert result.outcome is TaskOutcome.SUCCEEDED
    assert result.execution_report is not None
    assert result.execution_report.plan_digest == plan.plan_digest
    assert len(adapter.execute_calls) == 1
    assert adapter.execute_calls[0].step.location_name == "A"
