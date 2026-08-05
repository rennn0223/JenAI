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
    TaskStatus,
    TypedCapabilityStep,
    capability_binding_sha256,
)
from jenai.schemas import TaskOutcome


class _Executor:
    async def prepare(self, step, context):
        return PreparedCapabilityStep(
            step=step,
            context=context,
            binding_sha256=capability_binding_sha256(step, context),
        )

    async def execute(self, _prepared, _context, _events):
        return CapabilityExecutionReport(disposition="completed", summary="unexpected")

    async def snapshot(self, _request, _context):
        raise AssertionError("snapshot is outside this test seam")

    async def stop(self, _context, _events):
        return ExecutorStopResult(request_accepted=True, cancel_requested=False)


def test_observe_expires_pending_approval_and_releases_effectful_lease() -> None:
    async def run() -> None:
        current_time = [datetime(2026, 8, 5, 4, 0, tzinfo=UTC)]
        registration = RuntimeTaskRegistration(
            capability_id="inspect_fixture",
            input_schema_version="1",
            workflow_definition_version="1",
            effectful=True,
            requires_approval=True,
            approval_ttl=timedelta(seconds=30),
            build_steps=lambda task: (
                TypedCapabilityStep(
                    capability_id=task.capability_id,
                    input_schema_version=task.input_schema_version,
                    input=task.input,
                ),
            ),
            evaluate_completion=lambda _reports: TaskOutcome.SUCCEEDED,
        )
        authority = InMemoryRuntimeAuthority(
            runtime_id="runtime_test",
            boot_id="boot_test",
            robot_id="robot_test",
            authority_generation=1,
            initial_safety_epoch=4,
            executor=_Executor(),
            registrations=(registration,),
            now=lambda: current_time[0],
        )
        await authority.submit(
            SubmitTask(
                robot_id="robot_test",
                capability_id="inspect_fixture",
                input_schema_version="1",
                input={"target": "fixture_a"},
                expected_safety_epoch=4,
            )
        )
        current_time[0] += timedelta(minutes=1)

        snapshot = await authority.observe()

        assert snapshot.tasks[0].status == TaskStatus.BLOCKED
        assert snapshot.tasks[0].outcome == TaskOutcome.BLOCKED
        assert snapshot.tasks[0].pending_approval is None
        assert snapshot.active_lease_task_id is None
        assert [event.kind for event in snapshot.events][-2:] == [
            "ApprovalInvalidated",
            "TaskFinished",
        ]
        assert snapshot.events[-2].data["reason"] == "expired"

    asyncio.run(run())
