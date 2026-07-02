from __future__ import annotations

import asyncio

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
