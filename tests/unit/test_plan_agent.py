from __future__ import annotations

import asyncio
from pathlib import Path

from agents import Runner

from jenai.agent.context import JenAIRunContext
from jenai.agent.plan_agent import review_plan, run_plan
from jenai.config.store import build_minimal_config
from jenai.schemas import EffectScope, PlanOutput, PlanStep, RiskLevel
from jenai.state.runs import RunStore
from jenai.state.session import create_session
from jenai.tools.registry import TOOL_RISK_REGISTRY, ToolRiskInfo


class _FakeResult:
    def __init__(self, plan_output: PlanOutput) -> None:
        self._plan_output = plan_output

    def final_output_as(self, output_type: type) -> PlanOutput:
        assert output_type is PlanOutput
        return self._plan_output


def _ctx(monkeypatch) -> JenAIRunContext:
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="gpt-test",
        api_key_env="JENAI_TEST_KEY",
    )
    monkeypatch.setenv("JENAI_TEST_KEY", "secret")
    run_store = RunStore()
    run = run_store.create_run("session-1", "patrol area A")
    session = create_session(config, working_directory="/tmp")
    return JenAIRunContext(
        config=config,
        config_path=Path("/tmp/config.toml"),
        session=session,
        run=run,
        run_store=run_store,
    )


def test_run_plan_produces_completed_run_with_tool_less_agent(monkeypatch) -> None:
    captured_agents = []

    async def fake_run(agent, task_input, *, context=None, **kwargs):
        captured_agents.append(agent)
        plan_output = PlanOutput(
            task_summary="Patrol area A",
            plan_steps=[
                PlanStep(
                    title="Move to A",
                    description="drive there",
                    reason="task asked",
                    status="done",
                )
            ],
            expected_output="Patrol complete report",
        )
        return _FakeResult(plan_output)

    monkeypatch.setattr(Runner, "run", fake_run)

    ctx = _ctx(monkeypatch)
    result = asyncio.run(run_plan(ctx, "patrol area A"))

    assert result.status == "completed"
    assert result.task_summary == "Patrol area A"
    assert result.final_output == "Patrol complete report"
    assert len(result.plan_steps) == 1
    assert result.plan_steps[0].status == "pending"
    assert captured_agents[0].tools == []


def test_run_plan_flags_steps_that_use_approval_gated_tools(monkeypatch) -> None:
    TOOL_RISK_REGISTRY["__test_only_pub_tool__"] = ToolRiskInfo(
        risk_level=RiskLevel.P1,
        effect_scope=EffectScope.SIM_CONTROL,
        needs_approval=True,
        description="publish to a ROS2 topic",
    )
    try:

        async def fake_run(agent, task_input, *, context=None, **kwargs):
            plan_output = PlanOutput(
                task_summary="Move forward",
                plan_steps=[
                    PlanStep(
                        title="Publish velocity",
                        description="send cmd_vel",
                        reason="requested",
                        candidate_tools=["__test_only_pub_tool__"],
                    )
                ],
                expected_output="Moved",
            )
            return _FakeResult(plan_output)

        monkeypatch.setattr(Runner, "run", fake_run)

        ctx = _ctx(monkeypatch)
        result = asyncio.run(run_plan(ctx, "move forward"))

        assert result.plan_steps[0].requires_approval is True
    finally:
        TOOL_RISK_REGISTRY.pop("__test_only_pub_tool__", None)


def test_review_plan_replaces_plan_steps(monkeypatch) -> None:
    async def fake_run(agent, task_input, *, context=None, **kwargs):
        plan_output = PlanOutput(
            task_summary="Revised plan",
            plan_steps=[PlanStep(title="Better step", description="...", reason="...")],
            expected_output="revised",
        )
        return _FakeResult(plan_output)

    monkeypatch.setattr(Runner, "run", fake_run)

    ctx = _ctx(monkeypatch)
    ctx.run.plan_steps = [PlanStep(title="Old step", description="...", reason="...")]

    result = asyncio.run(review_plan(ctx, "patrol area A"))

    assert result.task_summary == "Revised plan"
    assert len(result.plan_steps) == 1
    assert result.plan_steps[0].title == "Better step"
    assert result.status == "completed"
    assert result.final_output == "revised"


def test_plan_and_review_install_local_tracing_before_runner(monkeypatch) -> None:
    events: list[str] = []

    def install() -> None:
        events.append("local-tracing")

    async def fake_run(agent, task_input, *, context=None, **kwargs):
        assert events == ["local-tracing"]
        events.append("runner")
        return _FakeResult(
            PlanOutput(
                task_summary="Safe plan",
                plan_steps=[
                    PlanStep(
                        title="Check readiness",
                        description="Verify the robot is ready",
                        reason="Keep the tracing-order test schema-valid",
                    )
                ],
                expected_output="done",
            )
        )

    monkeypatch.setattr("jenai.agent.plan_agent.install_local_tracing", install)
    monkeypatch.setattr(Runner, "run", fake_run)

    asyncio.run(run_plan(_ctx(monkeypatch), "plan"))
    assert events == ["local-tracing", "runner"]

    events.clear()
    asyncio.run(review_plan(_ctx(monkeypatch), "review"))
    assert events == ["local-tracing", "runner"]
