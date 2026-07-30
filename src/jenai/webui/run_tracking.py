"""Record WebUI confirmations in the shared run lifecycle."""

from __future__ import annotations

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


def finish_confirmation(
    store: RunStore,
    run: RunRecord,
    tool_call_id: str,
    *,
    succeeded: bool,
) -> None:
    """Finalize both tool and run from the confirmed action response."""
    summary = "WebUI 動作已完成。" if succeeded else "WebUI 動作失敗；請查看互動紀錄。"
    store.update_tool_call(
        run,
        tool_call_id,
        status=ToolCallStatus.SUCCEEDED if succeeded else ToolCallStatus.FAILED,
        ended_at=utc_now(),
        output_summary=summary,
    )
    store.finish(
        run,
        status=RunStatus.COMPLETED if succeeded else RunStatus.FAILED,
        outcome=TaskOutcome.SUCCEEDED if succeeded else TaskOutcome.FAILED,
        final_output=summary,
    )
