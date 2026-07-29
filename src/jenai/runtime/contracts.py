"""Versioned contracts for the high-level Robot Runtime interface."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from jenai.schemas.models import JenAIModel, new_id, utc_now

RUNTIME_SCHEMA_VERSION = 1


class RuntimeSource(StrEnum):
    """Trusted caller identity assigned by a JenAI interaction adapter."""

    TUI = "tui"
    WEBUI = "webui"
    MCP = "mcp"
    DAEMON = "daemon"
    CLI = "cli"
    SYSTEM = "system"


class RuntimeState(StrEnum):
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    UNAVAILABLE = "unavailable"


class RuntimeEventKind(StrEnum):
    RUNTIME_STARTED = "runtime_started"
    RUNTIME_STATE_CHANGED = "runtime_state_changed"
    COMMAND_ACCEPTED = "command_accepted"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    ACTION_STARTED = "action_started"
    ACTION_PROGRESS = "action_progress"
    EVIDENCE_OBSERVED = "evidence_observed"
    STOP_REQUESTED = "stop_requested"
    STOP_EVIDENCE_UPDATED = "stop_evidence_updated"
    TASK_FINISHED = "task_finished"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    SAFETY_EPOCH_ADVANCED = "safety_epoch_advanced"


class RuntimeCommandRequest(JenAIModel):
    """One high-level capability request entering the runtime authority."""

    schema_version: int = RUNTIME_SCHEMA_VERSION
    request_id: str = Field(default_factory=lambda: new_id("request"))
    capability_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    source: RuntimeSource
    requested_safety_epoch: int = Field(ge=0)
    requested_at: datetime = Field(default_factory=utc_now)

    @field_validator("capability_id")
    @classmethod
    def capability_id_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("capability_id must not be blank")
        return normalized


class RuntimeEvent(JenAIModel):
    """A replayable, monotonically sequenced observation from the authority."""

    schema_version: int = RUNTIME_SCHEMA_VERSION
    event_id: str = Field(default_factory=lambda: new_id("event"))
    sequence: int = Field(ge=1)
    safety_epoch: int = Field(ge=0)
    kind: RuntimeEventKind
    source: RuntimeSource
    occurred_at: datetime = Field(default_factory=utc_now)
    run_id: str | None = None
    command_id: str | None = None
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("summary")
    @classmethod
    def summary_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("summary must not be blank")
        return normalized


class RuntimeEventPage(JenAIModel):
    """One cursor page returned by polling or an SSE replay handshake."""

    schema_version: int = RUNTIME_SCHEMA_VERSION
    after_sequence: int = Field(ge=0)
    first_available_sequence: int | None = Field(default=None, ge=1)
    last_sequence: int = Field(ge=0)
    replay_gap: bool = False
    events: list[RuntimeEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_cursor_order(self) -> Self:
        sequences = [event.sequence for event in self.events]
        if sequences != sorted(set(sequences)):
            raise ValueError("events must have unique, monotonically increasing sequences")
        if any(sequence <= self.after_sequence for sequence in sequences):
            raise ValueError("events must be newer than after_sequence")
        if sequences and sequences[-1] > self.last_sequence:
            raise ValueError("last_sequence cannot precede the newest event")
        if self.first_available_sequence is not None and self.first_available_sequence > (
            self.last_sequence or self.first_available_sequence
        ):
            raise ValueError("first_available_sequence cannot follow last_sequence")
        return self


class RuntimeSnapshot(JenAIModel):
    """Small read model shared by interaction-specific projections."""

    schema_version: int = RUNTIME_SCHEMA_VERSION
    runtime_id: str
    state: RuntimeState
    safety_epoch: int = Field(ge=0)
    last_sequence: int = Field(ge=0)
    observed_at: datetime = Field(default_factory=utc_now)
    active_run_id: str | None = None
    active_command_id: str | None = None
    pending_approval_ids: list[str] = Field(default_factory=list)
    message: str | None = None
