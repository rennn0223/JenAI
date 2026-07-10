from __future__ import annotations

import asyncio

from jenai.config.store import build_minimal_config
from jenai.schemas import Location, Pose2D
from jenai.tools import mission_core
from jenai.tools.mission_core import parse_mission, run_mission


def _config():
    return build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )


def test_parse_mission_segments() -> None:
    steps = parse_mission("kitchen, drive turn left, goto lobby")
    assert [(s.kind, s.target) for s in steps] == [
        ("goto", "kitchen"),
        ("drive", "turn left"),
        ("goto", "lobby"),
    ]


def test_run_mission_goto_and_drive(monkeypatch) -> None:
    from jenai.schemas import RosPubOutput, RouteOutput
    from jenai.tools.drive_core import DriveIntent

    async def fake_navigation(config, outgoing):
        return RouteOutput(input_text="", execution_status="succeeded", route_preview="arrived")

    async def fake_extract(config, text):
        return DriveIntent(0.0, 0.6, 2.0, "turn left")

    async def fake_drive(topic, mt, payload, *, duration_s=1.0, **limits):
        return RosPubOutput(
            topic=topic, message_type=mt, execution_status="succeeded", result_message="drove"
        )

    monkeypatch.setattr(mission_core, "execute_navigation", fake_navigation)
    monkeypatch.setattr(mission_core, "extract_drive_command", fake_extract)
    monkeypatch.setattr(mission_core, "ros_drive", fake_drive)

    locs = [Location(name="kitchen", frame_id="map", pose=Pose2D(x=1, y=1, yaw=0))]
    seen: list = []

    async def on_step(r):
        seen.append(r.status)

    report = asyncio.run(
        run_mission(_config(), locs, parse_mission("kitchen, drive turn left"), on_step=on_step)
    )
    assert [r.kind for r in report.results] == ["goto", "drive"]
    assert all(r.status == "succeeded" for r in report.results)
    assert "2/2" in report.summary
    assert seen == ["succeeded", "succeeded"]  # progress streamed


def test_run_mission_unknown_location_fails_gracefully() -> None:
    report = asyncio.run(run_mission(_config(), [], parse_mission("nowhere")))
    assert report.results[0].status == "failed"
    assert "0/1" in report.summary


def test_run_mission_continues_after_a_raising_step(monkeypatch) -> None:
    # A raising drive step must be recorded as failed and NOT abort the mission,
    # so a later location step still runs and the report stays complete.
    from jenai.schemas import RouteOutput
    from jenai.tools.drive_core import DriveIntent

    async def fake_extract(config, text):
        return DriveIntent(0.5, 0.0, 1.0, "forward")

    async def boom_drive(*a, **k):
        raise RuntimeError("ros2 not available")

    async def fake_navigation(config, outgoing):
        return RouteOutput(input_text="", execution_status="succeeded", route_preview="arrived")

    monkeypatch.setattr(mission_core, "extract_drive_command", fake_extract)
    monkeypatch.setattr(mission_core, "ros_drive", boom_drive)
    monkeypatch.setattr(mission_core, "execute_navigation", fake_navigation)

    locs = [Location(name="lobby", frame_id="map", pose=Pose2D(x=1, y=1, yaw=0))]
    report = asyncio.run(run_mission(_config(), locs, parse_mission("drive forward, lobby")))

    assert [r.kind for r in report.results] == ["drive", "goto"]
    assert report.results[0].status == "failed" and "ros2 not available" in report.results[0].detail
    assert report.results[1].status == "succeeded"
