from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from jenai.runtime import (
    ApprovalDecision,
    CancelTask,
    CapabilityExecutionReport,
    ExecutorCancelResult,
    ExecutorEvent,
    ExecutorStopResult,
    InMemoryRuntimeAuthority,
    LeaseBusyError,
    PreparedCapabilityStep,
    ResolveApproval,
    RuntimeAuthorityError,
    RuntimeTaskRegistration,
    StopRequest,
    SubmitTask,
    TaskStatus,
    TypedCapabilityStep,
    capability_binding_sha256,
)
from jenai.schemas import TaskOutcome


class _Executor:
    def __init__(self) -> None:
        self.execution_started = asyncio.Event()
        self.execution_gate = asyncio.Event()
        self.effects: list[str] = []
        self.cancel_calls = 0
        self.stop_calls = 0
        self.stop_entered = asyncio.Event()
        self.stop_release = asyncio.Event()

    async def prepare(self, step, context):
        return PreparedCapabilityStep(
            step=step,
            context=context,
            binding_sha256=capability_binding_sha256(step, context),
        )

    async def execute(self, prepared, _context, _events):
        self.execution_started.set()
        await self.execution_gate.wait()
        self.effects.append(str(prepared.step.input["target"]))
        return CapabilityExecutionReport(disposition="completed", summary="done")

    async def snapshot(self, _request, _context):
        raise AssertionError("snapshot is outside this test seam")

    async def cancel(self, _context, _events):
        self.cancel_calls += 1
        return ExecutorCancelResult(
            request_accepted=True,
            cancel_requested=True,
            cancel_acknowledged=True,
        )

    async def stop(self, _context, _events):
        self.stop_calls += 1
        self.stop_entered.set()
        await self.stop_release.wait()
        return ExecutorStopResult(
            request_accepted=True,
            cancel_requested=True,
            cancel_acknowledged=True,
        )


def _registration() -> RuntimeTaskRegistration:
    return RuntimeTaskRegistration(
        capability_id="navigate_fixture",
        input_schema_version="1",
        workflow_definition_version="1",
        effectful=True,
        requires_approval=True,
        approval_ttl=timedelta(minutes=1),
        build_steps=lambda task: (
            TypedCapabilityStep(
                capability_id=task.capability_id,
                input_schema_version=task.input_schema_version,
                input=task.input,
            ),
        ),
        evaluate_completion=lambda reports: (
            TaskOutcome.SUCCEEDED
            if len(reports) == 1 and reports[0].disposition == "completed"
            else TaskOutcome.FAILED
        ),
    )


def _authority(
    executor: _Executor,
    *,
    stop_timeout: timedelta = timedelta(seconds=1),
    cancel_timeout: timedelta = timedelta(seconds=1),
) -> InMemoryRuntimeAuthority:
    return InMemoryRuntimeAuthority(
        runtime_id="runtime_test",
        boot_id="boot_test",
        robot_id="robot_test",
        authority_generation=1,
        initial_safety_epoch=4,
        executor=executor,
        registrations=(_registration(),),
        now=lambda: datetime(2026, 8, 5, 4, 0, tzinfo=UTC),
        stop_timeout=stop_timeout,
        cancel_timeout=cancel_timeout,
    )


async def _submit(authority: InMemoryRuntimeAuthority):
    return await authority.submit(
        SubmitTask(
            robot_id="robot_test",
            capability_id="navigate_fixture",
            input_schema_version="1",
            input={"target": "fixture_a"},
            expected_safety_epoch=4,
        )
    )


async def _approve(authority: InMemoryRuntimeAuthority, task) -> None:
    assert task.pending_approval is not None
    await authority.resolve_approval(
        ResolveApproval(
            task_id=task.task_id,
            approval_id=task.pending_approval.approval_id,
            decision=ApprovalDecision.APPROVE,
            expected_safety_epoch=4,
        )
    )


def test_cancel_pending_approval_is_terminal_without_adapter_cancel() -> None:
    async def run() -> None:
        executor = _Executor()
        authority = _authority(executor)
        task = await _submit(authority)

        cancelled = await authority.cancel(
            CancelTask(
                command_id=task.command_id,
                reason="operator rejected pending work",
                expected_safety_epoch=4,
            )
        )

        assert cancelled.task.status == TaskStatus.CANCELLED
        assert cancelled.task.outcome == TaskOutcome.CANCELLED
        assert cancelled.cancel_requested is False
        assert executor.cancel_calls == 0

    asyncio.run(run())


def test_cancel_running_task_prevents_effect_and_calls_adapter_once() -> None:
    async def run() -> None:
        executor = _Executor()
        authority = _authority(executor)
        task = await _submit(authority)
        await _approve(authority, task)
        await executor.execution_started.wait()

        cancelled = await authority.cancel(
            CancelTask(
                command_id=task.command_id,
                reason="operator cancel",
                expected_safety_epoch=4,
            )
        )
        executor.execution_gate.set()
        await asyncio.sleep(0)

        assert cancelled.task.outcome == TaskOutcome.CANCELLED
        assert cancelled.cancel_acknowledged is True
        assert executor.cancel_calls == 1
        assert executor.effects == []

    asyncio.run(run())


def test_stop_wrong_robot_cannot_replay_completed_idempotency_key() -> None:
    async def run() -> None:
        executor = _Executor()
        authority = _authority(executor)
        executor.stop_release.set()
        await authority.stop(StopRequest(robot_id="robot_test", idempotency_key="same"))

        with pytest.raises(RuntimeAuthorityError):
            await authority.stop(StopRequest(robot_id="other_robot", idempotency_key="same"))

    asyncio.run(run())


def test_cancelling_stop_caller_does_not_abandon_accepted_stop() -> None:
    async def run() -> None:
        executor = _Executor()
        authority = _authority(executor)
        request = StopRequest(robot_id="robot_test", idempotency_key="durable-stop")
        caller = asyncio.create_task(authority.stop(request))
        await executor.stop_entered.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        executor.stop_release.set()

        replay = await authority.stop(request)
        snapshot = await authority.observe()

        assert replay.replayed is True
        assert replay.terminal is True
        assert executor.stop_calls == 1
        assert snapshot.safety_epoch == 5
        assert len([event for event in snapshot.events if event.kind == "StopFinished"]) == 1

    asyncio.run(run())


def test_overlapping_distinct_stop_keys_share_one_robot_wide_operation() -> None:
    async def run() -> None:
        executor = _Executor()
        authority = _authority(executor)
        first = asyncio.create_task(
            authority.stop(StopRequest(robot_id="robot_test", idempotency_key="stop-a"))
        )
        await executor.stop_entered.wait()
        second = asyncio.create_task(
            authority.stop(StopRequest(robot_id="robot_test", idempotency_key="stop-b"))
        )
        await asyncio.sleep(0)
        executor.stop_release.set()
        first_result, second_result = await asyncio.gather(first, second)

        assert first_result.stop_id == second_result.stop_id
        assert first_result.replayed is False
        assert second_result.replayed is True
        assert executor.stop_calls == 1

    asyncio.run(run())


def test_effectful_admission_stays_closed_until_stop_cleanup_is_confirmed() -> None:
    async def run() -> None:
        executor = _Executor()
        authority = _authority(executor)
        stopping = asyncio.create_task(
            authority.stop(StopRequest(robot_id="robot_test", idempotency_key="stop-fence"))
        )
        await executor.stop_entered.wait()

        with pytest.raises(LeaseBusyError, match="effectful admission is closed"):
            await authority.submit(
                SubmitTask(
                    robot_id="robot_test",
                    capability_id="navigate_fixture",
                    input_schema_version="1",
                    input={"target": "fixture_a"},
                    expected_safety_epoch=5,
                )
            )

        executor.stop_release.set()
        await stopping
        admitted = await authority.submit(
            SubmitTask(
                robot_id="robot_test",
                capability_id="navigate_fixture",
                input_schema_version="1",
                input={"target": "fixture_a"},
                expected_safety_epoch=5,
            )
        )
        assert admitted.status == TaskStatus.AWAITING_APPROVAL

    asyncio.run(run())


def test_stop_timeout_finishes_fail_closed() -> None:
    async def run() -> None:
        class CancellationResistantStopExecutor(_Executor):
            async def stop(self, _context, _events):
                self.stop_calls += 1
                self.stop_entered.set()
                try:
                    await self.stop_release.wait()
                except asyncio.CancelledError:
                    await self.stop_release.wait()
                return ExecutorStopResult(
                    request_accepted=True,
                    cancel_requested=True,
                    cancel_acknowledged=True,
                )

        executor = CancellationResistantStopExecutor()
        authority = _authority(executor, stop_timeout=timedelta(milliseconds=1))

        result = await authority.stop(
            StopRequest(robot_id="robot_test", idempotency_key="timeout-stop")
        )
        snapshot = await authority.observe()

        assert result.terminal is True
        assert result.request_accepted is True
        assert result.cancel_acknowledged is None
        assert result.limitations == ("executor_stop_timeout",)
        assert snapshot.effectful_admission_blocked_reason is not None
        assert snapshot.effectful_admission_blocked_reason.startswith("stop_unverified:")
        with pytest.raises(LeaseBusyError, match="positively reconciled"):
            await authority.submit(
                SubmitTask(
                    robot_id="robot_test",
                    capability_id="navigate_fixture",
                    input_schema_version="1",
                    input={"target": "fixture_b"},
                    expected_safety_epoch=5,
                )
            )
        executor.stop_release.set()
        await asyncio.sleep(0)

    asyncio.run(run())


def test_unacknowledged_stop_is_preserved_in_task_receipt() -> None:
    async def run() -> None:
        class UnacknowledgedStopExecutor(_Executor):
            async def stop(self, _context, _events):
                self.stop_calls += 1
                return ExecutorStopResult(
                    request_accepted=True,
                    cancel_requested=True,
                    cancel_acknowledged=False,
                    limitations=("adapter_cancel_unacknowledged",),
                )

        executor = UnacknowledgedStopExecutor()
        authority = _authority(executor)
        task = await _submit(authority)
        await _approve(authority, task)
        await executor.execution_started.wait()

        await authority.stop(StopRequest(robot_id="robot_test", idempotency_key="unknown-stop"))
        snapshot = await authority.observe()
        terminal = snapshot.tasks[0]

        assert terminal.outcome == TaskOutcome.UNAVAILABLE
        assert terminal.receipt is not None
        assert terminal.receipt.terminal_data["cancel_acknowledged"] is False
        assert terminal.receipt.terminal_data["limitations"] == ("adapter_cancel_unacknowledged",)
        assert snapshot.effectful_admission_blocked_reason is not None

    asyncio.run(run())


def test_active_cancel_without_cancel_request_remains_unverified() -> None:
    async def run() -> None:
        class NoCancelRequestExecutor(_Executor):
            async def cancel(self, _context, _events):
                self.cancel_calls += 1
                return ExecutorCancelResult(
                    request_accepted=True,
                    cancel_requested=False,
                )

        executor = NoCancelRequestExecutor()
        authority = _authority(executor)
        task = await _submit(authority)
        await _approve(authority, task)
        await executor.execution_started.wait()

        cancelled = await authority.cancel(
            CancelTask(
                command_id=task.command_id,
                reason="operator cancel",
                expected_safety_epoch=4,
            )
        )
        snapshot = await authority.observe()

        assert cancelled.task.outcome == TaskOutcome.UNAVAILABLE
        assert cancelled.cancel_requested is False
        assert snapshot.effectful_admission_blocked_reason is not None
        assert snapshot.effectful_admission_blocked_reason.startswith("task_cancel_unverified:")

    asyncio.run(run())


def test_active_stop_without_cancel_request_remains_unverified() -> None:
    async def run() -> None:
        class NoCancelRequestExecutor(_Executor):
            async def stop(self, _context, _events):
                self.stop_calls += 1
                return ExecutorStopResult(
                    request_accepted=True,
                    cancel_requested=False,
                )

        executor = NoCancelRequestExecutor()
        authority = _authority(executor)
        task = await _submit(authority)
        await _approve(authority, task)
        await executor.execution_started.wait()

        await authority.stop(StopRequest(robot_id="robot_test", idempotency_key="no-cancel"))
        snapshot = await authority.observe()

        assert snapshot.tasks[0].outcome == TaskOutcome.UNAVAILABLE
        assert snapshot.effectful_admission_blocked_reason is not None
        assert snapshot.effectful_admission_blocked_reason.startswith("stop_unverified:")

    asyncio.run(run())


def test_task_cancel_joins_active_stop_and_cannot_hide_stop_timeout() -> None:
    async def run() -> None:
        class CancellationResistantStopExecutor(_Executor):
            async def stop(self, _context, _events):
                self.stop_calls += 1
                self.stop_entered.set()
                try:
                    await self.stop_release.wait()
                except asyncio.CancelledError:
                    await self.stop_release.wait()
                return ExecutorStopResult(
                    request_accepted=True,
                    cancel_requested=True,
                    cancel_acknowledged=True,
                )

        executor = CancellationResistantStopExecutor()
        authority = _authority(executor, stop_timeout=timedelta(milliseconds=10))
        task = await _submit(authority)
        await _approve(authority, task)
        await executor.execution_started.wait()

        stopping = asyncio.create_task(
            authority.stop(StopRequest(robot_id="robot_test", idempotency_key="dominant-stop"))
        )
        await executor.stop_entered.wait()
        cancelling = asyncio.create_task(
            authority.cancel(
                CancelTask(
                    command_id=task.command_id,
                    reason="cancel during stop",
                    expected_safety_epoch=5,
                )
            )
        )
        stop_result, cancel_result = await asyncio.gather(stopping, cancelling)
        snapshot = await authority.observe()

        assert stop_result.cancel_acknowledged is None
        assert cancel_result.task.outcome == TaskOutcome.UNAVAILABLE
        assert cancel_result.limitations == (
            "task_cancel_superseded_by_robot_stop",
            "executor_stop_timeout",
        )
        assert executor.cancel_calls == 0
        assert snapshot.effectful_admission_blocked_reason is not None
        executor.stop_release.set()
        await asyncio.sleep(0)

    asyncio.run(run())


def test_cancel_callback_after_timeout_is_late_and_payload_is_not_published() -> None:
    async def run() -> None:
        class RetainedCancelExecutor(_Executor):
            def __init__(self) -> None:
                super().__init__()
                self.cancel_release = asyncio.Event()

            async def cancel(self, _context, events):
                self.cancel_calls += 1
                try:
                    await self.cancel_release.wait()
                except asyncio.CancelledError:
                    await self.cancel_release.wait()
                await events.publish(
                    ExecutorEvent(kind="cancel_late", data={"secret": "must-not-publish"})
                )
                return ExecutorCancelResult(
                    request_accepted=True,
                    cancel_requested=True,
                    cancel_acknowledged=True,
                )

        executor = RetainedCancelExecutor()
        authority = _authority(executor, cancel_timeout=timedelta(milliseconds=1))
        task = await _submit(authority)
        await _approve(authority, task)
        await executor.execution_started.wait()

        result = await authority.cancel(
            CancelTask(
                command_id=task.command_id,
                reason="timeout cancel",
                expected_safety_epoch=4,
            )
        )
        executor.cancel_release.set()
        await asyncio.sleep(0)
        snapshot = await authority.observe()
        serialized = str([event.model_dump(mode="json") for event in snapshot.events])

        assert result.task.outcome == TaskOutcome.UNAVAILABLE
        assert "cancel_event_after_operation" in serialized
        assert "must-not-publish" not in serialized

    asyncio.run(run())


def test_stop_cannot_clear_inflight_effectful_cancel_without_acknowledgement() -> None:
    async def run() -> None:
        class InflightCancelExecutor(_Executor):
            def __init__(self) -> None:
                super().__init__()
                self.cancel_entered = asyncio.Event()
                self.cancel_release = asyncio.Event()

            async def cancel(self, _context, _events):
                self.cancel_calls += 1
                self.cancel_entered.set()
                await self.cancel_release.wait()
                return ExecutorCancelResult(
                    request_accepted=True,
                    cancel_requested=True,
                    cancel_acknowledged=True,
                )

            async def stop(self, _context, _events):
                self.stop_calls += 1
                return ExecutorStopResult(
                    request_accepted=True,
                    cancel_requested=False,
                )

        executor = InflightCancelExecutor()
        authority = _authority(executor)
        task = await _submit(authority)
        await _approve(authority, task)
        await executor.execution_started.wait()
        cancelling = asyncio.create_task(
            authority.cancel(
                CancelTask(
                    command_id=task.command_id,
                    reason="operator cancel",
                    expected_safety_epoch=4,
                )
            )
        )
        await executor.cancel_entered.wait()

        stop_result = await authority.stop(
            StopRequest(robot_id="robot_test", idempotency_key="stop-over-cancel")
        )
        snapshot = await authority.observe()

        assert stop_result.cancel_requested is False
        assert snapshot.tasks[0].outcome == TaskOutcome.UNAVAILABLE
        assert snapshot.effectful_admission_blocked_reason is not None
        assert snapshot.effectful_admission_blocked_reason.startswith("stop_unverified:")
        executor.cancel_release.set()
        await cancelling

    asyncio.run(run())


def test_stop_cannot_clear_prior_unverified_cancel_without_acknowledgement() -> None:
    async def run() -> None:
        class UnverifiedThenNoopStopExecutor(_Executor):
            async def cancel(self, _context, _events):
                self.cancel_calls += 1
                return ExecutorCancelResult(
                    request_accepted=False,
                    cancel_requested=True,
                    cancel_acknowledged=None,
                    limitations=("cancel_unknown",),
                )

            async def stop(self, _context, _events):
                self.stop_calls += 1
                return ExecutorStopResult(
                    request_accepted=True,
                    cancel_requested=False,
                )

        executor = UnverifiedThenNoopStopExecutor()
        authority = _authority(executor)
        task = await _submit(authority)
        await _approve(authority, task)
        await executor.execution_started.wait()
        await authority.cancel(
            CancelTask(
                command_id=task.command_id,
                reason="unknown cancel",
                expected_safety_epoch=4,
            )
        )

        await authority.stop(
            StopRequest(robot_id="robot_test", idempotency_key="stop-over-unknown")
        )
        snapshot = await authority.observe()

        assert snapshot.effectful_admission_blocked_reason is not None
        assert snapshot.effectful_admission_blocked_reason.startswith("stop_unverified:")
        with pytest.raises(LeaseBusyError, match="positively reconciled"):
            await authority.submit(
                SubmitTask(
                    robot_id="robot_test",
                    capability_id="navigate_fixture",
                    input_schema_version="1",
                    input={"target": "fixture_b"},
                    expected_safety_epoch=5,
                )
            )

    asyncio.run(run())
