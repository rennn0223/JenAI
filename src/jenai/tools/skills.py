"""Task-level skills: patrol, return-to-dock.

Deterministic sequences built on the same navigate/vision primitives as
missions — no LLM in the loop, so they are reliable, testable, and
cancellable (a task cancel propagates straight into the live Nav2 goal).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from jenai.adapters.locations import LocationNotFoundError, find_location
from jenai.config.models import AppConfig
from jenai.schemas import Location, RouteOutput

_LOOPS_TOKEN = re.compile(r"^[x×](\d{1,3})$", re.IGNORECASE)
_PHOTO_TOKENS = {"photo", "photos", "拍照"}


@dataclass(frozen=True)
class PatrolSpec:
    points: list[str]
    loops: int = 1
    photo: bool = False

    def describe(self) -> str:
        route = " → ".join(self.points)
        parts = [route]
        if self.loops > 1:
            parts.append(f"×{self.loops} loops")
        if self.photo:
            parts.append("photo at each point")
        return " · ".join(parts)


@dataclass
class PatrolStepResult:
    loop: int  # 1-based
    point: str
    status: str  # succeeded | failed | unavailable
    detail: str
    observation: str | None = None  # camera + VLM summary, when photo is on


@dataclass
class PatrolReport:
    spec: PatrolSpec
    results: list[PatrolStepResult] = field(default_factory=list)

    @property
    def summary(self) -> str:
        ok = sum(r.status == "succeeded" for r in self.results)
        return f"Patrol finished: {ok}/{len(self.results)} waypoints reached."


def parse_patrol(text: str) -> PatrolSpec | None:
    """Parse '/patrol A, B, C x3 photo' → points, loop count, photo flag.

    The loop token (x3/×3) and the photo flag may trail the last point or
    stand alone after a comma; everything else is a location name.
    """
    points: list[str] = []
    loops = 1
    photo = False
    for raw in text.split(","):
        words = raw.strip().split()
        kept: list[str] = []
        for word in words:
            match = _LOOPS_TOKEN.match(word)
            if match:
                loops = max(1, int(match.group(1)))
            elif word.lower() in _PHOTO_TOKENS:
                photo = True
            else:
                kept.append(word)
        if kept:
            points.append(" ".join(kept))
    if not points:
        return None
    return PatrolSpec(points=points, loops=loops, photo=photo)


async def run_patrol(
    config: AppConfig,
    locations: list[Location],
    spec: PatrolSpec,
    *,
    navigate: Callable[[dict], Awaitable[RouteOutput]],
    on_step: Callable[[PatrolStepResult], Awaitable[None]] | None = None,
    observe: Callable[[], Awaitable[str | None]] | None = None,
) -> PatrolReport:
    """Visit every point, `loops` times over. One failed waypoint is recorded
    and the patrol carries on — a blocked corridor must not strand the robot
    mid-route silently. `observe` (camera → VLM summary) runs per waypoint
    when the spec asks for photos; its failures degrade to a note, honestly,
    never to a fake observation.
    """
    report = PatrolReport(spec=spec)
    for loop in range(1, spec.loops + 1):
        for point in spec.points:
            try:
                location = find_location(locations, point)
            except LocationNotFoundError as exc:
                hint = ", ".join(c.name for c in exc.candidates)
                detail = f"unknown location (near: {hint})" if hint else "unknown location"
                result = PatrolStepResult(loop, point, "failed", detail)
            else:
                try:
                    out = await navigate({"goal": location.model_dump(mode="json")})
                    result = PatrolStepResult(
                        loop, location.name, out.execution_status, out.route_preview
                    )
                except Exception as exc:  # noqa: BLE001 — record and continue
                    result = PatrolStepResult(loop, location.name, "failed", f"error: {exc}")
            if (
                spec.photo
                and observe is not None
                and result.status == "succeeded"
            ):
                try:
                    result.observation = await observe()
                except Exception as exc:  # noqa: BLE001 — observation is best-effort
                    result.observation = f"(camera unavailable: {exc})"
            report.results.append(result)
            if on_step is not None:
                await on_step(result)
    return report


_DOCK_TAG = "dock"
_DOCK_NAMES = ("dock", "充電站", "充电站", "charging station")


def find_dock(locations: list[Location]) -> Location | None:
    """The docking location: tagged 'dock', or named/aliased like one."""
    for location in locations:
        if any(tag.strip().lower() == _DOCK_TAG for tag in location.tags):
            return location
    for location in locations:
        names = (location.name, *location.aliases)
        if any(name.strip().lower() in _DOCK_NAMES for name in names):
            return location
    return None
