"""Transport-neutral contracts behind the Robot Runtime Authority."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jenai.runtime.immutable_json import FrozenJsonObject, ImmutableJsonObject


class RuntimeModel(BaseModel):
    """Frozen Runtime value object; JSON fields are detached and recursively immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)


def _required_text(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("value must not be blank")
    return stripped


class TypedCapabilityStep(RuntimeModel):
    """One platform-neutral step selected from the Capability Registry."""

    capability_id: str
    input_schema_version: str
    input: ImmutableJsonObject

    _capability_id_required = field_validator("capability_id")(_required_text)
    _schema_version_required = field_validator("input_schema_version")(_required_text)


class AuthorityContext(RuntimeModel):
    """Identity shared by work owned by one Runtime authority generation."""

    runtime_id: str
    boot_id: str
    authority_generation: int = Field(ge=1)
    safety_epoch: int = Field(ge=0)

    _required_ids = field_validator("runtime_id", "boot_id")(_required_text)


class ExecutionContext(RuntimeModel):
    """Opaque fencing identity supplied by the owning Runtime Authority."""

    authority: AuthorityContext
    fencing_token: int = Field(ge=1)
    robot_id: str
    task_id: str
    command_id: str

    _required_ids = field_validator("robot_id", "task_id", "command_id")(_required_text)


class PreparedCapabilityStep(RuntimeModel):
    """Prepared step cryptographically bound to its execution context."""

    step: TypedCapabilityStep
    context: ExecutionContext
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SnapshotRequest(RuntimeModel):
    robot_id: str

    _robot_id_required = field_validator("robot_id")(_required_text)


class ObservationContext(RuntimeModel):
    authority: AuthorityContext


class EvidenceTimestampStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class TransportSecurity(StrEnum):
    AUTHENTICATED = "authenticated"
    UNAUTHENTICATED = "unauthenticated"
    UNKNOWN = "unknown"


class SourceAssurance(StrEnum):
    VENDOR_TELEMETRY = "vendor_telemetry"
    RUNTIME_OBSERVED = "runtime_observed"
    OPERATOR_OBSERVED = "operator_observed"
    DERIVED = "derived"
    UNKNOWN = "unknown"


class EvidenceContentDigest(RuntimeModel):
    algorithm: str
    value: str

    _required_text_fields = field_validator("algorithm", "value")(_required_text)


class EvidenceSourceAttestation(RuntimeModel):
    kind: str
    reference: str

    _required_text_fields = field_validator("kind", "reference")(_required_text)


class ExecutorEvidence(RuntimeModel):
    """Adapter-known provenance and payload returned for Authority evaluation."""

    kind: str
    source: str
    source_observed_at: datetime | None = None
    source_timestamp_status: EvidenceTimestampStatus
    content_digest: EvidenceContentDigest | None = None
    transport_security: TransportSecurity
    source_assurance: SourceAssurance
    source_attestation: EvidenceSourceAttestation | None = None
    payload_schema_version: str
    payload: ImmutableJsonObject
    limitations: tuple[str, ...] = ()

    _required_text_fields = field_validator("kind", "source", "payload_schema_version")(
        _required_text
    )

    @model_validator(mode="after")
    def timestamp_contract_is_explicit(self) -> ExecutorEvidence:
        has_timestamp = self.source_observed_at is not None
        status_available = self.source_timestamp_status == EvidenceTimestampStatus.AVAILABLE
        if has_timestamp != status_available:
            raise ValueError("source timestamp status does not match source_observed_at")
        return self


class ObservationSnapshot(RuntimeModel):
    """Read-only robot state expressed only as source-attributed Evidence."""

    robot_id: str
    evidence: tuple[ExecutorEvidence, ...]
    limitations: tuple[str, ...] = ()

    _robot_id_required = field_validator("robot_id")(_required_text)


class ExecutorEvent(RuntimeModel):
    """Executor-local progress fact; the Authority assigns public sequencing."""

    kind: str
    data: ImmutableJsonObject = Field(default_factory=lambda: FrozenJsonObject({}))
    evidence: tuple[ExecutorEvidence, ...] = ()

    _kind_required = field_validator("kind")(_required_text)


class ExecutionDisposition(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CapabilityExecutionReport(RuntimeModel):
    """Adapter execution fact; only the Authority may derive a Task Outcome."""

    disposition: ExecutionDisposition
    summary: str
    evidence: tuple[ExecutorEvidence, ...] = ()

    _summary_required = field_validator("summary")(_required_text)


class CancelContext(RuntimeModel):
    """Task-scoped cancellation identity supplied by the Runtime Authority."""

    authority: AuthorityContext
    robot_id: str
    task_id: str
    command_id: str
    reason: str

    _required_ids = field_validator("robot_id", "task_id", "command_id", "reason")(_required_text)


class ExecutorCancelResult(RuntimeModel):
    request_accepted: bool
    cancel_requested: bool
    cancel_acknowledged: bool | None = None
    limitations: tuple[str, ...] = ()


class StopTrigger(StrEnum):
    OPERATOR = "operator"
    POLICY = "policy"
    WATCHDOG = "watchdog"
    RUNTIME_SHUTDOWN = "runtime_shutdown"


class StopContext(RuntimeModel):
    authority: AuthorityContext
    robot_id: str
    stop_id: str
    trigger: StopTrigger

    _required_ids = field_validator("robot_id", "stop_id")(_required_text)

    @model_validator(mode="after")
    def stop_requires_advanced_epoch(self) -> StopContext:
        if self.authority.safety_epoch < 1:
            raise ValueError("STOP requires an already advanced safety epoch")
        return self


class ExecutorStopResult(RuntimeModel):
    request_accepted: bool
    cancel_requested: bool
    cancel_acknowledged: bool | None = None
    zero_velocity_command_published: bool | None = None
    limitations: tuple[str, ...] = ()


def capability_binding_sha256(
    step: TypedCapabilityStep,
    context: ExecutionContext,
) -> str:
    """Return the canonical binding between prepared work and supplied context."""

    payload = {
        "context": context.model_dump(mode="json"),
        "step": step.model_dump(mode="json"),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()
