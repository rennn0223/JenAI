"""Bounded SQLite audit events for runs, approvals, tools, and safety gates."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AuditEvent:
    event_id: int
    occurred_at: str
    event_type: str
    run_id: str | None
    session_id: str | None
    entity_id: str | None
    status: str | None
    summary: str | None
    details: dict[str, Any]


class AuditStore:
    """Append-only event log with bounded retention and no raw action payloads."""

    def __init__(self, path: Path, *, max_events: int = 10_000) -> None:
        self.path = path
        self.max_events = max(1, max_events)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    run_id TEXT,
                    session_id TEXT,
                    entity_id TEXT,
                    status TEXT,
                    summary TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS audit_events_run_id
                    ON audit_events(run_id, event_id);
                CREATE INDEX IF NOT EXISTS audit_events_type
                    ON audit_events(event_type, event_id);
                """
            )
        os.chmod(self.path, 0o600)

    @classmethod
    def best_effort(cls, path: Path, *, max_events: int = 10_000) -> AuditStore | None:
        """Open an audit store without making telemetry a runtime dependency."""
        try:
            return cls(path, max_events=max_events)
        except (OSError, sqlite3.Error):
            return None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def record(
        self,
        event_type: str,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
        entity_id: str | None = None,
        status: str | None = None,
        summary: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> int:
        occurred_at = datetime.now(UTC).isoformat()
        details_json = json.dumps(
            details or {}, ensure_ascii=False, allow_nan=False, default=str
        )
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO audit_events (
                    occurred_at, event_type, run_id, session_id,
                    entity_id, status, summary, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    occurred_at,
                    event_type,
                    run_id,
                    session_id,
                    entity_id,
                    status,
                    summary,
                    details_json,
                ),
            )
            event_id = int(cursor.lastrowid or 0)
            connection.execute(
                """
                DELETE FROM audit_events
                WHERE event_id NOT IN (
                    SELECT event_id FROM audit_events
                    ORDER BY event_id DESC LIMIT ?
                )
                """,
                (self.max_events,),
            )
        os.chmod(self.path, 0o600)
        return event_id

    def list_events(self, *, run_id: str | None = None, limit: int = 100) -> list[AuditEvent]:
        query = (
            "SELECT event_id, occurred_at, event_type, run_id, session_id, "
            "entity_id, status, summary, details_json FROM audit_events"
        )
        params: list[Any] = []
        if run_id is not None:
            query += " WHERE run_id = ?"
            params.append(run_id)
        query += " ORDER BY event_id DESC LIMIT ?"
        params.append(max(0, limit))
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(query, params).fetchall()
        return [
            AuditEvent(
                event_id=row[0],
                occurred_at=row[1],
                event_type=row[2],
                run_id=row[3],
                session_id=row[4],
                entity_id=row[5],
                status=row[6],
                summary=row[7],
                details=json.loads(row[8]),
            )
            for row in rows
        ]
