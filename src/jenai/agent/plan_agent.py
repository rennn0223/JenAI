"""/plan and /review: produce or critique a plan with no side effects."""

from __future__ import annotations

from agents import Runner

from jenai.agent.context import JenAIRunContext
from jenai.agent.runtime import build_plan_agent, build_review_agent
from jenai.agent.tracing import install_local_tracing
from jenai.schemas import PlanOutput, PlanStep, PlanStepStatus, RunRecord, RunStatus, TaskOutcome
from jenai.tools.registry import TOOL_RISK_REGISTRY


def _steps_with_approval_flags(plan_output: PlanOutput) -> list[PlanStep]:
    steps: list[PlanStep] = []
    for step in plan_output.plan_steps:
        needs_approval = step.requires_approval or any(
            TOOL_RISK_REGISTRY[tool_name].needs_approval
            for tool_name in step.candidate_tools
            if tool_name in TOOL_RISK_REGISTRY
        )
        # A planner has not executed anything. Model-authored status values
        # are therefore untrusted; only the runtime may advance a plan step.
        steps.append(
            step.model_copy(
                update={"requires_approval": needs_approval, "status": PlanStepStatus.PENDING}
            )
        )
    return steps


async def run_plan(ctx: JenAIRunContext, task: str) -> RunRecord:
    # Replace the SDK's hosted exporter before the first Runner call.  Planning
    # may be the first Agent SDK operation in a process, before /run installs it.
    install_local_tracing()
    run, run_store = ctx.run, ctx.run_store

    run_store.set_status(run, RunStatus.UNDERSTANDING)
    run_store.set_status(run, RunStatus.PLANNING)

    agent = build_plan_agent(ctx.config)
    result = await Runner.run(agent, task, context=ctx)
    plan_output = result.final_output_as(PlanOutput)

    run_store.add_plan_steps(run, _steps_with_approval_flags(plan_output))
    run.task_summary = plan_output.task_summary
    run_store.finish(
        run,
        status=RunStatus.COMPLETED,
        outcome=TaskOutcome.SUCCEEDED,
        final_output=plan_output.expected_output,
    )
    return run


async def review_plan(ctx: JenAIRunContext, task: str) -> RunRecord:
    """Re-plan the current task, asking the model to critique/revise the existing plan."""
    install_local_tracing()
    run, run_store = ctx.run, ctx.run_store

    run_store.set_status(run, RunStatus.PLANNING)
    existing_steps = "\n".join(
        f"- {step.title}: {step.description} (reason: {step.reason})" for step in run.plan_steps
    )
    prompt = (
        f"Task: {task}\n\nExisting plan:\n{existing_steps or '(no plan yet)'}\n\n"
        "Critique this plan and produce a revised, improved plan."
    )

    agent = build_review_agent(ctx.config)
    result = await Runner.run(agent, prompt, context=ctx)
    plan_output = result.final_output_as(PlanOutput)

    run_store.add_plan_steps(run, _steps_with_approval_flags(plan_output))
    run.task_summary = plan_output.task_summary
    run_store.finish(
        run,
        status=RunStatus.COMPLETED,
        outcome=TaskOutcome.SUCCEEDED,
        final_output=plan_output.expected_output,
    )
    return run
