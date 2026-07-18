from __future__ import annotations

import asyncio
import contextlib
import gc
import sys
import warnings
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


def test_bridge_stop_awaits_reader_and_reaps_transport(fake_bridge) -> None:
    """stop() owns the cancelled reader until both it and its process are done."""
    async def run() -> None:
        cleanup_started = asyncio.Event()
        cleanup_release = asyncio.Event()
        cleanup_finished = asyncio.Event()

        class SlowReaderCleanupClient(RosBridgeClient):
            async def _read_loop(self) -> None:
                try:
                    await super()._read_loop()
                except asyncio.CancelledError:
                    cleanup_started.set()
                    await cleanup_release.wait()
                    cleanup_finished.set()
                    raise

        client = SlowReaderCleanupClient()
        await client.start()
        proc = client._proc
        reader_task = client._reader_task
        assert proc is not None and reader_task is not None
        transport = proc._transport

        stop_task = asyncio.create_task(client.stop())
        await asyncio.wait_for(cleanup_started.wait(), 1.0)
        await asyncio.wait_for(proc.wait(), 2.0)
        try:
            # Even after the child is reaped, stop must not return until the
            # reader's cancellation cleanup has completed.
            done, _ = await asyncio.wait({stop_task}, timeout=0.2)
            assert not done
        finally:
            cleanup_release.set()
            await stop_task
            with contextlib.suppress(asyncio.CancelledError):
                await reader_task

        assert cleanup_finished.is_set()
        assert reader_task.done() and reader_task.cancelled()
        assert proc.returncode is not None
        assert transport.is_closing()
        assert client._proc is None and client._reader_task is None

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        asyncio.run(run())
        gc.collect()

    resource_messages = [
        str(item.message)
        for item in caught
        if issubclass(item.category, ResourceWarning)
    ]
    assert not any(
        "subprocess" in message or "transport" in message
        for message in resource_messages
    )
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


def test_bridge_halt_and_watchdog_roundtrip(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        await client.configure_safety(watchdog_s=5.0, cmd_vel_topic="/cmd_vel")
        assert await client.halt() is False  # fake: halted, but no nav goal to cancel
        await client.stop()

    asyncio.run(run())


# --- fault injection (A4): start/stream failure modes ------------------------


def _script(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "bad_bridge.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_bridge_never_ready_times_out_and_cleans_up(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        client_module,
        "_BRIDGE_SCRIPT",
        _script(tmp_path, "import time\ntime.sleep(30)\n"),
    )
    monkeypatch.setenv("JENAI_BRIDGE_PYTHON", sys.executable)
    monkeypatch.setattr(RosBridgeClient, "available", staticmethod(lambda: True))

    async def run() -> None:
        client = RosBridgeClient()
        with pytest.raises(BridgeError, match="did not become ready"):
            await client.start(timeout=0.5)
        assert not client.running  # no zombie sidecar left behind

    asyncio.run(run())


def test_bridge_watchdog_arming_failure_fails_the_start(tmp_path, monkeypatch) -> None:
    """A bridge that can't arm its watchdog must never be handed out — an
    unprotected bridge can actuate with no dead-client failsafe."""
    body = (
        "import json, sys\n"
        'sys.stdout.write(json.dumps({"event": "ready"}) + "\\n")\n'
        "sys.stdout.flush()\n"
        "for line in sys.stdin:\n"
        "    req = json.loads(line)\n"
        '    sys.stdout.write(json.dumps('
        '{"id": req["id"], "ok": False, "error": "no watchdog"}) + "\\n")\n'
        "    sys.stdout.flush()\n"
    )
    monkeypatch.setattr(client_module, "_BRIDGE_SCRIPT", _script(tmp_path, body))
    monkeypatch.setenv("JENAI_BRIDGE_PYTHON", sys.executable)
    monkeypatch.setattr(RosBridgeClient, "available", staticmethod(lambda: True))

    async def run() -> None:
        client = RosBridgeClient()
        await client.configure_safety(watchdog_s=5.0, cmd_vel_topic="/cmd_vel")
        with pytest.raises(BridgeError):
            await client.start()
        assert not client.running

    asyncio.run(run())


def test_bridge_garbage_stream_lines_are_ignored(tmp_path, monkeypatch) -> None:
    body = (
        "import json, sys\n"
        'sys.stdout.write("this is not json\\n")\n'
        'sys.stdout.write(json.dumps({"event": "ready"}) + "\\n")\n'
        "sys.stdout.flush()\n"
        "for line in sys.stdin:\n"
        "    req = json.loads(line)\n"
        '    sys.stdout.write("<<garbage>>\\n")\n'
        '    sys.stdout.write(json.dumps('
        '{"id": req["id"], "ok": True, "result": {"pong": True}}) + "\\n")\n'
        "    sys.stdout.flush()\n"
    )
    monkeypatch.setattr(client_module, "_BRIDGE_SCRIPT", _script(tmp_path, body))
    monkeypatch.setenv("JENAI_BRIDGE_PYTHON", sys.executable)
    monkeypatch.setattr(RosBridgeClient, "available", staticmethod(lambda: True))

    async def run() -> None:
        client = RosBridgeClient()
        await client.start()
        assert await client.ping()  # garbage between frames never poisons real replies
        await client.stop()

    asyncio.run(run())
