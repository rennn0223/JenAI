"""Run records plus durable agents-SDK approval pause/resume state."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

from agents import Agent, RunState

from jenai.schemas import (
    ApprovalRequest,
    ApprovalStatus,
    GoldenPathReceipt,
    JenAIError,
    PlanStep,
    RunRecord,
    RunStatus,
    TaskOutcome,
    ToolCallRecord,
    ToolCallStatus,
)
from jenai.schemas.models import utc_now
from jenai.secure_files import atomic_write_text
from jenai.state.audit import AuditStore
from jenai.state.task_receipts import TaskReceiptStore

TERMINAL_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.BLOCKED,
    RunStatus.INTERRUPTED,
}
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
        self._lock = threading.RLock()
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
        with self._lock:
            self._runs[run.run_id] = run
        self.audit_event(run, "run_created", status=run.status)
        return run

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._runs.get(run_id)

    def list_runs(self) -> list[RunRecord]:
        """Return live run references in creation order for lifecycle owners."""
        with self._lock:
            return list(self._runs.values())

    def snapshot_runs(self) -> list[RunRecord]:
        """Return one consistent, detached view for concurrent readers."""
        with self._lock:
            return [run.model_copy(deep=True) for run in self._runs.values()]

    def _persist_receipt(self, run: RunRecord, snapshot: RunRecord) -> None:
        """Persist one lock-free terminal snapshot without affecting robot control."""
        if self.receipt_store is None:
            return
        try:
            self.receipt_store.save(snapshot)
        except Exception as exc:
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

    def set_status(self, run: RunRecord, status: RunStatus) -> None:
        with self._lock:
            previous = run.status.value
            run.status = RunStatus(status)
            if run.status in TERMINAL_STATUSES:
                run.finished_at = utc_now()
            current = run.status
        if current != previous:
            self.audit_event(
                run,
                "run_status",
                status=current,
                details={"previous": previous},
            )

    def add_plan_steps(self, run: RunRecord, steps: list[PlanStep]) -> None:
        with self._lock:
            run.plan_steps = steps

    def set_golden_path_receipt(
        self,
        run: RunRecord,
        receipt: GoldenPathReceipt,
    ) -> None:
        detached = GoldenPathReceipt.model_validate(receipt.model_dump(mode="json"))
        with self._lock:
            run.golden_path = detached

    def add_tool_call(self, run: RunRecord, tool_call: ToolCallRecord) -> None:
        with self._lock:
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
        with self._lock:
            matched: ToolCallRecord | None = None
            for call in run.tool_calls:
                if call.tool_call_id == tool_call_id:
                    unknown = set(fields) - _MUTABLE_TOOL_FIELDS
                    if unknown:
                        names = ", ".join(sorted(unknown))
                        raise ValueError(f"Tool call fields are immutable or unknown: {names}")
                    candidate = ToolCallRecord.model_validate(
                        {**call.model_dump(mode="python"), **fields}
                    )
                    for key in fields:
                        setattr(call, key, getattr(candidate, key))
                    matched = call
                    break
            if matched is None:
                raise KeyError(f"Unknown tool call {tool_call_id}")
            status = matched.status
            tool_name = matched.tool_name
            has_error = matched.error is not None
        self.audit_event(
            run,
            "tool_updated",
            entity_id=tool_call_id,
            status=status,
            details={
                "tool_name": tool_name,
                "changed_fields": sorted(fields),
                "has_error": has_error,
            },
        )

    def add_interruption(self, run: RunRecord, approval: ApprovalRequest) -> None:
        with self._lock:
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

    def register_pending_approval(
        self,
        run: RunRecord,
        tool_call: ToolCallRecord,
        approval: ApprovalRequest,
    ) -> None:
        """Atomically publish the tool, approval, and awaiting run state."""
        with self._lock:
            previous = run.status.value
            run.tool_calls.append(tool_call)
            run.interruptions.append(approval)
            run.status = RunStatus.AWAITING_APPROVAL
            run.finished_at = None
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
        if previous != RunStatus.AWAITING_APPROVAL.value:
            self.audit_event(
                run,
                "run_status",
                status=RunStatus.AWAITING_APPROVAL,
                details={"previous": previous},
            )

    def start_approved_tool(
        self,
        run: RunRecord,
        tool_call_id: str,
    ) -> None:
        """Atomically publish approval, tool start, and run start to readers."""
        with self._lock:
            approval = next(
                (item for item in run.interruptions if item.tool_call_id == tool_call_id),
                None,
            )
            call = next(
                (item for item in run.tool_calls if item.tool_call_id == tool_call_id),
                None,
            )
            if approval is None or call is None:
                raise KeyError(f"Unknown confirmation tool call {tool_call_id}")
            if (
                ApprovalStatus(approval.status) != ApprovalStatus.PENDING
                or ToolCallStatus(call.status) != ToolCallStatus.AWAITING_APPROVAL
                or RunStatus(run.status) != RunStatus.AWAITING_APPROVAL
            ):
                raise RuntimeError("Confirmation is no longer pending")
            previous = run.status.value
            now = utc_now()
            approval.status = ApprovalStatus.APPROVED
            approval.resolved_at = now
            candidate = ToolCallRecord.model_validate(
                {
                    **call.model_dump(mode="python"),
                    "status": ToolCallStatus.RUNNING,
                    "started_at": now,
                }
            )
            call.status = candidate.status
            call.started_at = candidate.started_at
            run.status = RunStatus.RUNNING
            run.finished_at = None
            tool_name = approval.tool_name
        self.audit_event(
            run,
            "approval_resolved",
            entity_id=tool_call_id,
            status=ApprovalStatus.APPROVED,
            details={"tool_name": tool_name},
        )
        self.audit_event(
            run,
            "tool_updated",
            entity_id=tool_call_id,
            status=ToolCallStatus.RUNNING,
            details={
                "tool_name": call.tool_name,
                "changed_fields": ["started_at", "status"],
                "has_error": False,
            },
        )
        if previous != RunStatus.RUNNING.value:
            self.audit_event(
                run,
                "run_status",
                status=RunStatus.RUNNING,
                details={"previous": previous},
            )

    def reject_approval_and_finish(
        self,
        run: RunRecord,
        tool_call_id: str,
        *,
        summary: str,
    ) -> None:
        """Atomically reject an approval, its tool, and the containing run."""
        with self._lock:
            approval = next(
                (item for item in run.interruptions if item.tool_call_id == tool_call_id),
                None,
            )
            call = next(
                (item for item in run.tool_calls if item.tool_call_id == tool_call_id),
                None,
            )
            if approval is None or call is None:
                raise KeyError(f"Unknown confirmation tool call {tool_call_id}")
            previous = run.status.value
            now = utc_now()
            approval.status = ApprovalStatus.REJECTED
            approval.resolved_at = now
            candidate = ToolCallRecord.model_validate(
                {
                    **call.model_dump(mode="python"),
                    "status": ToolCallStatus.REJECTED,
                    "ended_at": now,
                    "output_summary": summary,
                }
            )
            call.status = candidate.status
            call.ended_at = candidate.ended_at
            call.output_summary = candidate.output_summary
            run.final_output = summary
            run.outcome = TaskOutcome.BLOCKED
            run.status = RunStatus.BLOCKED
            run.finished_at = now
            tool_name = approval.tool_name
            receipt_snapshot = run.model_copy(deep=True)
        self.audit_event(
            run,
            "approval_resolved",
            entity_id=tool_call_id,
            status=ApprovalStatus.REJECTED,
            details={"tool_name": tool_name},
        )
        self.audit_event(
            run,
            "tool_updated",
            entity_id=tool_call_id,
            status=ToolCallStatus.REJECTED,
            details={
                "tool_name": call.tool_name,
                "changed_fields": ["ended_at", "output_summary", "status"],
                "has_error": False,
            },
        )
        if previous != RunStatus.BLOCKED.value:
            self.audit_event(
                run,
                "run_status",
                status=RunStatus.BLOCKED,
                details={"previous": previous},
            )
        self.audit_event(
            run,
            "run_finished",
            status=RunStatus.BLOCKED,
            details={
                "has_output": bool(summary),
                "outcome": TaskOutcome.BLOCKED.value,
                "error_type": None,
            },
        )
        self._persist_receipt(run, receipt_snapshot)

    def finish_tool_and_run(
        self,
        run: RunRecord,
        tool_call_id: str,
        *,
        tool_status: ToolCallStatus,
        run_status: RunStatus,
        outcome: TaskOutcome,
        summary: str,
    ) -> None:
        """Atomically publish a tool result and its containing run outcome."""
        tool_status = ToolCallStatus(tool_status)
        run_status = RunStatus(run_status)
        outcome = TaskOutcome(outcome)
        with self._lock:
            call = next(
                (item for item in run.tool_calls if item.tool_call_id == tool_call_id),
                None,
            )
            if call is None:
                raise KeyError(f"Unknown tool call {tool_call_id}")
            previous = run.status.value
            now = utc_now()
            candidate = ToolCallRecord.model_validate(
                {
                    **call.model_dump(mode="python"),
                    "status": tool_status,
                    "ended_at": now,
                    "output_summary": summary,
                }
            )
            call.status = candidate.status
            call.ended_at = candidate.ended_at
            call.output_summary = candidate.output_summary
            run.final_output = summary
            run.outcome = outcome
            run.status = run_status
            run.finished_at = now if run_status in TERMINAL_STATUSES else None
            receipt_snapshot = run.model_copy(deep=True)
        self.audit_event(
            run,
            "tool_updated",
            entity_id=tool_call_id,
            status=tool_status,
            details={
                "tool_name": call.tool_name,
                "changed_fields": ["ended_at", "output_summary", "status"],
                "has_error": call.error is not None,
            },
        )
        if previous != run_status.value:
            self.audit_event(
                run,
                "run_status",
                status=run_status,
                details={"previous": previous},
            )
        self.audit_event(
            run,
            "run_finished",
            status=run_status,
            details={
                "has_output": bool(summary),
                "outcome": outcome.value,
                "error_type": None,
            },
        )
        self._persist_receipt(run, receipt_snapshot)

    def resolve_interruption(
        self,
        run: RunRecord,
        tool_call_id: str,
        status: ApprovalStatus,
    ) -> None:
        with self._lock:
            matched: ApprovalRequest | None = None
            for approval in run.interruptions:
                if approval.tool_call_id == tool_call_id:
                    approval.status = ApprovalStatus(status)
                    approval.resolved_at = utc_now()
                    matched = approval
                    break
            if matched is None:
                return
            resolved_status = matched.status
            tool_name = matched.tool_name
        self.audit_event(
            run,
            "approval_resolved",
            entity_id=tool_call_id,
            status=resolved_status,
            details={"tool_name": tool_name},
        )

    def finish(
        self,
        run: RunRecord,
        *,
        status: RunStatus,
        outcome: TaskOutcome | None = None,
        final_output: str | None = None,
        error: JenAIError | None = None,
    ) -> None:
        with self._lock:
            previous = run.status.value
            run.final_output = final_output
            if outcome is not None:
                run.outcome = outcome
            elif run.outcome is None and status == RunStatus.BLOCKED:
                run.outcome = TaskOutcome.BLOCKED
            elif run.outcome is None and status == RunStatus.FAILED:
                run.outcome = TaskOutcome.FAILED
            elif run.outcome is None and status == RunStatus.INTERRUPTED:
                run.outcome = TaskOutcome.CANCELLED
            run.error = error
            run.status = RunStatus(status)
            if run.status in TERMINAL_STATUSES:
                run.finished_at = utc_now()
            current_status = run.status
            current_outcome = run.outcome
            receipt_snapshot = run.model_copy(deep=True)
        if current_status != previous:
            self.audit_event(
                run,
                "run_status",
                status=current_status,
                details={"previous": previous},
            )
        self.audit_event(
            run,
            "run_finished",
            status=current_status,
            details={
                "has_output": bool(final_output),
                "outcome": str(current_outcome) if current_outcome is not None else None,
                "error_type": str(error.error_type) if error is not None else None,
            },
        )
        self._persist_receipt(run, receipt_snapshot)

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
        with self._lock:
            self._pending_state[run_id] = state
            self._pending_approval_ids[run_id] = list(approval_ids or [])
            run = self._runs.get(run_id)
            approval_snapshot = list(self._pending_approval_ids[run_id])
            run_snapshot = run.model_copy(deep=True) if run is not None else None
        if self._pending_dir is None or not hasattr(state, "to_json"):
            return
        if run_snapshot is None:
            raise ValueError(f"Cannot persist unknown run {run_id}")
        sdk_state = state.to_json(
            context_serializer=lambda _context: {},
            include_tracing_api_key=False,
        )
        payload = {
            "schema_version": 1,
            "run": run_snapshot.model_dump(mode="json"),
            "approval_ids": approval_snapshot,
            "sdk_state": sdk_state,
        }
        path = self._pending_path(run_id)
        atomic_write_text(
            path,
            json.dumps(payload, ensure_ascii=False),
            harden_parent=True,
        )

    def pop_pending_state(self, run_id: str) -> Any | None:
        with self._lock:
            return self._pending_state.pop(run_id, None)

    def pop_pending_approval_ids(self, run_id: str) -> list[str]:
        with self._lock:
            return self._pending_approval_ids.pop(run_id, [])

    def discard_pending_state(self, run_id: str) -> None:
        """Irreversibly discard a paused approval state without resuming it."""
        with self._lock:
            self._pending_state.pop(run_id, None)
            self._pending_approval_ids.pop(run_id, None)
        if self._pending_dir is not None:
            self._pending_path(run_id).unlink(missing_ok=True)

    async def take_pending_state(
        self,
        run_id: str,
        *,
        initial_agent: Agent[Any],
        context: Any,
    ) -> tuple[Any, list[str]] | None:
        """Claim a paused state once, restoring it from disk when necessary."""
        with self._lock:
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
