from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from jenai.runtime import (
    CapabilityExecutionReport,
    ExecutorStopResult,
    InMemoryRuntimeAuthority,
    PreparedCapabilityStep,
    RuntimeTaskRegistration,
    SubmitTask,
    TypedCapabilityStep,
    capability_binding_sha256,
)
from jenai.schemas import TaskOutcome


class _PolicyExecutor:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.executed: list[str] = []

    async def prepare(self, step, context):
        return PreparedCapabilityStep(
            step=step,
            context=context,
            binding_sha256=capability_binding_sha256(step, context),
        )

    async def execute(self, prepared, _context, _events):
        target = str(prepared.step.input["target"])
        self.executed.append(target)
        if self.fail:
            raise RuntimeError("synthetic adapter failure")
        return CapabilityExecutionReport(disposition="completed", summary="observed")

    async def snapshot(self, _request, _context):
        raise AssertionError("snapshot is outside this test seam")

    async def stop(self, _context, _events):
        return ExecutorStopResult(request_accepted=True, cancel_requested=False)


def _registration(*, effectful: bool) -> RuntimeTaskRegistration:
    return RuntimeTaskRegistration(
        capability_id="inspect_state",
        input_schema_version="1",
        workflow_definition_version="1",
        effectful=effectful,
        requires_approval=effectful,
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


def _authority(executor: object, *, effectful: bool) -> InMemoryRuntimeAuthority:
    return InMemoryRuntimeAuthority(
        runtime_id="runtime_test",
        boot_id="boot_test",
        robot_id="robot_test",
        authority_generation=1,
        initial_safety_epoch=4,
        executor=executor,  # type: ignore[arg-type]
        registrations=(_registration(effectful=effectful),),
        now=lambda: datetime(2026, 8, 5, 4, 0, tzinfo=UTC),
    )


async def _wait_for_outcomes(authority: InMemoryRuntimeAuthority, expected_count: int) -> None:
    for _ in range(20):
        snapshot = await authority.observe()
        if sum(task.outcome is not None for task in snapshot.tasks) == expected_count:
            return
        await asyncio.sleep(0)
    raise AssertionError("Tasks did not reach terminal outcomes")


def test_read_only_tasks_run_without_acquiring_effectful_command_lease() -> None:
    async def run() -> None:
        executor = _PolicyExecutor()
        authority = _authority(executor, effectful=False)

        first = await authority.submit(
            SubmitTask(
                robot_id="robot_test",
                capability_id="inspect_state",
                input_schema_version="1",
                input={"target": "pose"},
                expected_safety_epoch=4,
            )
        )
        second = await authority.submit(
            SubmitTask(
                robot_id="robot_test",
                capability_id="inspect_state",
                input_schema_version="1",
                input={"target": "battery"},
                expected_safety_epoch=4,
            )
        )
        await _wait_for_outcomes(authority, 2)
        snapshot = await authority.observe()

        assert first.pending_approval is None
        assert second.pending_approval is None
        assert snapshot.active_lease_task_id is None
        assert executor.executed == ["pose", "battery"]
        assert [task.outcome for task in snapshot.tasks] == [
            TaskOutcome.SUCCEEDED,
            TaskOutcome.SUCCEEDED,
        ]

    asyncio.run(run())


def test_unexpected_executor_failure_becomes_authority_owned_failed_outcome() -> None:
    async def run() -> None:
        executor = _PolicyExecutor(fail=True)
        authority = _authority(executor, effectful=False)
        accepted = await authority.submit(
            SubmitTask(
                robot_id="robot_test",
                capability_id="inspect_state",
                input_schema_version="1",
                input={"target": "pose"},
                expected_safety_epoch=4,
            )
        )
        await _wait_for_outcomes(authority, 1)
        snapshot = await authority.observe()
        task = snapshot.tasks[0]

        assert task.task_id == accepted.task_id
        assert task.outcome == TaskOutcome.FAILED
        assert task.receipt is not None
        assert task.receipt.outcome == TaskOutcome.FAILED
        assert snapshot.events[-1].data["error_type"] == "RuntimeError"

    asyncio.run(run())
