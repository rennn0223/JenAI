from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from jenai.runtime import (
    AuthorityContext,
    CancelContext,
    CapabilityExecutionRegistration,
    CapabilityExecutionReport,
    CapabilityPreparationError,
    CapabilityUnavailableError,
    EvidenceContentDigest,
    EvidenceTimestampStatus,
    ExecutionContext,
    ExecutionContextMismatchError,
    ExecutorCancelResult,
    ExecutorEvent,
    ExecutorEvidence,
    ExecutorStopResult,
    InMemoryCapabilityExecutor,
    ObservationContext,
    ObservationSnapshot,
    SnapshotRequest,
    SourceAssurance,
    StopContext,
    StopTrigger,
    TransportSecurity,
    TypedCapabilityStep,
    UnsupportedCapabilitySchemaError,
    capability_binding_sha256,
)


class RecordingEvents:
    def __init__(self) -> None:
        self.items: list[ExecutorEvent] = []

    async def publish(self, event: ExecutorEvent) -> None:
        self.items.append(event)


def _authority_context(**changes: object) -> AuthorityContext:
    values: dict[str, object] = {
        "runtime_id": "runtime_test",
        "boot_id": "boot_test",
        "authority_generation": 3,
        "safety_epoch": 7,
    }
    values.update(changes)
    return AuthorityContext.model_validate(values)


def _execution_context(**changes: object) -> ExecutionContext:
    authority_changes = {
        key: changes.pop(key)
        for key in ("runtime_id", "boot_id", "authority_generation", "safety_epoch")
        if key in changes
    }
    values: dict[str, object] = {
        "authority": _authority_context(**authority_changes),
        "fencing_token": 11,
        "robot_id": "robot_test",
        "task_id": "task_test",
        "command_id": "command_test",
    }
    values.update(changes)
    return ExecutionContext.model_validate(values)


def _executor(
    *,
    executed: list[str] | None = None,
    stopped: list[StopContext] | None = None,
    execution_handler=None,
    preparation_handler=None,
) -> InMemoryCapabilityExecutor:
    async def snapshot(request, _context):
        return ObservationSnapshot(
            robot_id=request.robot_id,
            evidence=(
                ExecutorEvidence(
                    kind="health",
                    source="in_memory",
                    source_timestamp_status=EvidenceTimestampStatus.UNAVAILABLE,
                    transport_security=TransportSecurity.UNKNOWN,
                    source_assurance=SourceAssurance.DERIVED,
                    payload_schema_version="1",
                    payload={"status": "available"},
                ),
            ),
        )

    async def prepare(step, _context):
        allowed = {"include_pose", "target"}
        unexpected = set(step.input) - allowed
        if unexpected:
            raise CapabilityPreparationError(
                f"unexpected inspect_state input: {sorted(unexpected)!r}"
            )
        include_pose = step.input.get("include_pose", False)
        if not isinstance(include_pose, bool):
            raise CapabilityPreparationError("include_pose must be a boolean")
        canonical_input = dict(step.input)
        canonical_input["include_pose"] = include_pose
        return TypedCapabilityStep(
            capability_id=step.capability_id,
            input_schema_version=step.input_schema_version,
            input=canonical_input,
        )

    async def execute(prepared, events):
        if executed is not None:
            executed.append(prepared.step.capability_id)
        await events.publish(ExecutorEvent(kind="step_observed", data={"ready": True}))
        return CapabilityExecutionReport(
            disposition="completed",
            summary="Observation complete",
        )

    async def cancel(_context, events):
        await events.publish(ExecutorEvent(kind="cancel_delivered"))
        return ExecutorCancelResult(
            request_accepted=True,
            cancel_requested=True,
            cancel_acknowledged=True,
        )

    async def stop(context, events):
        if stopped is not None:
            stopped.append(context)
        await events.publish(ExecutorEvent(kind="stop_delivered"))
        return ExecutorStopResult(
            request_accepted=True,
            cancel_requested=True,
            cancel_acknowledged=True,
            zero_velocity_command_published=True,
        )

    return InMemoryCapabilityExecutor(
        snapshot_handler=snapshot,
        registrations=(
            CapabilityExecutionRegistration(
                capability_id="inspect_state",
                input_schema_version="1",
                prepare=preparation_handler or prepare,
                execute=execution_handler or execute,
            ),
        ),
        stop_handler=stop,
        cancel_handler=cancel,
    )


def test_prepare_and_execute_one_typed_capability_through_the_port() -> None:
    async def run() -> None:
        executed: list[str] = []
        events = RecordingEvents()
        executor = _executor(executed=executed)
        context = _execution_context()
        step = TypedCapabilityStep(
            capability_id="inspect_state",
            input_schema_version="1",
            input={"include_pose": True},
        )

        prepared = await executor.prepare(step, context)
        result = await executor.execute(prepared, context, events)

        assert prepared.context == context
        assert len(prepared.binding_sha256) == 64
        assert executed == ["inspect_state"]
        assert result.disposition == "completed"
        assert [event.kind for event in events.items] == ["step_observed"]

    asyncio.run(run())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("boot_id", "boot_restarted"),
        ("authority_generation", 4),
        ("safety_epoch", 8),
        ("fencing_token", 12),
        ("task_id", "task_other"),
    ],
)
def test_execute_rejects_any_mismatched_or_cross_task_context(field: str, value: object) -> None:
    async def run() -> None:
        executed: list[str] = []
        executor = _executor(executed=executed)
        original = _execution_context()
        step = TypedCapabilityStep(
            capability_id="inspect_state",
            input_schema_version="1",
            input={},
        )
        prepared = await executor.prepare(step, original)
        stale = _execution_context(**{field: value})

        with pytest.raises(ExecutionContextMismatchError):
            await executor.execute(prepared, stale, RecordingEvents())
        assert executed == []

    asyncio.run(run())


def test_prepare_rejects_an_unregistered_capability_without_side_effects() -> None:
    async def run() -> None:
        executor = _executor()
        step = TypedCapabilityStep(
            capability_id="vendor_raw_command",
            input_schema_version="1",
            input={},
        )

        with pytest.raises(CapabilityUnavailableError):
            await executor.prepare(step, _execution_context())

    asyncio.run(run())


def test_prepare_rejects_an_unsupported_input_schema_before_effect() -> None:
    async def run() -> None:
        executed: list[str] = []
        executor = _executor(executed=executed)

        with pytest.raises(UnsupportedCapabilitySchemaError):
            await executor.prepare(
                TypedCapabilityStep(
                    capability_id="inspect_state",
                    input_schema_version="99999",
                    input={"include_pose": True},
                ),
                _execution_context(),
            )

        assert executed == []

    asyncio.run(run())


def test_prepare_rejects_invalid_payload_before_effect() -> None:
    async def run() -> None:
        executed: list[str] = []
        executor = _executor(executed=executed)

        with pytest.raises(CapabilityPreparationError, match="include_pose"):
            await executor.prepare(
                TypedCapabilityStep(
                    capability_id="inspect_state",
                    input_schema_version="1",
                    input={"include_pose": "yes"},
                ),
                _execution_context(),
            )

        assert executed == []

    asyncio.run(run())


def test_prepare_failure_never_calls_the_execution_handler() -> None:
    async def run() -> None:
        executed: list[str] = []

        async def reject(_step, _context):
            raise ValueError("invalid payload shape")

        executor = _executor(executed=executed, preparation_handler=reject)

        with pytest.raises(CapabilityPreparationError, match="failed preparation"):
            await executor.prepare(
                TypedCapabilityStep(
                    capability_id="inspect_state",
                    input_schema_version="1",
                    input={},
                ),
                _execution_context(),
            )

        assert executed == []

    asyncio.run(run())


def test_prepare_binds_the_canonicalized_input() -> None:
    async def run() -> None:
        executor = _executor()
        context = _execution_context()
        original = TypedCapabilityStep(
            capability_id="inspect_state",
            input_schema_version="1",
            input={},
        )

        prepared = await executor.prepare(original, context)

        assert original.input == {}
        assert prepared.step.input == {"include_pose": False}
        assert prepared.binding_sha256 == capability_binding_sha256(prepared.step, context)

    asyncio.run(run())


def test_execute_rejects_a_tampered_copy_after_prepare() -> None:
    async def run() -> None:
        executed: list[str] = []
        executor = _executor(executed=executed)
        context = _execution_context()
        prepared = await executor.prepare(
            TypedCapabilityStep(
                capability_id="inspect_state",
                input_schema_version="1",
                input={"include_pose": True},
            ),
            context,
        )
        tampered = prepared.model_copy(
            update={
                "step": TypedCapabilityStep(
                    capability_id="inspect_state",
                    input_schema_version="1",
                    input={"include_pose": False},
                )
            }
        )

        with pytest.raises(ExecutionContextMismatchError):
            await executor.execute(tampered, context, RecordingEvents())
        assert executed == []

    asyncio.run(run())


def test_execution_handler_cannot_mutate_the_bound_input() -> None:
    async def run() -> None:
        mutation_rejected = False

        async def execute(prepared, _events):
            nonlocal mutation_rejected
            try:
                prepared.step.input["target"] = "changed"
            except TypeError:
                mutation_rejected = True
            return CapabilityExecutionReport(
                disposition="completed",
                summary="Observation complete",
            )

        executor = _executor(execution_handler=execute)
        context = _execution_context()
        prepared = await executor.prepare(
            TypedCapabilityStep(
                capability_id="inspect_state",
                input_schema_version="1",
                input={"target": "bound"},
            ),
            context,
        )
        retained_input = {"target": "bound", "include_pose": False}
        bypassed_step = prepared.step.model_copy(update={"input": retained_input})
        bypassed = prepared.model_copy(
            update={
                "step": bypassed_step,
                "binding_sha256": capability_binding_sha256(bypassed_step, context),
            }
        )

        await executor.execute(bypassed, context, RecordingEvents())

        assert mutation_rejected is True
        assert retained_input["target"] == "bound"

    asyncio.run(run())


def test_prepare_detaches_the_bound_input_from_the_original_object() -> None:
    async def run() -> None:
        raw_input = {"target": {"name": "A"}}
        executor = _executor()
        step = TypedCapabilityStep(
            capability_id="inspect_state",
            input_schema_version="1",
            input=raw_input,
        )
        prepared = await executor.prepare(step, _execution_context())

        with pytest.raises(TypeError):
            step.input["target"] = {"name": "B"}
        raw_input["target"]["name"] = "B"

        assert prepared.step.input["target"] == {"name": "A"}

    asyncio.run(run())


def test_external_coroutine_cannot_change_input_after_binding_validation() -> None:
    async def run() -> None:
        handler_started = asyncio.Event()
        continue_handler = asyncio.Event()
        observed_targets: list[object] = []

        async def execute(prepared, _events):
            handler_started.set()
            await continue_handler.wait()
            observed_targets.append(prepared.step.input["target"])
            return CapabilityExecutionReport(
                disposition="completed",
                summary="Observation complete",
            )

        executor = _executor(execution_handler=execute)
        context = _execution_context()
        retained_input = {"target": "A", "include_pose": False}
        prepared = await executor.prepare(
            TypedCapabilityStep(
                capability_id="inspect_state",
                input_schema_version="1",
                input={"target": "A"},
            ),
            context,
        )
        bypassed_step = prepared.step.model_copy(update={"input": retained_input})
        bypassed = prepared.model_copy(
            update={
                "step": bypassed_step,
                "binding_sha256": capability_binding_sha256(bypassed_step, context),
            }
        )
        execution = asyncio.create_task(executor.execute(bypassed, context, RecordingEvents()))
        await handler_started.wait()

        retained_input["target"] = "B"
        continue_handler.set()
        await execution

        assert observed_targets == ["A"]

    asyncio.run(run())


def test_published_event_is_recursively_immutable_and_detached() -> None:
    async def run() -> None:
        raw_data = {"state": {"ready": True}}

        async def execute(_prepared, events):
            event = ExecutorEvent(kind="progress").model_copy(update={"data": raw_data})
            await events.publish(event)
            raw_data["state"]["ready"] = False
            return CapabilityExecutionReport(
                disposition="completed",
                summary="Observation complete",
            )

        executor = _executor(execution_handler=execute)
        context = _execution_context()
        prepared = await executor.prepare(
            TypedCapabilityStep(
                capability_id="inspect_state",
                input_schema_version="1",
                input={},
            ),
            context,
        )
        sink = RecordingEvents()

        await executor.execute(prepared, context, sink)

        assert sink.items[0].data["state"] == {"ready": True}

    asyncio.run(run())


def test_returned_report_evidence_cannot_change_through_retained_references() -> None:
    async def run() -> None:
        raw_payload = {"result": {"ok": True}}

        async def execute(_prepared, _events):
            evidence = ExecutorEvidence(
                kind="inspection",
                source="in_memory",
                source_timestamp_status=EvidenceTimestampStatus.UNAVAILABLE,
                transport_security=TransportSecurity.UNKNOWN,
                source_assurance=SourceAssurance.DERIVED,
                payload_schema_version="1",
                payload={},
            ).model_copy(update={"payload": raw_payload})
            return CapabilityExecutionReport(
                disposition="completed",
                summary="Observation complete",
                evidence=(evidence,),
            )

        executor = _executor(execution_handler=execute)
        context = _execution_context()
        prepared = await executor.prepare(
            TypedCapabilityStep(
                capability_id="inspect_state",
                input_schema_version="1",
                input={},
            ),
            context,
        )
        report = await executor.execute(prepared, context, RecordingEvents())
        raw_payload["result"]["ok"] = False

        assert report.evidence[0].payload["result"] == {"ok": True}

    asyncio.run(run())


def test_snapshot_is_detached_and_robot_bound() -> None:
    async def run() -> None:
        executor = _executor()
        snapshot = await executor.snapshot(
            SnapshotRequest(robot_id="robot_test"),
            ObservationContext(authority=_authority_context()),
        )

        assert snapshot.robot_id == "robot_test"
        assert snapshot.evidence[0].payload == {"status": "available"}
        with pytest.raises(ValidationError):
            ObservationSnapshot.model_validate(
                {
                    **snapshot.model_dump(mode="python"),
                    "unexpected": "not accepted",
                }
            )

    asyncio.run(run())


def test_executor_evidence_preserves_independent_source_provenance() -> None:
    observed_at = datetime(2026, 8, 4, tzinfo=UTC)
    evidence = ExecutorEvidence(
        kind="pose",
        source="nav2_amcl",
        source_observed_at=observed_at,
        source_timestamp_status=EvidenceTimestampStatus.AVAILABLE,
        content_digest=EvidenceContentDigest(algorithm="sha256", value="a" * 64),
        transport_security=TransportSecurity.AUTHENTICATED,
        source_assurance=SourceAssurance.RUNTIME_OBSERVED,
        payload_schema_version="1",
        payload={"x": 1.0, "y": 2.0},
    )

    assert evidence.source_observed_at == observed_at
    assert evidence.content_digest is not None
    assert evidence.transport_security == "authenticated"
    assert evidence.source_assurance == "runtime_observed"


def test_executor_evidence_rejects_a_false_source_timestamp_claim() -> None:
    with pytest.raises(ValidationError, match="source timestamp status"):
        ExecutorEvidence(
            kind="pose",
            source="vendor",
            source_timestamp_status=EvidenceTimestampStatus.AVAILABLE,
            transport_security=TransportSecurity.UNKNOWN,
            source_assurance=SourceAssurance.UNKNOWN,
            payload_schema_version="1",
            payload={},
        )


def test_stop_uses_a_separate_provider_free_port() -> None:
    async def run() -> None:
        stopped: list[StopContext] = []
        events = RecordingEvents()
        executor = _executor(stopped=stopped)
        context = StopContext(
            authority=_authority_context(safety_epoch=8),
            robot_id="robot_test",
            stop_id="stop_test",
            trigger=StopTrigger.OPERATOR,
        )

        result = await executor.stop(context, events)

        assert stopped == [context]
        assert result.request_accepted is True
        assert result.cancel_acknowledged is True
        assert [event.kind for event in events.items] == ["stop_delivered"]

    asyncio.run(run())


def test_cancel_uses_a_task_scoped_provider_free_port() -> None:
    async def run() -> None:
        events = RecordingEvents()
        executor = _executor()
        context = CancelContext(
            authority=_authority_context(),
            robot_id="robot_test",
            task_id="task_test",
            command_id="command_test",
            reason="operator cancel",
        )

        result = await executor.cancel(context, events)

        assert result.request_accepted is True
        assert result.cancel_requested is True
        assert result.cancel_acknowledged is True
        assert [event.kind for event in events.items] == ["cancel_delivered"]

    asyncio.run(run())
