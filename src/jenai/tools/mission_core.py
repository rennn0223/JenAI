from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from jenai.adapters.locations import LocationNotFoundError, find_location
from jenai.config.models import AppConfig
from jenai.schemas import Location
from jenai.tools.drive_core import extract_drive_command
from jenai.tools.ros2_core import ros_drive
from jenai.tools.route_core import route_execute

TWIST = "geometry_msgs/msg/Twist"


@dataclass(frozen=True)
class MissionStep:
    kind: str  # "goto" (a named location) | "drive" (a plain-language motion)
    target: str


@dataclass
class StepResult:
    kind: str
    target: str
    status: str  # succeeded | failed | unavailable
    detail: str


@dataclass
class MissionReport:
    results: list[StepResult] = field(default_factory=list)

    @property
    def summary(self) -> str:
        ok = sum(r.status == "succeeded" for r in self.results)
        return f"Mission finished: {ok}/{len(self.results)} steps succeeded."


def parse_mission(text: str) -> list[MissionStep]:
    """Parse a comma-separated mission spec. Each segment is a location to visit,
    or ``drive <plain language>`` for a motion, or ``goto <location>`` explicitly.
    Example: ``kitchen, drive turn left, lobby``.
    """
    steps: list[MissionStep] = []
    for raw in text.split(","):
        seg = raw.strip()
        if not seg:
            continue
        lowered = seg.lower()
        if lowered.startswith("drive "):
            steps.append(MissionStep("drive", seg[6:].strip()))
        elif lowered.startswith("goto "):
            steps.append(MissionStep("goto", seg[5:].strip()))
        else:
            steps.append(MissionStep("goto", seg))
    return steps


async def run_mission(
    config: AppConfig,
    locations: list[Location],
    steps: list[MissionStep],
    *,
    on_step: Callable[[StepResult], Awaitable[None]] | None = None,
) -> MissionReport:
    """Run a mission as a deterministic sequence (no LLM loop, so it is reliable
    and testable). Each step reuses the existing, safety-clamped tools; results
    are collected into a report. `on_step` streams progress to the UI if given.
    """
    report = MissionReport()
    for step in steps:
        if step.kind == "goto":
            result = await _goto(config, locations, step.target)
        elif step.kind == "drive":
            result = await _drive(config, step.target)
        else:
            result = StepResult(step.kind, step.target, "failed", f"unknown step '{step.kind}'")
        report.results.append(result)
        if on_step is not None:
            await on_step(result)
    return report


async def _goto(config: AppConfig, locations: list[Location], target: str) -> StepResult:
    try:
        location = find_location(locations, target)
    except LocationNotFoundError as exc:
        hint = ", ".join(c.name for c in exc.candidates)
        detail = f"unknown location (near: {hint})" if hint else "unknown location"
        return StepResult("goto", target, "failed", detail)
    out = await route_execute(config, {"goal": location.model_dump(mode="json")})
    return StepResult("goto", location.name, out.execution_status, out.route_preview)


async def _drive(config: AppConfig, target: str) -> StepResult:
    intent = await extract_drive_command(config, target)
    if intent is None:
        return StepResult("drive", target, "failed", "could not understand the motion")
    out = await ros_drive(
        "/cmd_vel", TWIST, intent.to_payload(), duration_s=intent.duration_s
    )
    return StepResult("drive", intent.description, out.execution_status, out.result_message)
