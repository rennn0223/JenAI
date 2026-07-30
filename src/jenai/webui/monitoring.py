"""Safe WebUI projections of runtime records.

The monitor needs enough lifecycle detail to explain current work, approvals,
and tools after a browser refresh.  Raw tool inputs, outputs, and approval
actions deliberately remain server-side because they can contain credentials
or executable payloads.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from jenai.redaction import redact_sensitive_text
from jenai.schemas import RunRecord

_MAX_VISIBLE_RUNS = 50


def _safe_optional(value: str | None, secret_values: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    return redact_sensitive_text(value, secret_values=secret_values)


def build_monitoring_transcript(
    runs: Iterable[RunRecord],
    *,
    secret_values: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Return a bounded, chronological, browser-safe run transcript."""
    known_secrets = tuple(secret_values)

    def safe(value: str) -> str:
        return redact_sensitive_text(value, secret_values=known_secrets)

    visible = list(runs)[-_MAX_VISIBLE_RUNS:]
    return [
        {
            "run_id": run.run_id,
            "status": str(run.status),
            "outcome": str(run.outcome) if run.outcome is not None else None,
            "summary": safe(run.user_input),
            "final_output": _safe_optional(run.final_output, known_secrets),
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "approvals": [
                {
                    "approval_id": approval.approval_id,
                    "tool_call_id": approval.tool_call_id,
                    "title": safe(approval.title),
                    "summary": safe(approval.summary),
                    "tool_name": safe(approval.tool_name),
                    "risk_level": str(approval.risk_level),
                    "status": str(approval.status),
                    "created_at": approval.created_at.isoformat(),
                }
                for approval in run.interruptions
            ],
            "tool_calls": [
                {
                    "tool_call_id": call.tool_call_id,
                    "tool_name": safe(call.tool_name),
                    "input_summary": safe(call.input_summary),
                    "status": str(call.status),
                    "output_summary": _safe_optional(call.output_summary, known_secrets),
                    "started_at": call.started_at.isoformat() if call.started_at else None,
                    "ended_at": call.ended_at.isoformat() if call.ended_at else None,
                }
                for call in run.tool_calls
            ],
        }
        for run in visible
    ]
