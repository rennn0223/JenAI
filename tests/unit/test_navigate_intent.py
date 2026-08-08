from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agents import Model
from agents.items import ModelResponse
from agents.usage import Usage
from openai.types.responses import ResponseOutputMessage, ResponseOutputText

from jenai.adapters.locations import save_locations
from jenai.agent.navigate_intent import (
    NavigateIntentBypass,
    build_navigate_intent_agent,
    prepare_navigation_golden_path,
    run_navigate_intent,
)
from jenai.config.models import AppConfig, SiteProfile, VehicleProfile
from jenai.config.store import build_minimal_config
from jenai.schemas import Location, Pose2D
from jenai.site_assets import fingerprint_locations_file
from jenai.workflows.patrol_mission import NavigateMissionDraft


class _StructuredOutputModel(Model):
    def __init__(self, output: NavigateMissionDraft) -> None:
        self._output = output
        self.calls = 0

    async def get_response(self, *args, **kwargs) -> ModelResponse:
        self.calls += 1
        message = ResponseOutputMessage(
            id=f"msg_{self.calls}",
            type="message",
            role="assistant",
            status="completed",
            content=[
                ResponseOutputText(
                    type="output_text",
                    text=json.dumps(self._output.model_dump(mode="json")),
                    annotations=[],
                )
            ],
        )
        return ModelResponse(
            output=[message],
            usage=Usage(requests=1, input_tokens=11, output_tokens=7, total_tokens=18),
            response_id=None,
        )

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError


class _UnexpectedModel(Model):
    async def get_response(self, *args, **kwargs) -> ModelResponse:
        raise AssertionError("STOP must bypass the Intent Agent")

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError


def _provider_config() -> AppConfig:
    return build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="gpt-test",
        api_key_env="JENAI_TEST_KEY",
    )


def _site_config(tmp_path: Path) -> tuple[AppConfig, Path]:
    locations_path = tmp_path / "locations.toml"
    save_locations(
        [
            Location(
                id="loc-map-left-up",
                name="map_left_up",
                pose=Pose2D(x=-8.5, y=15.5, yaw=-0.785),
            ),
            Location(
                id="loc-dock",
                name="Dock",
                pose=Pose2D(x=0.0, y=0.0, yaw=0.0),
            ),
        ],
        locations_path,
    )
    config = _provider_config()
    config.locations_path = "locations.toml"
    config.vehicle = VehicleProfile(robot_id="robot-1")
    config.site = SiteProfile(
        site_id="warehouse",
        display_name="Warehouse",
        version="1",
        active=True,
        validated=True,
        map_sha256="a" * 64,
        locations_sha256=fingerprint_locations_file(locations_path),
        locations_path="locations.toml",
        validated_routes=["map_left_up", "Dock"],
        home_location="Dock",
        dock_location="Dock",
    )
    return config, tmp_path / "config.toml"


def test_toolless_intent_agent_returns_typed_draft_in_one_runner_turn(monkeypatch) -> None:
    monkeypatch.setenv("JENAI_TEST_KEY", "secret")
    monkeypatch.setattr("jenai.agent.navigate_intent.install_local_tracing", lambda: None)
    monkeypatch.setattr("jenai.agent.navigate_intent.record_local_trace_event", lambda **_: None)
    model = _StructuredOutputModel(
        NavigateMissionDraft(decision="navigate", location_reference="map_left_up")
    )
    agent = build_navigate_intent_agent(_provider_config(), model=model)

    result = asyncio.run(
        run_navigate_intent(
            _provider_config(),
            "去 map_left_up",
            registered_locations=(("loc-map-left-up", "map_left_up"),),
            model=model,
        )
    )

    assert agent.tools == []
    assert agent.handoffs == []
    assert agent.output_type is NavigateMissionDraft
    assert model.calls == 1
    assert result.draft == NavigateMissionDraft(
        decision="navigate",
        location_reference="map_left_up",
    )
    assert result.input_tokens == 11
    assert result.output_tokens == 7


def test_preparation_binds_agent_output_and_compiles_exact_single_step(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("JENAI_TEST_KEY", "secret")
    monkeypatch.setattr("jenai.agent.navigate_intent.install_local_tracing", lambda: None)
    monkeypatch.setattr("jenai.agent.navigate_intent.record_local_trace_event", lambda **_: None)
    config, config_path = _site_config(tmp_path)
    model = _StructuredOutputModel(
        NavigateMissionDraft(decision="navigate", location_reference="map_left_up")
    )

    prepared = asyncio.run(
        prepare_navigation_golden_path(
            config,
            config_path,
            "去 map_left_up",
            mission_id="mission-1",
            model=model,
        )
    )

    assert prepared.bypass is None
    assert prepared.plan is not None
    assert prepared.plan.mission.target_location.location_name == "map_left_up"
    assert len(prepared.plan.steps) == 1
    assert prepared.preview is not None
    assert "1. 前往 map_left_up" in prepared.preview
    assert "抵達容差：≤ 0.15 m" in prepared.preview
    assert "朝向要求：無" in prepared.preview


def test_stop_bypasses_site_loading_and_intent_model(tmp_path: Path) -> None:
    config = _provider_config()

    prepared = asyncio.run(
        prepare_navigation_golden_path(
            config,
            tmp_path / "missing-config.toml",
            "STOP",
            mission_id="mission-stop",
            model=_UnexpectedModel(),
        )
    )

    assert prepared.bypass is NavigateIntentBypass.EMERGENCY_STOP
    assert prepared.plan is None
    assert prepared.preview is None
