from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from jenai.bridge import BridgeError, RosBridgeClient
from jenai.bridge import client as client_module
from jenai.schemas import GateReport, Location, Pose2D, RouteOutput
from jenai.tools.mission_core import parse_mission, run_mission
from jenai.tools.nav_live import navigate_live

FAKE_BRIDGE = Path(__file__).parent / "fake_bridge.py"


@pytest.fixture
def fake_bridge(monkeypatch):
    monkeypatch.setattr(client_module, "_BRIDGE_SCRIPT", FAKE_BRIDGE)
    monkeypatch.setenv("JENAI_BRIDGE_PYTHON", sys.executable)
    monkeypatch.setattr(RosBridgeClient, "available", staticmethod(lambda: True))


ACTION = {"goal": {"name": "Kitchen", "frame_id": "map", "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0}}}


def test_navigate_live_success_with_progress(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        progress = []
        output = await navigate_live(client, ACTION, on_progress=progress.append)
        assert output.execution_status == "succeeded"
        assert "Arrived" in output.route_preview
        assert progress and progress[0].distance_remaining == 3.2
        await client.stop()

    asyncio.run(run())


def test_navigate_live_reports_unavailable_when_bridge_fails(fake_bridge, monkeypatch) -> None:
    async def run() -> None:
        client = RosBridgeClient()

        async def failing_send(**_kw) -> None:
            raise BridgeError("Nav2 (/navigate_to_pose) action server is not running.")

        monkeypatch.setattr(client, "nav_send", failing_send)
        output = await navigate_live(client, ACTION)
        assert output.execution_status == "unavailable"
        assert "NOT sent" in output.route_preview

    asyncio.run(run())


def test_navigate_live_detaches_event_handlers(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        await navigate_live(client, ACTION)
        assert client._event_handlers.get("nav_feedback", []) == []
        assert client._event_handlers.get("nav_result", []) == []
        await client.stop()

    asyncio.run(run())


def test_run_mission_uses_injected_navigator() -> None:
    async def run() -> None:
        locations = [
            Location(name="Kitchen", frame_id="map", pose=Pose2D(x=2, y=1, yaw=0)),
            Location(name="Lobby", frame_id="map", pose=Pose2D(x=0, y=0, yaw=0)),
        ]
        navigated: list[str] = []

        async def fake_navigate(action: dict) -> RouteOutput:
            navigated.append(action["goal"]["name"])
            return RouteOutput(
                input_text="",
                outgoing_action=action,
                execution_status="succeeded",
                route_preview="Arrived at the goal.",
            )

        report = await run_mission(
            None,  # config unused when navigate is injected for goto-only missions
            locations,
            parse_mission("kitchen, lobby"),
            navigate=fake_navigate,
        )
        assert navigated == ["Kitchen", "Lobby"]
        assert all(r.status == "succeeded" for r in report.results)

    asyncio.run(run())


def test_navigate_live_direct_mode_uses_drive_to_pose(fake_bridge) -> None:
    """route_adapter='odom': navigate_live drives via drive_to_pose (odom→cmd_vel),
    consuming the same nav_feedback/nav_result events."""

    class _Vehicle:
        cmd_vel_topic = "/cmd_vel"
        cmd_vel_stamped = False
        max_linear = 1.5
        max_angular = 0.53

    async def run() -> None:
        client = RosBridgeClient()
        progress: list = []
        output = await navigate_live(
            client, ACTION, on_progress=progress.append, direct=True, vehicle=_Vehicle()
        )
        assert output.execution_status == "succeeded"
        assert progress and progress[0].distance_remaining == 1.5
        await client.stop()

    asyncio.run(run())


def test_navigate_with_fallback_odom_dispatches_direct(fake_bridge, monkeypatch) -> None:
    from jenai.config.store import build_minimal_config
    from jenai.tools.nav_live import navigate_with_fallback

    config = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )
    config.route_adapter = "odom"

    seen: dict = {}

    async def fake_drive(**kwargs):
        seen.update(kwargs)

    async def run() -> None:
        client = RosBridgeClient()
        monkeypatch.setattr(client, "drive_to_pose", fake_drive)

        async def get_bridge():
            return client

        # Emit the terminal result so navigate_live completes.
        import threading

        def _late_result():
            import time

            time.sleep(0.2)
            client._dispatch_event({"event": "nav_result", "tag": "", "status": "succeeded"})

        threading.Thread(target=_late_result, daemon=True).start()
        out = await navigate_with_fallback(config, get_bridge, ACTION)
        assert out.execution_status == "succeeded"
        assert seen["x"] == 2.0 and seen["y"] == 1.5  # goal reached drive_to_pose
        await client.stop()

    asyncio.run(run())


def test_navigate_with_fallback_surfaces_structured_gate_report(monkeypatch) -> None:
    from jenai.config.store import build_minimal_config
    from jenai.tools.nav_live import navigate_with_fallback

    config = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )
    config.twin.enabled = True
    report = GateReport(verdict="block", reason="collision")

    async def fake_rehearse(*_args, **_kwargs):
        return report

    monkeypatch.setattr("jenai.twin.rehearse_goal", fake_rehearse)
    seen: list[GateReport] = []

    async def unused_bridge():
        raise AssertionError("a blocked gate must not request the real bridge")

    output = asyncio.run(
        navigate_with_fallback(
            config,
            unused_bridge,
            ACTION,
            on_gate_report=seen.append,
        )
    )

    assert seen == [report]
    assert output.execution_status == "failed"
