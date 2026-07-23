"""Deterministic task receipts derived from terminal run records."""

from __future__ import annotations

import json
import re
from pathlib import Path

from jenai.schemas import (
    ApprovalStatus,
    ErrorType,
    FailureCode,
    RunRecord,
    RunStatus,
    TaskActionReceipt,
    TaskOutcome,
    TaskReceipt,
    ToolCallStatus,
)
from jenai.secure_files import atomic_write_text

_TIMEOUT = re.compile(r"\b(timeout|timed out|逾時|超時)\b", re.IGNORECASE)
_SAFETY = re.compile(
    r"\b(twin gate|safety|forbidden|collision|watchdog|blocked by)\b|安全|禁區|碰撞",
    re.IGNORECASE,
)
_INTERRUPTED = re.compile(r"\b(interrupt|abort|cancel)\w*\b|中斷|取消", re.IGNORECASE)
_UNAVAILABLE = re.compile(
    r"\b(unavailable|not available|not configured|missing|no ros|no nav2)\b|不可用|未設定|缺少",
    re.IGNORECASE,
)
_BUSY = re.compile(r"\b(busy|already in progress|queue full)\b|忙碌|進行中", re.IGNORECASE)
_NAVIGATION = re.compile(
    r"\b(nav2|navigate|navigation|route|goal|amcl|localization|odom)\b|導航|定位|目標點",
    re.IGNORECASE,
)


def classify_failure(run: RunRecord) -> FailureCode | None:
    """Map detailed run state onto a stable, deliberately small taxonomy."""

    if any(item.status == ApprovalStatus.REJECTED for item in run.interruptions):
        return FailureCode.APPROVAL_REJECTED

    failed_tools = [call for call in run.tool_calls if call.status == ToolCallStatus.FAILED]
    if run.status == RunStatus.COMPLETED and run.error is None and not failed_tools:
        return None

    # Structured error types identify the failing subsystem more reliably
    # than words such as "unavailable" in a provider response.
    if run.error is not None:
        structured = {
            ErrorType.CONFIG_ERROR: FailureCode.CONFIGURATION,
            ErrorType.ENV_ERROR: FailureCode.ENVIRONMENT,
            ErrorType.VALIDATION_ERROR: FailureCode.VALIDATION,
            ErrorType.MODEL_ERROR: FailureCode.PROVIDER,
            ErrorType.APPROVAL_REJECTED: FailureCode.APPROVAL_REJECTED,
        }.get(run.error.error_type)
        if structured is not None:
            return structured

    text = " ".join(
        part
        for part in (
            run.final_output,
            run.error.message if run.error is not None else None,
            *(call.error.message for call in run.tool_calls if call.error is not None),
            *(call.output_summary for call in failed_tools),
        )
        if part
    )
    # Safety language outranks generic "cancelled": a safety gate commonly
    # cancels navigation, but the actionable root cause is the safety block.
    if _SAFETY.search(text):
        return FailureCode.SAFETY_BLOCKED
    if _TIMEOUT.search(text):
        return FailureCode.TIMEOUT
    if _BUSY.search(text):
        return FailureCode.BUSY
    if _UNAVAILABLE.search(text):
        return FailureCode.UNAVAILABLE
    if _NAVIGATION.search(text):
        return FailureCode.NAVIGATION
    if _INTERRUPTED.search(text):
        return FailureCode.INTERRUPTED

    if run.error is not None:
        return (
            FailureCode.TOOL
            if run.error.error_type == ErrorType.TOOL_ERROR
            else FailureCode.UNKNOWN
        )
    if failed_tools:
        return FailureCode.TOOL
    return FailureCode.UNKNOWN


def classify_outcome(run: RunRecord, failure_code: FailureCode | None = None) -> TaskOutcome:
    """Map the run lifecycle to the product-level completion contract."""

    if run.outcome is not None:
        return TaskOutcome(run.outcome)
    failure_code = failure_code or classify_failure(run)
    if run.status == RunStatus.COMPLETED:
        if failure_code == FailureCode.UNAVAILABLE:
            return TaskOutcome.UNAVAILABLE
        if failure_code is not None:
            return TaskOutcome.FAILED
        return TaskOutcome.PARTIAL
    if failure_code == FailureCode.UNAVAILABLE:
        return TaskOutcome.UNAVAILABLE
    if failure_code == FailureCode.INTERRUPTED:
        return TaskOutcome.CANCELLED
    if run.status == RunStatus.BLOCKED:
        return TaskOutcome.BLOCKED
    return TaskOutcome.FAILED


def build_task_receipt(run: RunRecord) -> TaskReceipt:
    if run.finished_at is None:
        raise ValueError("cannot build a task receipt before the run finishes")
    failure_code = classify_failure(run)
    duration_ms = max(
        0,
        round((run.finished_at - run.started_at).total_seconds() * 1000),
    )
    return TaskReceipt(
        run_id=run.run_id,
        session_id=run.session_id,
        request=run.user_input,
        status=run.status,
        outcome=classify_outcome(run, failure_code),
        failure_code=failure_code,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_ms=duration_ms,
        approval_requested=len(run.interruptions),
        approval_approved=sum(item.status == ApprovalStatus.APPROVED for item in run.interruptions),
        approval_rejected=sum(item.status == ApprovalStatus.REJECTED for item in run.interruptions),
        actions=[
            TaskActionReceipt(
                tool_name=call.tool_name,
                status=call.status,
                risk_level=call.risk_level,
                effect_scope=call.effect_scope,
                summary=call.output_summary,
            )
            for call in run.tool_calls
        ],
        result=run.final_output or (run.error.message if run.error is not None else None),
    )


class TaskReceiptStore:
    """One atomic JSON document per task; newest receipts are easy to list."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save(self, run: RunRecord) -> Path:
        receipt = build_task_receipt(run)
        # The start time is stable across review/resume, so repeated finalization
        # atomically replaces one receipt instead of creating duplicates.
        stamp = receipt.started_at.strftime("%Y%m%dT%H%M%S%fZ")
        path = self.directory / f"task-{stamp}-{receipt.run_id}.json"
        return atomic_write_text(
            path,
            json.dumps(receipt.model_dump(mode="json"), ensure_ascii=False, indent=2),
            harden_parent=True,
        )

    def list_paths(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        return sorted(self.directory.glob("task-*.json"), reverse=True)

    def load(self, path: Path) -> TaskReceipt | None:
        try:
            return TaskReceipt.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def render_task_receipt(receipt: TaskReceipt) -> str:
    seconds = receipt.duration_ms / 1000
    lines = [
        f"Request: {receipt.request}",
        f"Status: {receipt.status}",
        f"Outcome: {receipt.outcome}",
        f"Duration: {seconds:.2f}s",
        (
            "Approvals: "
            f"{receipt.approval_approved} approved, "
            f"{receipt.approval_rejected} rejected, "
            f"{receipt.approval_requested} requested"
        ),
    ]
    if receipt.failure_code is not None:
        lines.append(f"Failure code: {receipt.failure_code}")
    if receipt.actions:
        lines.append("Actions:")
        lines.extend(
            f"  - {action.tool_name}: {action.status}"
            + (f" — {action.summary}" if action.summary else "")
            for action in receipt.actions
        )
    else:
        lines.append("Actions: none")
    if receipt.result:
        lines.extend(("", "Result:", receipt.result))
    return "\n".join(lines)
