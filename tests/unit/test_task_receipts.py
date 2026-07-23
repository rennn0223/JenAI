from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jenai.schemas import (
    ApprovalRequest,
    ApprovalStatus,
    EffectScope,
    ErrorType,
    JenAIError,
    RiskLevel,
    RunRecord,
    RunStatus,
    TaskOutcome,
    ToolCallCategory,
    ToolCallRecord,
    ToolCallStatus,
)
from jenai.state.runs import RunStore
from jenai.state.task_receipts import (
    TaskReceiptStore,
    build_task_receipt,
    classify_failure,
    render_task_receipt,
)


def _terminal_run(status: RunStatus = RunStatus.COMPLETED) -> RunRecord:
    started = datetime(2026, 7, 23, 1, 2, 3, tzinfo=UTC)
    return RunRecord(
        run_id="run_test",
        session_id="session_test",
        user_input="inspect the robot",
        status=status,
        started_at=started,
        finished_at=started + timedelta(seconds=1.234),
        final_output="robot ready",
        tool_calls=[
            ToolCallRecord(
                tool_name="ros_state_tool",
                category=ToolCallCategory.ROS2,
                input_summary="read state",
                status=ToolCallStatus.SUCCEEDED,
                risk_level=RiskLevel.P0,
                effect_scope=EffectScope.READ,
                output_summary="pose and Nav2 ready",
            )
        ],
    )


def test_completed_task_without_explicit_outcome_is_partial() -> None:
    receipt = build_task_receipt(_terminal_run())
    assert receipt.status == RunStatus.COMPLETED
    assert receipt.outcome == TaskOutcome.PARTIAL
    assert receipt.failure_code is None


def test_build_and_render_explicitly_successful_task_receipt() -> None:
    run = _terminal_run()
    run.outcome = TaskOutcome.SUCCEEDED

    receipt = build_task_receipt(run)

    assert receipt.outcome == TaskOutcome.SUCCEEDED
    assert receipt.duration_ms == 1234
    assert receipt.actions[0].tool_name == "ros_state_tool"
    assert "Duration: 1.23s" in render_task_receipt(receipt)
    assert "pose and Nav2 ready" in render_task_receipt(receipt)


def test_task_receipt_preserves_explicit_unverified_arrival() -> None:
    run = _terminal_run()
    run.outcome = TaskOutcome.ARRIVED_UNVERIFIED

    receipt = build_task_receipt(run)

    assert receipt.outcome == TaskOutcome.ARRIVED_UNVERIFIED
    assert "Outcome: arrived_unverified" in render_task_receipt(receipt)


def test_failure_taxonomy_prefers_rejection_and_recognizes_timeout() -> None:
    rejected = _terminal_run(RunStatus.BLOCKED)
    rejected.interruptions = [
        ApprovalRequest(
            run_id=rejected.run_id,
            tool_call_id="tool_1",
            title="Move",
            summary="move",
            raw_action="move",
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
            justification="requested",
            status=ApprovalStatus.REJECTED,
        )
    ]
    assert classify_failure(rejected) == "approval_rejected"

    timed_out = _terminal_run(RunStatus.FAILED)
    timed_out.error = JenAIError(
        error_type=ErrorType.TOOL_ERROR,
        message="NavigateToPose timed out",
    )
    assert classify_failure(timed_out) == "timeout"

    unavailable = _terminal_run(RunStatus.BLOCKED)
    unavailable.final_output = "Nav2 is unavailable on this host"
    assert classify_failure(unavailable) == "unavailable"

    navigation = _terminal_run(RunStatus.FAILED)
    navigation.final_output = "NavigateToPose goal failed in controller"
    assert classify_failure(navigation) == "navigation"

    safety_cancel = _terminal_run(RunStatus.BLOCKED)
    safety_cancel.final_output = "Twin Gate canceled navigation after collision"
    assert classify_failure(safety_cancel) == "safety_blocked"

    provider = _terminal_run(RunStatus.FAILED)
    provider.error = JenAIError(
        error_type=ErrorType.MODEL_ERROR,
        message="model unavailable",
    )
    assert classify_failure(provider) == "provider"


def test_completed_run_with_failed_tool_is_not_reported_as_success() -> None:
    unavailable = _terminal_run()
    unavailable.tool_calls[0].status = ToolCallStatus.FAILED
    unavailable.tool_calls[0].output_summary = "Nav2 is unavailable"

    receipt = build_task_receipt(unavailable)

    assert receipt.failure_code == "unavailable"
    assert receipt.outcome == TaskOutcome.UNAVAILABLE


def test_run_store_preserves_outcome_set_by_domain_tool() -> None:
    run_store = RunStore()
    run = run_store.create_run("session_test", "dock")
    run.outcome = TaskOutcome.ARRIVED_UNVERIFIED

    run_store.finish(run, status=RunStatus.COMPLETED, final_output="arrived")

    assert run.outcome == TaskOutcome.ARRIVED_UNVERIFIED


def test_failed_run_gets_default_failed_outcome() -> None:
    run_store = RunStore()
    run = run_store.create_run("session_test", "move")

    run_store.finish(run, status=RunStatus.FAILED, final_output="controller failed")

    assert run.outcome == TaskOutcome.FAILED


def test_receipt_write_failure_is_audited_without_masking_task(tmp_path, monkeypatch) -> None:
    from jenai.state.audit import AuditStore

    receipt_store = TaskReceiptStore(tmp_path / "reports" / "tasks")
    audit_store = AuditStore(tmp_path / "audit.sqlite3")
    run_store = RunStore(audit_store=audit_store, receipt_store=receipt_store)
    run = run_store.create_run("session_test", "inspect")

    def fail_save(_run):
        raise OSError("secret filesystem detail")

    monkeypatch.setattr(receipt_store, "save", fail_save)
    run_store.finish(run, status=RunStatus.COMPLETED, final_output="done")

    assert run.status == RunStatus.COMPLETED
    failed = next(
        event for event in audit_store.list_events() if event.event_type == "task_receipt_failed"
    )
    assert failed.status == "failed"
    assert failed.summary == "Task receipt could not be persisted."
    assert failed.details == {"exception_type": "OSError"}
    assert "secret filesystem detail" not in str(failed)


def test_task_receipt_store_roundtrip_and_run_store_auto_save(tmp_path) -> None:
    receipt_store = TaskReceiptStore(tmp_path / "reports" / "tasks")
    run_store = RunStore(receipt_store=receipt_store)
    run = run_store.create_run("session_test", "go to the dock")
    run_store.finish(run, status=RunStatus.COMPLETED, final_output="arrived")

    paths = receipt_store.list_paths()
    assert len(paths) == 1
    receipt = receipt_store.load(paths[0])
    assert receipt is not None
    assert receipt.run_id == run.run_id
    assert receipt.request == "go to the dock"
    assert receipt.result == "arrived"

    # A review/resume of the same run updates one receipt rather than duplicating it.
    run_store.finish(run, status=RunStatus.COMPLETED, final_output="reviewed")
    assert receipt_store.list_paths() == paths
    assert receipt_store.load(paths[0]).result == "reviewed"
