"""Deterministic task receipts derived from terminal run records."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from jenai.schemas import (
    ApprovalStatus,
    ErrorType,
    FailureCode,
    GoldenPathApprovedStep,
    GoldenPathEndpointResult,
    GoldenPathReceipt,
    GoldenPathStepAttemptReceipt,
    GoldenPathStepResultReceipt,
    RunRecord,
    RunStatus,
    TaskActionReceipt,
    TaskOutcome,
    TaskReceipt,
    ToolCallRecord,
    ToolCallStatus,
)
from jenai.secure_files import atomic_write_text
from jenai.workflows.execution_engine import (
    ExecutionReport,
    StepDisposition,
    StepEvidenceKind,
)
from jenai.workflows.patrol_mission import ExecutionPlan

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

_STRUCTURED_FAILURE_CODES = {
    ErrorType.CONFIG_ERROR: FailureCode.CONFIGURATION,
    ErrorType.ENV_ERROR: FailureCode.ENVIRONMENT,
    ErrorType.VALIDATION_ERROR: FailureCode.VALIDATION,
    ErrorType.MODEL_ERROR: FailureCode.PROVIDER,
    ErrorType.APPROVAL_REJECTED: FailureCode.APPROVAL_REJECTED,
}


@dataclass(frozen=True)
class _TextFailureRule:
    pattern: re.Pattern[str]
    failure_code: FailureCode


_TEXT_FAILURE_RULES = (
    _TextFailureRule(_SAFETY, FailureCode.SAFETY_BLOCKED),
    _TextFailureRule(_TIMEOUT, FailureCode.TIMEOUT),
    _TextFailureRule(_BUSY, FailureCode.BUSY),
    _TextFailureRule(_UNAVAILABLE, FailureCode.UNAVAILABLE),
    _TextFailureRule(_NAVIGATION, FailureCode.NAVIGATION),
    _TextFailureRule(_INTERRUPTED, FailureCode.INTERRUPTED),
)


def _failure_text(run: RunRecord, failed_tools: list[ToolCallRecord]) -> str:
    return " ".join(
        part
        for part in (
            run.final_output,
            run.error.message if run.error is not None else None,
            *(call.error.message for call in run.tool_calls if call.error is not None),
            *(call.output_summary for call in failed_tools),
        )
        if part
    )


def _classify_text(text: str) -> FailureCode | None:
    for rule in _TEXT_FAILURE_RULES:
        if rule.pattern.search(text):
            return rule.failure_code
    return None


def classify_failure(run: RunRecord) -> FailureCode | None:
    """Map detailed run state onto a stable, deliberately small taxonomy."""

    if any(item.status == ApprovalStatus.REJECTED for item in run.interruptions):
        return FailureCode.APPROVAL_REJECTED

    failed_tools = [call for call in run.tool_calls if call.status == ToolCallStatus.FAILED]
    if run.status == RunStatus.COMPLETED and run.error is None and not failed_tools:
        return None
    if run.status == RunStatus.INTERRUPTED:
        return FailureCode.INTERRUPTED

    # Structured error types identify the failing subsystem more reliably
    # than words such as "unavailable" in a provider response.
    if run.error is not None:
        structured = _STRUCTURED_FAILURE_CODES.get(run.error.error_type)
        if structured is not None:
            return structured

    # Safety language outranks generic "cancelled": a safety gate commonly
    # cancels navigation, but the actionable root cause is the safety block.
    text_failure = _classify_text(_failure_text(run, failed_tools))
    if text_failure is not None:
        return text_failure

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


def build_golden_path_receipt(
    plan: ExecutionPlan,
    report: ExecutionReport,
) -> GoldenPathReceipt:
    """Bind the approved immutable Plan to the Engine's actual attempt records."""

    detached_plan = ExecutionPlan.model_validate(plan.model_dump(mode="json"))
    detached_report = ExecutionReport.model_validate(report.model_dump(mode="json"))
    if detached_report.plan_digest != detached_plan.plan_digest:
        raise ValueError("ExecutionReport does not match the approved plan digest")

    approved_steps = tuple(
        GoldenPathApprovedStep(
            step_index=index,
            kind=step.kind,
            location_id=step.location_id,
            location_name=step.location_name,
            position_tolerance_m=step.position_tolerance_m,
            require_yaw=step.require_yaw,
        )
        for index, step in enumerate(detached_plan.steps)
    )
    step_results: list[GoldenPathStepResultReceipt] = []
    for record in detached_report.step_records:
        if record.step_index >= len(detached_plan.steps):
            raise ValueError("ExecutionReport contains an unknown plan step")
        approved = detached_plan.steps[record.step_index]
        if record.step != approved:
            raise ValueError("ExecutionReport step differs from the approved plan")
        step_results.append(
            GoldenPathStepResultReceipt(
                step_index=record.step_index,
                kind=record.step.kind,
                location_id=record.step.location_id,
                location_name=record.step.location_name,
                skipped=record.skipped,
                attempts=tuple(
                    GoldenPathStepAttemptReceipt(
                        dispatch_id=attempt.dispatch_id,
                        attempt=attempt.attempt,
                        disposition=attempt.result.disposition.value,
                        summary=attempt.result.summary,
                        evidence_references=tuple(
                            evidence.evidence_id for evidence in attempt.result.evidence
                        ),
                        cancel_acknowledged=attempt.result.cancel_acknowledged,
                        position_error_m=attempt.result.position_error_m,
                    )
                    for attempt in record.attempts
                ),
            )
        )

    if detached_report.outcome is TaskOutcome.SUCCEEDED and len(step_results) != len(
        approved_steps
    ):
        raise ValueError("successful Golden Path report does not contain every approved step")

    final_endpoint: GoldenPathEndpointResult | None = None
    if detached_report.step_records:
        final_record = detached_report.step_records[-1]
        if final_record.attempts:
            final_attempt = final_record.attempts[-1].result
            terminal_ref = next(
                (
                    evidence.evidence_id
                    for evidence in final_attempt.evidence
                    if evidence.kind is StepEvidenceKind.NAVIGATION_TERMINAL
                ),
                None,
            )
            endpoint_ref = next(
                (
                    evidence.evidence_id
                    for evidence in final_attempt.evidence
                    if evidence.kind is StepEvidenceKind.ENDPOINT_POSE
                ),
                None,
            )
            final_endpoint = GoldenPathEndpointResult(
                location_id=final_record.step.location_id,
                location_name=final_record.step.location_name,
                disposition=final_attempt.disposition.value,
                position_error_m=final_attempt.position_error_m,
                position_tolerance_m=final_record.step.position_tolerance_m,
                within_tolerance=(
                    final_attempt.disposition is StepDisposition.SUCCEEDED
                    and final_attempt.position_error_m is not None
                    and final_attempt.position_error_m <= final_record.step.position_tolerance_m
                ),
                terminal_evidence_reference=terminal_ref,
                endpoint_evidence_reference=endpoint_ref,
            )
    if detached_report.outcome is TaskOutcome.SUCCEEDED and (
        final_endpoint is None
        or not final_endpoint.within_tolerance
        or final_endpoint.terminal_evidence_reference is None
        or final_endpoint.endpoint_evidence_reference is None
    ):
        raise ValueError("successful Golden Path report lacks verified final endpoint Evidence")

    return GoldenPathReceipt(
        mission_id=detached_plan.mission.mission_id,
        mission_digest=detached_plan.mission.mission_digest,
        plan_digest=detached_plan.plan_digest,
        approved_steps=approved_steps,
        step_results=tuple(step_results),
        final_endpoint_result=final_endpoint,
    )


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
        golden_path=run.golden_path,
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
    if receipt.golden_path is not None:
        lines.append(f"Mission digest: {receipt.golden_path.mission_digest}")
        lines.append(f"Plan digest: {receipt.golden_path.plan_digest}")
        endpoint = receipt.golden_path.final_endpoint_result
        if endpoint is not None:
            error = (
                "unavailable"
                if endpoint.position_error_m is None
                else f"{endpoint.position_error_m:.3f} m"
            )
            lines.append(f"Final endpoint: {endpoint.location_name} — error {error}")
    if receipt.result:
        lines.extend(("", "Result:", receipt.result))
    return "\n".join(lines)
