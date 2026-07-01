from __future__ import annotations

import json
from typing import Any

from agents import Agent, MaxTurnsExceeded, ModelBehaviorError, Runner, ToolTimeoutError

from jenai.agent.context import JenAIRunContext
from jenai.schemas import (
    ApprovalRequest,
    ApprovalStatus,
    EffectScope,
    ErrorType,
    JenAIError,
    RiskLevel,
    RunRecord,
    RunStatus,
)
from jenai.schemas.models import new_id
from jenai.tools.approval_formatters import format_approval
from jenai.tools.registry import TOOL_RISK_REGISTRY

_RUN_ERRORS = (MaxTurnsExceeded, ModelBehaviorError, ToolTimeoutError)


async def start_run(
    agent: Agent[JenAIRunContext], ctx: JenAIRunContext, task_input: str
) -> RunRecord:
    run, run_store = ctx.run, ctx.run_store
    run_store.set_status(run, RunStatus.UNDERSTANDING)
    run_store.set_status(run, RunStatus.RUNNING)

    try:
        result = await Runner.run(agent, task_input, context=ctx)
    except _RUN_ERRORS as exc:
        run_store.finish(run, status=RunStatus.FAILED, error=_error_from_exc(exc))
        return run

    return _process_result(ctx, result)


async def resume_with_approvals(
    agent: Agent[JenAIRunContext],
    ctx: JenAIRunContext,
    decisions: dict[str, bool],
    *,
    rejection_message: str | None = None,
) -> RunRecord:
    """Resolve a paused run's approval interruptions and resume it.

    `decisions` is keyed by the tool call's `call_id` (the same id stored as
    `ApprovalRequest.tool_call_id`), mapping to True (approve) / False (reject).
    """
    run, run_store = ctx.run, ctx.run_store
    state = run_store.pop_pending_state(run.run_id)
    if state is None:
        raise ValueError(f"No pending approval state for run {run.run_id}")

    for item in state.get_interruptions():
        call_id = item.call_id or ""
        approved = decisions.get(call_id, False)
        if approved:
            state.approve(item)
            status = ApprovalStatus.APPROVED
        else:
            state.reject(
                item,
                rejection_message=rejection_message or "The user rejected this action.",
            )
            status = ApprovalStatus.REJECTED
        if call_id:
            run_store.resolve_interruption(run, call_id, status)

    run_store.set_status(run, RunStatus.RUNNING)
    try:
        result = await Runner.run(agent, state, context=ctx)
    except _RUN_ERRORS as exc:
        run_store.finish(run, status=RunStatus.FAILED, error=_error_from_exc(exc))
        return run

    return _process_result(ctx, result)


def _process_result(ctx: JenAIRunContext, result: Any) -> RunRecord:
    run, run_store = ctx.run, ctx.run_store
    state = result.to_state()
    interruptions = state.get_interruptions()

    if interruptions:
        run_store.stash_pending_state(run.run_id, state)
        for item in interruptions:
            arguments = json.loads(item.arguments) if item.arguments else {}
            fields = format_approval(item.tool_name, arguments)
            risk_info = TOOL_RISK_REGISTRY.get(item.tool_name)
            approval = ApprovalRequest(
                run_id=run.run_id,
                tool_call_id=item.call_id or new_id("call"),
                title=fields.title,
                summary=fields.summary,
                raw_action=fields.raw_action,
                risk_level=risk_info.risk_level if risk_info else RiskLevel.P1,
                effect_scope=risk_info.effect_scope if risk_info else EffectScope.SIM_CONTROL,
                justification=fields.justification,
            )
            run_store.add_interruption(run, approval)
        run_store.set_status(run, RunStatus.AWAITING_APPROVAL)
        return run

    run_store.finish(run, status=RunStatus.COMPLETED, final_output=str(result.final_output))
    return run


def _error_from_exc(exc: Exception) -> JenAIError:
    return JenAIError(error_type=ErrorType.TOOL_ERROR, message=str(exc))
