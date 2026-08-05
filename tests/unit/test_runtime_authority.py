from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from jenai.runtime import (
    ApprovalDecision,
    ApprovalDigestMismatchError,
    ApprovalNotPendingError,
    CapabilityExecutionReport,
    CapabilityUnavailableError,
    EvidenceTimestampStatus,
    ExecutorEvent,
    ExecutorEvidence,
    ExecutorStopResult,
    InMemoryRuntimeAuthority,
    LeaseBusyError,
    PreparedCapabilityStep,
    ResolveApproval,
    RuntimeTaskRegistration,
    SourceAssurance,
    StopRequest,
    SubmitTask,
    TaskStatus,
    TaskView,
    TransportSecurity,
    TypedCapabilityStep,
    capability_binding_sha256,
)
from jenai.schemas import TaskOutcome


class RecordingExecutor:
    def __init__(self) -> None:
        self.prepared: list[TypedCapabilityStep] = []
        self.executed: list[TypedCapabilityStep] = []
        self.stop_contexts: list[object] = []

    async def prepare(self, step, context):
        self.prepared.append(step)
        return PreparedCapabilityStep(
            step=step,
            context=context,
            binding_sha256=capability_binding_sha256(step, context),
        )

    async def execute(self, prepared, _context, _events):
        self.executed.append(prepared.step)
        return CapabilityExecutionReport(disposition="completed", summary="done")

    async def snapshot(self, _request, _context):
        raise AssertionError("snapshot is not part of this lifecycle test")

    async def stop(self, _context, _events):
        self.stop_contexts.append(_context)
        return ExecutorStopResult(
            request_accepted=True,
            cancel_requested=True,
            cancel_acknowledged=True,
        )


def _clock() -> datetime:
    return datetime(2026, 8, 5, 4, 0, tzinfo=UTC)


def _effectful_registration() -> RuntimeTaskRegistration:
    def build_steps(task: SubmitTask) -> tuple[TypedCapabilityStep, ...]:
        return (
            TypedCapabilityStep(
                capability_id=task.capability_id,
                input_schema_version=task.input_schema_version,
                input=task.input,
            ),
        )

    def evaluate(reports) -> TaskOutcome:
        if (
            len(reports) == 1
            and reports[0].disposition == "completed"
            and any(item.kind == "inspection" for item in reports[0].evidence)
        ):
            return TaskOutcome.SUCCEEDED
        return TaskOutcome.FAILED

    return RuntimeTaskRegistration(
        capability_id="inspect_fixture",
        input_schema_version="1",
        workflow_definition_version="1",
        effectful=True,
        requires_approval=True,
        approval_ttl=timedelta(minutes=1),
        build_steps=build_steps,
        evaluate_completion=evaluate,
    )


async def _wait_for_terminal(
    authority: InMemoryRuntimeAuthority,
    task_id: str,
) -> TaskView:
    for _ in range(20):
        snapshot = await authority.observe()
        task = next(item for item in snapshot.tasks if item.task_id == task_id)
        if task.outcome is not None:
            return task
        await asyncio.sleep(0)
    raise AssertionError("Task did not reach a terminal outcome")


def test_submit_effectful_task_creates_authoritative_approval_without_execution() -> None:
    async def run() -> None:
        executor = RecordingExecutor()
        authority = InMemoryRuntimeAuthority(
            runtime_id="runtime_test",
            boot_id="boot_test",
            robot_id="robot_test",
            authority_generation=1,
            initial_safety_epoch=4,
            executor=executor,
            registrations=(_effectful_registration(),),
            now=_clock,
        )

        accepted = await authority.submit(
            SubmitTask(
                robot_id="robot_test",
                capability_id="inspect_fixture",
                input_schema_version="1",
                input={"target": "fixture_a"},
                expected_safety_epoch=4,
            )
        )
        snapshot = await authority.observe()

        assert accepted.status == TaskStatus.AWAITING_APPROVAL
        assert accepted.pending_approval is not None
        assert accepted.pending_approval.task_id == accepted.task_id
        assert accepted.pending_approval.command_id == accepted.command_id
        assert accepted.pending_approval.capability_id == "inspect_fixture"
        assert accepted.pending_approval.safety_epoch == 4
        assert snapshot.active_lease_task_id is None
        assert [event.kind for event in snapshot.events] == [
            "TaskAccepted",
            "ApprovalRequired",
        ]
        assert [event.sequence for event in snapshot.events] == [1, 2]
        assert snapshot.head_sequence == 2
        assert executor.prepared == []
        assert executor.executed == []

    asyncio.run(run())


def test_approved_atomic_task_derives_outcome_and_receipt_from_completion_contract() -> None:
    async def run() -> None:
        class CompletingExecutor(RecordingExecutor):
            async def execute(self, prepared, _context, events):
                self.executed.append(prepared.step)
                await events.publish(
                    ExecutorEvent(kind="step_observed", data={"phase": "inspection"})
                )
                return CapabilityExecutionReport(
                    disposition="completed",
                    summary="adapter completed",
                    evidence=(
                        ExecutorEvidence(
                            kind="inspection",
                            source="synthetic_fixture",
                            source_timestamp_status=EvidenceTimestampStatus.UNAVAILABLE,
                            transport_security=TransportSecurity.UNKNOWN,
                            source_assurance=SourceAssurance.DERIVED,
                            payload_schema_version="1",
                            payload={"observed": True},
                        ),
                    ),
                )

        executor = CompletingExecutor()
        authority = InMemoryRuntimeAuthority(
            runtime_id="runtime_test",
            boot_id="boot_test",
            robot_id="robot_test",
            authority_generation=1,
            initial_safety_epoch=4,
            executor=executor,
            registrations=(_effectful_registration(),),
            now=_clock,
        )
        accepted = await authority.submit(
            SubmitTask(
                robot_id="robot_test",
                capability_id="inspect_fixture",
                input_schema_version="1",
                input={"target": "fixture_a"},
                expected_safety_epoch=4,
            )
        )
        assert accepted.pending_approval is not None

        await authority.resolve_approval(
            ResolveApproval(
                task_id=accepted.task_id,
                approval_id=accepted.pending_approval.approval_id,
                decision=ApprovalDecision.APPROVE,
                expected_safety_epoch=4,
            )
        )
        terminal = await _wait_for_terminal(authority, accepted.task_id)
        snapshot = await authority.observe()

        assert [step.input["target"] for step in executor.prepared] == ["fixture_a"]
        assert [step.input["target"] for step in executor.executed] == ["fixture_a"]
        assert terminal.status == TaskStatus.COMPLETED
        assert terminal.outcome == TaskOutcome.SUCCEEDED
        assert terminal.receipt is not None
        assert terminal.receipt.outcome == TaskOutcome.SUCCEEDED
        assert terminal.receipt.terminal_sequence == terminal.latest_event_sequence
        assert snapshot.active_lease_task_id is None
        assert [event.sequence for event in snapshot.events] == list(
            range(1, snapshot.head_sequence + 1)
        )
        assert snapshot.events[-1].kind == "TaskFinished"

    asyncio.run(run())


def test_second_effectful_task_is_rejected_without_hidden_queue() -> None:
    async def run() -> None:
        class BlockingExecutor(RecordingExecutor):
            def __init__(self) -> None:
                super().__init__()
                self.started = asyncio.Event()
                self.release = asyncio.Event()

            async def execute(self, prepared, _context, _events):
                self.executed.append(prepared.step)
                self.started.set()
                await self.release.wait()
                return CapabilityExecutionReport(disposition="completed", summary="done")

        executor = BlockingExecutor()
        authority = InMemoryRuntimeAuthority(
            runtime_id="runtime_test",
            boot_id="boot_test",
            robot_id="robot_test",
            authority_generation=1,
            initial_safety_epoch=4,
            executor=executor,
            registrations=(_effectful_registration(),),
            now=_clock,
        )
        first = await authority.submit(
            SubmitTask(
                robot_id="robot_test",
                capability_id="inspect_fixture",
                input_schema_version="1",
                input={"target": "fixture_a"},
                expected_safety_epoch=4,
            )
        )
        assert first.pending_approval is not None
        await authority.resolve_approval(
            ResolveApproval(
                task_id=first.task_id,
                approval_id=first.pending_approval.approval_id,
                decision=ApprovalDecision.APPROVE,
                expected_safety_epoch=4,
            )
        )
        await executor.started.wait()

        second = await authority.submit(
            SubmitTask(
                robot_id="robot_test",
                capability_id="inspect_fixture",
                input_schema_version="1",
                input={"target": "fixture_b"},
                expected_safety_epoch=4,
            )
        )
        assert second.pending_approval is not None
        with pytest.raises(LeaseBusyError):
            await authority.resolve_approval(
                ResolveApproval(
                    task_id=second.task_id,
                    approval_id=second.pending_approval.approval_id,
                    decision=ApprovalDecision.APPROVE,
                    expected_safety_epoch=4,
                )
            )
        still_pending = next(
            task for task in (await authority.observe()).tasks if task.task_id == second.task_id
        )
        assert still_pending.status == TaskStatus.AWAITING_APPROVAL
        executor.release.set()
        await _wait_for_terminal(authority, first.task_id)

    asyncio.run(run())


def test_stop_advances_epoch_before_side_effect_and_is_idempotent() -> None:
    async def run() -> None:
        executor = RecordingExecutor()
        authority = InMemoryRuntimeAuthority(
            runtime_id="runtime_test",
            boot_id="boot_test",
            robot_id="robot_test",
            authority_generation=1,
            initial_safety_epoch=4,
            executor=executor,
            registrations=(_effectful_registration(),),
            now=_clock,
        )
        accepted = await authority.submit(
            SubmitTask(
                robot_id="robot_test",
                capability_id="inspect_fixture",
                input_schema_version="1",
                input={"target": "fixture_a"},
                expected_safety_epoch=4,
            )
        )
        assert accepted.pending_approval is not None

        first = await authority.stop(
            StopRequest(
                robot_id="robot_test",
                idempotency_key="operator-stop-1",
            )
        )
        replay = await authority.stop(
            StopRequest(
                robot_id="robot_test",
                idempotency_key="operator-stop-1",
            )
        )
        snapshot = await authority.observe()
        task = next(item for item in snapshot.tasks if item.task_id == accepted.task_id)

        assert first.stop_id == replay.stop_id
        assert first.accepted_sequence == replay.accepted_sequence
        assert first.replayed is False
        assert replay.replayed is True
        assert first.safety_epoch == 5
        assert snapshot.safety_epoch == 5
        assert snapshot.active_lease_task_id is None
        assert task.pending_approval is None
        assert task.status == TaskStatus.CANCELLED
        assert task.outcome == TaskOutcome.CANCELLED
        assert task.receipt is not None
        assert len(executor.stop_contexts) == 1
        stop_context = executor.stop_contexts[0]
        assert stop_context.authority.safety_epoch == 5
        kinds = [event.kind for event in snapshot.events]
        assert kinds == [
            "TaskAccepted",
            "ApprovalRequired",
            "SafetyEpochAdvanced",
            "ApprovalInvalidated",
            "TaskFinished",
            "StopFinished",
        ]

    asyncio.run(run())


def test_stop_terminal_outcome_cannot_be_overwritten_by_late_success() -> None:
    async def run() -> None:
        class DelayedSuccessExecutor(RecordingExecutor):
            def __init__(self) -> None:
                super().__init__()
                self.execution_started = asyncio.Event()
                self.release_success = asyncio.Event()

            async def execute(self, prepared, _context, _events):
                self.executed.append(prepared.step)
                self.execution_started.set()
                try:
                    await self.release_success.wait()
                except asyncio.CancelledError:
                    pass
                return CapabilityExecutionReport(
                    disposition="completed",
                    summary="late adapter success",
                    evidence=(
                        ExecutorEvidence(
                            kind="inspection",
                            source="synthetic_fixture",
                            source_timestamp_status=EvidenceTimestampStatus.UNAVAILABLE,
                            transport_security=TransportSecurity.UNKNOWN,
                            source_assurance=SourceAssurance.DERIVED,
                            payload_schema_version="1",
                            payload={"observed": True},
                        ),
                    ),
                )

        executor = DelayedSuccessExecutor()
        authority = InMemoryRuntimeAuthority(
            runtime_id="runtime_test",
            boot_id="boot_test",
            robot_id="robot_test",
            authority_generation=1,
            initial_safety_epoch=4,
            executor=executor,
            registrations=(_effectful_registration(),),
            now=_clock,
        )
        accepted = await authority.submit(
            SubmitTask(
                robot_id="robot_test",
                capability_id="inspect_fixture",
                input_schema_version="1",
                input={"target": "fixture_a"},
                expected_safety_epoch=4,
            )
        )
        assert accepted.pending_approval is not None
        await authority.resolve_approval(
            ResolveApproval(
                task_id=accepted.task_id,
                approval_id=accepted.pending_approval.approval_id,
                decision=ApprovalDecision.APPROVE,
                expected_safety_epoch=4,
            )
        )
        await executor.execution_started.wait()

        stop = await authority.stop(
            StopRequest(robot_id="robot_test", idempotency_key="stop-running-1")
        )
        cancelled = await _wait_for_terminal(authority, accepted.task_id)
        executor.release_success.set()
        for _ in range(5):
            await asyncio.sleep(0)
        final_snapshot = await authority.observe()
        final = next(item for item in final_snapshot.tasks if item.task_id == accepted.task_id)

        assert stop.preempted_task_id == accepted.task_id
        assert cancelled.outcome == TaskOutcome.CANCELLED
        assert final.status == TaskStatus.CANCELLED
        assert final.outcome == TaskOutcome.CANCELLED
        assert final.receipt == cancelled.receipt
        terminal_events = [event for event in final_snapshot.events if event.kind == "TaskFinished"]
        assert len(terminal_events) == 1
        assert terminal_events[0].data["outcome"] == TaskOutcome.CANCELLED
        assert any(
            event.kind == "TaskProgressed" and event.data.get("late") is True
            for event in final_snapshot.events
        )

    asyncio.run(run())


def test_expired_approval_blocks_task_and_releases_lease_without_execution() -> None:
    async def run() -> None:
        current_time = [_clock()]
        executor = RecordingExecutor()
        authority = InMemoryRuntimeAuthority(
            runtime_id="runtime_test",
            boot_id="boot_test",
            robot_id="robot_test",
            authority_generation=1,
            initial_safety_epoch=4,
            executor=executor,
            registrations=(_effectful_registration(),),
            now=lambda: current_time[0],
        )
        accepted = await authority.submit(
            SubmitTask(
                robot_id="robot_test",
                capability_id="inspect_fixture",
                input_schema_version="1",
                input={"target": "fixture_a"},
                expected_safety_epoch=4,
            )
        )
        assert accepted.pending_approval is not None
        current_time[0] += timedelta(minutes=2)

        with pytest.raises(ApprovalNotPendingError):
            await authority.resolve_approval(
                ResolveApproval(
                    task_id=accepted.task_id,
                    approval_id=accepted.pending_approval.approval_id,
                    decision=ApprovalDecision.APPROVE,
                    expected_safety_epoch=4,
                )
            )

        snapshot = await authority.observe()
        blocked = next(item for item in snapshot.tasks if item.task_id == accepted.task_id)
        assert blocked.status == TaskStatus.BLOCKED
        assert blocked.outcome == TaskOutcome.BLOCKED
        assert blocked.pending_approval is None
        assert blocked.receipt is not None
        assert snapshot.active_lease_task_id is None
        assert executor.prepared == []
        assert executor.executed == []
        assert [event.kind for event in snapshot.events] == [
            "TaskAccepted",
            "ApprovalRequired",
            "ApprovalInvalidated",
            "TaskFinished",
        ]

    asyncio.run(run())


def test_approval_digest_mismatch_blocks_changed_workflow_definition() -> None:
    async def run() -> None:
        revision = ["a"]

        def build_steps(task: SubmitTask) -> tuple[TypedCapabilityStep, ...]:
            return (
                TypedCapabilityStep(
                    capability_id=task.capability_id,
                    input_schema_version=task.input_schema_version,
                    input={
                        "target": task.input["target"],
                        "workflow_revision": revision[0],
                    },
                ),
            )

        registration = RuntimeTaskRegistration(
            capability_id="inspect_fixture",
            input_schema_version="1",
            workflow_definition_version="1",
            effectful=True,
            requires_approval=True,
            approval_ttl=timedelta(minutes=1),
            build_steps=build_steps,
            evaluate_completion=lambda _reports: TaskOutcome.SUCCEEDED,
        )
        executor = RecordingExecutor()
        authority = InMemoryRuntimeAuthority(
            runtime_id="runtime_test",
            boot_id="boot_test",
            robot_id="robot_test",
            authority_generation=1,
            initial_safety_epoch=4,
            executor=executor,
            registrations=(registration,),
            now=_clock,
        )
        accepted = await authority.submit(
            SubmitTask(
                robot_id="robot_test",
                capability_id="inspect_fixture",
                input_schema_version="1",
                input={"target": "fixture_a"},
                expected_safety_epoch=4,
            )
        )
        assert accepted.pending_approval is not None
        revision[0] = "b"

        with pytest.raises(ApprovalDigestMismatchError):
            await authority.resolve_approval(
                ResolveApproval(
                    task_id=accepted.task_id,
                    approval_id=accepted.pending_approval.approval_id,
                    decision=ApprovalDecision.APPROVE,
                    expected_safety_epoch=4,
                )
            )

        snapshot = await authority.observe()
        blocked = next(item for item in snapshot.tasks if item.task_id == accepted.task_id)
        assert blocked.status == TaskStatus.BLOCKED
        assert blocked.outcome == TaskOutcome.BLOCKED
        assert blocked.pending_approval is None
        assert blocked.receipt is not None
        assert snapshot.active_lease_task_id is None
        assert executor.prepared == []
        assert executor.executed == []

    asyncio.run(run())


def test_authority_owns_deterministic_multi_step_workflow_progress() -> None:
    async def run() -> None:
        class MultiStepExecutor(RecordingExecutor):
            async def execute(self, prepared, _context, events):
                self.executed.append(prepared.step)
                stage = prepared.step.input["stage"]
                await events.publish(ExecutorEvent(kind="stage_observed", data={"stage": stage}))
                return CapabilityExecutionReport(
                    disposition="completed",
                    summary=f"{stage} complete",
                    evidence=(
                        ExecutorEvidence(
                            kind="inspection",
                            source="synthetic_fixture",
                            source_timestamp_status=EvidenceTimestampStatus.UNAVAILABLE,
                            transport_security=TransportSecurity.UNKNOWN,
                            source_assurance=SourceAssurance.DERIVED,
                            payload_schema_version="1",
                            payload={"stage": stage, "observed": True},
                        ),
                    ),
                )

        def build_steps(_task: SubmitTask) -> tuple[TypedCapabilityStep, ...]:
            return (
                TypedCapabilityStep(
                    capability_id="inspect_fixture",
                    input_schema_version="1",
                    input={"stage": "area_a"},
                ),
                TypedCapabilityStep(
                    capability_id="inspect_fixture",
                    input_schema_version="1",
                    input={"stage": "return_home"},
                ),
            )

        def evaluate(reports) -> TaskOutcome:
            stages = [report.evidence[0].payload["stage"] for report in reports]
            return (
                TaskOutcome.SUCCEEDED
                if stages == ["area_a", "return_home"]
                else TaskOutcome.PARTIAL
            )

        registration = RuntimeTaskRegistration(
            capability_id="synthetic_workflow",
            input_schema_version="1",
            workflow_definition_version="1",
            effectful=True,
            requires_approval=True,
            approval_ttl=timedelta(minutes=1),
            build_steps=build_steps,
            evaluate_completion=evaluate,
        )
        executor = MultiStepExecutor()
        authority = InMemoryRuntimeAuthority(
            runtime_id="runtime_test",
            boot_id="boot_test",
            robot_id="robot_test",
            authority_generation=1,
            initial_safety_epoch=4,
            executor=executor,
            registrations=(registration,),
            now=_clock,
        )
        accepted = await authority.submit(
            SubmitTask(
                robot_id="robot_test",
                capability_id="synthetic_workflow",
                input_schema_version="1",
                input={"mission": "fixture_patrol"},
                expected_safety_epoch=4,
            )
        )
        assert accepted.pending_approval is not None
        await authority.resolve_approval(
            ResolveApproval(
                task_id=accepted.task_id,
                approval_id=accepted.pending_approval.approval_id,
                decision=ApprovalDecision.APPROVE,
                expected_safety_epoch=4,
            )
        )
        terminal = await _wait_for_terminal(authority, accepted.task_id)
        snapshot = await authority.observe()

        assert [step.input["stage"] for step in executor.executed] == [
            "area_a",
            "return_home",
        ]
        assert terminal.current_step is None
        assert terminal.outcome == TaskOutcome.SUCCEEDED
        assert len([event for event in snapshot.events if event.kind == "TaskStarted"]) == 1
        progress = [event for event in snapshot.events if event.kind == "TaskProgressed"]
        assert [event.data["data"]["stage"] for event in progress] == [
            "area_a",
            "return_home",
        ]

    asyncio.run(run())


def test_executor_unavailable_becomes_authority_owned_unavailable_outcome() -> None:
    async def run() -> None:
        class UnavailableExecutor(RecordingExecutor):
            async def prepare(self, step, _context):
                self.prepared.append(step)
                raise CapabilityUnavailableError("fixture adapter unavailable")

        executor = UnavailableExecutor()
        authority = InMemoryRuntimeAuthority(
            runtime_id="runtime_test",
            boot_id="boot_test",
            robot_id="robot_test",
            authority_generation=1,
            initial_safety_epoch=4,
            executor=executor,
            registrations=(_effectful_registration(),),
            now=_clock,
        )
        accepted = await authority.submit(
            SubmitTask(
                robot_id="robot_test",
                capability_id="inspect_fixture",
                input_schema_version="1",
                input={"target": "fixture_a"},
                expected_safety_epoch=4,
            )
        )
        assert accepted.pending_approval is not None
        await authority.resolve_approval(
            ResolveApproval(
                task_id=accepted.task_id,
                approval_id=accepted.pending_approval.approval_id,
                decision=ApprovalDecision.APPROVE,
                expected_safety_epoch=4,
            )
        )
        terminal = await _wait_for_terminal(authority, accepted.task_id)
        snapshot = await authority.observe()

        assert terminal.status == TaskStatus.FAILED
        assert terminal.outcome == TaskOutcome.UNAVAILABLE
        assert terminal.receipt is not None
        assert terminal.receipt.outcome == TaskOutcome.UNAVAILABLE
        assert snapshot.active_lease_task_id is None
        assert snapshot.events[-1].kind == "TaskFinished"
        assert snapshot.events[-1].data["outcome"] == TaskOutcome.UNAVAILABLE
        assert snapshot.events[-1].data["error_type"] == "CapabilityUnavailableError"
        assert executor.executed == []

    asyncio.run(run())
