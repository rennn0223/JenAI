from __future__ import annotations

import asyncio

from jenai.config.store import build_minimal_config
from jenai.schemas import Location, Pose2D, RouteOutput
from jenai.tools.skills import find_dock, parse_patrol, run_patrol


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
