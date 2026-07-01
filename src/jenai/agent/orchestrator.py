from __future__ import annotations

import json
from typing import Any

from agents import (
    Agent,
    InputGuardrailTripwireTriggered,
    MaxTurnsExceeded,
    ModelBehaviorError,
    RunConfig,
    Runner,
    ToolTimeoutError,
)

from jenai.agent.context import JenAIRunContext
from jenai.agent.session import JenAIFileSession
from jenai.agent.tracing import install_local_tracing
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

# Cap agent turns so a weak model that loops (e.g. re-issuing a drive to sustain
# motion) is stopped quickly instead of prompting for approval over and over.
_MAX_TURNS = 6

# Hard cap on approval prompts per /run. A weak local model (and some SDK/model
# combos that omit tool call_ids) can loop, re-raising an approval every cycle.
# Capping the total interruptions a run may raise guarantees the user is never
# asked to approve endlessly, regardless of model or SDK behaviour.
_MAX_APPROVALS_PER_RUN = 1


async def start_run(
    agent: Agent[JenAIRunContext], ctx: JenAIRunContext, task_input: str
) -> RunRecord:
    install_local_tracing()  # observability: log SDK traces to a local JSONL
    run, run_store = ctx.run, ctx.run_store
    run_store.set_status(run, RunStatus.UNDERSTANDING)
    run_store.set_status(run, RunStatus.RUNNING)

    try:
        # `session` gives cross-run memory (see JenAIFileSession); `run_config`
        # names the SDK trace for observability. Session is used only on the
        # initial run — resume replays the paused RunState, which already carries
        # this run's conversation.
        result = await Runner.run(
            agent,
            task_input,
            context=ctx,
            max_turns=_MAX_TURNS,
            session=JenAIFileSession(run.session_id),
            run_config=RunConfig(workflow_name="JenAI /run"),
        )
    except Exception as exc:  # includes provider/API errors, not just _RUN_ERRORS
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

    `decisions` is keyed by each interruption's unique `ApprovalRequest.tool_call_id`.
    The paused state's interruptions are position-aligned with the id list stashed
    at pause time (the SDK often supplies no call_id, so a per-turn index would
    collide across resume cycles and leave stale approvals pending).
    """
    run, run_store = ctx.run, ctx.run_store
    state = run_store.pop_pending_state(run.run_id)
    approval_ids = run_store.pop_pending_approval_ids(run.run_id)
    if state is None:
        raise ValueError(f"No pending approval state for run {run.run_id}")

    for index, item in enumerate(state.get_interruptions()):
        call_id = approval_ids[index] if index < len(approval_ids) else (item.call_id or "")
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
        run_store.resolve_interruption(run, call_id, status)

    run_store.set_status(run, RunStatus.RUNNING)
    try:
        result = await Runner.run(
            agent,
            state,
            context=ctx,
            max_turns=_MAX_TURNS,
            run_config=RunConfig(workflow_name="JenAI /run (resume)"),
        )
    except Exception as exc:  # includes provider/API errors, not just _RUN_ERRORS
        run_store.finish(run, status=RunStatus.FAILED, error=_error_from_exc(exc))
        return run

    return _process_result(ctx, result)


def _process_result(ctx: JenAIRunContext, result: Any) -> RunRecord:
    run, run_store = ctx.run, ctx.run_store
    state = result.to_state()
    interruptions = state.get_interruptions()

    if interruptions and len(run.interruptions) >= _MAX_APPROVALS_PER_RUN:
        # Already asked once this run; a re-raised/looped approval would spam the
        # user. Stop here instead of prompting again. Issue another /run for a
        # further action.
        run_store.finish(
            run,
            status=RunStatus.COMPLETED,
            final_output=_final_text(result)
            or (
                "Stopped after one approval. If the robot did not move, run the action "
                "directly, e.g. /ros drive /cmd_vel '{\"linear\": {\"x\": 0.2}}' 1"
            ),
        )
        return run

    if interruptions:
        approval_ids: list[str] = []
        for item in interruptions:
            # Unique id per approval (SDK call_id when present, else a fresh id)
            # so resume and resolve never confuse approvals across turns.
            call_id = item.call_id or new_id("call")
            approval_ids.append(call_id)
            arguments = json.loads(item.arguments) if item.arguments else {}
            fields = format_approval(item.tool_name, arguments)
            risk_info = TOOL_RISK_REGISTRY.get(item.tool_name)
            approval = ApprovalRequest(
                run_id=run.run_id,
                tool_call_id=call_id,
                tool_name=item.tool_name,
                title=fields.title,
                summary=fields.summary,
                raw_action=fields.raw_action,
                risk_level=risk_info.risk_level if risk_info else RiskLevel.P1,
                effect_scope=risk_info.effect_scope if risk_info else EffectScope.SIM_CONTROL,
                justification=fields.justification,
            )
            run_store.add_interruption(run, approval)
        run_store.stash_pending_state(run.run_id, state, approval_ids)
        run_store.set_status(run, RunStatus.AWAITING_APPROVAL)
        return run

    run_store.finish(run, status=RunStatus.COMPLETED, final_output=_final_text(result))
    return run


def _final_text(result: Any) -> str:
    """Final output as a display string, avoiding the literal 'None'."""
    final = getattr(result, "final_output", None)
    return str(final) if final not in (None, "") else ""


def _error_from_exc(exc: Exception) -> JenAIError:
    """Classify a run failure so the UI shows an actionable message instead of a
    blanket 'tool_error' (which used to hide max-turns loops and provider faults).
    """
    if isinstance(exc, InputGuardrailTripwireTriggered):
        return JenAIError(
            error_type=ErrorType.VALIDATION_ERROR,
            message="Blocked by a safety guardrail (the request tried to bypass safety).",
            fix_suggestion="Rephrase without disabling safety or forcing unsafe motion.",
        )
    if isinstance(exc, MaxTurnsExceeded):
        return JenAIError(
            error_type=ErrorType.MODEL_ERROR,
            message="The agent kept taking actions and hit its turn limit.",
            fix_suggestion=(
                "It likely looped (e.g. re-publishing to sustain motion). Ask for a single "
                "action, or use a duration-based command like 'drive forward for 1 second'."
            ),
        )
    if isinstance(exc, ToolTimeoutError):
        return JenAIError(error_type=ErrorType.TOOL_ERROR, message=f"A tool timed out: {exc}")
    if isinstance(exc, ModelBehaviorError):
        return JenAIError(error_type=ErrorType.MODEL_ERROR, message=str(exc))
    module = type(exc).__module__.split(".")[0]
    if module == "openai":  # provider / API failures
        return JenAIError(
            error_type=ErrorType.MODEL_ERROR,
            message=f"Provider request failed: {exc}",
            fix_suggestion="Check the model/base URL and that Ollama is running (JenAI doctor).",
        )
    return JenAIError(error_type=ErrorType.TOOL_ERROR, message=str(exc))
