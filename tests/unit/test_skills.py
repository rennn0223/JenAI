from __future__ import annotations

import asyncio
import random

from jenai.config.store import build_minimal_config
from jenai.schemas import Location, Pose2D, RouteOutput
from jenai.tools.skills import (
    ExploreReport,
    ExploreSpec,
    ExploreStepResult,
    exploration_candidates,
    find_dock,
    parse_explore,
    parse_patrol,
    run_explore,
    run_patrol,
)


def _config():
    return build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )


def _locs() -> list[Location]:
    return [
        Location(name="A", frame_id="map", pose=Pose2D(x=0, y=0, yaw=0)),
        Location(name="B", frame_id="map", pose=Pose2D(x=1, y=1, yaw=0)),
        Location(
            name="Charger", aliases=["充電站"], tags=["dock"],
            frame_id="map", pose=Pose2D(x=9, y=9, yaw=0),
        ),
    ]


def test_parse_patrol_points_loops_photo() -> None:
    spec = parse_patrol("A, B x3 photo")
    assert spec is not None
    assert spec.points == ["A", "B"]
    assert spec.loops == 3
    assert spec.photo is True

    plain = parse_patrol("Engineering Building, Mechanical Hall")
    assert plain is not None
    assert plain.points == ["Engineering Building", "Mechanical Hall"]
    assert plain.loops == 1 and plain.photo is False

    assert parse_patrol("x3 photo") is None  # no points at all
    assert parse_patrol("A ×2") is not None and parse_patrol("A ×2").loops == 2


def test_run_patrol_loops_and_continues_after_failure() -> None:
    visited: list[str] = []

    async def navigate(action: dict) -> RouteOutput:
        name = action["goal"]["name"]
        visited.append(name)
        status = "failed" if name == "B" else "succeeded"
        return RouteOutput(
            input_text="", outgoing_action=action,
            execution_status=status, route_preview=f"{name} done",
        )

    spec = parse_patrol("A, B x2")
    report = asyncio.run(
        run_patrol(_config(), _locs(), spec, navigate=navigate)
    )

    assert visited == ["A", "B", "A", "B"]  # B's failure didn't stop the loop
    assert [r.status for r in report.results] == [
        "succeeded", "failed", "succeeded", "failed",
    ]
    assert "2/4" in report.summary
    assert report.results[2].loop == 2


def test_run_patrol_photo_observation_and_unknown_point() -> None:
    async def navigate(action: dict) -> RouteOutput:
        return RouteOutput(
            input_text="", outgoing_action=action,
            execution_status="succeeded", route_preview="arrived",
        )

    async def observe() -> str:
        return "a red box on the floor"

    spec = parse_patrol("A, nowhere photo")
    report = asyncio.run(
        run_patrol(_config(), _locs(), spec, navigate=navigate, observe=observe)
    )

    assert report.results[0].observation == "a red box on the floor"
    # Unknown waypoint: recorded as failed, never navigated, no observation.
    assert report.results[1].status == "failed"
    assert "unknown location" in report.results[1].detail
    assert report.results[1].observation is None


def test_find_dock_by_tag_then_name() -> None:
    assert find_dock(_locs()).name == "Charger"  # tags=["dock"] wins

    named = [Location(name="充電站", frame_id="map", pose=Pose2D(x=0, y=0, yaw=0))]
    assert find_dock(named).name == "充電站"

    assert find_dock([_locs()[0]]) is None  # plain location isn't a dock


def test_parse_patrol_preserves_token_like_names() -> None:
    # Interior words must never be consumed as flags (review finding).
    spec = parse_patrol("Photo Lab, B")
    assert spec.points == ["Photo Lab", "B"]
    assert spec.photo is False

    spec = parse_patrol("X2 Hall, Dock")
    assert spec.points == ["X2 Hall", "Dock"]
    assert spec.loops == 1

    # Tokens still work at the tail — trailing the last point or standalone.
    spec = parse_patrol("Photo Lab, B x2 photo")
    assert spec.points == ["Photo Lab", "B"]
    assert spec.loops == 2 and spec.photo is True

    spec = parse_patrol("A, B, x3, photo")
    assert spec.points == ["A", "B"]
    assert spec.loops == 3 and spec.photo is True


def test_parse_explore_defaults_options_and_bounds() -> None:
    default = parse_explore("")
    assert default == ExploreSpec()
    assert "fresh random order" in default.describe()

    spec = parse_explore("2m goals=6 failures=3 tag=room photo seed=42")
    assert spec is not None
    assert spec.duration_s == 120
    assert spec.max_goals == 6
    assert spec.max_failures == 3
    assert spec.tag == "room"
    assert spec.photo is True
    assert spec.seed == 42
    assert "same seed repeats" in spec.describe()

    assert parse_explore("9s") is None
    assert parse_explore("61m") is None
    assert parse_explore("goals=0") is None
    assert parse_explore("failures=11") is None
    assert parse_explore("somewhere") is None


def test_exploration_candidates_exclude_dock_and_blocked_tags() -> None:
    locations = _locs() + [
        Location(
            name="Lab",
            tags=["room"],
            frame_id="map",
            pose=Pose2D(x=2, y=2, yaw=0),
        ),
        Location(
            name="Hazard",
            tags=["room", "no-explore"],
            frame_id="map",
            pose=Pose2D(x=3, y=3, yaw=0),
        ),
    ]
    assert [item.name for item in exploration_candidates(locations)] == ["A", "B", "Lab"]
    assert [item.name for item in exploration_candidates(locations, "ROOM")] == ["Lab"]


def test_run_explore_visits_least_used_points_before_repeating() -> None:
    visited: list[str] = []

    async def navigate(action: dict) -> RouteOutput:
        name = action["goal"]["name"]
        visited.append(name)
        return RouteOutput(
            input_text="",
            outgoing_action=action,
            execution_status="succeeded",
            route_preview=f"arrived at {name}",
        )

    report = asyncio.run(
        run_explore(
            _config(),
            _locs(),
            ExploreSpec(duration_s=60, max_goals=4, seed=7),
            navigate=navigate,
            rng=random.Random(7),
        )
    )

    assert set(visited[:2]) == {"A", "B"}
    assert set(visited[2:]) == {"A", "B"}
    assert all(left != right for left, right in zip(visited, visited[1:], strict=False))
    assert report.stop_reason == "max_goals"
    assert report.success_count == 4


def test_run_explore_does_not_retry_failed_point_and_stops_at_failure_limit() -> None:
    visited: list[str] = []

    async def navigate(action: dict) -> RouteOutput:
        name = action["goal"]["name"]
        visited.append(name)
        return RouteOutput(
            input_text="",
            outgoing_action=action,
            execution_status="failed",
            route_preview=f"blocked at {name}",
        )

    report = asyncio.run(
        run_explore(
            _config(),
            _locs(),
            ExploreSpec(duration_s=60, max_goals=8, max_failures=2),
            navigate=navigate,
            rng=random.Random(2),
        )
    )

    assert len(visited) == 2
    assert len(set(visited)) == 2
    assert report.stop_reason == "failure_limit"
    assert report.success_count == 0


def test_explore_report_distinguishes_referred_from_failed() -> None:
    report = ExploreReport(
        spec=ExploreSpec(duration_s=60, max_goals=1),
        candidates=["A", "B"],
        results=[
            ExploreStepResult(
                1,
                "A",
                "referred",
                "Twin Gate refer — endpoint deviation",
            )
        ],
        stop_reason="max_goals",
    )

    assert "attempt limit reached" in report.summary
    assert "1 referred for review" in report.summary
    assert "navigation failed" not in report.summary


def test_run_explore_honors_soft_time_limit_and_photo_observation() -> None:
    times = iter([0.0, 0.0, 11.0])

    async def navigate(action: dict) -> RouteOutput:
        return RouteOutput(
            input_text="",
            outgoing_action=action,
            execution_status="succeeded",
            route_preview="arrived",
        )

    async def observe() -> str:
        return "clear corridor"

    report = asyncio.run(
        run_explore(
            _config(),
            _locs(),
            ExploreSpec(duration_s=10, max_goals=8, photo=True),
            navigate=navigate,
            observe=observe,
            now=lambda: next(times),
            rng=random.Random(1),
        )
    )

    assert len(report.results) == 1
    assert report.results[0].observation == "clear corridor"
    assert report.stop_reason == "duration"


def test_run_explore_time_limit_cancels_an_active_goal() -> None:
    canceled = False

    async def navigate(action: dict) -> RouteOutput:
        nonlocal canceled
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            canceled = True
            raise
        raise AssertionError("navigation should have timed out")

    report = asyncio.run(
        run_explore(
            _config(),
            _locs(),
            ExploreSpec(duration_s=10, max_goals=8),
            navigate=navigate,
            now=iter([0.0, 9.999]).__next__,
            rng=random.Random(1),
        )
    )

    assert canceled is True
    assert report.stop_reason == "duration"
    assert report.results[0].status == "failed"
    assert "canceled" in report.results[0].detail
