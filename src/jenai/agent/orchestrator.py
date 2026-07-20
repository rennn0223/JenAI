"""/run supervisor loop: plan → tool calls → approval pauses → report."""

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
from jenai.providers.agent_model import ModelGenerationTimeoutError
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

# A discover → validate → execute → verify workflow commonly needs more than six
# turns once an approval pause is included. Duplicate side-effect requests are
# blocked separately below, so twelve turns permit the observation loop without
# weakening the actuation boundary.
_MAX_TURNS = 12
_RAW_ACTUATION_TOOLS = {"ros_drive_execute_tool", "ros_pub_execute_tool"}
_POST_ACTION_OBSERVATION_TOOLS = {"ros_echo_tool", "ros_state_tool"}
_FAILED_TURN_MEMORY = (
    "The previous JenAI run failed before completion. Do not assume any unreported action "
    "succeeded; use only recorded tool results."
)


async def _append_failed_turn_memory(session_id: str) -> None:
    """Close a failed session turn so the next request has coherent history."""

    try:
        session = JenAIFileSession(session_id)
        tail = await session.get_items(limit=1)
        if not tail or tail[-1].get("role") == "assistant":
            return
        await session.add_items([{"role": "assistant", "content": _FAILED_TURN_MEMORY}])
    except Exception:
        # Conversation memory is best-effort; never hide the original run error.
        pass


async def start_run(
    agent: Agent[JenAIRunContext], ctx: JenAIRunContext, task_input: str
) -> RunRecord:
    install_local_tracing()  # observability: log SDK traces to a local JSONL
    run, run_store = ctx.run, ctx.run_store
    run_store.set_status(run, RunStatus.UNDERSTANDING)
    run_store.set_status(run, RunStatus.RUNNING)

    try:
        # `session` gives cross-run memory (see JenAIFileSession); `run_config`
        # names the SDK trace for observability. The same session id is passed on
        # resume too, so an approved action's result is persisted like any other.
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
        await _append_failed_turn_memory(run.session_id)
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
    pending = await run_store.take_pending_state(run.run_id, initial_agent=agent, context=ctx)
    if pending is None:
        raise ValueError(f"No pending approval state for run {run.run_id}")
    state, approval_ids = pending

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
        # Deliberately NO `context=` here: approvals live on the state's own
        # context wrapper (state.approve → wrapper._approvals), and the SDK's
        # resolve_resumed_context REPLACES run_state._context with a fresh,
        # empty wrapper whenever a context is passed — wiping every approval
        # just recorded, so the tool re-interrupts instead of executing. The
        # state already carries ctx: in-memory it was captured at pause; after
        # a restart take_pending_state injects it via context_override.
        result = await Runner.run(
            agent,
            state,
            max_turns=_MAX_TURNS,
            session=JenAIFileSession(run.session_id),
            run_config=RunConfig(workflow_name="JenAI /run (resume)"),
        )
    except Exception as exc:  # includes provider/API errors, not just _RUN_ERRORS
        run_store.finish(run, status=RunStatus.FAILED, error=_error_from_exc(exc))
        await _append_failed_turn_memory(run.session_id)
        return run

    return _process_result(ctx, result)


def _process_result(ctx: JenAIRunContext, result: Any) -> RunRecord:
    run, run_store = ctx.run, ctx.run_store
    state = result.to_state()
    interruptions = state.get_interruptions()

    if not interruptions:
        if _ros_developer_actuation_is_unverified(result, run):
            run_store.finish(
                run,
                status=RunStatus.BLOCKED,
                final_output=(
                    "Unverified: ROS Developer executed one bounded action but did not "
                    "record any post-action echo/state observation. The action will not "
                    "be repeated; inspect feedback or refer to the operator."
                ),
            )
            return run
        final_output = (
            _deterministic_state_report(run) or _final_text(result) or _tool_result_summary(run)
        )
        run_store.finish(run, status=RunStatus.COMPLETED, final_output=final_output)
        return run

    # Build the approval request for each raised interruption. A weak model (or
    # some SDK/model combos) can loop, re-raising the SAME action every turn; we
    # detect that by comparing each action against the ones already surfaced this
    # run (tool + rendered command). A genuinely new action is still allowed
    # through — only a round where EVERY action repeats one already approved is
    # treated as a loop and stopped honestly (BLOCKED, never a fake COMPLETED).
    seen = {(ir.tool_name, ir.raw_action) for ir in run.interruptions}
    all_repeats = bool(seen)
    approval_ids: list[str] = []
    requests: list[ApprovalRequest] = []
    for item in interruptions:
        # Unique id per approval (SDK call_id when present, else a fresh id)
        # so resume and resolve never confuse approvals across turns.
        call_id = item.call_id or new_id("call")
        approval_ids.append(call_id)
        arguments = json.loads(item.arguments) if item.arguments else {}
        fields = format_approval(item.tool_name, arguments)
        if (item.tool_name, fields.raw_action) not in seen:
            all_repeats = False
        risk_info = TOOL_RISK_REGISTRY.get(item.tool_name)
        requests.append(
            ApprovalRequest(
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
        )

    if all_repeats:
        run_store.finish(
            run,
            status=RunStatus.BLOCKED,
            final_output=_final_text(result)
            or (
                "Stopped: the agent kept re-requesting an action it had already asked to "
                "approve this run (a model loop). Start a new /run for the next step, or run "
                'the action directly, e.g. /ros drive /cmd_vel \'{"linear": {"x": 0.2}}\' 1'
            ),
        )
        return run

    for approval in requests:
        run_store.add_interruption(run, approval)
    run_store.set_status(run, RunStatus.AWAITING_APPROVAL)
    run_store.stash_pending_state(run.run_id, state, approval_ids)
    return run


def _final_text(result: Any) -> str:
    """Final output as a display string, avoiding the literal 'None'."""
    final = getattr(result, "final_output", None)
    return str(final) if final not in (None, "") else ""


def _tool_result_summary(run: RunRecord) -> str:
    """Deterministic honest fallback when a model ends after its tool calls.

    Some local OpenAI-compatible models return a terminal tool-call turn with
    no assistant text. A completed run must not look like nothing happened;
    summarize only recorded outcomes and never invent a success claim.
    """
    if not run.tool_calls:
        return "Completed without a textual result or recorded tool call."
    parts = []
    for call in run.tool_calls:
        outcome = call.output_summary or (call.error.message if call.error else call.status)
        parts.append(f"{call.tool_name}: {outcome}")
    return "Recorded tool results — " + "; ".join(parts)


_STATE_SUBJECTS = ("state", "status", "position", "pose", "scan", "nav2", "位置", "雷射", "狀態")
_STATE_INSPECTION = ("check", "inspect", "show", "current", "檢查", "查看", "現在", "目前")
_STATE_DECISION = ("should", "recommend", "是否應該", "建議", "該不該")


def _deterministic_state_report(run: RunRecord) -> str:
    """Render factual status-only observations without letting an LLM alter measurements."""

    calls = [call for call in run.tool_calls if call.tool_name == "ros_state_tool"]
    if len(run.tool_calls) != 1 or len(calls) != 1 or calls[0].raw_output is None:
        return ""
    text = run.user_input.lower()
    if not any(term in text for term in _STATE_SUBJECTS):
        return ""
    if not any(term in text for term in _STATE_INSPECTION):
        return ""
    if any(term in text for term in _STATE_DECISION):
        return ""

    payload = calls[0].raw_output
    pose = payload.get("pose_summary") or {}
    scan = payload.get("scan_summary") or {}
    nav2 = payload.get("nav2") or {}
    checks = nav2.get("checks") or {}
    availability = payload.get("availability") or {}
    zh = any("\u4e00" <= char <= "\u9fff" for char in run.user_input)

    def _number(value: object, digits: int = 2) -> str:
        return f"{value:.{digits}f}" if isinstance(value, (int, float)) else "not measured"

    check_text = (
        ", ".join(f"{name}={'PASS' if ok else 'FAIL'}" for name, ok in checks.items())
        or "not measured"
    )
    if zh:
        scan_count = (
            f"預期樣本={scan.get('expected_sample_count', 'not measured')}，"
            f"CLI 顯示={scan.get('observed_sample_count', 'not measured')}"
        )
        if scan.get("ranges_truncated"):
            scan_count += "（序列已截斷）"
        return (
            "即時機器人狀態（由工具量測值確定性產生）\n"
            f"- 位置（{pose.get('frame_id') or 'map'}）："
            f"x={_number(pose.get('x'), 3)} m，y={_number(pose.get('y'), 3)} m，"
            f"yaw={_number(pose.get('yaw_rad'), 3)} rad\n"
            f"- 雷射：總視角={_number(scan.get('field_of_view_deg'))}°，"
            f"量測範圍={_number(scan.get('range_min'))}–{_number(scan.get('range_max'))} m，"
            f"{scan_count}，"
            f"已顯示有限回傳={scan.get('observed_finite_sample_count', 'not measured')}，"
            f"最近已顯示有效回傳={_number(scan.get('nearest_observed_valid_range_m'))} m\n"
            f"- Nav2：{'READY' if nav2.get('ready') else 'NOT READY'}；{check_text}\n"
            f"- Odom：{'有快照' if availability.get('odom') else '本次未取得快照'}\n"
            "- Nav2 任務活動：本工具未量測，不能判定目前無任務、閒置、停止或移動中。\n"
            "- 本次查詢未送出任何移動指令。"
        )
    return (
        "Live robot status (deterministic tool report)\n"
        f"- Position ({pose.get('frame_id') or 'map'}): "
        f"x={_number(pose.get('x'), 3)} m, y={_number(pose.get('y'), 3)} m, "
        f"yaw={_number(pose.get('yaw_rad'), 3)} rad\n"
        f"- Laser: total FOV={_number(scan.get('field_of_view_deg'))} deg, "
        f"measurement range={_number(scan.get('range_min'))}–"
        f"{_number(scan.get('range_max'))} m, expected samples="
        f"{scan.get('expected_sample_count', 'not measured')}, CLI-displayed samples="
        f"{scan.get('observed_sample_count', 'not measured')}"
        f"{' (truncated)' if scan.get('ranges_truncated') else ''}, displayed finite returns="
        f"{scan.get('observed_finite_sample_count', 'not measured')}, nearest displayed valid "
        f"return={_number(scan.get('nearest_observed_valid_range_m'))} m\n"
        f"- Nav2: {'READY' if nav2.get('ready') else 'NOT READY'}; {check_text}\n"
        f"- Odom: {'snapshot available' if availability.get('odom') else 'not captured'}\n"
        "- Nav2 task activity was not measured; no idle, stopped, moving, or no-goal "
        "conclusion is available.\n"
        "- This query issued no motion command."
    )


def _ros_developer_actuation_is_unverified(result: Any, run: RunRecord) -> bool:
    """Prevent a ROS Developer run from completing after unverified raw motion.

    The normal Motion specialist remains suitable for an explicitly requested
    one-shot drive. The combined ROS Developer workflow, however, promises a
    discover → execute → verify loop and must fail closed when a weak model ends
    immediately after the actuation tool result.
    """
    last_agent = getattr(result, "last_agent", None)
    if getattr(last_agent, "name", "") != "ROS Developer":
        return False
    names = [call.tool_name for call in run.tool_calls]
    actuation_indexes = [index for index, name in enumerate(names) if name in _RAW_ACTUATION_TOOLS]
    if not actuation_indexes:
        return False
    last_actuation = actuation_indexes[-1]
    return not any(name in _POST_ACTION_OBSERVATION_TOOLS for name in names[last_actuation + 1 :])


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
                "The workflow may be too broad or the model may be looping. Split unrelated "
                "work into separate runs; movement should remain a single duration-based action."
            ),
        )
    if isinstance(exc, ToolTimeoutError):
        return JenAIError(error_type=ErrorType.TOOL_ERROR, message=f"A tool timed out: {exc}")
    if isinstance(exc, ModelGenerationTimeoutError):
        return JenAIError(
            error_type=ErrorType.MODEL_ERROR,
            message=str(exc),
            fix_suggestion=(
                "Try again with a smaller local model, shorten the request, or check whether "
                "Ollama is overloaded. No unreported robot action should be assumed successful."
            ),
        )
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
