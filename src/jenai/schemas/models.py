"""Core pydantic models (extra=forbid): Location, RunRecord, SceneAnalysis, …"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class RunStatus(StrEnum):
    IDLE = "idle"
    UNDERSTANDING = "understanding"
    PLANNING = "planning"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class SessionMode(StrEnum):
    CHAT = "chat"
    ASSIST = "assist"
    OPERATE = "operate"


class Theme(StrEnum):
    SYSTEM = "system"
    DARK = "dark"
    LIGHT = "light"


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class ToolCallCategory(StrEnum):
    ROS2 = "ros2"
    VISION = "vision"
    SHELL = "shell"
    PROVIDER = "provider"
    ROUTE = "route"
    LOC = "loc"


class ToolCallStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"


class RiskLevel(StrEnum):
    P0 = "p0"
    P1 = "p1"
    P2 = "p2"


class EffectScope(StrEnum):
    NONE = "none"
    READ = "read"
    LOCAL_WRITE = "local_write"
    SIM_CONTROL = "sim_control"
    HOST_COMMAND = "host_command"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ErrorType(StrEnum):
    CONFIG_ERROR = "config_error"
    ENV_ERROR = "env_error"
    VALIDATION_ERROR = "validation_error"
    TOOL_ERROR = "tool_error"
    APPROVAL_REJECTED = "approval_rejected"
    MODEL_ERROR = "model_error"


class DoctorStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class JenAIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class JenAIError(JenAIModel):
    error_type: ErrorType
    message: str
    details: dict[str, Any] | None = None
    fix_suggestion: str | None = None


class ModelBindings(JenAIModel):
    chat: str
    plan: str
    vision: str
    route: str
    default: str

    @field_validator("chat", "plan", "vision", "route", "default")
    @classmethod
    def model_name_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("model binding must not be blank")
        return stripped


class RosContext(JenAIModel):
    available: bool = False
    domain_id: int | None = None
    namespace: str | None = None
    known_topics: list[str] = Field(default_factory=list)
    last_checked: datetime | None = None


class Pose2D(JenAIModel):
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    yaw: float = Field(allow_inf_nan=False)


class Location(JenAIModel):
    id: str = Field(default_factory=lambda: new_id("loc"))
    name: str
    aliases: list[str] = Field(default_factory=list)
    frame_id: str = "map"
    pose: Pose2D
    tags: list[str] = Field(default_factory=list)
    description: str | None = None

    @field_validator("name", "frame_id")
    @classmethod
    def required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped


class PlanStep(JenAIModel):
    step_id: str = Field(default_factory=lambda: new_id("step"))
    title: str
    description: str
    reason: str
    candidate_tools: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    status: PlanStepStatus = PlanStepStatus.PENDING


class ToolCallRecord(JenAIModel):
    tool_call_id: str = Field(default_factory=lambda: new_id("tool"))
    tool_name: str
    category: ToolCallCategory
    input_summary: str
    raw_input: dict[str, Any] = Field(default_factory=dict)
    status: ToolCallStatus = ToolCallStatus.QUEUED
    risk_level: RiskLevel = RiskLevel.P0
    effect_scope: EffectScope = EffectScope.NONE
    started_at: datetime | None = None
    ended_at: datetime | None = None
    output_summary: str | None = None
    raw_output: dict[str, Any] | None = None
    error: JenAIError | None = None


class ApprovalRequest(JenAIModel):
    approval_id: str = Field(default_factory=lambda: new_id("approval"))
    run_id: str
    tool_call_id: str
    tool_name: str = ""
    title: str
    summary: str
    raw_action: str
    risk_level: RiskLevel
    effect_scope: EffectScope
    justification: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None


class RunRecord(JenAIModel):
    run_id: str = Field(default_factory=lambda: new_id("run"))
    session_id: str
    user_input: str
    status: RunStatus = RunStatus.IDLE
    task_summary: str | None = None
    plan_steps: list[PlanStep] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    interruptions: list[ApprovalRequest] = Field(default_factory=list)
    final_output: str | None = None
    error: JenAIError | None = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None


class SessionState(JenAIModel):
    session_id: str = Field(default_factory=lambda: new_id("session"))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    mode: SessionMode = SessionMode.ASSIST
    theme: Theme = Theme.SYSTEM
    provider_profile: str
    model_bindings: ModelBindings
    working_directory: str
    ros_context: RosContext = Field(default_factory=RosContext)
    current_run_id: str | None = None
    history_cursor: int = 0
    input_history: list[str] = Field(default_factory=list)


class DoctorCheckItem(JenAIModel):
    section: str
    check_name: str
    status: DoctorStatus
    message: str
    fix_suggestion: str | None = None


class DoctorResult(JenAIModel):
    overall: DoctorStatus
    items: list[DoctorCheckItem]
    checked_at: datetime = Field(default_factory=utc_now)

    @classmethod
    def from_items(cls, items: list[DoctorCheckItem]) -> DoctorResult:
        statuses = {item.status for item in items}
        if DoctorStatus.FAIL in statuses:
            overall = DoctorStatus.FAIL
        elif DoctorStatus.WARN in statuses:
            overall = DoctorStatus.WARN
        else:
            overall = DoctorStatus.PASS
        return cls(overall=overall, items=items)
