"""Translate execution facts into authoritative product task results."""

from __future__ import annotations

from dataclasses import dataclass

from jenai.schemas import (
    ApprovalStatus,
    FailureCode,
    RouteOutput,
    RunRecord,
    RunStatus,
    TaskOutcome,
    ToolCallStatus,
)
from jenai.state.task_receipts import classify_failure


@dataclass(frozen=True, slots=True)
class TaskResult:
    """One terminal lifecycle state paired with its product-level outcome."""

    run_status: RunStatus
    outcome: TaskOutcome

    @property
    def succeeded(self) -> bool:
        return self.outcome in {TaskOutcome.SUCCEEDED, TaskOutcome.ARRIVED_UNVERIFIED}


_NAVIGATION_RESULTS: dict[str, TaskResult] = {
    "succeeded": TaskResult(RunStatus.COMPLETED, TaskOutcome.SUCCEEDED),
    "endpoint_mismatch": TaskResult(RunStatus.COMPLETED, TaskOutcome.ENDPOINT_MISMATCH),
    "blocked": TaskResult(RunStatus.BLOCKED, TaskOutcome.BLOCKED),
    "referred": TaskResult(RunStatus.BLOCKED, TaskOutcome.BLOCKED),
    "unavailable": TaskResult(RunStatus.FAILED, TaskOutcome.UNAVAILABLE),
    "failed": TaskResult(RunStatus.FAILED, TaskOutcome.FAILED),
    "cancelled": TaskResult(RunStatus.INTERRUPTED, TaskOutcome.CANCELLED),
    "canceled": TaskResult(RunStatus.INTERRUPTED, TaskOutcome.CANCELLED),
    "aborted": TaskResult(RunStatus.INTERRUPTED, TaskOutcome.CANCELLED),
}


def navigation_result(
    execution_status: str,
    *,
    success_outcome: TaskOutcome = TaskOutcome.SUCCEEDED,
) -> TaskResult:
    """Return the lifecycle and outcome for a navigation adapter status.

    Unknown or pre-execution values fail honestly. Callers may specialize only
    the verified success outcome; failure mappings remain authoritative.
    """

    normalized = execution_status.strip().lower()
    result = _NAVIGATION_RESULTS.get(
        normalized,
        TaskResult(RunStatus.FAILED, TaskOutcome.FAILED),
    )
    if normalized == "succeeded":
        return TaskResult(result.run_status, TaskOutcome(success_outcome))
    return result


def navigation_output_result(output: RouteOutput) -> TaskResult:
    """Derive the result from the gateway's canonical executed action."""

    success_outcome = (
        TaskOutcome.ARRIVED_UNVERIFIED
        if output.outgoing_action.get("capability_id") == "dock_approach"
        else TaskOutcome.SUCCEEDED
    )
    return navigation_result(output.execution_status, success_outcome=success_outcome)


def navigation_receipt_text(output: RouteOutput) -> str:
    """Render one honest navigation result for every product surface."""

    result = navigation_output_result(output)
    return (
        f"status={result.run_status.value}; outcome={result.outcome.value}; "
        f"adapter_status={output.execution_status}\n{output.route_preview}"
    )


def aggregate_step_outcome(statuses: list[str]) -> TaskOutcome:
    """Aggregate observed multi-step statuses without promoting partial work."""

    normalized = [status.strip().lower() for status in statuses]
    if not normalized:
        return TaskOutcome.FAILED
    reached = [status for status in normalized if status in {"succeeded", "partial"}]
    if reached:
        if all(status == "succeeded" for status in normalized):
            return TaskOutcome.SUCCEEDED
        return TaskOutcome.PARTIAL
    if all(status in {"blocked", "referred"} for status in normalized):
        return TaskOutcome.BLOCKED
    if "unavailable" in normalized:
        return TaskOutcome.UNAVAILABLE
    return TaskOutcome.FAILED


def run_status_for_outcome(outcome: TaskOutcome) -> RunStatus:
    """Return the terminal lifecycle required by one product outcome."""

    normalized = TaskOutcome(outcome)
    if normalized == TaskOutcome.BLOCKED:
        return RunStatus.BLOCKED
    if normalized in {TaskOutcome.UNAVAILABLE, TaskOutcome.FAILED}:
        return RunStatus.FAILED
    if normalized == TaskOutcome.CANCELLED:
        return RunStatus.INTERRUPTED
    return RunStatus.COMPLETED


def agent_completion_outcome(run: RunRecord) -> TaskOutcome:
    """Derive an Agent result only from recorded completion evidence.

    Tools that verify a completion contract set ``run.outcome``. Without that
    evidence, a normally ending model turn is only a partial result.
    """

    if any(item.status == ApprovalStatus.REJECTED for item in run.interruptions):
        return TaskOutcome.BLOCKED
    if run.outcome is not None:
        return TaskOutcome(run.outcome)
    if any(call.status == ToolCallStatus.FAILED for call in run.tool_calls):
        failure_code = classify_failure(run)
        if failure_code == FailureCode.UNAVAILABLE:
            return TaskOutcome.UNAVAILABLE
        return TaskOutcome.FAILED
    return TaskOutcome.PARTIAL
