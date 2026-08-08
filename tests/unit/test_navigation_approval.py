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
    PendingNavigationApproval,
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

    stale_pending = scope.prepare(plan_a)
    scope.advance_generation("runtime state unknown")
    with pytest.raises(NavigationApprovalMismatchError, match="generation"):
        asyncio.run(scope.resolve(stale_pending, plan_a, ApprovalChoice.YES, execute))


def test_unissued_pending_request_cannot_forge_automatic_execution() -> None:
    scope = NavigationApprovalScope(session_id="session-1")
    plan = _plan()
    issued = scope.prepare(plan)
    forged = PendingNavigationApproval(
        request_id="forged-request",
        session_id=issued.session_id,
        approval_generation=issued.approval_generation,
        mission_id=issued.mission_id,
        plan_digest=issued.plan_digest,
        preview=issued.preview,
        requires_operator_input=False,
    )
    calls = 0

    async def execute(received):
        nonlocal calls
        calls += 1
        return _successful_report(received.plan_digest)

    with pytest.raises(NavigationApprovalMismatchError, match="not issued"):
        asyncio.run(scope.resolve(forged, plan, None, execute))
    assert calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("preview", "偽造的計畫畫面"),
        ("requires_operator_input", False),
    ),
)
def test_scope_rejects_modified_issued_pending_content(field: str, value: object) -> None:
    scope = NavigationApprovalScope(session_id="session-1")
    plan = _plan()
    pending = scope.prepare(plan)
    modified = pending.model_copy(update={field: value})
    calls = 0

    async def execute(received):
        nonlocal calls
        calls += 1
        return _successful_report(received.plan_digest)

    with pytest.raises(NavigationApprovalMismatchError, match="content was modified"):
        asyncio.run(scope.resolve(modified, plan, None, execute))
    assert calls == 0


def test_auto_is_remembered_only_after_valid_execution_report() -> None:
    async def scenario() -> None:
        scope = NavigationApprovalScope(session_id="session-1")
        plan = _plan()
        pending = scope.prepare(plan)
        started = asyncio.Event()
        release = asyncio.Event()

        async def execute(received):
            started.set()
            await release.wait()
            return _successful_report(received.plan_digest)

        resolution = asyncio.create_task(scope.resolve(pending, plan, ApprovalChoice.AUTO, execute))
        await started.wait()
        assert scope.prepare(plan).requires_operator_input is True
        release.set()
        await resolution
        assert scope.prepare(plan).requires_operator_input is False

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", (RuntimeError("failed"), asyncio.CancelledError()))
def test_failed_or_cancelled_execution_does_not_leave_auto_enabled(
    failure: BaseException,
) -> None:
    scope = NavigationApprovalScope(session_id="session-1")
    plan = _plan()
    pending = scope.prepare(plan)

    async def execute(_received):
        raise failure

    with pytest.raises(type(failure)):
        asyncio.run(scope.resolve(pending, plan, ApprovalChoice.AUTO, execute))
    assert scope.prepare(plan).requires_operator_input is True


@pytest.mark.parametrize("advance_during_execution", (False, True))
def test_cancelled_report_never_reinstalls_auto_after_stop(
    advance_during_execution: bool,
) -> None:
    scope = NavigationApprovalScope(session_id="session-1")
    plan = _plan()
    pending = scope.prepare(plan)

    async def execute(received):
        if advance_during_execution:
            scope.advance_generation("STOP")
        return ExecutionReport(
            plan_digest=received.plan_digest,
            outcome=TaskOutcome.CANCELLED,
            step_records=(),
        )

    result = asyncio.run(scope.resolve(pending, plan, ApprovalChoice.AUTO, execute))

    assert result.outcome is TaskOutcome.CANCELLED
    assert scope.prepare(plan).requires_operator_input is True


def test_cancelled_automatic_execution_clears_existing_auto_authorization() -> None:
    scope = NavigationApprovalScope(session_id="session-1")
    plan = _plan()

    async def succeed(received):
        return _successful_report(received.plan_digest)

    initial = scope.prepare(plan)
    asyncio.run(scope.resolve(initial, plan, ApprovalChoice.AUTO, succeed))
    automatic = scope.prepare(plan)
    assert automatic.requires_operator_input is False

    async def cancel(received):
        return ExecutionReport(
            plan_digest=received.plan_digest,
            outcome=TaskOutcome.CANCELLED,
            step_records=(),
        )

    result = asyncio.run(scope.resolve(automatic, plan, None, cancel))

    assert result.outcome is TaskOutcome.CANCELLED
    assert scope.prepare(plan).requires_operator_input is True


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
