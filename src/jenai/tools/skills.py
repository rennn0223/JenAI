"""Task-level skills: patrol, bounded exploration, return-to-dock.

Deterministic sequences built on the same navigate/vision primitives as
missions — no LLM in the loop, so they are reliable, testable, and
cancellable (a task cancel propagates straight into the live Nav2 goal).
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from jenai.config.models import AppConfig
from jenai.schemas import Location, RouteOutput
from jenai.tools.mission_core import resolve_and_navigate

_LOOPS_TOKEN = re.compile(r"^[x×](\d{1,3})$", re.IGNORECASE)
_PHOTO_TOKENS = {"photo", "photos", "拍照"}
_EXPLORE_DURATION_TOKEN = re.compile(
    r"^(\d+(?:\.\d+)?)(s|sec|secs|m|min|mins)$", re.IGNORECASE
)
_BLOCKED_EXPLORE_TAGS = frozenset(
    {"no-explore", "no_explore", "restricted", "forbidden", "hazard"}
)


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

    The loop token (x3/×3) and the photo flag are recognized ONLY at the
    tail of the command — trailing the last point or standing alone after
    the final comma. Interior words are never consumed: a waypoint literally
    named 'Photo Lab' or 'X2 Hall' stays intact.
    """
    segments = [seg.strip() for seg in text.split(",") if seg.strip()]
    loops = 1
    photo = False
    while segments:
        words = segments[-1].split()
        while words:
            match = _LOOPS_TOKEN.match(words[-1])
            if match:
                loops = max(1, int(match.group(1)))
                words.pop()
            elif words[-1].lower() in _PHOTO_TOKENS:
                photo = True
                words.pop()
            else:
                break
        if words:
            segments[-1] = " ".join(words)
            break
        segments.pop()  # segment was tokens only — keep stripping the new tail
    if not segments:
        return None
    return PatrolSpec(points=segments, loops=loops, photo=photo)


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
                name, status, detail = await resolve_and_navigate(
                    config, locations, point, navigate=navigate
                )
                result = PatrolStepResult(loop, name, status, detail)
            except Exception as exc:  # noqa: BLE001 — record and continue
                result = PatrolStepResult(loop, point, "failed", f"error: {exc}")
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


@dataclass(frozen=True)
class ExploreSpec:
    """Hard bounds for one known-location exploration run."""

    duration_s: float = 300.0
    max_goals: int = 8
    max_failures: int = 2
    tag: str | None = None
    photo: bool = False
    seed: int | None = None

    def __post_init__(self) -> None:
        if not 10.0 <= self.duration_s <= 3600.0:
            raise ValueError("duration must be between 10 seconds and 60 minutes")
        if not 1 <= self.max_goals <= 100:
            raise ValueError("goals must be between 1 and 100")
        if not 1 <= self.max_failures <= 10:
            raise ValueError("failures must be between 1 and 10")
        if self.tag is not None and not self.tag.strip():
            raise ValueError("tag cannot be empty")
        if self.seed is not None and not 0 <= self.seed <= 4_294_967_295:
            raise ValueError("seed must be between 0 and 4294967295")

    def describe(self) -> str:
        duration = (
            f"{self.duration_s / 60:g} min"
            if self.duration_s % 60 == 0
            else f"{self.duration_s:g} sec"
        )
        goal_word = "navigation goal" if self.max_goals == 1 else "navigation goals"
        parts = [duration, f"up to {self.max_goals} {goal_word}"]
        parts.append(f"stop after {self.max_failures} consecutive failures")
        if self.tag:
            parts.append(f"tag={self.tag}")
        if self.photo:
            parts.append("photo at each reached point")
        if self.seed is None:
            parts.append("fresh random order")
        else:
            parts.append(f"reproducible order (seed={self.seed}; same seed repeats)")
        return " · ".join(parts)


@dataclass
class ExploreStepResult:
    attempt: int
    point: str
    status: str  # succeeded | failed | unavailable | blocked | referred
    detail: str
    observation: str | None = None


@dataclass
class ExploreReport:
    spec: ExploreSpec
    candidates: list[str]
    results: list[ExploreStepResult] = field(default_factory=list)
    stop_reason: str = "max_goals"

    @property
    def success_count(self) -> int:
        return sum(result.status == "succeeded" for result in self.results)

    @property
    def completed_normally(self) -> bool:
        return self.stop_reason in {"duration", "max_goals"}

    @property
    def summary(self) -> str:
        reason = {
            "duration": "time limit reached",
            "max_goals": "navigation-goal attempt limit reached",
            "failure_limit": "consecutive failure limit reached",
            "no_candidates": "no reachable candidates remain",
        }.get(self.stop_reason, self.stop_reason)
        route = " → ".join(result.point for result in self.results)
        suffix = f" Route: {route}." if route else ""
        outcomes: list[str] = []
        for status, label in (
            ("referred", "referred for review"),
            ("blocked", "blocked by the safety boundary"),
            ("failed", "navigation failed"),
            ("unavailable", "backend unavailable"),
        ):
            count = sum(result.status == status for result in self.results)
            if count:
                outcomes.append(f"{count} {label}")
        outcome_suffix = f" Outcomes: {', '.join(outcomes)}." if outcomes else ""
        return (
            f"Exploration stopped ({reason}): {self.success_count}/"
            f"{len(self.results)} navigation goals reached.{outcome_suffix}{suffix}"
        )


def parse_explore(text: str) -> ExploreSpec | None:
    """Parse exploration bounds.

    Accepted tokens: ``5m``, ``goals=8``, ``failures=2``, ``tag=room``,
    ``photo``, and ``seed=42``. An empty string selects the safe defaults.
    Invalid or out-of-range input returns ``None``.
    """
    duration_s = 300.0
    max_goals = 8
    max_failures = 2
    tag: str | None = None
    photo = False
    seed: int | None = None

    try:
        for token in text.split():
            lowered = token.lower()
            duration_match = _EXPLORE_DURATION_TOKEN.fullmatch(lowered)
            if duration_match:
                duration_s = float(duration_match.group(1))
                if duration_match.group(2).lower().startswith("m"):
                    duration_s *= 60
            elif lowered in _PHOTO_TOKENS:
                photo = True
            elif lowered.startswith("goals="):
                max_goals = int(token.split("=", 1)[1])
            elif lowered.startswith("failures="):
                max_failures = int(token.split("=", 1)[1])
            elif lowered.startswith("tag="):
                tag = token.split("=", 1)[1].strip()
            elif lowered.startswith("seed="):
                seed = int(token.split("=", 1)[1])
            else:
                return None
        return ExploreSpec(
            duration_s=duration_s,
            max_goals=max_goals,
            max_failures=max_failures,
            tag=tag,
            photo=photo,
            seed=seed,
        )
    except (TypeError, ValueError):
        return None


def exploration_candidates(
    locations: list[Location], tag: str | None = None
) -> list[Location]:
    """Return saved locations eligible for bounded exploration.

    Docking points and locations tagged as restricted/no-explore/hazard are
    never selected. An optional tag narrows the candidate set.
    """
    dock = find_dock(locations)
    dock_id = dock.id if dock is not None else None
    required_tag = tag.strip().lower() if tag else None
    candidates: list[Location] = []
    for location in locations:
        tags = {value.strip().lower() for value in location.tags}
        if location.id == dock_id or tags & _BLOCKED_EXPLORE_TAGS:
            continue
        if required_tag is not None and required_tag not in tags:
            continue
        candidates.append(location)
    return candidates


async def run_explore(
    config: AppConfig,
    locations: list[Location],
    spec: ExploreSpec,
    *,
    navigate: Callable[[dict], Awaitable[RouteOutput]],
    on_step: Callable[[ExploreStepResult], Awaitable[None]] | None = None,
    observe: Callable[[], Awaitable[str | None]] | None = None,
    rng: random.Random | None = None,
    now: Callable[[], float] = time.monotonic,
) -> ExploreReport:
    """Explore saved locations with low repetition and explicit stop bounds.

    The selector chooses randomly among the least-visited eligible locations,
    avoids selecting the immediately previous point when possible, and does
    not retry a failed point during the same run. Navigation still goes
    through the injected Nav2 gateway, so cancellation and all existing
    physical/twin protections remain in force.
    """
    candidates = exploration_candidates(locations, spec.tag)
    report = ExploreReport(spec=spec, candidates=[item.name for item in candidates])
    if not candidates:
        report.stop_reason = "no_candidates"
        return report

    chooser = rng or random.Random(spec.seed)
    visits = {location.id: 0 for location in candidates}
    failed: set[str] = set()
    previous_id: str | None = None
    consecutive_failures = 0
    started = now()

    while len(report.results) < spec.max_goals:
        remaining_s = spec.duration_s - (now() - started)
        if remaining_s <= 0:
            report.stop_reason = "duration"
            break

        available = [item for item in candidates if item.id not in failed]
        if not available:
            report.stop_reason = "no_candidates"
            break
        least_visits = min(visits[item.id] for item in available)
        pool = [item for item in available if visits[item.id] == least_visits]
        alternatives = [item for item in pool if item.id != previous_id]
        location = chooser.choice(alternatives or pool)

        attempt = len(report.results) + 1
        try:
            async with asyncio.timeout(remaining_s):
                name, status, detail = await resolve_and_navigate(
                    config, candidates, location.name, navigate=navigate
                )
            result = ExploreStepResult(attempt, name, status, detail)
        except TimeoutError:
            result = ExploreStepResult(
                attempt,
                location.name,
                "failed",
                "exploration time limit reached; active navigation canceled",
            )
            report.results.append(result)
            if on_step is not None:
                await on_step(result)
            report.stop_reason = "duration"
            break
        except Exception as exc:  # noqa: BLE001 — record and stop by policy
            result = ExploreStepResult(
                attempt, location.name, "failed", f"error: {exc}"
            )

        if result.status == "succeeded":
            visits[location.id] += 1
            consecutive_failures = 0
            previous_id = location.id
            if spec.photo and observe is not None:
                try:
                    result.observation = await observe()
                except Exception as exc:  # noqa: BLE001 — best-effort camera
                    result.observation = f"(camera unavailable: {exc})"
        else:
            failed.add(location.id)
            consecutive_failures += 1

        report.results.append(result)
        if on_step is not None:
            await on_step(result)
        if consecutive_failures >= spec.max_failures:
            report.stop_reason = "failure_limit"
            break
    else:
        report.stop_reason = "max_goals"

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
