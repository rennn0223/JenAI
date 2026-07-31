from __future__ import annotations

import importlib
import sys
import threading
from collections.abc import Iterator
from functools import partial
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


class _FakeNode:
    nav_active = True

    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def halt(self, _topic: str, _stamped: bool) -> None:
        self._calls.append("halt")
        raise RuntimeError("halt failed")

    def destroy_node(self) -> None:
        self._calls.append("destroy_node")


class _FakeExecutor:
    def __init__(self, calls: list[str], *, num_threads: int) -> None:
        assert num_threads == 4
        self._calls = calls

    def add_node(self, _node: _FakeNode) -> None:
        self._calls.append("add_node")

    def spin(self) -> None:
        pass

    def shutdown(self) -> None:
        self._calls.append("executor_shutdown")
        raise RuntimeError("executor cleanup failed")


class _FakeThread:
    def __init__(
        self,
        calls: list[str],
        threads: list[Any],
        *,
        target: Any,
        args: tuple[Any, ...] = (),
        daemon: bool,
    ) -> None:
        self.target = target
        self.args = args
        self.daemon = daemon
        self._calls = calls
        threads.append(self)

    def start(self) -> None:
        self._calls.append("thread_start")


class _FakeWatchdog:
    cmd_vel_topic = "/cmd_vel"
    stamped = False

    def touch(self) -> None:
        pass


def _fail_requests(calls: list[str], *_args: Any, **_kwargs: Any) -> None:
    calls.append("serve_requests")
    raise RuntimeError("request loop failed")


def _load_ros_bridge_with_stubbed_rclpy(monkeypatch: pytest.MonkeyPatch) -> Any:
    rclpy = ModuleType("rclpy")
    action = ModuleType("rclpy.action")
    executors = ModuleType("rclpy.executors")
    node = ModuleType("rclpy.node")
    qos = ModuleType("rclpy.qos")
    utilities = ModuleType("rclpy.utilities")
    action.ActionClient = object  # type: ignore[attr-defined]
    executors.MultiThreadedExecutor = object  # type: ignore[attr-defined]
    node.Node = object  # type: ignore[attr-defined]
    qos.QoSPresetProfiles = SimpleNamespace(  # type: ignore[attr-defined]
        SENSOR_DATA=SimpleNamespace(value=object())
    )
    utilities.get_rmw_implementation_identifier = lambda: "rmw_fastrtps_cpp"  # type: ignore[attr-defined]
    for name, module in {
        "rclpy": rclpy,
        "rclpy.action": action,
        "rclpy.executors": executors,
        "rclpy.node": node,
        "rclpy.qos": qos,
        "rclpy.utilities": utilities,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    for sibling in (
        "_latched_observation",
        "_nav_plan",
        "_navigation_state",
        "_node_identity",
        "_occupancy",
        "_protocol",
        "_runtime_identity",
        "_server",
        "_watchdog",
    ):
        monkeypatch.setitem(
            sys.modules,
            sibling,
            importlib.import_module(f"jenai.bridge.{sibling}"),
        )

    sys.modules.pop("jenai.bridge.ros_bridge", None)
    return importlib.import_module("jenai.bridge.ros_bridge")


@pytest.fixture
def stubbed_ros_bridge(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    bridge = _load_ros_bridge_with_stubbed_rclpy(monkeypatch)
    try:
        yield bridge
    finally:
        sys.modules.pop("jenai.bridge.ros_bridge", None)


def test_ros_bridge_main_tears_down_every_resource_when_request_loop_fails(
    stubbed_ros_bridge: Any,
) -> None:
    bridge = stubbed_ros_bridge
    calls: list[str] = []
    threads: list[Any] = []

    bridge.rclpy.init = lambda: calls.append("rclpy_init")
    bridge.rclpy.shutdown = lambda: calls.append("rclpy_shutdown")
    bridge.BridgeNode = lambda: _FakeNode(calls)
    bridge.MultiThreadedExecutor = partial(_FakeExecutor, calls)
    bridge.WatchdogState = _FakeWatchdog
    bridge.threading = SimpleNamespace(
        Thread=partial(_FakeThread, calls, threads),
        Event=threading.Event,
    )
    bridge.build_runtime_identity_payload = lambda **_kwargs: {}
    bridge.get_rmw_implementation_identifier = lambda: "rmw_fastrtps_cpp"
    bridge._emit = lambda _payload: calls.append("emit_ready")

    bridge.serve_requests = partial(_fail_requests, calls)

    with pytest.raises(RuntimeError, match="request loop failed"):
        bridge.main()

    watchdog_thread = next(thread for thread in threads if thread.target is bridge._watchdog_loop)
    watchdog_stop = watchdog_thread.args[2]
    assert watchdog_stop.is_set()
    assert calls == [
        "rclpy_init",
        "add_node",
        "thread_start",
        "thread_start",
        "emit_ready",
        "serve_requests",
        "halt",
        "executor_shutdown",
        "destroy_node",
        "rclpy_shutdown",
    ]
