"""Deterministic multi-stop mission stepping + resolve_and_navigate (shared)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from jenai.adapters.locations import LocationNotFoundError, find_location
from jenai.config.models import AppConfig
from jenai.schemas import Location, RouteOutput
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
    navigate: Callable[[dict], Awaitable[RouteOutput]] | None = None,
) -> MissionReport:
    """Run a mission as a deterministic sequence (no LLM loop, so it is reliable
    and testable). Each step reuses the existing, safety-clamped tools; results
    are collected into a report. `on_step` streams progress to the UI if given.

    `navigate` overrides how goto steps are executed (the TUI injects the live
    rclpy-bridge navigator when Nav2 + the bridge are available); the default
    stays the blocking route_execute adapter path.
    """
    report = MissionReport()
    for step in steps:
        try:
            if step.kind == "goto":
                result = await _goto(config, locations, step.target, navigate=navigate)
            elif step.kind == "drive":
                result = await _drive(config, step.target)
            else:
                result = StepResult(step.kind, step.target, "failed", f"unknown step '{step.kind}'")
        except Exception as exc:
            # One failing step (a ROS error, a provider hiccup) must not abort the
            # whole mission — record it and carry on so the report stays complete.
            result = StepResult(step.kind, step.target, "failed", f"error: {exc}")
        report.results.append(result)
        if on_step is not None:
            await on_step(result)
    return report


async def resolve_and_navigate(
    config: AppConfig,
    locations: list[Location],
    target: str,
    *,
    navigate: Callable[[dict], Awaitable[RouteOutput]] | None = None,
) -> tuple[str, str, str]:
    """Resolve a location name and navigate to it: (name, status, detail).

    The one place the goal-dict schema and the not-found UX live — missions
    and patrols both build on this, so they can never drift apart.
    """
    try:
        location = find_location(locations, target)
    except LocationNotFoundError as exc:
        hint = ", ".join(c.name for c in exc.candidates)
        detail = f"unknown location (near: {hint})" if hint else "unknown location"
        return target, "failed", detail
    action = {"goal": location.model_dump(mode="json")}
    out = await navigate(action) if navigate is not None else await route_execute(config, action)
    return location.name, out.execution_status, out.route_preview


async def _goto(
    config: AppConfig,
    locations: list[Location],
    target: str,
    *,
    navigate: Callable[[dict], Awaitable[RouteOutput]] | None = None,
) -> StepResult:
    name, status, detail = await resolve_and_navigate(
        config, locations, target, navigate=navigate
    )
    return StepResult("goto", name, status, detail)


async def _drive(config: AppConfig, target: str) -> StepResult:
    intent = await extract_drive_command(config, target)
    if intent is None:
        return StepResult("drive", target, "failed", "could not understand the motion")
    out = await ros_drive(
        config.vehicle.cmd_vel_topic,
        TWIST,
        intent.to_payload(),
        duration_s=intent.duration_s,
        max_linear=config.vehicle.max_linear,
        max_angular=config.vehicle.max_angular,
    )
    return StepResult("drive", intent.description, out.execution_status, out.result_message)
