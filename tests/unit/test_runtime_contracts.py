from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from jenai.runtime.contracts import (
    RuntimeCommandRequest,
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeEventPage,
    RuntimeSource,
    RuntimeState,
)
from jenai.runtime.journal import RuntimeJournal


def test_runtime_command_request_is_a_strict_high_level_capability() -> None:
    request = RuntimeCommandRequest(
        capability_id="navigate",
        arguments={"location": "Dock"},
        source=RuntimeSource.TUI,
        requested_safety_epoch=4,
    )

    assert request.capability_id == "navigate"
    assert request.arguments == {"location": "Dock"}
    assert request.source == "tui"
    assert request.request_id.startswith("request_")
    assert request.requested_safety_epoch == 4

    with pytest.raises(ValidationError):
        RuntimeCommandRequest(
            capability_id="   ",
            source=RuntimeSource.TUI,
            requested_safety_epoch=4,
        )


def test_runtime_event_page_rejects_non_monotonic_or_stale_events() -> None:
    now = datetime.now(UTC)
    first = RuntimeEvent(
        sequence=2,
        safety_epoch=1,
        kind=RuntimeEventKind.RUNTIME_STARTED,
        source=RuntimeSource.SYSTEM,
        summary="Runtime ready.",
        occurred_at=now,
    )
    second = first.model_copy(
        update={
            "event_id": "event_second",
            "sequence": 3,
            "kind": RuntimeEventKind.RUNTIME_STATE_CHANGED,
        }
    )

    page = RuntimeEventPage(after_sequence=1, last_sequence=3, events=[first, second])
    assert [event.sequence for event in page.events] == [2, 3]

    with pytest.raises(ValidationError):
        RuntimeEventPage(after_sequence=1, last_sequence=3, events=[second, first])

    with pytest.raises(ValidationError):
        RuntimeEventPage(after_sequence=2, last_sequence=2, events=[first])


def test_runtime_journal_provides_cursor_replay_and_safety_epoch() -> None:
    journal = RuntimeJournal(runtime_id="runtime_test", event_limit=8)
    started = journal.publish(
        RuntimeEventKind.RUNTIME_STARTED,
        source=RuntimeSource.SYSTEM,
        summary="Runtime ready.",
    )
    advanced = journal.advance_safety_epoch(
        source=RuntimeSource.WEBUI,
        reason="Emergency stop requested.",
    )

    assert started.sequence == 1
    assert advanced.sequence == 2
    assert advanced.safety_epoch == 1
    assert journal.snapshot().state == RuntimeState.READY
    assert journal.snapshot().safety_epoch == 1
    assert journal.events(after_sequence=1).events == [advanced]
    assert journal.events(after_sequence=2).events == []


def test_runtime_journal_reports_replay_gap_after_bounded_history_rolls_over() -> None:
    journal = RuntimeJournal(runtime_id="runtime_test", event_limit=2)
    for index in range(3):
        journal.publish(
            RuntimeEventKind.ACTION_PROGRESS,
            source=RuntimeSource.SYSTEM,
            summary=f"Progress {index}",
        )

    page = journal.events(after_sequence=0)

    assert [event.sequence for event in page.events] == [2, 3]
    assert page.replay_gap is True
    assert page.first_available_sequence == 2
