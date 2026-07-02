from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from jenai.bridge import BridgeError, RosBridgeClient
from jenai.bridge import client as client_module

FAKE_BRIDGE = Path(__file__).parent / "fake_bridge.py"


@pytest.fixture
def fake_bridge(monkeypatch):
    # Point the client at the protocol-faithful fake so no ROS is needed.
    monkeypatch.setattr(client_module, "_BRIDGE_SCRIPT", FAKE_BRIDGE)
    monkeypatch.setenv("JENAI_BRIDGE_PYTHON", sys.executable)
    monkeypatch.setattr(RosBridgeClient, "available", staticmethod(lambda: True))


def test_bridge_ping_and_clean_stop(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        await client.start()
        assert await client.ping()
        await client.stop()
        assert not client.running

    asyncio.run(run())


def test_bridge_pose_roundtrip(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        pose = await client.get_pose()
        assert (pose.x, pose.y, pose.frame_id) == (1.5, -2.0, "map")
        await client.stop()

    asyncio.run(run())


def test_bridge_error_is_raised_not_swallowed(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        with pytest.raises(BridgeError, match="synthetic failure"):
            await client.request("boom")
        # The bridge keeps serving after an op error.
        assert await client.ping()
        await client.stop()

    asyncio.run(run())


def test_bridge_request_timeout(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        with pytest.raises(BridgeError, match="timed out"):
            await client.request("slow", timeout=0.3)
        await client.stop()

    asyncio.run(run())


def test_bridge_watch_events_reach_handler(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        seen: list[dict] = []
        await client.watch("/battery_state", "sensor_msgs/msg/BatteryState", seen.append)
        await asyncio.sleep(0.1)  # let the reader task dispatch the event
        assert seen and seen[0]["percentage"] == 0.42
        await client.stop()

    asyncio.run(run())


def test_bridge_nav_feedback_and_result_events(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        feedback: list[dict] = []
        results: list[dict] = []
        await client.start()
        client.on_event("nav_feedback", feedback.append)
        client.on_event("nav_result", results.append)
        await client.nav_send(1.0, 2.0, tag="goal-1")
        await asyncio.sleep(0.1)
        assert feedback and feedback[0]["distance_remaining"] == 3.2
        # The fake emits a stale-tagged result first; raw listeners see both,
        # and the tag is what lets consumers (nav_live) tell them apart.
        assert [r["status"] for r in results] == ["canceled", "succeeded"]
        assert [r["tag"] for r in results] == ["stale-goal", "goal-1"]
        await client.stop()

    asyncio.run(run())


def test_bridge_process_death_fails_pending_requests(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        await client.start()
        client._proc.kill()
        with pytest.raises(BridgeError):
            await client.request("ping", timeout=2.0)

    asyncio.run(run())
