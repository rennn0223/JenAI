"""Run records plus durable agents-SDK approval pause/resume state."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from agents import Agent, RunState

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
    """Session runs and optional durable SDK state for approval interruptions."""

    def __init__(self, pending_dir: Path | None = None) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._pending_state: dict[str, Any] = {}
        self._pending_dir = pending_dir
        # Position-aligned approval ids for the paused state's interruptions, so
        # resume can map each interruption back to its unique ApprovalRequest id
        # (the SDK often gives no call_id, so index alone would collide).
        self._pending_approval_ids: dict[str, list[str]] = {}
        if self._pending_dir is not None:
            self._pending_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._load_pending_run_records()

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
        if self._pending_dir is None or not hasattr(state, "to_json"):
            return
        run = self.get(run_id)
        if run is None:
            raise ValueError(f"Cannot persist unknown run {run_id}")
        sdk_state = state.to_json(
            context_serializer=lambda _context: {},
            include_tracing_api_key=False,
        )
        payload = {
            "schema_version": 1,
            "run": run.model_dump(mode="json"),
            "approval_ids": self._pending_approval_ids[run_id],
            "sdk_state": sdk_state,
        }
        path = self._pending_path(run_id)
        temp = path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(temp, path)

    def pop_pending_state(self, run_id: str) -> Any | None:
        return self._pending_state.pop(run_id, None)

    def pop_pending_approval_ids(self, run_id: str) -> list[str]:
        return self._pending_approval_ids.pop(run_id, [])

    async def take_pending_state(
        self,
        run_id: str,
        *,
        initial_agent: Agent[Any],
        context: Any,
    ) -> tuple[Any, list[str]] | None:
        """Claim a paused state once, restoring it from disk when necessary."""
        state = self._pending_state.pop(run_id, None)
        approval_ids = self._pending_approval_ids.pop(run_id, [])
        path = self._pending_path(run_id) if self._pending_dir is not None else None
        if state is None and path is not None and path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != 1:
                raise ValueError(f"Unsupported pending-state version for run {run_id}")
            state = await RunState.from_json(
                initial_agent,
                payload["sdk_state"],
                context_override=context,
            )
            approval_ids = list(payload.get("approval_ids", []))
        if state is None:
            return None
        # Claim before execution. A crash may require a new run, but can never
        # replay a previously approved hardware action from the same file.
        if path is not None:
            path.unlink(missing_ok=True)
        return state, approval_ids

    def _pending_path(self, run_id: str) -> Path:
        assert self._pending_dir is not None
        digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        return self._pending_dir / f"{digest}.json"

    def _load_pending_run_records(self) -> None:
        assert self._pending_dir is not None
        for path in sorted(self._pending_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("schema_version") != 1:
                    continue
                run = RunRecord.model_validate(payload["run"])
            except (OSError, KeyError, TypeError, ValueError):
                continue
            self._runs[run.run_id] = run
