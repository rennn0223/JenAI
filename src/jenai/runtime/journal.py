"""Thread-safe runtime state and bounded replay journal."""

from __future__ import annotations

from collections import deque
from threading import RLock
from typing import Any

from jenai.runtime.contracts import (
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeEventPage,
    RuntimeSnapshot,
    RuntimeSource,
    RuntimeState,
)


class RuntimeJournal:
    """Own sequencing, safety epochs, state snapshots, and bounded replay."""

    def __init__(self, *, runtime_id: str, event_limit: int = 2048) -> None:
        if not runtime_id.strip():
            raise ValueError("runtime_id must not be blank")
        if event_limit < 1:
            raise ValueError("event_limit must be at least 1")
        self._runtime_id = runtime_id.strip()
        self._events: deque[RuntimeEvent] = deque(maxlen=event_limit)
        self._lock = RLock()
        self._next_sequence = 1
        self._safety_epoch = 0
        self._state = RuntimeState.STARTING
        self._active_run_id: str | None = None
        self._active_command_id: str | None = None
        self._pending_approval_ids: list[str] = []
        self._message: str | None = None

    def publish(
        self,
        kind: RuntimeEventKind,
        *,
        source: RuntimeSource,
        summary: str,
        run_id: str | None = None,
        command_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        with self._lock:
            if kind == RuntimeEventKind.RUNTIME_STARTED:
                self._state = RuntimeState.READY
            elif kind == RuntimeEventKind.RUNTIME_UNAVAILABLE:
                self._state = RuntimeState.UNAVAILABLE
            event = RuntimeEvent(
                sequence=self._next_sequence,
                safety_epoch=self._safety_epoch,
                kind=kind,
                source=source,
                summary=summary,
                run_id=run_id,
                command_id=command_id,
                details=dict(details or {}),
            )
            self._next_sequence += 1
            self._events.append(event)
            return event

    def advance_safety_epoch(self, *, source: RuntimeSource, reason: str) -> RuntimeEvent:
        with self._lock:
            self._safety_epoch += 1
            self._pending_approval_ids.clear()
            return self.publish(
                RuntimeEventKind.SAFETY_EPOCH_ADVANCED,
                source=source,
                summary=reason,
                details={"safety_epoch": self._safety_epoch},
            )

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            return RuntimeSnapshot(
                runtime_id=self._runtime_id,
                state=self._state,
                safety_epoch=self._safety_epoch,
                last_sequence=self._next_sequence - 1,
                active_run_id=self._active_run_id,
                active_command_id=self._active_command_id,
                pending_approval_ids=list(self._pending_approval_ids),
                message=self._message,
            )

    def events(self, *, after_sequence: int) -> RuntimeEventPage:
        if after_sequence < 0:
            raise ValueError("after_sequence must not be negative")
        with self._lock:
            events = list(self._events)
            first_available = events[0].sequence if events else None
            replay_gap = first_available is not None and first_available > after_sequence + 1
            selected = [event for event in events if event.sequence > after_sequence]
            return RuntimeEventPage(
                after_sequence=after_sequence,
                first_available_sequence=first_available,
                last_sequence=self._next_sequence - 1,
                replay_gap=replay_gap,
                events=selected,
            )
