from __future__ import annotations

import asyncio
import contextlib
import gc
import math
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
        assert client._stderr_task is None

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        asyncio.run(run())
        gc.collect()

    resource_messages = [
        str(item.message) for item in caught if issubclass(item.category, ResourceWarning)
    ]
    assert not any(
        "subprocess" in message or "transport" in message for message in resource_messages
    )


def test_bridge_pose_roundtrip(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        pose = await client.get_pose()
        assert (pose.x, pose.y, pose.frame_id) == (1.5, -2.0, "map")
        await client.stop()

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation",
    [
        {"x": "1.5"},
        {"y": math.nan},
        {"yaw": True},
        {"frame_id": ""},
        {"source": None},
    ],
)
def test_bridge_pose_rejects_malformed_evidence(monkeypatch, mutation) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        payload = {
            "x": 1.5,
            "y": -2.0,
            "yaw": 0.5,
            "frame_id": "map",
            "source": "/amcl_pose",
        }
        payload.update(mutation)

        async def request(*_args, **_kwargs):
            return payload

        monkeypatch.setattr(client, "request", request)
        with pytest.raises(BridgeError, match="invalid pose response"):
            await client.get_pose()

    asyncio.run(run())


def test_bridge_map_cell_returns_typed_bounded_summary(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        cell = await client.map_cell(1.0, 2.0)
        assert cell.in_bounds and cell.free
        assert (cell.value, cell.cell_x, cell.cell_y) == (0, 4, 6)
        assert (cell.frame_id, cell.source) == ("map", "/map")
        await client.stop()

    asyncio.run(run())


def _valid_map_cell_payload() -> dict:
    return {
        "in_bounds": True,
        "free": True,
        "value": 0,
        "cell_x": 4,
        "cell_y": 6,
        "width": 20,
        "height": 30,
        "resolution": 0.05,
        "origin_x": -1.0,
        "origin_y": -2.0,
        "frame_id": "map",
        "source": "/map",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        {"free": True, "value": 100},
        {"in_bounds": "true"},
        {"value": True},
        {"width": 0},
        {"resolution": math.nan},
        {"cell_x": 20},
        {"in_bounds": False, "free": False, "value": 0, "cell_x": -1},
        {"frame_id": ""},
        {"source": ""},
    ],
)
def test_bridge_map_cell_rejects_malformed_or_contradictory_evidence(monkeypatch, mutation) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        payload = _valid_map_cell_payload()
        payload.update(mutation)

        async def request(*_args, **_kwargs):
            return payload

        monkeypatch.setattr(client, "request", request)
        with pytest.raises(BridgeError, match="invalid map_cell response"):
            await client.map_cell(1.0, 2.0)

    asyncio.run(run())


def test_bridge_map_cell_accepts_consistent_out_of_bounds_evidence(monkeypatch) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        payload = _valid_map_cell_payload()
        payload.update(
            {
                "in_bounds": False,
                "free": False,
                "value": None,
                "cell_x": -1,
            }
        )

        async def request(*_args, **_kwargs):
            return payload

        monkeypatch.setattr(client, "request", request)
        cell = await client.map_cell(-100.0, 2.0)
        assert not cell.in_bounds
        assert not cell.free
        assert cell.value is None

    asyncio.run(run())


@pytest.mark.parametrize(
    ("x", "y", "timeout"),
    [
        (math.nan, 0.0, 1.0),
        (0.0, math.inf, 1.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.0, math.nan),
    ],
)
def test_bridge_map_cell_rejects_invalid_query_before_request(monkeypatch, x, y, timeout) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        requested = False

        async def request(*_args, **_kwargs):
            nonlocal requested
            requested = True
            return _valid_map_cell_payload()

        monkeypatch.setattr(client, "request", request)
        with pytest.raises(BridgeError, match="invalid map_cell query"):
            await client.map_cell(x, y, timeout=timeout)
        assert not requested

    asyncio.run(run())


def test_bridge_nav_plan_returns_typed_bounded_summary(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        plan = await client.nav_plan(3.0, 4.0, timeout=1.0)
        assert plan.feasible is True
        assert plan.pose_count == 12
        assert plan.path_length_m == 4.25
        assert plan.error_name == "NONE"
        await client.stop()

    asyncio.run(run())


def _valid_nav_plan_payload() -> dict:
    return {
        "feasible": True,
        "pose_count": 12,
        "path_length_m": 4.25,
        "planning_time_s": 0.02,
        "error_code": 0,
        "error_name": "NONE",
        "error_message": "",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        {"feasible": "false"},
        {"pose_count": True},
        {"pose_count": -1},
        {"path_length_m": math.nan},
        {"planning_time_s": -0.1},
        {"error_code": -1},
        {"error_name": ""},
        {"error_message": None},
        {"feasible": True, "pose_count": 0},
        {"feasible": True, "error_code": 208},
    ],
)
def test_bridge_nav_plan_rejects_malformed_or_contradictory_evidence(monkeypatch, mutation) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        payload = _valid_nav_plan_payload()
        payload.update(mutation)

        async def request(*_args, **_kwargs):
            return payload

        monkeypatch.setattr(client, "request", request)
        with pytest.raises(BridgeError, match="invalid nav_plan response"):
            await client.nav_plan(3.0, 4.0)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("method", "args", "kwargs", "message"),
    [
        ("nav_send", (math.nan, 0.0), {}, "invalid nav_send request"),
        ("nav_send", (0.0, 0.0), {"frame_id": ""}, "invalid nav_send request"),
        ("nav_plan", (0.0, math.inf), {}, "invalid nav_plan request"),
        ("nav_plan", (0.0, 0.0), {"timeout": 0.0}, "invalid nav_plan request"),
    ],
)
def test_bridge_navigation_rejects_invalid_requests_before_dispatch(
    monkeypatch, method, args, kwargs, message
) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        requested = False

        async def request(*_args, **_kwargs):
            nonlocal requested
            requested = True
            return _valid_nav_plan_payload()

        monkeypatch.setattr(client, "request", request)
        with pytest.raises(BridgeError, match=message):
            await getattr(client, method)(*args, **kwargs)
        assert not requested

    asyncio.run(run())


@pytest.mark.parametrize(
    ("method", "args", "kwargs", "message"),
    [
        ("drive_to_pose", (math.nan, 0.0), {}, "invalid drive_to_pose request"),
        ("drive_to_pose", ("0", 0.0), {}, "invalid drive_to_pose request"),
        ("drive_to_pose", (0.0, 0.0), {"stamped": "false"}, "invalid drive_to_pose request"),
        ("drive_to_pose", (0.0, 0.0), {"tag": 7}, "invalid drive_to_pose request"),
        ("drive_to_pose", (0.0, 0.0), {"max_linear": 0.0}, "invalid drive_to_pose request"),
        ("drive_to_pose", (0.0, 0.0), {"timeout": math.inf}, "invalid drive_to_pose request"),
        ("drive_to_pose", (0.0, 0.0), {"avoidance": []}, "invalid drive_to_pose request"),
        ("halt", (), {"cmd_vel_topic": ""}, "invalid halt request"),
        ("halt", (), {"stamped": 1}, "invalid halt request"),
        ("avoid_snapshot", ("/tmp/floor.npy",), {"frames": 0}, "invalid avoid_snapshot request"),
        ("capture_frame", ("",), {}, "invalid capture_frame request"),
        ("unwatch", (True,), {}, "invalid unwatch request"),
    ],
)
def test_bridge_typed_helpers_reject_invalid_requests_before_dispatch(
    monkeypatch, method, args, kwargs, message
) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        requested = False

        async def request(*_args, **_kwargs):
            nonlocal requested
            requested = True
            return {}

        monkeypatch.setattr(client, "request", request)
        with pytest.raises(BridgeError, match=message):
            await getattr(client, method)(*args, **kwargs)
        assert not requested

    asyncio.run(run())


def test_bridge_typed_helpers_dispatch_validated_payloads(monkeypatch) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        requests: list[tuple[str, dict | None]] = []

        async def request(op, *_args, params=None, **_kwargs):
            requests.append((op, params))
            return {"path": "/tmp/frame.png"} if op == "capture_frame" else {"ok": True}

        monkeypatch.setattr(client, "request", request)
        await client.drive_to_pose(1.0, 2.0, tag="direct")
        assert await client.avoid_snapshot("/tmp/floor.npy") == {"ok": True}
        assert await client.capture_frame("/camera") == Path("/tmp/frame.png")
        await client.unwatch(1)

        assert [op for op, _params in requests] == [
            "drive_to_pose",
            "avoid_snapshot",
            "capture_frame",
            "unwatch",
        ]

    asyncio.run(run())


def test_bridge_configure_safety_updates_running_sidecar(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        await client.start()
        await client.configure_safety(watchdog_s=5.0, cmd_vel_topic="/safe_cmd", stamped=True)
        assert client._safety == {
            "timeout": 5.0,
            "cmd_vel_topic": "/safe_cmd",
            "stamped": True,
            "pose_jump_threshold_m": 5.0,
            "pose_jump_window_s": 2.0,
        }
        await client.stop()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("throttle", "handler"),
    [(-1.0, lambda _data: None), (1.0, None)],
)
def test_bridge_watch_rejects_invalid_arguments_before_dispatch(
    monkeypatch, throttle, handler
) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        requested = False

        async def request(*_args, **_kwargs):
            nonlocal requested
            requested = True

        monkeypatch.setattr(client, "request", request)
        with pytest.raises(BridgeError, match="invalid watch request"):
            await client.watch("/scan", "sensor_msgs/msg/LaserScan", handler, throttle=throttle)
        assert not requested

    asyncio.run(run())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"watchdog_s": 0.0},
        {"watchdog_s": math.nan},
        {"cmd_vel_topic": ""},
        {"stamped": "false"},
        {"pose_jump_threshold_m": math.inf},
        {"pose_jump_window_s": -1.0},
    ],
)
def test_bridge_configure_safety_rejects_invalid_config_without_arming(kwargs) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        with pytest.raises(BridgeError, match="invalid configure_safety request"):
            await client.configure_safety(**kwargs)
        assert client._safety is None

    asyncio.run(run())


@pytest.mark.parametrize(
    ("op", "timeout", "params"),
    [
        ("", 1.0, None),
        ("ping", 0.0, None),
        ("ping", math.nan, None),
        ("ping", 1.0, []),
    ],
)
def test_bridge_request_rejects_invalid_envelope_before_start(
    monkeypatch, op, timeout, params
) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        started = False

        async def start(*_args, **_kwargs):
            nonlocal started
            started = True

        monkeypatch.setattr(client, "start", start)
        with pytest.raises(BridgeError, match="invalid bridge request"):
            await client.request(op, timeout=timeout, params=params)
        assert not started

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


def test_cancelled_request_does_not_leak_pending_future(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        task = asyncio.create_task(client.request("slow", timeout=10.0))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client._pending == {}
        await client.stop()

    asyncio.run(run())


def test_event_handler_failure_is_isolated_from_reader(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        await client.start()

        def broken_handler(_event: dict) -> None:
            raise RuntimeError("consumer bug")

        client.on_event("nav_feedback", broken_handler)
        await client.nav_send(1.0, 2.0)
        await asyncio.sleep(0.05)
        assert client.running
        assert await client.ping()
        await client.stop()

    asyncio.run(run())


def test_bridge_launch_paths_are_shell_arguments_not_source_code(monkeypatch) -> None:
    monkeypatch.setattr(client_module, "_BRIDGE_SCRIPT", Path("/tmp/bridge;not-code.py"))
    args = client_module._bridge_process_args(
        '/tmp/setup"; touch /tmp/injected; #',
        "/tmp/python $(touch /tmp/injected)",
    )

    assert args[:4] == (
        "bash",
        "-c",
        client_module._BRIDGE_LAUNCHER,
        "jenai-ros-bridge",
    )
    assert args[4:] == (
        '/tmp/setup"; touch /tmp/injected; #',
        "/tmp/python $(touch /tmp/injected)",
        "/tmp/bridge;not-code.py",
    )


def test_bridge_watch_events_reach_handler(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        seen: list[dict] = []
        await client.watch("/battery_state", "sensor_msgs/msg/BatteryState", seen.append)
        await asyncio.sleep(0.1)  # let the reader task dispatch the event
        assert seen and seen[0]["percentage"] == 0.42
        await client.stop()

    asyncio.run(run())


def test_failed_watch_registration_does_not_leak_handler(monkeypatch) -> None:
    async def run() -> None:
        client = RosBridgeClient()

        async def fail_request(*_args, **_kwargs):
            raise BridgeError("watch rejected")

        monkeypatch.setattr(client, "request", fail_request)
        with pytest.raises(BridgeError, match="watch rejected"):
            await client.watch("/scan", "sensor_msgs/msg/LaserScan", lambda _data: None)
        assert client._watch_handlers == {}

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


@pytest.mark.parametrize(
    "payload",
    [
        {"halted": "true", "nav_canceled": False},
        {"halted": True, "nav_canceled": "false"},
        {"halted": False, "nav_canceled": False},
        {"nav_canceled": False},
    ],
)
def test_bridge_halt_requires_exact_positive_delivery_confirmation(monkeypatch, payload) -> None:
    async def run() -> None:
        client = RosBridgeClient()

        async def request(*_args, **_kwargs):
            return payload

        monkeypatch.setattr(client, "request", request)
        with pytest.raises(BridgeError, match="invalid halt response"):
            await client.halt()

    asyncio.run(run())


def test_bridge_nav_cancel_rejects_truthy_string_acknowledgement(monkeypatch) -> None:
    async def run() -> None:
        client = RosBridgeClient()

        async def request(*_args, **_kwargs):
            return {"canceled": "false"}

        monkeypatch.setattr(client, "request", request)
        with pytest.raises(BridgeError, match="invalid nav_cancel response"):
            await client.nav_cancel()

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


def test_bridge_protocol_failure_before_ready_returns_immediately(tmp_path, monkeypatch) -> None:
    body = (
        "import json, sys, time\n"
        'sys.stdout.write(json.dumps({"event": 7}) + "\\n")\n'
        "sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )
    monkeypatch.setattr(client_module, "_BRIDGE_SCRIPT", _script(tmp_path, body))
    monkeypatch.setenv("JENAI_BRIDGE_PYTHON", sys.executable)
    monkeypatch.setattr(RosBridgeClient, "available", staticmethod(lambda: True))

    async def run() -> None:
        client = RosBridgeClient()
        started = asyncio.get_running_loop().time()
        with pytest.raises(BridgeError, match="exited before"):
            await client.start(timeout=5.0)
        assert asyncio.get_running_loop().time() - started < 2.0
        assert not client.running

    asyncio.run(run())


def test_bridge_failure_surfaces_bounded_stderr_diagnostic(tmp_path, monkeypatch) -> None:
    body = (
        "import sys\n"
        'sys.stderr.write("x" * 20000 + "\\nmissing-rclpy-sentinel\\n")\n'
        "sys.stderr.flush()\n"
        "raise SystemExit(2)\n"
    )
    monkeypatch.setattr(client_module, "_BRIDGE_SCRIPT", _script(tmp_path, body))
    monkeypatch.setenv("JENAI_BRIDGE_PYTHON", sys.executable)
    monkeypatch.setattr(RosBridgeClient, "available", staticmethod(lambda: True))

    async def run() -> None:
        client = RosBridgeClient()
        with pytest.raises(BridgeError, match="missing-rclpy-sentinel") as caught:
            await client.start(timeout=5.0)
        assert len(str(caught.value)) < 2_500
        assert len(client._stderr_tail) <= client_module._STDERR_TAIL_LIMIT
        assert not client.running

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
        "    sys.stdout.write(json.dumps("
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
        "    sys.stdout.write(json.dumps("
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
