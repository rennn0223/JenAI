from __future__ import annotations

import asyncio

from agents import Agent

from jenai.schemas import (
    ApprovalRequest,
    ApprovalStatus,
    EffectScope,
    ModelBindings,
    RiskLevel,
    RunStatus,
    SessionState,
    ToolCallCategory,
    ToolCallRecord,
    ToolCallStatus,
)
from jenai.state.history import InputHistory
from jenai.state.runs import RunStore


def _session() -> SessionState:
    bindings = ModelBindings(chat="m", plan="m", vision="m", route="m", default="m")
    return SessionState(provider_profile="test", model_bindings=bindings, working_directory="/tmp")


def test_input_history_navigates_back_and_forward() -> None:
    session = _session()
    history = InputHistory(session)

    history.record("first")
    history.record("second")
    history.record("third")

    assert history.previous() == "third"
    assert history.previous() == "second"
    assert history.previous() == "first"
    assert history.previous() == "first"

    assert history.next() == "second"
    assert history.next() == "third"
    assert history.next() == ""


def test_input_history_ignores_blank_submissions() -> None:
    session = _session()
    history = InputHistory(session)
    history.record("")
    assert session.input_history == []


def test_input_history_empty_returns_none() -> None:
    session = _session()
    history = InputHistory(session)
    assert history.previous() is None


def test_run_store_no_tool_flow_reaches_completed() -> None:
    store = RunStore()
    run = store.create_run("session-1", "do something")

    store.set_status(run, RunStatus.UNDERSTANDING)
    store.set_status(run, RunStatus.PLANNING)
    store.set_status(run, RunStatus.RUNNING)
    store.finish(run, status=RunStatus.COMPLETED, final_output="done")

    assert run.status == "completed"
    assert run.final_output == "done"
    assert run.finished_at is not None


def test_run_store_approval_flow_add_and_resolve() -> None:
    store = RunStore()
    run = store.create_run("session-1", "publish a message")
    store.set_status(run, RunStatus.RUNNING)

    tool_call = ToolCallRecord(
        tool_name="ros_pub_execute_tool",
        category=ToolCallCategory.ROS2,
        input_summary="publish to /cmd_vel",
        risk_level=RiskLevel.P1,
        effect_scope=EffectScope.SIM_CONTROL,
    )
    store.add_tool_call(run, tool_call)
    store.update_tool_call(run, tool_call.tool_call_id, status=ToolCallStatus.AWAITING_APPROVAL)

    approval = ApprovalRequest(
        run_id=run.run_id,
        tool_call_id=tool_call.tool_call_id,
        title="Publish to /cmd_vel",
        summary="Send a velocity command",
        raw_action="ros2 topic pub ...",
        risk_level=RiskLevel.P1,
        effect_scope=EffectScope.SIM_CONTROL,
        justification="user asked to move forward",
    )
    store.add_interruption(run, approval)
    store.set_status(run, RunStatus.AWAITING_APPROVAL)

    assert run.tool_calls[0].status == "awaiting_approval"
    assert run.interruptions[0].status == "pending"

    store.resolve_interruption(run, approval.tool_call_id, ApprovalStatus.APPROVED)
    store.set_status(run, RunStatus.RUNNING)
    store.finish(run, status=RunStatus.COMPLETED, final_output="published")

    assert run.interruptions[0].status == "approved"
    assert run.interruptions[0].resolved_at is not None
    assert run.status == "completed"


def test_run_store_rejection_sets_blocked() -> None:
    store = RunStore()
    run = store.create_run("session-1", "publish a message")
    store.finish(run, status=RunStatus.BLOCKED)

    assert run.status == "blocked"
    assert run.finished_at is not None


def test_run_store_pending_state_roundtrip() -> None:
    store = RunStore()
    run = store.create_run("session-1", "task")
    sentinel = object()

    store.stash_pending_state(run.run_id, sentinel)
    assert store.pop_pending_state(run.run_id) is sentinel
    assert store.pop_pending_state(run.run_id) is None


def test_run_store_restores_and_claims_serialized_sdk_state(tmp_path, monkeypatch) -> None:
    class SerializableState:
        def to_json(self, **kwargs):
            assert kwargs["include_tracing_api_key"] is False
            return {"$schemaVersion": "test", "current_turn": 2}

    store = RunStore(pending_dir=tmp_path)
    run = store.create_run("session-1", "move after approval")
    store.set_status(run, RunStatus.AWAITING_APPROVAL)
    store.stash_pending_state(run.run_id, SerializableState(), ["call-1"])

    restored = RunStore(pending_dir=tmp_path)
    restored_run = restored.get(run.run_id)
    assert restored_run is not None
    assert restored_run.status == "awaiting_approval"

    sentinel = object()

    async def fake_from_json(initial_agent, state_json, *, context_override):
        assert state_json["current_turn"] == 2
        assert context_override == "fresh-context"
        return sentinel

    monkeypatch.setattr("jenai.state.runs.RunState.from_json", fake_from_json)
    pending = asyncio.run(
        restored.take_pending_state(
            run.run_id,
            initial_agent=Agent(name="restore", instructions="restore"),
            context="fresh-context",
        )
    )

    assert pending == (sentinel, ["call-1"])
    assert list(tmp_path.glob("*.json")) == []
