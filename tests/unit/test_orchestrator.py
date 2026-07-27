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
from jenai.tools.safety import NavigationCancelStatus


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


def test_process_result_normalizes_chinese_model_output(monkeypatch) -> None:
    ctx = _ctx(monkeypatch)
    ctx.run.user_input = "检查机器人状态"
    result = _FakeResult(_FakeState([]), final_output="机器人位于仓库区域。")

    processed = orchestrator._process_result(ctx, result)

    assert processed.final_output == "機器人位於倉庫區域。"


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

    assert "x -5.856 m · y -1.298 m" in report
    assert "視角 180.00° · 範圍 0.05–100.00 m" in report
    assert "樣本：預期 362 · CLI 顯示 128（截斷）" in report
    assert "最近已顯示有效回傳 19.81 m" in report
    assert "不能判定閒置、停止或移動中" in report
    assert "未送出任何移動指令" in report


@pytest.mark.parametrize(
    "query",
    [
        "幫我檢查現在機器人的位置，然後停止機器人",
        "Check the robot position, then stop the robot.",
        "查看 Nav2 狀態後取消導航",
    ],
)
def test_state_request_with_stop_intent_is_not_read_only(query: str) -> None:
    assert orchestrator.is_read_only_state_request(query) is False


@pytest.mark.parametrize(
    "query",
    [
        "幫我檢查現在機器人的位置，不要移動機器人",
        "Check the current robot position without moving the robot.",
    ],
)
def test_state_request_with_non_actuation_constraint_remains_read_only(query: str) -> None:
    assert orchestrator.is_read_only_state_request(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "停止機器人",
        "停止移動",
        "停止巡邏",
        "請立刻停車",
        "取消目前的導航",
        "Stop the robot.",
        "Stop moving.",
        "Cancel the active navigation goal.",
        "幫我檢查現在機器人的位置，然後停止機器人",
        "請停下來思考，然後停止機器人",
    ],
)
def test_explicit_stop_request_uses_emergency_stop_reflex(query: str) -> None:
    assert orchestrator.is_emergency_stop_request(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "不要停止機器人",
        "不要停車",
        "別停下",
        "不要急停",
        "別急停",
        "請檢查位置，但不要取消導航",
        "Check the robot without stopping it.",
        "Do not stop the robot.",
        "Do not halt the robot.",
        "Don't halt the robot.",
        "Report status without halting the robot.",
        "Don't cancel the active navigation goal.",
        "How does the emergency stop work?",
        "停止機器人是否安全？",
        "停止服務的文件",
        "請停下來思考這個問題",
        "Is it safe to stop the robot?",
        "前往 dock",
    ],
)
def test_negated_or_informational_stop_text_does_not_trigger_reflex(query: str) -> None:
    assert orchestrator.is_emergency_stop_request(query) is False


@pytest.mark.parametrize(
    "query",
    [
        "導航到停車場",
        "停車場在哪裡？",
        "請前往停車位",
        "Tell me where the robot stop is.",
    ],
)
def test_stop_nouns_do_not_trigger_emergency_stop(query: str) -> None:
    assert orchestrator.is_emergency_stop_request(query) is False


@pytest.mark.parametrize(
    "query",
    [
        "先停車，再回報機器人狀態",
        "停止機器人後檢查位置",
        "Stop the robot, then report its status.",
        "停止機器人並說明目前狀態",
        "Stop the robot and explain its current state.",
    ],
)
def test_stop_then_state_phrasings_request_post_stop_inspection(query: str) -> None:
    assert orchestrator.is_emergency_stop_request(query) is True
    assert orchestrator.requests_state_inspection(query) is True


def test_emergency_stop_run_halts_before_optional_state_snapshot(monkeypatch) -> None:
    events: list[str] = []

    async def fake_execute(config):
        events.append("halt")
        return SimpleNamespace(
            navigation_cancel_status=NavigationCancelStatus.NOT_ACTIVE,
            navigation_goal_canceled=False,
            zero_velocity_delivered=True,
            message="Robot halted (no active navigation goal, zero velocity sent).",
        )

    async def fake_inspect(wrapper) -> dict:
        events.append("inspect")
        call = ToolCallRecord(
            tool_name="ros_state_tool",
            category=ToolCallCategory.ROS2,
            input_summary="read robot state",
            status="succeeded",
            raw_output={
                "pose_summary": {"frame_id": "map", "x": 1.0, "y": 2.0, "yaw_rad": 0.0},
                "scan_summary": {},
                "availability": {},
                "nav2": {"ready": True, "checks": {}},
            },
        )
        wrapper.context.run_store.add_tool_call(wrapper.context.run, call)
        return call.raw_output or {}

    monkeypatch.setattr(orchestrator, "execute_emergency_stop", fake_execute)
    monkeypatch.setattr(orchestrator, "inspect_robot_state", fake_inspect)

    ctx = _ctx(monkeypatch)
    ctx.run.user_input = "幫我檢查現在機器人的位置，然後停止機器人"
    result = asyncio.run(orchestrator.start_emergency_stop_run(ctx))

    assert events == ["halt", "inspect"]
    assert [call.tool_name for call in result.tool_calls] == [
        "emergency_stop",
        "ros_state_tool",
    ]
    assert result.status == "completed"
    assert result.outcome == "succeeded"
    assert "停止命令回執" in (result.final_output or "")
    assert "上述狀態是在命令發布後擷取" in (result.final_output or "")


@pytest.mark.parametrize(
    ("deployment_mode", "expected_scope"),
    [
        ("simulation", "sim_control"),
        ("physical", "robot_control"),
    ],
)
def test_emergency_stop_run_records_deployment_aware_effect_scope(
    monkeypatch,
    deployment_mode: str,
    expected_scope: str,
) -> None:
    async def fake_execute(config):
        return SimpleNamespace(
            navigation_cancel_status=NavigationCancelStatus.ACKNOWLEDGED,
            navigation_goal_canceled=True,
            zero_velocity_delivered=True,
            message="Robot halted.",
        )

    monkeypatch.setattr(orchestrator, "execute_emergency_stop", fake_execute)
    ctx = _ctx(monkeypatch)
    ctx.config.deployment_mode = deployment_mode
    ctx.run.user_input = "停止機器人"

    result = asyncio.run(orchestrator.start_emergency_stop_run(ctx))

    assert str(result.tool_calls[0].effect_scope) == expected_scope


def test_emergency_stop_run_does_not_claim_observed_motion_stop_without_evidence(
    monkeypatch,
) -> None:
    async def fake_execute(config):
        return SimpleNamespace(
            navigation_cancel_status=NavigationCancelStatus.ACKNOWLEDGED,
            navigation_goal_canceled=True,
            zero_velocity_delivered=True,
            message=(
                "Zero-velocity command published; navigation cancellation acknowledged. "
                "Motion stop was not independently observed."
            ),
        )

    monkeypatch.setattr(orchestrator, "execute_emergency_stop", fake_execute)
    ctx = _ctx(monkeypatch)
    ctx.run.user_input = "停止機器人"

    result = asyncio.run(orchestrator.start_emergency_stop_run(ctx))

    assert "已發布零速度命令" in (result.final_output or "")
    assert "導航取消已確認" in (result.final_output or "")
    assert "尚未由運動狀態證據確認車體停止" in (result.final_output or "")
    assert "機器人已停止" not in (result.final_output or "")
    assert "已送達" not in (result.final_output or "")
    assert result.tool_calls[0].raw_output["zero_velocity_command_published"] is True
    assert result.tool_calls[0].raw_output["motion_stop_observed"] is None


def test_emergency_stop_run_preserves_success_when_state_inspection_fails(monkeypatch) -> None:
    async def fake_execute(config):
        return SimpleNamespace(
            navigation_cancel_status=NavigationCancelStatus.NOT_ACTIVE,
            navigation_goal_canceled=False,
            zero_velocity_delivered=True,
            message="Robot halted (no active navigation goal, zero velocity sent).",
        )

    async def fake_inspect(wrapper) -> None:
        raise RuntimeError("state bridge unavailable")

    monkeypatch.setattr(orchestrator, "execute_emergency_stop", fake_execute)
    monkeypatch.setattr(orchestrator, "inspect_robot_state", fake_inspect)
    ctx = _ctx(monkeypatch)
    ctx.run.user_input = "停止機器人並回報目前狀態"

    result = asyncio.run(orchestrator.start_emergency_stop_run(ctx))

    assert result.status == "completed"
    assert result.outcome == "partial"
    assert result.tool_calls[0].status == "succeeded"
    assert result.tool_calls[0].raw_output["zero_velocity_delivered"] is True
    assert "已發布零速度命令" in (result.final_output or "")
    assert "無法取得命令發布後狀態" in (result.final_output or "")


def test_emergency_stop_run_downgrades_recorded_state_tool_failure(monkeypatch) -> None:
    async def fake_execute(config):
        return SimpleNamespace(
            navigation_cancel_status=NavigationCancelStatus.NOT_ACTIVE,
            navigation_goal_canceled=False,
            zero_velocity_delivered=True,
            message="Robot halted (no active navigation goal, zero velocity sent).",
        )

    async def fake_inspect(wrapper) -> dict:
        wrapper.context.run_store.add_tool_call(
            wrapper.context.run,
            ToolCallRecord(
                tool_name="ros_state_tool",
                category=ToolCallCategory.ROS2,
                input_summary="read robot state",
                status="failed",
                output_summary="read nothing; Nav2 not ready",
                raw_output={"nav2": {"ready": False, "checks": {}}},
            ),
        )
        return {"nav2": {"ready": False, "checks": {}}}

    monkeypatch.setattr(orchestrator, "execute_emergency_stop", fake_execute)
    monkeypatch.setattr(orchestrator, "inspect_robot_state", fake_inspect)
    ctx = _ctx(monkeypatch)
    ctx.run.user_input = "停止機器人並回報目前狀態"

    result = asyncio.run(orchestrator.start_emergency_stop_run(ctx))

    assert result.status == "completed"
    assert result.outcome == "partial"
    assert result.tool_calls[-1].status == "failed"
    assert "已發布零速度命令" in (result.final_output or "")
    assert "無法取得命令發布後狀態" in (result.final_output or "")


def test_emergency_stop_run_does_not_report_unconfirmed_cancel_as_success(monkeypatch) -> None:
    async def fake_execute(config):
        return SimpleNamespace(
            navigation_cancel_status=NavigationCancelStatus.UNCONFIRMED,
            navigation_goal_canceled=False,
            zero_velocity_delivered=True,
            message="Zero velocity delivered; navigation cancellation unconfirmed.",
        )

    monkeypatch.setattr(orchestrator, "execute_emergency_stop", fake_execute)
    ctx = _ctx(monkeypatch)
    ctx.run.user_input = "停止機器人"

    result = asyncio.run(orchestrator.start_emergency_stop_run(ctx))

    assert result.status == "completed"
    assert result.outcome == "partial"
    assert result.tool_calls[0].status == "failed"
    assert result.tool_calls[0].raw_output["navigation_cancel_status"] == "unconfirmed"
    assert result.tool_calls[0].raw_output["zero_velocity_delivered"] is True
    assert "導航取消尚未獲得確認" in (result.final_output or "")
    assert "Zero velocity" not in (result.final_output or "")


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
    assert result.outcome == "partial"


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
    assert result.status == "blocked"
    assert result.outcome == "blocked"
    assert "rejected" in result.final_output
    assert result.interruptions[0].status == "rejected"
    assert result.outcome == "blocked"


def test_resume_stops_blocked_when_model_loops_same_action(monkeypatch) -> None:
    # After approving an action, the model re-raises the SAME action (a loop):
    # the run must stop honestly as BLOCKED, not re-prompt or fake COMPLETED.
    first = _FakeState([_FakeApprovalItem("some_tool", "call_1", {"topic": "/cmd_vel"})])
    looped = _FakeState([_FakeApprovalItem("some_tool", "call_2", {"topic": "/cmd_vel"})])
    calls: list[str] = []

    async def fake_run(agent, task_input, *, context=None, **kwargs):
        calls.append(task_input)
        return (
            _FakeResult(first)
            if len(calls) == 1
            else _FakeResult(looped, final_output="机器人重复请求批准。")
        )

    monkeypatch.setattr(Runner, "run", fake_run)
    ctx = _ctx(monkeypatch)
    ctx.run.user_input = "讓機器人往前"
    asyncio.run(orchestrator.start_run(_agent(), ctx, "讓機器人往前"))
    result = asyncio.run(orchestrator.resume_with_approvals(_agent(), ctx, {"call_1": True}))

    assert result.status == "blocked"
    assert result.final_output == "機器人重複請求批准。"


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
