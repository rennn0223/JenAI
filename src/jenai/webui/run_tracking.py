"""Record WebUI confirmations in the shared run lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jenai.config.models import AppConfig
from jenai.schemas import (
    ApprovalRequest,
    ApprovalStatus,
    RiskLevel,
    RunRecord,
    RunStatus,
    TaskOutcome,
    ToolCallCategory,
    ToolCallRecord,
    ToolCallStatus,
)
from jenai.schemas.models import utc_now
from jenai.state.runs import RunStore
from jenai.task_results import TaskResult, run_status_for_outcome
from jenai.tools.emergency_stop import emergency_stop_effect_scope


def _action_identity(action: dict[str, Any]) -> tuple[str, ToolCallCategory]:
    kind = str(action.get("type") or "unknown")
    if kind == "route":
        return "navigate", ToolCallCategory.ROUTE
    if kind == "drive":
        return "ros_drive", ToolCallCategory.ROS2
    if kind == "pub":
        return "ros_pub", ToolCallCategory.ROS2
    return kind, ToolCallCategory.ROS2


def register_confirmation(
    store: RunStore,
    run: RunRecord,
    config: AppConfig,
    action: dict[str, Any],
    danger: str,
) -> str:
    """Attach one pending approval and safe tool summary to a run."""
    tool_name, category = _action_identity(action)
    effect_scope = emergency_stop_effect_scope(config)
    call = ToolCallRecord(
        tool_name=tool_name,
        category=category,
        input_summary=danger or tool_name,
        status=ToolCallStatus.AWAITING_APPROVAL,
        risk_level=RiskLevel.P1,
        effect_scope=effect_scope,
    )
    store.add_tool_call(run, call)
    store.add_interruption(
        run,
        ApprovalRequest(
            run_id=run.run_id,
            tool_call_id=call.tool_call_id,
            tool_name=tool_name,
            title=f"執行 {tool_name}",
            summary=danger or "這項動作需要操作員批准。",
            raw_action=str(action.get("type") or "unknown"),
            risk_level=RiskLevel.P1,
            effect_scope=effect_scope,
            justification="WebUI 動作必須經過一次性批准。",
        ),
    )
    store.set_status(run, RunStatus.AWAITING_APPROVAL)
    return call.tool_call_id


def start_confirmation(store: RunStore, run: RunRecord, tool_call_id: str) -> None:
    """Resolve approval and mark its tool as running."""
    store.resolve_interruption(run, tool_call_id, ApprovalStatus.APPROVED)
    store.update_tool_call(
        run,
        tool_call_id,
        status=ToolCallStatus.RUNNING,
        started_at=utc_now(),
    )
    store.set_status(run, RunStatus.RUNNING)


def reject_confirmation(
    store: RunStore,
    run: RunRecord,
    tool_call_id: str,
    *,
    summary: str = "操作員已取消；動作未執行。",
) -> None:
    """Reject a pending action and make the run terminal without executing it."""
    store.resolve_interruption(run, tool_call_id, ApprovalStatus.REJECTED)
    store.update_tool_call(
        run,
        tool_call_id,
        status=ToolCallStatus.REJECTED,
        ended_at=utc_now(),
        output_summary=summary,
    )
    store.finish(
        run,
        status=RunStatus.BLOCKED,
        outcome=TaskOutcome.BLOCKED,
        final_output=summary,
    )


_TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.BLOCKED,
        RunStatus.INTERRUPTED,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
    }
)


def web_response_task_result(response: Mapping[str, Any]) -> TaskResult:
    """Parse typed terminal metadata, failing closed when it is absent or invalid."""
    try:
        run_status = RunStatus(str(response["run_status"]))
        outcome = TaskOutcome(str(response["outcome"]))
    except (KeyError, TypeError, ValueError):
        return TaskResult(
            run_status=RunStatus.FAILED,
            outcome=TaskOutcome.FAILED,
        )
    if run_status not in _TERMINAL_RUN_STATUSES:
        return TaskResult(run_status=RunStatus.FAILED, outcome=TaskOutcome.FAILED)
    if run_status != run_status_for_outcome(outcome):
        return TaskResult(run_status=RunStatus.FAILED, outcome=TaskOutcome.FAILED)
    return TaskResult(run_status=run_status, outcome=outcome)


def finish_confirmation(
    store: RunStore,
    run: RunRecord,
    tool_call_id: str,
    *,
    result: TaskResult,
) -> None:
    """Finalize both tool and run from authoritative task metadata."""
    summaries = {
        TaskOutcome.SUCCEEDED: "WebUI 動作已完成。",
        TaskOutcome.ARRIVED_UNVERIFIED: "已抵達目標附近，但最終物理效果尚未驗證。",
        TaskOutcome.PARTIAL: "WebUI 動作只完成一部分；請查看互動紀錄。",
        TaskOutcome.ENDPOINT_MISMATCH: "導航已結束，但終點超出允許誤差。",
        TaskOutcome.BLOCKED: "WebUI 動作已被政策或前置條件阻擋。",
        TaskOutcome.UNAVAILABLE: "WebUI 動作所需能力目前不可用。",
        TaskOutcome.FAILED: "WebUI 動作失敗；請查看互動紀錄。",
        TaskOutcome.CANCELLED: "WebUI 動作已取消。",
    }
    summary = summaries[result.outcome]
    store.update_tool_call(
        run,
        tool_call_id,
        status=ToolCallStatus.SUCCEEDED if result.succeeded else ToolCallStatus.FAILED,
        ended_at=utc_now(),
        output_summary=summary,
    )
    store.finish(
        run,
        status=result.run_status,
        outcome=result.outcome,
        final_output=summary,
    )


def cancel_confirmation(
    store: RunStore,
    run: RunRecord,
    tool_call_id: str,
) -> None:
    """Make an already-approved action terminal when emergency stop interrupts it."""
    summary = "急停已中斷這項動作；請以停止回執確認機器人狀態。"
    store.update_tool_call(
        run,
        tool_call_id,
        status=ToolCallStatus.FAILED,
        ended_at=utc_now(),
        output_summary=summary,
    )
    store.finish(
        run,
        status=RunStatus.INTERRUPTED,
        outcome=TaskOutcome.CANCELLED,
        final_output=summary,
    )
