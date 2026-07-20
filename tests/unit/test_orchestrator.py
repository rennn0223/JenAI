from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents import Agent, MaxTurnsExceeded, Runner

from jenai.agent import orchestrator
from jenai.agent.context import JenAIRunContext
from jenai.config.store import build_minimal_config
from jenai.schemas import EffectScope, RiskLevel, RunRecord, ToolCallCategory, ToolCallRecord
from jenai.state.runs import RunStore
from jenai.state.session import create_session
from jenai.tools.registry import TOOL_RISK_REGISTRY, ToolRiskInfo


class _FakeApprovalItem:
    def __init__(self, tool_name: str, call_id: str, arguments: dict | None) -> None:
        self.tool_name = tool_name
        self.call_id = call_id
        self.arguments = json.dumps(arguments) if arguments is not None else None


class _FakeState:
    def __init__(self, interruptions: list[_FakeApprovalItem]) -> None:
        self._interruptions = interruptions
        self.approved: list[str] = []
        self.rejected: list[tuple[str, str | None]] = []

    def get_interruptions(self) -> list[_FakeApprovalItem]:
        return self._interruptions

    def approve(self, item: _FakeApprovalItem, always_approve: bool = False) -> None:
        self.approved.append(item.call_id)

    def reject(
        self, item: _FakeApprovalItem, always_reject: bool = False, *, rejection_message=None
    ) -> None:
        self.rejected.append((item.call_id, rejection_message))


class _FakeResult:
    def __init__(self, state: _FakeState, final_output: str = "", last_agent=None) -> None:
        self._state = state
        self.final_output = final_output
        self.last_agent = last_agent

    def to_state(self) -> _FakeState:
        return self._state


def _ctx(monkeypatch) -> JenAIRunContext:
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="gpt-test",
        api_key_env="JENAI_TEST_KEY",
    )
    monkeypatch.setenv("JENAI_TEST_KEY", "secret")
    run_store = RunStore()
    run = run_store.create_run("session-1", "publish forward velocity")
    session = create_session(config, working_directory="/tmp")
    return JenAIRunContext(
        config=config,
        config_path=Path("/tmp/config.toml"),
        session=session,
        run=run,
        run_store=run_store,
    )


def _agent() -> Agent:
    return Agent(name="test-agent", instructions="test", tools=[])


def test_tool_result_summary_falls_back_to_recorded_outcomes(monkeypatch) -> None:
    ctx = _ctx(monkeypatch)
    ctx.run.tool_calls.append(
        ToolCallRecord(
            tool_name="ros_schema_tool",
            category=ToolCallCategory.ROS2,
            input_summary="schema for /cmd_vel",
            output_summary="geometry_msgs/msg/Twist",
        )
    )
    summary = orchestrator._tool_result_summary(ctx.run)
    assert "ros_schema_tool" in summary
    assert "geometry_msgs/msg/Twist" in summary


def test_status_only_run_uses_deterministic_measured_report() -> None:
    run = RunRecord(
        session_id="session-1",
        user_input="幫我檢查現在機器人的位置、雷射掃描與 Nav2 狀態",
        tool_calls=[
            ToolCallRecord(
                tool_name="ros_state_tool",
                category=ToolCallCategory.ROS2,
                input_summary="read robot state",
                status="succeeded",
                raw_output={
                    "pose_summary": {
                        "frame_id": "map",
                        "x": -5.856,
                        "y": -1.298,
                        "yaw_rad": 1.86,
                    },
                    "scan_summary": {
                        "field_of_view_deg": 180.0,
                        "range_min": 0.05,
                        "range_max": 100.0,
                        "expected_sample_count": 362,
                        "observed_sample_count": 128,
                        "ranges_truncated": True,
                        "observed_finite_sample_count": 61,
                        "nearest_observed_valid_range_m": 19.81,
                    },
                    "availability": {"pose": True, "odom": False, "scan": True},
                    "nav2": {
                        "ready": True,
                        "checks": {"map": True, "laser": True},
                        "activity": "NOT_MEASURED",
                    },
                },
            )
        ],
    )

    report = orchestrator._deterministic_state_report(run)

    assert "x=-5.856" in report
    assert "總視角=180.00°" in report
    assert "量測範圍=0.05–100.00 m" in report
    assert "預期樣本=362" in report
    assert "CLI 顯示=128（序列已截斷）" in report
    assert "最近已顯示有效回傳=19.81 m" in report
    assert "不能判定目前無任務" in report
    assert "未送出任何移動指令" in report


def test_ros_developer_cannot_complete_after_unverified_actuation(monkeypatch) -> None:
    ctx = _ctx(monkeypatch)
    ctx.run.tool_calls.append(
        ToolCallRecord(
            tool_name="ros_drive_execute_tool",
            category=ToolCallCategory.ROS2,
            input_summary="bounded drive",
            output_summary="drove and stopped",
        )
    )
    result = _FakeResult(
        _FakeState([]),
        final_output="done",
        last_agent=SimpleNamespace(name="ROS Developer"),
    )
    processed = orchestrator._process_result(ctx, result)
    assert processed.status == "blocked"
    assert "Unverified" in processed.final_output


def test_start_run_with_interruption_sets_awaiting_approval(monkeypatch) -> None:
    TOOL_RISK_REGISTRY["__test_only_pub_tool__"] = ToolRiskInfo(
        risk_level=RiskLevel.P1,
        effect_scope=EffectScope.SIM_CONTROL,
        needs_approval=True,
        description="publish",
    )
    try:
        item = _FakeApprovalItem("__test_only_pub_tool__", "call_1", {"topic": "/cmd_vel"})
        state = _FakeState([item])

        async def fake_run(agent, task_input, *, context=None, **kwargs):
            return _FakeResult(state)

        monkeypatch.setattr(Runner, "run", fake_run)

        ctx = _ctx(monkeypatch)
        result = asyncio.run(orchestrator.start_run(_agent(), ctx, "publish forward velocity"))

        assert result.status == "awaiting_approval"
        assert len(result.interruptions) == 1
        assert result.interruptions[0].tool_call_id == "call_1"
        assert result.interruptions[0].risk_level == "p1"
        assert ctx.run_store.pop_pending_state(result.run_id) is state
    finally:
        TOOL_RISK_REGISTRY.pop("__test_only_pub_tool__", None)


def test_resume_with_approval_completes_run(monkeypatch) -> None:
    item = _FakeApprovalItem("some_tool", "call_1", {})
    first_state = _FakeState([item])
    second_state = _FakeState([])

    calls = []

    async def fake_run(agent, task_input, *, context=None, **kwargs):
        calls.append(task_input)
        if len(calls) == 1:
            return _FakeResult(first_state)
        return _FakeResult(second_state, final_output="published successfully")

    monkeypatch.setattr(Runner, "run", fake_run)

    ctx = _ctx(monkeypatch)
    asyncio.run(orchestrator.start_run(_agent(), ctx, "publish forward velocity"))
    assert ctx.run.status == "awaiting_approval"

    result = asyncio.run(orchestrator.resume_with_approvals(_agent(), ctx, {"call_1": True}))

    assert first_state.approved == ["call_1"]
    assert result.status == "completed"
    assert result.final_output == "published successfully"
    assert result.interruptions[0].status == "approved"


def test_resume_with_rejection_feeds_rejection_message(monkeypatch) -> None:
    item = _FakeApprovalItem("some_tool", "call_1", {})
    first_state = _FakeState([item])
    second_state = _FakeState([])

    calls = []

    async def fake_run(agent, task_input, *, context=None, **kwargs):
        calls.append(task_input)
        if len(calls) == 1:
            return _FakeResult(first_state)
        return _FakeResult(second_state, final_output="Could not complete: user rejected.")

    monkeypatch.setattr(Runner, "run", fake_run)

    ctx = _ctx(monkeypatch)
    asyncio.run(orchestrator.start_run(_agent(), ctx, "publish forward velocity"))

    result = asyncio.run(orchestrator.resume_with_approvals(_agent(), ctx, {"call_1": False}))

    assert first_state.rejected == [("call_1", "The user rejected this action.")]
    assert result.status == "completed"
    assert "rejected" in result.final_output
    assert result.interruptions[0].status == "rejected"


def test_resume_stops_blocked_when_model_loops_same_action(monkeypatch) -> None:
    # After approving an action, the model re-raises the SAME action (a loop):
    # the run must stop honestly as BLOCKED, not re-prompt or fake COMPLETED.
    first = _FakeState([_FakeApprovalItem("some_tool", "call_1", {"topic": "/cmd_vel"})])
    looped = _FakeState([_FakeApprovalItem("some_tool", "call_2", {"topic": "/cmd_vel"})])
    calls: list[str] = []

    async def fake_run(agent, task_input, *, context=None, **kwargs):
        calls.append(task_input)
        return _FakeResult(first) if len(calls) == 1 else _FakeResult(looped)

    monkeypatch.setattr(Runner, "run", fake_run)
    ctx = _ctx(monkeypatch)
    asyncio.run(orchestrator.start_run(_agent(), ctx, "drive forward"))
    result = asyncio.run(orchestrator.resume_with_approvals(_agent(), ctx, {"call_1": True}))

    assert result.status == "blocked"


def test_resume_asks_again_for_a_genuinely_new_action(monkeypatch) -> None:
    # A distinct second action (different args) is legitimate multi-step work and
    # must still prompt for approval rather than be silently truncated.
    first = _FakeState([_FakeApprovalItem("some_tool", "call_1", {"topic": "/cmd_vel"})])
    different = _FakeState([_FakeApprovalItem("some_tool", "call_2", {"topic": "/arm"})])
    calls: list[str] = []

    async def fake_run(agent, task_input, *, context=None, **kwargs):
        calls.append(task_input)
        return _FakeResult(first) if len(calls) == 1 else _FakeResult(different)

    monkeypatch.setattr(Runner, "run", fake_run)
    ctx = _ctx(monkeypatch)
    asyncio.run(orchestrator.start_run(_agent(), ctx, "drive then move arm"))
    result = asyncio.run(orchestrator.resume_with_approvals(_agent(), ctx, {"call_1": True}))

    assert result.status == "awaiting_approval"


def test_start_run_handles_max_turns_exceeded(monkeypatch) -> None:
    recorded: list[dict] = []

    class FakeSession:
        def __init__(self, session_id):
            pass

        async def add_items(self, items):
            recorded.extend(items)

        async def get_items(self, limit=None):
            return [{"role": "user", "content": "do something"}]

    async def fake_run(agent, task_input, *, context=None, **kwargs):
        assert kwargs["max_turns"] == 12
        raise MaxTurnsExceeded("too many turns")

    monkeypatch.setattr(Runner, "run", fake_run)

    monkeypatch.setattr(orchestrator, "JenAIFileSession", FakeSession)
    ctx = _ctx(monkeypatch)
    result = asyncio.run(orchestrator.start_run(_agent(), ctx, "do something"))

    assert result.status == "failed"
    assert result.error is not None
    # Max-turns loops are classified as model_error with an actionable hint,
    # not a blanket tool_error.
    assert result.error.error_type == "model_error"
    assert "turn limit" in result.error.message

    assert recorded == [{"role": "assistant", "content": orchestrator._FAILED_TURN_MEMORY}]
    assert "failed before completion" in recorded[0]["content"]


def test_resume_without_pending_state_raises(monkeypatch) -> None:
    ctx = _ctx(monkeypatch)
    with pytest.raises(ValueError):
        asyncio.run(orchestrator.resume_with_approvals(_agent(), ctx, {}))
