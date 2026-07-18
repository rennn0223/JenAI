"""JSON request protocol dispatch for the rclpy bridge sidecar.

The bridge process owns ROS entities and real-time-ish safety behavior.  This
module only validates/defaults one request and invokes the matching node method,
which keeps wire compatibility testable without importing rclpy.
"""

from __future__ import annotations

from typing import Any

if __package__:
    # Package import in unit tests / JenAI runtime. Do not catch ImportError
    # here: a broken dependency must remain visible instead of silently using
    # a different module from sys.path.
    from ._watchdog import WatchdogState
else:  # pragma: no cover - exercised by the system-Python sidecar
    # Script import when ros_bridge.py runs outside the JenAI venv.
    from _watchdog import WatchdogState


def dispatch_request(node: Any, op: str, req: dict, watchdog: WatchdogState) -> dict:
    """Dispatch one already-decoded bridge request or reject an unknown op."""
    if op == "ping":
        return {"pong": True}
    if op == "pose":
        return node.get_pose(float(req.get("timeout", 2.0)))
    if op == "nav_send":
        return node.nav_send(
            req["x"],
            req["y"],
            req.get("yaw", 0.0),
            req.get("frame_id", "map"),
            req.get("tag", ""),
        )
    if op == "drive_to_pose":
        return node.drive_to_pose(
            req["x"],
            req["y"],
            req.get("yaw", 0.0),
            tag=req.get("tag", ""),
            cmd_vel_topic=req.get("cmd_vel_topic", "/cmd_vel"),
            stamped=bool(req.get("stamped", False)),
            max_linear=float(req.get("max_linear", 1.0)),
            max_angular=float(req.get("max_angular", 2.0)),
            tolerance=float(req.get("tolerance", 0.3)),
            timeout=float(req.get("timeout", 600.0)),
            avoidance=req.get("avoidance"),
        )
    if op == "nav_cancel":
        return node.nav_cancel()
    if op == "halt":
        return node.halt(
            req.get("cmd_vel_topic", "/cmd_vel"),
            bool(req.get("stamped", False)),
        )
    if op == "watchdog":
        result = watchdog.configure(req)
        # Pre-create the zero-velocity publisher now so DDS discovery is done
        # long before an emergency halt needs it.
        with node._halt_lock:
            node.ensure_halt_publisher(watchdog.cmd_vel_topic, watchdog.stamped)
        return result
    if op == "capture_frame":
        return node.capture_frame(req["topic"], float(req.get("timeout", 5.0)))
    if op == "avoid_snapshot":
        return node.avoid_snapshot(
            req.get("depth_topic", "/depth"),
            req["path"],
            int(req.get("frames", 5)),
            float(req.get("timeout", 10.0)),
        )
    if op == "watch":
        return node.watch(
            req["watch_id"],
            req["topic"],
            req["msg_type"],
            float(req.get("throttle", 1.0)),
        )
    if op == "unwatch":
        return node.unwatch(req["watch_id"])
    raise RuntimeError(f"unknown op '{op}'")
