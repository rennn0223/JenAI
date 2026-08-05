from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from jenai.runtime import (
    ApprovalDecision,
    CapabilityExecutionReport,
    ExecutorStopResult,
    InMemoryRuntimeAuthority,
    ResolveApproval,
    RuntimeTaskRegistration,
    StaleSafetyEpochError,
    StopRequest,
    SubmitTask,
    TaskStatus,
    TypedCapabilityStep,
)
from jenai.runtime.models import PreparedCapabilityStep, capability_binding_sha256
from jenai.schemas import TaskOutcome


class _RecordingExecutor:
    def __init__(self) -> None:
        self.executed_inputs: list[dict[str, object]] = []
        self.stop_calls = 0

    async def prepare(self, step, context):
        return PreparedCapabilityStep(
            step=step,
            context=context,
            binding_sha256=capability_binding_sha256(step, context),
        )

    async def execute(self, prepared, _context, _events):
        self.executed_inputs.append(prepared.step.model_dump(mode="json")["input"])
        return CapabilityExecutionReport(disposition="completed", summary="adapter complete")

    async def snapshot(self, _request, _context):
        raise AssertionError("snapshot is outside this test seam")

    async def stop(self, _context, _events):
        self.stop_calls += 1
        return ExecutorStopResult(request_accepted=True, cancel_requested=True)


def _registration() -> RuntimeTaskRegistration:
    def build_steps(task: SubmitTask) -> tuple[TypedCapabilityStep, ...]:
        return (
            TypedCapabilityStep(
                capability_id=task.capability_id,
                input_schema_version=task.input_schema_version,
                input=task.input,
            ),
        )

    return RuntimeTaskRegistration(
        capability_id="inspect_fixture",
        input_schema_version="1",
        workflow_definition_version="1",
        effectful=True,
        requires_approval=True,
        approval_ttl=timedelta(minutes=1),
        build_steps=build_steps,
        evaluate_completion=lambda reports: (
            TaskOutcome.SUCCEEDED
            if len(reports) == 1 and reports[0].disposition == "completed"
            else TaskOutcome.FAILED
        ),
    )


def _authority(executor: object) -> InMemoryRuntimeAuthority:
    return InMemoryRuntimeAuthority(
        runtime_id="runtime_test",
        boot_id="boot_test",
        robot_id="robot_test",
        authority_generation=1,
        initial_safety_epoch=4,
        executor=executor,  # type: ignore[arg-type]
        registrations=(_registration(),),
        now=lambda: datetime(2026, 8, 5, 4, 0, tzinfo=UTC),
    )


def _request(input_value: dict[str, object] | None = None) -> SubmitTask:
    return SubmitTask(
        robot_id="robot_test",
        capability_id="inspect_fixture",
        input_schema_version="1",
        input=input_value or {"target": "fixture_a"},
        expected_safety_epoch=4,
    )


def test_rejected_approval_is_terminal_and_releases_lease_without_execution() -> None:
    async def run() -> None:
        executor = _RecordingExecutor()
        authority = _authority(executor)
        accepted = await authority.submit(_request())
        assert accepted.pending_approval is not None

        rejected = await authority.resolve_approval(
            ResolveApproval(
                task_id=accepted.task_id,
                approval_id=accepted.pending_approval.approval_id,
                decision=ApprovalDecision.REJECT,
                expected_safety_epoch=4,
            )
        )
        snapshot = await authority.observe()

        assert rejected.status == TaskStatus.BLOCKED
        assert rejected.outcome == TaskOutcome.BLOCKED
        assert rejected.receipt is not None
        assert snapshot.active_lease_task_id is None
        assert executor.executed_inputs == []
        assert [event.kind for event in snapshot.events][-2:] == [
            "ApprovalResolved",
            "TaskFinished",
        ]

    asyncio.run(run())


def test_stale_epoch_rejects_submit_and_approval_before_execution() -> None:
    async def run() -> None:
        executor = _RecordingExecutor()
        authority = _authority(executor)
        stale_request = _request().model_copy(update={"expected_safety_epoch": 3})

        with pytest.raises(StaleSafetyEpochError):
            await authority.submit(stale_request)
        accepted = await authority.submit(_request())
        assert accepted.pending_approval is not None
        with pytest.raises(StaleSafetyEpochError):
            await authority.resolve_approval(
                ResolveApproval(
                    task_id=accepted.task_id,
                    approval_id=accepted.pending_approval.approval_id,
                    decision=ApprovalDecision.APPROVE,
                    expected_safety_epoch=3,
                )
            )

        snapshot = await authority.observe()
        assert len(snapshot.tasks) == 1
        assert snapshot.tasks[0].status == TaskStatus.AWAITING_APPROVAL
        assert executor.executed_inputs == []

    asyncio.run(run())


def test_authority_detaches_request_and_public_projection_data() -> None:
    async def run() -> None:
        raw_input: dict[str, object] = {
            "target": "fixture_a",
            "options": {"include_pose": True},
        }
        request = _request(raw_input)
        executor = _RecordingExecutor()
        authority = _authority(executor)
        accepted = await authority.submit(request)
        assert accepted.pending_approval is not None
        raw_input["target"] = "fixture_b"
        nested = raw_input["options"]
        assert isinstance(nested, dict)
        nested["include_pose"] = False

        await authority.resolve_approval(
            ResolveApproval(
                task_id=accepted.task_id,
                approval_id=accepted.pending_approval.approval_id,
                decision=ApprovalDecision.APPROVE,
                expected_safety_epoch=4,
            )
        )
        for _ in range(20):
            snapshot = await authority.observe()
            if snapshot.tasks[0].outcome is not None:
                break
            await asyncio.sleep(0)

        assert executor.executed_inputs == [
            {"target": "fixture_a", "options": {"include_pose": True}}
        ]
        detached = snapshot.model_dump(mode="json")
        detached["events"][0]["data"]["forged"] = True
        fresh = await authority.observe()
        assert "forged" not in fresh.events[0].data
        assert fresh.tasks[0].receipt is not None
        assert fresh.tasks[0].receipt.outcome == TaskOutcome.SUCCEEDED

    asyncio.run(run())


def test_executor_report_contract_cannot_spoof_task_outcome() -> None:
    with pytest.raises(ValidationError):
        CapabilityExecutionReport.model_validate(
            {
                "disposition": "completed",
                "summary": "adapter assertion",
                "outcome": "succeeded",
            }
        )


def test_concurrent_duplicate_stop_advances_epoch_and_calls_executor_once() -> None:
    async def run() -> None:
        class BlockingStopExecutor(_RecordingExecutor):
            def __init__(self) -> None:
                super().__init__()
                self.entered = asyncio.Event()
                self.release = asyncio.Event()

            async def stop(self, _context, _events):
                self.stop_calls += 1
                self.entered.set()
                await self.release.wait()
                return ExecutorStopResult(request_accepted=True, cancel_requested=True)

        executor = BlockingStopExecutor()
        authority = _authority(executor)
        request = StopRequest(robot_id="robot_test", idempotency_key="same-stop")
        first_task = asyncio.create_task(authority.stop(request))
        await executor.entered.wait()
        second_task = asyncio.create_task(authority.stop(request))
        await asyncio.sleep(0)
        executor.release.set()
        first, replay = await asyncio.gather(first_task, second_task)
        snapshot = await authority.observe()

        assert executor.stop_calls == 1
        assert snapshot.safety_epoch == 5
        assert first.stop_id == replay.stop_id
        assert first.replayed is False
        assert replay.replayed is True
        assert len([event for event in snapshot.events if event.kind == "StopFinished"]) == 1

    asyncio.run(run())


def test_stop_revokes_authority_state_before_executor_side_effect() -> None:
    async def run() -> None:
        observed_state: list[tuple[int, str | None, TaskStatus, bool]] = []

        class InspectingStopExecutor(_RecordingExecutor):
            async def stop(self, _context, _events):
                self.stop_calls += 1
                snapshot = await authority.observe()
                task = snapshot.tasks[0]
                observed_state.append(
                    (
                        snapshot.safety_epoch,
                        snapshot.active_lease_task_id,
                        task.status,
                        task.pending_approval is None,
                    )
                )
                return ExecutorStopResult(request_accepted=True, cancel_requested=True)

        executor = InspectingStopExecutor()
        authority = _authority(executor)
        await authority.submit(_request())

        await authority.stop(StopRequest(robot_id="robot_test", idempotency_key="ordering-stop"))

        assert observed_state == [(5, None, TaskStatus.STOPPING, True)]

    asyncio.run(run())
