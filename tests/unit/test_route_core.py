from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from jenai.config.store import build_minimal_config
from jenai.schemas import Location, Pose2D
from jenai.tools import route_core


def _config():
    return build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="gpt-test",
        api_key_env="",
    )


def _locations() -> list[Location]:
    return [
        Location(name="Engineering Building", frame_id="map", pose=Pose2D(x=0, y=0, yaw=0)),
        Location(name="Mechanical Hall", frame_id="map", pose=Pose2D(x=10, y=0, yaw=0)),
    ]


def test_route_preview_resolves_via_regex_chinese() -> None:
    output = asyncio.run(
        route_core.route_preview(
            _config(), _locations(), "從Engineering Building到Mechanical Hall"
        )
    )
    assert output.resolved_start.name == "Engineering Building"
    assert output.resolved_goal.name == "Mechanical Hall"
    assert output.outgoing_action["start"]["name"] == "Engineering Building"


def test_route_preview_resolves_via_regex_english() -> None:
    text = "from Engineering Building to Mechanical Hall"
    output = asyncio.run(route_core.route_preview(_config(), _locations(), text))
    assert output.resolved_start.name == "Engineering Building"
    assert output.resolved_goal.name == "Mechanical Hall"


def test_route_preview_missing_start_or_goal_asks_for_clarification(monkeypatch) -> None:
    async def fake_ask_json(config, prompt, *, binding="chat"):
        return None

    monkeypatch.setattr("jenai.tools.route_core.ask_json", fake_ask_json)

    output = asyncio.run(route_core.route_preview(_config(), _locations(), "take me somewhere"))

    assert output.outgoing_action == {}
    assert "Could not determine" in output.route_preview


def test_route_preview_unresolvable_goal_lists_candidates() -> None:
    # An unresolvable GOAL blocks the route and offers close matches.
    text = "from Engineering Building to Mechnical Hll"
    output = asyncio.run(route_core.route_preview(_config(), _locations(), text))
    assert output.outgoing_action == {}
    assert output.candidate_matches
    assert output.candidate_matches[0].name == "Mechanical Hall"


def test_route_preview_unresolvable_start_still_navigates_to_goal() -> None:
    # Nav2 navigates from the robot's current pose, so a start we can't resolve
    # must not block a resolvable goal — it is simply omitted, not sent.
    text = "from Nowhere Place to Mechanical Hall"
    output = asyncio.run(route_core.route_preview(_config(), _locations(), text))
    assert output.resolved_goal.name == "Mechanical Hall"
    assert output.outgoing_action["goal"]["name"] == "Mechanical Hall"
    assert "start" not in output.outgoing_action


def test_route_execute_reports_no_backend_honestly() -> None:
    output = asyncio.run(route_core.route_execute(_config(), {"start": "a", "goal": "b"}))
    # No navigation backend is wired: report "unavailable", never fake success.
    assert output.execution_status == "unavailable"
    assert "not sent" in output.route_preview.lower()


def test_route_preview_goal_only_chinese_goes_from_current_position() -> None:
    # 「去X」/「到X」 must resolve without a provider: goal-only regex fast path.
    for text in ("去Mechanical Hall", "到Mechanical Hall", "前往Mechanical Hall"):
        output = asyncio.run(route_core.route_preview(_config(), _locations(), text))
        assert output.resolved_goal.name == "Mechanical Hall", text
        assert "start" not in output.outgoing_action
        assert "current position" in output.route_preview


def test_route_preview_goal_only_english_goes_from_current_position() -> None:
    # "Go to X" is what the /run agent itself produces on its first attempt.
    for text in ("Go to Mechanical Hall", "navigate to Mechanical Hall", "to Mechanical Hall"):
        output = asyncio.run(route_core.route_preview(_config(), _locations(), text))
        assert output.resolved_goal.name == "Mechanical Hall", text
        assert "start" not in output.outgoing_action


def test_route_preview_llm_fallback_accepts_empty_start(monkeypatch) -> None:
    # The LLM prompt says "use an empty string" for a missing start; the parser
    # must accept that instead of rejecting every destination-only request.
    async def fake_ask_json(config, prompt, *, binding="chat"):
        return {"start": "", "goal": "Mechanical Hall"}

    monkeypatch.setattr("jenai.tools.route_core.ask_json", fake_ask_json)
    output = asyncio.run(
        route_core.route_preview(_config(), _locations(), "head over towards the hall")
    )
    assert output.resolved_goal.name == "Mechanical Hall"
    assert "start" not in output.outgoing_action


def test_route_preview_bare_location_name_is_the_goal() -> None:
    # Agents pass the bare place name; it must resolve without a provider.
    output = asyncio.run(route_core.route_preview(_config(), _locations(), "Mechanical Hall"))
    assert output.resolved_goal.name == "Mechanical Hall"
    assert "start" not in output.outgoing_action



@pytest.mark.skipif(os.name != "posix", reason="process-group cleanup is POSIX-specific")
def test_route_cli_fallback_cancellation_kills_and_reaps_send_goal(
    monkeypatch, tmp_path: Path
) -> None:
    """The fallback must not send/finish a Nav2 goal after Esc reports cancel."""

    events = tmp_path / "route-events.log"
    pid_file = tmp_path / "route.pid"
    fake_ros2 = tmp_path / "ros2"
    fake_ros2.write_text(
        """#!/usr/bin/env python3
import os
import signal
import sys
import time
from pathlib import Path

if sys.argv[1:3] == ["action", "list"]:
    print("/navigate_to_pose")
    raise SystemExit(0)

events = Path(os.environ["FAKE_ROUTE_EVENTS"])
pid_file = Path(os.environ["FAKE_ROUTE_PID"])
running = True

def stop(signum, frame):
    global running
    running = False

signal.signal(signal.SIGTERM, stop)
pid_file.write_text(str(os.getpid()))
with events.open("a") as stream:
    stream.write("goal-started\\n")
deadline = time.monotonic() + 0.4
while running and time.monotonic() < deadline:
    time.sleep(0.01)
with events.open("a") as stream:
    stream.write("goal-reaped\\n" if not running else "late-goal-complete\\n")
""",
        encoding="utf-8",
    )
    fake_ros2.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_ROUTE_EVENTS", str(events))
    monkeypatch.setenv("FAKE_ROUTE_PID", str(pid_file))
    config = _config().model_copy(update={"route_adapter": "nav2"})
    action = {
        "goal": {
            "name": "A",
            "frame_id": "map",
            "pose": {"x": 1.0, "y": 2.0, "yaw": 0.0},
        }
    }

    async def scenario() -> int:
        task = asyncio.create_task(route_core.route_execute(config, action))
        for _ in range(100):
            if pid_file.exists() and events.exists():
                break
            await asyncio.sleep(0.01)
        assert pid_file.exists() and events.exists()
        pid = int(pid_file.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return pid

    pid = asyncio.run(scenario())
    lines_after_cancel = events.read_text().splitlines()
    assert lines_after_cancel == ["goal-started", "goal-reaped"]
    time.sleep(0.5)
    assert events.read_text().splitlines() == lines_after_cancel
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
