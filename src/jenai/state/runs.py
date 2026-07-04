"""RunStore: session run records + the agents-SDK pause/resume side table."""

from __future__ import annotations

from typing import Any

from jenai.schemas import (
    ApprovalRequest,
    ApprovalStatus,
    JenAIError,
    PlanStep,
    RunRecord,
    RunStatus,
    ToolCallRecord,
)
from jenai.schemas.models import utc_now

TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.BLOCKED}


class RunStore:
    """In-memory session-scoped store for `RunRecord`s and their non-serializable
    runtime state (the `agents` SDK's pausable `RunState`, kept in a side table
    since it cannot be represented in the pydantic `RunRecord` schema).
    """

    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._pending_state: dict[str, Any] = {}
        # Position-aligned approval ids for the paused state's interruptions, so
        # resume can map each interruption back to its unique ApprovalRequest id
        # (the SDK often gives no call_id, so index alone would collide).
        self._pending_approval_ids: dict[str, list[str]] = {}

    def create_run(self, session_id: str, user_input: str) -> RunRecord:
        run = RunRecord(session_id=session_id, user_input=user_input)
        self._runs[run.run_id] = run
        return run

    def get(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def list_runs(self) -> list[RunRecord]:
        """Return all runs in creation order (oldest first)."""
        return list(self._runs.values())

    def set_status(self, run: RunRecord, status: RunStatus) -> None:
        run.status = RunStatus(status).value
        if run.status in {s.value for s in TERMINAL_STATUSES}:
            run.finished_at = utc_now()

    def add_plan_steps(self, run: RunRecord, steps: list[PlanStep]) -> None:
        run.plan_steps = steps

    def add_tool_call(self, run: RunRecord, tool_call: ToolCallRecord) -> None:
        run.tool_calls.append(tool_call)

    def update_tool_call(self, run: RunRecord, tool_call_id: str, **fields: Any) -> None:
        for call in run.tool_calls:
            if call.tool_call_id == tool_call_id:
                for key, value in fields.items():
                    setattr(call, key, value)
                return

    def add_interruption(self, run: RunRecord, approval: ApprovalRequest) -> None:
        run.interruptions.append(approval)

    def resolve_interruption(
        self,
        run: RunRecord,
        tool_call_id: str,
        status: ApprovalStatus,
    ) -> None:
        for approval in run.interruptions:
            if approval.tool_call_id == tool_call_id:
                approval.status = ApprovalStatus(status).value
                approval.resolved_at = utc_now()
                return

    def finish(
        self,
        run: RunRecord,
        *,
        status: RunStatus,
        final_output: str | None = None,
        error: JenAIError | None = None,
    ) -> None:
        run.final_output = final_output
        run.error = error
        self.set_status(run, status)

    def stash_pending_state(
        self, run_id: str, state: Any, approval_ids: list[str] | None = None
    ) -> None:
        self._pending_state[run_id] = state
        self._pending_approval_ids[run_id] = list(approval_ids or [])

    def pop_pending_state(self, run_id: str) -> Any | None:
        return self._pending_state.pop(run_id, None)

    def pop_pending_approval_ids(self, run_id: str) -> list[str]:
        return self._pending_approval_ids.pop(run_id, [])
