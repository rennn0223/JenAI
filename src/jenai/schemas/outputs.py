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


class RosTopicInfoOutput(JenAIModel):
    name: str
    message_type: str = ""
    publisher_count: int = 0
    subscriber_count: int = 0
    publishers: list[str] = Field(default_factory=list)
    subscribers: list[str] = Field(default_factory=list)
    summary: str = ""
    candidates: list[str] = Field(default_factory=list)


class RosEchoOutput(JenAIModel):
    topic: str
    mode: Literal["stream", "snapshot"] = "snapshot"
    messages: list[dict] = Field(default_factory=list)
    summary: str = ""


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


class VisionOutput(JenAIModel):
    source: str
    summary: str = ""
    objects: list[str] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)
    relevance_to_task: str = ""
    next_action_suggestions: list[str] = Field(default_factory=list)


class SceneAnalysis(JenAIModel):
    """One structured VLM read of a camera frame (the PerceptionLoop unit).

    `requires_approval` defaults True: an analysis that doesn't explicitly
    say an action is safe to suggest must stay behind the human gate.
    """

    scene_context: str = ""
    objects: list[str] = Field(default_factory=list)
    affordances: list[str] = Field(default_factory=list)  # e.g. "path_clear", "door_open"
    suggested_action: str = ""
    confidence: float = 0.0  # 0.0–1.0
    requires_approval: bool = True
    ts: float = 0.0  # capture wall-clock time


class GateCriterion(JenAIModel):
    """One Twin Gate check (G1 collision, G2 timeout, G3 forbidden zone,
    G4 endpoint deviation, G5 Nav2 failure)."""

    criterion_id: Literal["G1", "G2", "G3", "G4", "G5"]
    name: str
    status: Literal["pass", "fail", "skipped"]
    detail: str = ""


class GateReport(JenAIModel):
    """Outcome of rehearsing one goal in the digital twin.

    `block` = a hard safety criterion failed (collision, forbidden zone) — the
    real robot must not move. `refer` = the rehearsal was infeasible or
    inconclusive (timeout, Nav2 failure, endpoint drift, twin unreachable) —
    a human decides; autonomous callers must treat refer as block.
    """

    verdict: Literal["pass", "block", "refer"]
    reason: str = ""
    criteria: list[GateCriterion] = Field(default_factory=list)
    twin_elapsed_s: float = 0.0

    @property
    def summary(self) -> str:
        failed = ", ".join(
            f"{c.criterion_id} {c.name}: {c.detail}" for c in self.criteria if c.status == "fail"
        )
        base = f"Twin Gate {self.verdict}" + (f" — {self.reason}" if self.reason else "")
        return f"{base} ({failed})" if failed else base


class ShellOutput(JenAIModel):
    command: str
    working_directory: str = ""
    risk_summary: str = ""
    approval_status: str = "pending"
    exit_code: int = 0
    stdout_summary: str = ""
    stderr_summary: str = ""


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
