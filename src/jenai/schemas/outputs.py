from __future__ import annotations

from typing import Literal

from pydantic import Field

from jenai.schemas.models import ApprovalRequest as _ApprovalRequest
from jenai.schemas.models import (
    JenAIModel,
    Location,
    PlanStep,
    Pose2D,
    RunStatus,
    ToolCallRecord,
)


class TopicItem(JenAIModel):
    name: str
    kind_hint: Literal["control", "sensor", "debug", "unknown"] = "unknown"


class RosTopicsOutput(JenAIModel):
    topics: list[TopicItem] = Field(default_factory=list)


class FieldSummary(JenAIModel):
    field_name: str
    field_type: str
    description: str = ""


class RosSchemaOutput(JenAIModel):
    topic: str
    message_type: str
    raw_interface: str
    field_summary: list[FieldSummary] = Field(default_factory=list)
    example_payload: dict = Field(default_factory=dict)


class RosPubOutput(JenAIModel):
    topic: str
    message_type: str
    payload_preview: dict = Field(default_factory=dict)
    approval_status: str = "pending"
    execution_status: str = "not_executed"
    result_message: str = ""


class RouteOutput(JenAIModel):
    input_text: str
    resolved_start: Location | None = None
    resolved_goal: Location | None = None
    candidate_matches: list[Location] = Field(default_factory=list)
    route_preview: str = ""
    outgoing_action: dict = Field(default_factory=dict)
    approval_status: str = "pending"
    execution_status: str = "not_executed"


class LocationSummary(JenAIModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class LocListOutput(JenAIModel):
    locations: list[LocationSummary] = Field(default_factory=list)


class LocShowOutput(JenAIModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    frame_id: str
    pose: Pose2D
    tags: list[str] = Field(default_factory=list)
    description: str | None = None


class PlanOutput(JenAIModel):
    task_summary: str
    assumptions: list[str] = Field(default_factory=list)
    plan_steps: list[PlanStep] = Field(default_factory=list)
    candidate_tools: list[str] = Field(default_factory=list)
    approval_checkpoints: list[str] = Field(default_factory=list)
    expected_output: str = ""


class RunOutput(JenAIModel):
    run_id: str
    status: RunStatus
    current_step: str = ""
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    interruptions: list[_ApprovalRequest] = Field(default_factory=list)
    final_output: str = ""


class CommandGroup(JenAIModel):
    name: str
    commands: list[str] = Field(default_factory=list)


class KeyboardShortcut(JenAIModel):
    key: str
    action: str


class HelpOutput(JenAIModel):
    title: str
    summary: str
    command_groups: list[CommandGroup] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    keyboard_shortcuts: list[KeyboardShortcut] = Field(default_factory=list)
