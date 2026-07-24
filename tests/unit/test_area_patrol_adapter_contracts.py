from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast

from agents.tool_context import ToolContext

from jenai.adapters.locations import save_locations
from jenai.agent.context import JenAIRunContext
from jenai.config.models import PatrolAreaProfile, SiteProfile
from jenai.config.store import build_minimal_config
from jenai.schemas import Location, Pose2D, RouteOutput, SessionState, VisionOutput
from jenai.site_assets import fingerprint_locations_file
from jenai.state.runs import RunStore
from jenai.tools.area_patrol_agent_tools import area_patrol_workflow_tool
from jenai.tools.area_patrol_service import AgentAreaPatrolRuntime
from jenai.workflows.area_patrol import (
    InspectionPoint,
    InspectionVerdict,
    StepVerdict,
)


def _context(tmp_path: Path, *, active_site: bool = True) -> JenAIRunContext:
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="model",
        api_key_env="",
    )
    locations = [
        Location(
            name="Inspection A",
            frame_id="map",
            pose=Pose2D(x=1.0, y=2.0, yaw=0.0),
        ),
        Location(
            name="Dock",
            frame_id="map",
            pose=Pose2D(x=0.0, y=0.0, yaw=0.0),
            tags=["dock"],
        ),
    ]
    locations_path = tmp_path / "locations.toml"
    save_locations(locations, locations_path)
    if active_site:
        config.locations_path = "locations.toml"
        config.site = SiteProfile(
            site_id="warehouse",
            display_name="Warehouse",
            active=True,
            validated=True,
            map_sha256="a" * 64,
            locations_path="locations.toml",
            locations_sha256=fingerprint_locations_file(locations_path),
            validated_routes=["Inspection A", "Dock"],
            home_location="Dock",
            dock_location="Dock",
            patrol_areas=[
                PatrolAreaProfile(
                    area_id="equipment",
                    display_name="Equipment",
                    inspection_locations=["Inspection A"],
                )
            ],
        )
    if config.model_bindings is None:
        raise RuntimeError("minimal test config has no model bindings")
    session = SessionState(
        provider_profile="test",
        model_bindings=config.model_bindings,
        working_directory=str(tmp_path),
    )
    store = RunStore()
    run = store.create_run(session.session_id, "inspect all areas and return home")
    return JenAIRunContext(
        config=config,
        config_path=tmp_path / "config.toml",
        session=session,
        run=run,
        run_store=store,
    )


def _decode_tool_output(raw: object) -> dict[str, object]:
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, dict):
        raise AssertionError("tool output must be a JSON object")
    return cast(dict[str, object], value)


def _tool_context(context: JenAIRunContext, arguments: str) -> ToolContext[JenAIRunContext]:
    return ToolContext(
        context=context,
        tool_name=area_patrol_workflow_tool.name,
        tool_call_id="test-tool-call",
        tool_arguments=arguments,
    )


def test_runtime_navigation_resolves_saved_locations_and_classifies_failures(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _context(tmp_path)
    locations_path = context.config.resolved_locations_path(context.config_path)
    if locations_path is None:
        raise AssertionError("active test site must resolve locations")
    from jenai.adapters.locations import load_locations

    locations = load_locations(locations_path)
    outgoing: list[dict[str, object]] = []

    async def fake_execute(config, action, **kwargs) -> RouteOutput:
        outgoing.append(action)
        return RouteOutput(
            input_text="",
            route_preview="arrived",
            execution_status="succeeded",
        )

    monkeypatch.setattr(
        "jenai.tools.area_patrol_service.execute_navigation",
        fake_execute,
    )
    runtime = AgentAreaPatrolRuntime(context, locations)

    async def run():
        reached = await runtime.navigate(InspectionPoint("Inspection A"))
        home = await runtime.return_home("Dock")
        missing = await runtime.navigate(InspectionPoint("Missing"))
        return reached, home, missing

    reached, home, missing = asyncio.run(run())

    assert reached.verdict is StepVerdict.SUCCEEDED
    assert home.verdict is StepVerdict.SUCCEEDED
    assert missing.verdict is StepVerdict.FAILED
    assert "unknown location" in missing.detail
    assert [item["goal"]["name"] for item in outgoing] == ["Inspection A", "Dock"]
    assert all(item["capability_id"] == "area_patrol" for item in outgoing)


def test_agent_workflow_retries_temporarily_unavailable_navigation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _context(tmp_path)
    navigation_calls = 0

    class FakeBridge:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    async def fake_execute(config, action, **kwargs) -> RouteOutput:
        nonlocal navigation_calls
        navigation_calls += 1
        if navigation_calls == 1:
            return RouteOutput(
                input_text="",
                route_preview="map identity is temporarily unavailable",
                execution_status="unavailable",
            )
        return RouteOutput(
            input_text="",
            route_preview="arrived",
            execution_status="succeeded",
        )

    async def fake_capture(*args, **kwargs) -> VisionOutput:
        return VisionOutput(source="image://inspection-a", summary="No anomaly observed.")

    monkeypatch.setattr("jenai.tools.area_patrol_service.RosBridgeClient", FakeBridge)
    monkeypatch.setattr("jenai.tools.area_patrol_service.execute_navigation", fake_execute)
    monkeypatch.setattr("jenai.tools.area_patrol_service.capture_and_analyze", fake_capture)

    async def invoke() -> dict[str, object]:
        arguments = json.dumps(
            {"target": "equipment", "max_navigation_retries": 1, "return_home": False}
        )
        raw = await area_patrol_workflow_tool.on_invoke_tool(
            _tool_context(context, arguments), arguments
        )
        return _decode_tool_output(raw)

    output = asyncio.run(invoke())

    assert output["execution_status"] == "success"
    assert navigation_calls == 2


def test_runtime_verified_inspection_and_model_unavailable_are_distinct(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _context(tmp_path)

    class FakeBridge:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    outputs = [
        VisionOutput(source="image://verified", summary="No anomaly observed."),
        VisionOutput(
            source="image://unverified",
            summary="Vision model is unavailable; image preserved.",
        ),
    ]

    async def fake_capture(*args, **kwargs) -> VisionOutput:
        return outputs.pop(0)

    monkeypatch.setattr(
        "jenai.tools.area_patrol_service.RosBridgeClient",
        FakeBridge,
    )
    monkeypatch.setattr(
        "jenai.tools.area_patrol_service.capture_and_analyze",
        fake_capture,
    )
    runtime = AgentAreaPatrolRuntime(context, [])

    async def run():
        verified = await runtime.inspect(InspectionPoint("Inspection A"))
        unavailable = await runtime.inspect(InspectionPoint("Inspection A"))
        await runtime.close()
        return verified, unavailable

    verified, unavailable = asyncio.run(run())

    assert verified.verdict is InspectionVerdict.VERIFIED
    assert verified.evidence == ("image://verified",)
    assert unavailable.verdict is InspectionVerdict.REQUIRES_REVIEW
    assert unavailable.evidence == ("image://unverified",)


def test_agent_workflow_tool_runs_one_complete_deterministic_mission(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _context(tmp_path)
    bridge_stopped = False

    class FakeBridge:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            nonlocal bridge_stopped
            bridge_stopped = True

    async def fake_execute(config, action, **kwargs) -> RouteOutput:
        return RouteOutput(
            input_text="",
            route_preview=f"arrived at {action['goal']['name']}",
            execution_status="succeeded",
        )

    async def fake_capture(*args, **kwargs) -> VisionOutput:
        return VisionOutput(
            source="image://inspection-a",
            summary="No anomaly observed.",
        )

    monkeypatch.setattr(
        "jenai.tools.area_patrol_service.RosBridgeClient",
        FakeBridge,
    )
    monkeypatch.setattr(
        "jenai.tools.area_patrol_service.execute_navigation",
        fake_execute,
    )
    monkeypatch.setattr(
        "jenai.tools.area_patrol_service.capture_and_analyze",
        fake_capture,
    )

    async def invoke() -> dict[str, object]:
        arguments = json.dumps(
            {
                "target": "equipment",
                "max_navigation_retries": 1,
                "return_home": True,
            }
        )
        raw = await area_patrol_workflow_tool.on_invoke_tool(
            _tool_context(context, arguments), arguments
        )
        return _decode_tool_output(raw)

    output = asyncio.run(invoke())

    assert output["execution_status"] == "success"
    assert output["coverage_ratio"] == 1.0
    assert output["returned_home"] is True
    assert output["report_saved"] is True
    assert Path(str(output["report_path"])).is_file()
    assert bridge_stopped is True
    assert context.run.tool_calls[0].status == "succeeded"


def test_agent_workflow_tool_rejects_invalid_site_and_retry_bounds(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, active_site=False)

    async def invoke(arguments: dict[str, object]) -> dict[str, object]:
        raw_arguments = json.dumps(arguments)
        raw = await area_patrol_workflow_tool.on_invoke_tool(
            _tool_context(context, raw_arguments), raw_arguments
        )
        return _decode_tool_output(raw)

    missing_site = asyncio.run(invoke({"target": "all"}))
    invalid_retry = asyncio.run(
        invoke(
            {
                "target": "all",
                "max_navigation_retries": 6,
                "return_home": True,
            }
        )
    )

    assert missing_site["execution_status"] == "failed"
    assert "Site Profile" in str(missing_site["summary"])
    assert invalid_retry["execution_status"] == "failed"
    assert "between 0 and 5" in str(invalid_retry["summary"])


def test_cancelled_agent_workflow_persists_aborted_report_and_finishes_tool(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _context(tmp_path)
    navigation_started = asyncio.Event()

    async def blocking_execute(config, action, **kwargs) -> RouteOutput:
        navigation_started.set()
        await asyncio.Event().wait()
        raise AssertionError("cancelled navigation must not resume")

    monkeypatch.setattr(
        "jenai.tools.area_patrol_service.execute_navigation",
        blocking_execute,
    )

    async def scenario() -> dict[str, object]:
        arguments = json.dumps(
            {
                "target": "equipment",
                "max_navigation_retries": 1,
                "return_home": True,
            }
        )
        task = asyncio.create_task(
            area_patrol_workflow_tool.on_invoke_tool(
                _tool_context(context, arguments),
                arguments,
            )
        )
        await navigation_started.wait()
        task.cancel()
        return _decode_tool_output(await task)

    output = asyncio.run(scenario())

    assert output["execution_status"] == "aborted"
    assert output["outcome"] == "cancelled"
    assert output["report_saved"] is True
    report_path = Path(str(output["report_path"]))
    assert report_path.is_file()
    report_payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert report_payload["execution_status"] == "aborted"
    assert report_payload["returned_home"] is False
    assert any(
        event["event_type"] == "inspection_point_interrupted" for event in report_payload["events"]
    )
    assert context.run.outcome == "cancelled"
    assert context.run.tool_calls[0].status == "failed"
