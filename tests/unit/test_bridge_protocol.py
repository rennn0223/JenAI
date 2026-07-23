from __future__ import annotations

import math
import threading

import pytest

from jenai.bridge._protocol import dispatch_request
from jenai.bridge._watchdog import WatchdogState


class FakeNode:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self._halt_lock = threading.Lock()

    def _call(self, name: str, *args, **kwargs) -> dict:
        self.calls.append((name, args, kwargs))
        return {"op": name}

    def get_pose(self, *args, **kwargs):
        return self._call("pose", *args, **kwargs)

    def map_cell(self, *args, **kwargs):
        return self._call("map_cell", *args, **kwargs)

    def nav_send(self, *args, **kwargs):
        return self._call("nav_send", *args, **kwargs)

    def nav_plan(self, *args, **kwargs):
        return self._call("nav_plan", *args, **kwargs)

    def drive_to_pose(self, *args, **kwargs):
        return self._call("drive_to_pose", *args, **kwargs)

    def nav_cancel(self, *args, **kwargs):
        return self._call("nav_cancel", *args, **kwargs)

    def halt(self, *args, **kwargs):
        return self._call("halt", *args, **kwargs)

    def ensure_halt_publisher(self, *args, **kwargs):
        return self._call("ensure_halt_publisher", *args, **kwargs)

    def configure_pose_jump_guard(self, *args, **kwargs):
        return self._call("configure_pose_jump_guard", *args, **kwargs)

    def capture_frame(self, *args, **kwargs):
        return self._call("capture_frame", *args, **kwargs)

    def avoid_snapshot(self, *args, **kwargs):
        return self._call("avoid_snapshot", *args, **kwargs)

    def watch(self, *args, **kwargs):
        return self._call("watch", *args, **kwargs)

    def unwatch(self, *args, **kwargs):
        return self._call("unwatch", *args, **kwargs)


def test_protocol_ping_pose_and_map_cell_defaults() -> None:
    node = FakeNode()
    watchdog = WatchdogState()

    assert dispatch_request(node, "ping", {}, watchdog) == {"pong": True}
    assert dispatch_request(node, "pose", {"timeout": 3.5}, watchdog) == {"op": "pose"}
    assert dispatch_request(node, "map_cell", {"x": 1, "y": 2}, watchdog) == {"op": "map_cell"}
    assert node.calls == [("pose", (3.5,), {}), ("map_cell", (1, 2, 3.0), {})]


def test_protocol_maps_nav_and_drive_parameters() -> None:
    node = FakeNode()
    watchdog = WatchdogState()

    dispatch_request(
        node,
        "nav_send",
        {"x": 1, "y": 2, "yaw": 0.5, "frame_id": "odom", "tag": "abc"},
        watchdog,
    )
    dispatch_request(
        node,
        "nav_plan",
        {"x": 5, "y": 6, "yaw": 0.25, "frame_id": "map", "timeout": 4.0},
        watchdog,
    )
    dispatch_request(
        node,
        "drive_to_pose",
        {
            "x": 3,
            "y": 4,
            "stamped": True,
            "max_linear": 0.4,
            "max_angular": 0.8,
            "tolerance": 0.2,
            "odom_timeout_s": 0.7,
            "timeout": 9.0,
            "avoidance": {"enabled": True},
        },
        watchdog,
    )

    assert node.calls[0] == ("nav_send", (1, 2, 0.5, "odom", "abc"), {})
    assert node.calls[1] == ("nav_plan", (5, 6, 0.25, "map", 4.0), {})
    name, args, kwargs = node.calls[2]
    assert name == "drive_to_pose"
    assert args == (3, 4, 0.0)
    avoidance = kwargs.pop("avoidance")
    assert kwargs == {
        "tag": "",
        "cmd_vel_topic": "/cmd_vel",
        "stamped": True,
        "max_linear": 0.4,
        "max_angular": 0.8,
        "tolerance": 0.2,
        "odom_timeout_s": 0.7,
        "timeout": 9.0,
    }
    assert avoidance["enabled"] is True
    assert avoidance["stop_distance"] == 0.6
    assert avoidance["slow_distance"] == 2.0


@pytest.mark.parametrize(
    ("op", "payload"),
    [
        ("pose", {"timeout": "3.5"}),
        ("nav_send", {"y": 2.0}),
        ("map_cell", {"x": math.nan, "y": 0.0}),
        ("nav_send", {"x": 1.0, "y": 2.0, "frame_id": ""}),
        ("nav_plan", {"x": 1.0, "y": 2.0, "timeout": 0.0}),
        ("drive_to_pose", {"x": 1.0, "y": 2.0, "stamped": "false"}),
        ("drive_to_pose", {"x": 1.0, "y": 2.0, "max_linear": math.inf}),
        ("drive_to_pose", {"x": 1.0, "y": 2.0, "avoidance": []}),
        ("drive_to_pose", {"x": 1.0, "y": 2.0, "avoidance": {"hfov_deg": 181.0}}),
        ("drive_to_pose", {"x": 1.0, "y": 2.0, "avoidance": {"sectors": 361}}),
        ("drive_to_pose", {"x": 1.0, "y": 2.0, "avoidance": {"band_lo": -0.1}}),
        ("drive_to_pose", {"x": 1.0, "y": 2.0, "avoidance": {"band_hi": 1.1}}),
        (
            "drive_to_pose",
            {
                "x": 1.0,
                "y": 2.0,
                "avoidance": {"enabled": True, "stop_distance": 2.0, "slow_distance": 1.0},
            },
        ),
        ("halt", {"stamped": 1}),
        ("watchdog", {"timeout": 0.0}),
        ("capture_frame", {"topic": ""}),
        ("avoid_snapshot", {"path": "/tmp/floor.npy", "frames": 2.5}),
        ("watch", {"watch_id": 1, "topic": "/scan", "msg_type": "Scan", "throttle": -1.0}),
        ("watch", {"watch_id": True, "topic": "/scan", "msg_type": "Scan"}),
        ("unwatch", {"watch_id": 0}),
    ],
)
def test_protocol_rejects_malformed_requests_before_node_dispatch(op, payload) -> None:
    node = FakeNode()

    with pytest.raises(ValueError, match="invalid bridge request"):
        dispatch_request(node, op, payload, WatchdogState())

    assert node.calls == []


def test_protocol_direct_drive_defaults_to_no_avoidance() -> None:
    node = FakeNode()

    dispatch_request(node, "drive_to_pose", {"x": 1.0, "y": 2.0}, WatchdogState())

    assert node.calls[0][2]["avoidance"] is None


def test_watchdog_config_prewarms_halt_publisher() -> None:
    node = FakeNode()
    watchdog = WatchdogState()

    result = dispatch_request(
        node,
        "watchdog",
        {"timeout": 4, "cmd_vel_topic": "/safe_cmd", "stamped": True},
        watchdog,
    )

    assert result == {"watchdog_s": 4.0}
    assert node.calls == [
        ("configure_pose_jump_guard", (5.0, 2.0, "/safe_cmd", True), {}),
        ("ensure_halt_publisher", ("/safe_cmd", True), {}),
    ]


@pytest.mark.parametrize(
    ("op", "payload", "expected"),
    [
        ("nav_cancel", {}, ("nav_cancel", (), {})),
        ("halt", {}, ("halt", ("/cmd_vel", False), {})),
        ("capture_frame", {"topic": "/rgb"}, ("capture_frame", ("/rgb", 5.0), {})),
        (
            "avoid_snapshot",
            {"path": "/tmp/floor.npy"},
            ("avoid_snapshot", ("/depth", "/tmp/floor.npy", 5, 10.0), {}),
        ),
        (
            "watch",
            {"watch_id": 7, "topic": "/scan", "msg_type": "sensor_msgs/msg/LaserScan"},
            ("watch", (7, "/scan", "sensor_msgs/msg/LaserScan", 1.0), {}),
        ),
        ("unwatch", {"watch_id": 7}, ("unwatch", (7,), {})),
    ],
)
def test_protocol_maps_remaining_ops(op, payload, expected) -> None:
    node = FakeNode()

    dispatch_request(node, op, payload, WatchdogState())

    assert node.calls == [expected]


def test_protocol_rejects_unknown_operation() -> None:
    with pytest.raises(RuntimeError, match="unknown op 'launch_missiles'"):
        dispatch_request(FakeNode(), "launch_missiles", {}, WatchdogState())
