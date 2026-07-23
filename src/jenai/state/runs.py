"""Run records plus durable agents-SDK approval pause/resume state."""

from __future__ import annotations

import hashlib
import json
import logging
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
    TaskOutcome,
    ToolCallRecord,
)
from jenai.schemas.models import utc_now
from jenai.secure_files import atomic_write_text
from jenai.state.audit import AuditStore
from jenai.state.task_receipts import TaskReceiptStore

TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.BLOCKED}
_MUTABLE_TOOL_FIELDS = frozenset(
    {
        "status",
        "started_at",
        "ended_at",
        "output_summary",
        "raw_output",
        "error",
    }
)

logger = logging.getLogger(__name__)


class RunStore:
    """Session runs and optional durable SDK state for approval interruptions."""

    def __init__(
        self,
        pending_dir: Path | None = None,
        *,
        audit_store: AuditStore | None = None,
        receipt_store: TaskReceiptStore | None = None,
    ) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._pending_state: dict[str, Any] = {}
        self._pending_dir = pending_dir
        self.audit_store = audit_store
        self.receipt_store = receipt_store
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
        self.audit_event(run, "run_created", status=run.status)
        return run

    def get(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def list_runs(self) -> list[RunRecord]:
        """Return all runs in creation order (oldest first)."""
        return list(self._runs.values())

    def set_status(self, run: RunRecord, status: RunStatus) -> None:
        previous = run.status.value
        run.status = RunStatus(status)
        if run.status in TERMINAL_STATUSES:
            run.finished_at = utc_now()
        if run.status != previous:
            self.audit_event(
                run,
                "run_status",
                status=run.status,
                details={"previous": previous},
            )

    def add_plan_steps(self, run: RunRecord, steps: list[PlanStep]) -> None:
        run.plan_steps = steps

    def add_tool_call(self, run: RunRecord, tool_call: ToolCallRecord) -> None:
        run.tool_calls.append(tool_call)
        self.audit_event(
            run,
            "tool_registered",
            entity_id=tool_call.tool_call_id,
            status=tool_call.status,
            details={
                "tool_name": tool_call.tool_name,
                "category": str(tool_call.category),
                "risk_level": str(tool_call.risk_level),
                "effect_scope": str(tool_call.effect_scope),
            },
        )

    def update_tool_call(self, run: RunRecord, tool_call_id: str, **fields: Any) -> None:
        for call in run.tool_calls:
            if call.tool_call_id == tool_call_id:
                unknown = set(fields) - _MUTABLE_TOOL_FIELDS
                if unknown:
                    names = ", ".join(sorted(unknown))
                    raise ValueError(f"Tool call fields are immutable or unknown: {names}")
                # model_copy(update=...) does not validate in Pydantic v2.
                # Re-validate the complete record, then copy only trusted
                # values back so existing references retain their identity.
                candidate = ToolCallRecord.model_validate(
                    {**call.model_dump(mode="python"), **fields}
                )
                for key in fields:
                    setattr(call, key, getattr(candidate, key))
                self.audit_event(
                    run,
                    "tool_updated",
                    entity_id=tool_call_id,
                    status=call.status,
                    details={
                        "tool_name": call.tool_name,
                        "changed_fields": sorted(fields),
                        "has_error": call.error is not None,
                    },
                )
                return
        raise KeyError(f"Unknown tool call {tool_call_id}")

    def add_interruption(self, run: RunRecord, approval: ApprovalRequest) -> None:
        run.interruptions.append(approval)
        self.audit_event(
            run,
            "approval_requested",
            entity_id=approval.tool_call_id,
            status=approval.status,
            details={
                "tool_name": approval.tool_name,
                "risk_level": str(approval.risk_level),
                "effect_scope": str(approval.effect_scope),
            },
        )

    def resolve_interruption(
        self,
        run: RunRecord,
        tool_call_id: str,
        status: ApprovalStatus,
    ) -> None:
        for approval in run.interruptions:
            if approval.tool_call_id == tool_call_id:
                approval.status = ApprovalStatus(status)
                approval.resolved_at = utc_now()
                self.audit_event(
                    run,
                    "approval_resolved",
                    entity_id=tool_call_id,
                    status=approval.status,
                    details={"tool_name": approval.tool_name},
                )
                return

    def finish(
        self,
        run: RunRecord,
        *,
        status: RunStatus,
        outcome: TaskOutcome | None = None,
        final_output: str | None = None,
        error: JenAIError | None = None,
    ) -> None:
        run.final_output = final_output
        if outcome is not None:
            run.outcome = outcome
        elif run.outcome is None and status == RunStatus.BLOCKED:
            run.outcome = TaskOutcome.BLOCKED
        elif run.outcome is None and status == RunStatus.FAILED:
            run.outcome = TaskOutcome.FAILED
        run.error = error
        self.set_status(run, status)
        self.audit_event(
            run,
            "run_finished",
            status=run.status,
            details={
                "has_output": bool(final_output),
                "outcome": str(run.outcome) if run.outcome is not None else None,
                "error_type": str(error.error_type) if error is not None else None,
            },
        )
        if self.receipt_store is not None:
            try:
                self.receipt_store.save(run)
            except Exception as exc:
                # Reporting is best-effort and must never mask the task result
                # or prevent an emergency/safety path from finishing.
                self.audit_event(
                    run,
                    "task_receipt_failed",
                    status="failed",
                    summary="Task receipt could not be persisted.",
                    details={"exception_type": type(exc).__name__},
                )
                logger.warning(
                    "Task receipt persistence failed for run %s",
                    run.run_id,
                    exc_info=True,
                )

    def audit_event(
        self,
        run: RunRecord,
        event_type: str,
        *,
        entity_id: str | None = None,
        status: object | None = None,
        summary: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.audit_store is None:
            return
        try:
            self.audit_store.record(
                event_type,
                run_id=run.run_id,
                session_id=run.session_id,
                entity_id=entity_id,
                status=str(status) if status is not None else None,
                summary=summary,
                details=details,
            )
        except Exception:
            # Audit failure must never block a stop, rejection, or robot action.
            logger.warning(
                "Audit event %s could not be persisted for run %s",
                event_type,
                run.run_id,
                exc_info=True,
            )

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
        atomic_write_text(
            path,
            json.dumps(payload, ensure_ascii=False),
            harden_parent=True,
        )

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
        if self._pending_dir is None:
            raise RuntimeError("pending run storage is not configured")
        digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        return self._pending_dir / f"{digest}.json"

    def _load_pending_run_records(self) -> None:
        if self._pending_dir is None:
            raise RuntimeError("pending run storage is not configured")
        for path in sorted(self._pending_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("schema_version") != 1:
                    continue
                run = RunRecord.model_validate(payload["run"])
            except (OSError, KeyError, TypeError, ValueError):
                logger.warning("Ignoring invalid pending run state: %s", path, exc_info=True)
                continue
            self._runs[run.run_id] = run
            self.audit_event(run, "run_restored", status=run.status)
