"""Terminal run, audit, and receipt recording for interface-level emergency stops."""

from __future__ import annotations

from dataclasses import dataclass

from jenai.config.models import AppConfig
from jenai.schemas import (
    ErrorType,
    JenAIError,
    RiskLevel,
    RunRecord,
    RunStatus,
    TaskOutcome,
    ToolCallCategory,
    ToolCallRecord,
    ToolCallStatus,
)
from jenai.state.runs import RunStore
from jenai.tools.emergency_stop import emergency_stop_effect_scope
from jenai.tools.safety import HaltReceipt, NavigationCancelStatus, halt_receipt_evidence


@dataclass(frozen=True, slots=True)
class EmergencyStopRunHandle:
    """The run and action record opened before an interface publishes a stop command."""

    run: RunRecord
    tool_call_id: str


def begin_emergency_stop_run(
    run_store: RunStore,
    config: AppConfig,
    *,
    session_id: str,
    user_input: str,
) -> EmergencyStopRunHandle:
    """Open the same recorded safety action for TUI, WebUI, and MCP surfaces."""

    run = run_store.create_run(session_id, user_input)
    run_store.set_status(run, RunStatus.UNDERSTANDING)
    run_store.set_status(run, RunStatus.RUNNING)
    call = ToolCallRecord(
        tool_name="emergency_stop",
        category=ToolCallCategory.ROS2,
        input_summary="cancel navigation and deliver zero velocity",
        status=ToolCallStatus.RUNNING,
        risk_level=RiskLevel.P0,
        effect_scope=emergency_stop_effect_scope(config),
    )
    run_store.add_tool_call(run, call)
    return EmergencyStopRunHandle(run=run, tool_call_id=call.tool_call_id)


def finish_emergency_stop_run(
    run_store: RunStore,
    handle: EmergencyStopRunHandle,
    *,
    receipt: HaltReceipt | None = None,
    error: Exception | None = None,
) -> RunRecord:
    """Finish a stop run from typed halt evidence or an unavailable error."""

    if (receipt is None) == (error is None):
        raise ValueError("provide exactly one of receipt or error")

    run = handle.run
    if error is not None:
        message = f"Emergency stop unavailable: {error}"
        structured_error = JenAIError(error_type=ErrorType.TOOL_ERROR, message=message)
        run_store.update_tool_call(
            run,
            handle.tool_call_id,
            status=ToolCallStatus.FAILED,
            output_summary=message,
            error=structured_error,
        )
        run_store.finish(
            run,
            status=RunStatus.FAILED,
            outcome=TaskOutcome.UNAVAILABLE,
            final_output=message,
            error=structured_error,
        )
        return run

    if receipt is None:
        raise ValueError("receipt is required when error is not provided")
    cancel_unconfirmed = receipt.navigation_cancel_status == NavigationCancelStatus.UNCONFIRMED
    run_store.update_tool_call(
        run,
        handle.tool_call_id,
        status=ToolCallStatus.FAILED if cancel_unconfirmed else ToolCallStatus.SUCCEEDED,
        output_summary=receipt.message,
        raw_output=halt_receipt_evidence(receipt),
    )
    run_store.finish(
        run,
        status=RunStatus.COMPLETED,
        outcome=TaskOutcome.PARTIAL if cancel_unconfirmed else TaskOutcome.SUCCEEDED,
        final_output=receipt.message,
    )
    return run
