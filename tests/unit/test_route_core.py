from __future__ import annotations

import asyncio

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


def test_explicit_route_goal_matches_safe_imperatives_without_a_model() -> None:
    locations = _locations()
    mechanical = locations[1]
    mechanical.aliases = ["machine hall"]

    for text in (
        "請前往 Mechanical Hall，抵達後回報結果。",
        "請回到 Mechanical Hall。",
        "Please navigate to machine hall and report back.",
    ):
        assert route_core.explicit_route_goal(locations, text) is mechanical, text


@pytest.mark.parametrize(
    "text",
    [
        "不要前往 Mechanical Hall。",
        "可以前往 Mechanical Hall 嗎？",
        "如何前往 Mechanical Hall？",
        "從 Engineering Building 到 Mechanical Hall",
        "inspect the route to Mechanical Hall",
    ],
)
def test_explicit_route_goal_rejects_unsafe_or_ambiguous_wording(text: str) -> None:
    assert route_core.explicit_route_goal(_locations(), text) is None


def test_route_preview_resolves_via_regex_chinese() -> None:
    output = asyncio.run(
        route_core.route_preview(_config(), _locations(), "從Engineering Building到Mechanical Hall")
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


def test_natural_language_dock_route_carries_unverified_dock_contract() -> None:
    dock = Location(
        name="charging_approach",
        aliases=["dock"],
        tags=["dock"],
        frame_id="map",
        pose=Pose2D(x=1, y=2, yaw=0),
    )

    config = _config()
    config.site.dock_location = dock.name
    output = asyncio.run(route_core.route_preview(config, [dock], "回到 dock"))

    assert output.outgoing_action["capability_id"] == "dock_approach"


def test_tagged_dock_without_site_binding_is_regular_navigation() -> None:
    dock = Location(
        name="charging_approach",
        aliases=["dock"],
        tags=["dock"],
        frame_id="map",
        pose=Pose2D(x=1, y=2, yaw=0),
    )

    output = asyncio.run(route_core.route_preview(_config(), [dock], "回到 dock"))

    assert "capability_id" not in output.outgoing_action
