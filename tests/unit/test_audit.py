from __future__ import annotations

import os

import pytest

from jenai.schemas import (
    ApprovalRequest,
    ApprovalStatus,
    EffectScope,
    RiskLevel,
    RunStatus,
    ToolCallCategory,
    ToolCallRecord,
    ToolCallStatus,
)
from jenai.state.audit import AuditStore
from jenai.state.runs import RunStore


def test_audit_store_persists_filters_and_bounds_events(tmp_path) -> None:
    path = tmp_path / "audit.sqlite3"
    store = AuditStore(path, max_events=3)
    for index in range(5):
        store.record("tick", run_id=f"run-{index % 2}", status="ok", details={"n": index})

    reloaded = AuditStore(path, max_events=3)
    events = reloaded.list_events()
    assert [event.details["n"] for event in events] == [4, 3, 2]
    assert all(event.run_id == "run-0" for event in reloaded.list_events(run_id="run-0"))
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_run_store_audits_lifecycle_without_raw_payloads(tmp_path) -> None:
    audit = AuditStore(tmp_path / "audit.sqlite3")
    store = RunStore(audit_store=audit)
    run = store.create_run("session-1", "secret user request")
    tool = ToolCallRecord(
        tool_name="ros_pub_execute_tool",
        category=ToolCallCategory.ROS2,
        input_summary="secret payload",
        raw_input={"secret": "raw"},
        risk_level=RiskLevel.P1,
        effect_scope=EffectScope.SIM_CONTROL,
    )
    store.add_tool_call(run, tool)
    store.update_tool_call(run, tool.tool_call_id, status=ToolCallStatus.SUCCEEDED)
    approval = ApprovalRequest(
        run_id=run.run_id,
        tool_call_id=tool.tool_call_id,
        tool_name=tool.tool_name,
        title="Publish",
        summary="secret approval summary",
        raw_action="secret raw action",
        risk_level=RiskLevel.P1,
        effect_scope=EffectScope.SIM_CONTROL,
        justification="secret justification",
    )
    store.add_interruption(run, approval)
    store.resolve_interruption(run, tool.tool_call_id, ApprovalStatus.APPROVED)
    store.finish(run, status=RunStatus.COMPLETED, final_output="secret output")

    events = list(reversed(audit.list_events(run_id=run.run_id)))
    assert [event.event_type for event in events] == [
        "run_created",
        "tool_registered",
        "tool_updated",
        "approval_requested",
        "approval_resolved",
        "run_status",
        "run_finished",
    ]
    serialized = " ".join(f"{event.summary} {event.details}" for event in events)
    assert "secret" not in serialized


def test_tool_call_updates_reject_unknown_fields_and_invalid_types() -> None:
    store = RunStore()
    run = store.create_run("session-1", "test")
    tool = ToolCallRecord(
        tool_name="ros_topics_tool",
        category=ToolCallCategory.ROS2,
        input_summary="topics",
    )
    store.add_tool_call(run, tool)

    with pytest.raises(ValueError, match="immutable or unknown"):
        store.update_tool_call(run, tool.tool_call_id, tool_name="forged")
    with pytest.raises(ValueError):
        store.update_tool_call(run, tool.tool_call_id, status="not-a-status")
    with pytest.raises(KeyError, match="missing"):
        store.update_tool_call(run, "missing", status=ToolCallStatus.SUCCEEDED)

    assert tool.tool_name == "ros_topics_tool"
    assert tool.status == ToolCallStatus.QUEUED


def test_audit_failure_is_logged_without_blocking_the_run(tmp_path, monkeypatch, caplog) -> None:
    audit = AuditStore(tmp_path / "audit.sqlite3")

    def broken_record(*_args, **_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(audit, "record", broken_record)
    store = RunStore(audit_store=audit)

    with caplog.at_level("WARNING"):
        run = store.create_run("session-1", "keep running")

    assert run.session_id == "session-1"
    assert "could not be persisted" in caplog.text
