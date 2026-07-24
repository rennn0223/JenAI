from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast

from agents.tool_context import ToolContext

from jenai.adapters.locations import save_locations
from jenai.agent.context import JenAIRunContext
from jenai.config.store import build_minimal_config
from jenai.schemas import Location, Pose2D, RouteOutput, SessionState, TaskOutcome
from jenai.state.runs import RunStore
from jenai.tools.route_agent_tools import (
    explore_area_tool,
    loc_lookup_tool,
    patrol_area_tool,
    route_execute_tool,
    route_preview_tool,
)


def _context(tmp_path: Path, *, location_count: int = 2) -> JenAIRunContext:
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="model",
        api_key_env="",
    )
    locations = [
        Location(
            name=f"Point {index}",
            frame_id="map",
            pose=Pose2D(x=float(index), y=0.0, yaw=0.0),
        )
        for index in range(1, location_count + 1)
    ]
    save_locations(locations, tmp_path / "locations.toml")
    config.locations_path = "locations.toml"
    if config.model_bindings is None:
        raise RuntimeError("minimal test config has no model bindings")
    session = SessionState(
        provider_profile="test",
        model_bindings=config.model_bindings,
        working_directory=str(tmp_path),
    )
    store = RunStore()
    run = store.create_run(session.session_id, "route tool contract")
    return JenAIRunContext(
        config=config,
        config_path=tmp_path / "config.toml",
        session=session,
        run=run,
        run_store=store,
    )


def _tool_context(
    context: JenAIRunContext,
    tool_name: str,
    arguments: str,
) -> ToolContext[JenAIRunContext]:
    return ToolContext(
        context=context,
        tool_name=tool_name,
        tool_call_id=f"test-{tool_name}",
        tool_arguments=arguments,
    )


async def _invoke(
    tool,
    context: JenAIRunContext,
    arguments: dict[str, object],
) -> dict[str, object]:
    raw_arguments = json.dumps(arguments)
    raw = await tool.on_invoke_tool(
        _tool_context(context, tool.name, raw_arguments),
        raw_arguments,
    )
    if not isinstance(raw, dict):
        raise AssertionError("route tool output must be a JSON object")
    return cast(dict[str, object], raw)


def test_preview_and_location_lookup_share_the_saved_location_source(tmp_path: Path) -> None:
    context = _context(tmp_path)

    preview = asyncio.run(_invoke(route_preview_tool, context, {"text": "Go to Point 2"}))
    found = asyncio.run(_invoke(loc_lookup_tool, context, {"name": "Point 1"}))
    missing = asyncio.run(_invoke(loc_lookup_tool, context, {"name": "Missing"}))

    assert preview["resolved_goal"]["name"] == "Point 2"
    assert preview["outgoing_action"]["goal"]["name"] == "Point 2"
    assert found["found"] is True
    assert found["location"]["name"] == "Point 1"
    assert missing["found"] is False
    assert len(context.run.tool_calls) == 3


def test_route_execute_rejects_malformed_action_and_records_navigation_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _context(tmp_path)
    malformed = asyncio.run(
        _invoke(
            route_execute_tool,
            context,
            {"outgoing_action_json": "not-json"},
        )
    )

    async def fake_execute(config, action, **kwargs) -> RouteOutput:
        return RouteOutput(
            input_text="",
            outgoing_action=action,
            route_preview="arrived",
            approval_status="approved",
            execution_status="succeeded",
        )

    monkeypatch.setattr(
        "jenai.tools.route_agent_tools.execute_navigation",
        fake_execute,
    )
    valid_action = {
        "goal": {
            "name": "Point 1",
            "frame_id": "map",
            "pose": {"x": 1.0, "y": 0.0, "yaw": 0.0},
        }
    }
    succeeded = asyncio.run(
        _invoke(
            route_execute_tool,
            context,
            {"outgoing_action_json": json.dumps(valid_action)},
        )
    )

    assert malformed["execution_status"] == "failed"
    assert "not a valid route action" in str(malformed["route_preview"])
    assert succeeded["execution_status"] == "succeeded"
    assert context.run.outcome is TaskOutcome.SUCCEEDED


def test_explore_validates_bounds_and_requires_two_candidates(tmp_path: Path) -> None:
    context = _context(tmp_path, location_count=1)

    bad_seed = asyncio.run(_invoke(explore_area_tool, context, {"seed": -2}))
    too_few = asyncio.run(
        _invoke(
            explore_area_tool,
            context,
            {
                "duration_minutes": 1.0,
                "max_goals": 1,
                "max_failures": 1,
                "photo": False,
            },
        )
    )

    assert bad_seed["execution_status"] == "failed"
    assert "seed" in str(bad_seed["summary"])
    assert too_few["execution_status"] == "failed"
    assert too_few["candidates"] == ["Point 1"]


def test_explore_runs_one_deterministic_workflow_without_llm_replanning(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _context(tmp_path)
    actions: list[dict[str, object]] = []

    async def fake_execute(config, action, **kwargs) -> RouteOutput:
        actions.append(action)
        return RouteOutput(
            input_text="",
            outgoing_action=action,
            route_preview="arrived",
            approval_status="approved",
            execution_status="succeeded",
        )

    monkeypatch.setattr(
        "jenai.tools.route_agent_tools.execute_navigation",
        fake_execute,
    )
    output = asyncio.run(
        _invoke(
            explore_area_tool,
            context,
            {
                "duration_minutes": 1.0,
                "max_goals": 2,
                "max_failures": 1,
                "seed": 7,
                "photo": False,
            },
        )
    )

    assert output["execution_status"] == "succeeded"
    assert output["success_count"] == 2
    assert output["attempt_count"] == 2
    assert len(actions) == 2
    assert all(action["capability_id"] == "explore_known_locations" for action in actions)


def test_patrol_validates_size_and_runs_the_shared_navigation_gateway(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _context(tmp_path)
    invalid = asyncio.run(_invoke(patrol_area_tool, context, {"points": [], "loops": 1}))
    actions: list[dict[str, object]] = []

    async def fake_execute(config, action, **kwargs) -> RouteOutput:
        actions.append(action)
        return RouteOutput(
            input_text="",
            outgoing_action=action,
            route_preview="arrived",
            approval_status="approved",
            execution_status="succeeded",
        )

    monkeypatch.setattr(
        "jenai.tools.route_agent_tools.execute_navigation",
        fake_execute,
    )
    completed = asyncio.run(
        _invoke(
            patrol_area_tool,
            context,
            {"points": ["Point 1", "Point 2"], "loops": 1, "photo": False},
        )
    )

    assert invalid["execution_status"] == "failed"
    assert completed["execution_status"] == "succeeded"
    assert completed["outcome"] == "succeeded"
    assert len(completed["results"]) == 2
    assert len(actions) == 2
    assert all(action["capability_id"] == "patrol_photo" for action in actions)
