"""JSON request protocol dispatch for the rclpy bridge sidecar.

The bridge process owns ROS entities and real-time-ish safety behavior.  This
module only validates/defaults one request and invokes the matching node method,
which keeps wire compatibility testable without importing rclpy.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ._watchdog import WatchdogState
elif __package__:
    # Package import in unit tests / JenAI runtime. Do not catch ImportError
    # here: a broken dependency must remain visible instead of silently using
    # a different module from sys.path.
    from ._watchdog import WatchdogState
else:  # pragma: no cover - exercised by the system-Python sidecar
    # Script import when ros_bridge.py runs outside the JenAI venv.
    from _watchdog import WatchdogState


WirePayload = dict[str, Any]
_MISSING = object()


def _field(req: WirePayload, name: str, default: object = _MISSING) -> object:
    if name in req:
        return req[name]
    if default is _MISSING:
        raise ValueError(f"invalid bridge request: missing {name}")
    return default


def _number(
    req: WirePayload,
    name: str,
    default: object = _MISSING,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> float:
    value = _field(req, name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid bridge request: {name} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"invalid bridge request: {name} must be finite")
    if positive and parsed <= 0.0:
        raise ValueError(f"invalid bridge request: {name} must be positive")
    if nonnegative and parsed < 0.0:
        raise ValueError(f"invalid bridge request: {name} cannot be negative")
    return parsed


def _integer(
    req: WirePayload,
    name: str,
    default: object = _MISSING,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = _field(req, name, default)
    if type(value) is not int:
        raise ValueError(f"invalid bridge request: {name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"invalid bridge request: {name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"invalid bridge request: {name} must be at most {maximum}")
    return value


def _boolean(req: WirePayload, name: str, default: object = _MISSING) -> bool:
    value = _field(req, name, default)
    if type(value) is not bool:
        raise ValueError(f"invalid bridge request: {name} must be a boolean")
    return value


def _text(
    req: WirePayload,
    name: str,
    default: object = _MISSING,
    *,
    allow_empty: bool = False,
) -> str:
    value = _field(req, name, default)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        qualifier = "text" if allow_empty else "non-empty text"
        raise ValueError(f"invalid bridge request: {name} must be {qualifier}")
    return value


def _avoidance(req: WirePayload) -> WirePayload | None:
    raw = req.get("avoidance")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("invalid bridge request: avoidance must be an object or null")

    normalized: WirePayload = {
        "enabled": _boolean(raw, "enabled", False),
        "depth_topic": _text(raw, "depth_topic", "/depth"),
        "stop_distance": _number(raw, "stop_distance", 0.6, positive=True),
        "slow_distance": _number(raw, "slow_distance", 2.0, positive=True),
        "hfov_deg": _number(raw, "hfov_deg", 90.0, positive=True),
        "sectors": _integer(raw, "sectors", 15, minimum=3, maximum=360),
        "band_lo": _number(raw, "band_lo", 0.45, nonnegative=True),
        "band_hi": _number(raw, "band_hi", 0.60, nonnegative=True),
        "min_valid": _number(raw, "min_valid", 0.1, nonnegative=True),
        "floor_ref": _number(raw, "floor_ref", 0.0, nonnegative=True),
        "floor_tol": _number(raw, "floor_tol", 0.2, nonnegative=True),
        "floor_snapshot": _text(raw, "floor_snapshot", "", allow_empty=True),
        "detour_clearance": _number(raw, "detour_clearance", 0.5, positive=True),
        "detour_beyond": _number(raw, "detour_beyond", 1.2, positive=True),
        "max_replans": _integer(raw, "max_replans", 4, minimum=0, maximum=100),
        "depth_timeout_s": _number(raw, "depth_timeout_s", 1.0, positive=True),
    }
    if normalized["stop_distance"] >= normalized["slow_distance"]:
        raise ValueError("invalid bridge request: stop_distance must be less than slow_distance")
    if normalized["hfov_deg"] > 180.0:
        raise ValueError("invalid bridge request: hfov_deg must be at most 180")
    if not 0.0 <= normalized["band_lo"] <= normalized["band_hi"] <= 1.0:
        raise ValueError("invalid bridge request: depth band must be ordered within 0..1")
    return normalized


class BridgeNodeProtocol(Protocol):
    """Operations exposed by the rclpy sidecar to the wire dispatcher."""

    _halt_lock: Any

    def get_pose(self, timeout: float) -> WirePayload: ...

    def map_cell(self, x: float, y: float, timeout: float) -> WirePayload: ...

    def map_identity(self, timeout: float) -> WirePayload: ...

    def nav_send(self, x: float, y: float, yaw: float, frame_id: str, tag: str) -> WirePayload: ...

    def nav_plan(
        self, x: float, y: float, yaw: float, frame_id: str, timeout: float
    ) -> WirePayload: ...

    def drive_to_pose(
        self,
        x: float,
        y: float,
        yaw: float,
        *,
        tag: str,
        cmd_vel_topic: str,
        stamped: bool,
        max_linear: float,
        max_angular: float,
        tolerance: float,
        odom_timeout_s: float,
        timeout: float,
        avoidance: Any,
    ) -> WirePayload: ...

    def nav_cancel(self) -> WirePayload: ...

    def halt(self, cmd_vel_topic: str, stamped: bool) -> WirePayload: ...

    def configure_pose_jump_guard(
        self, threshold_m: float, window_s: float, cmd_vel_topic: str, stamped: bool
    ) -> WirePayload: ...

    def ensure_halt_publisher(self, cmd_vel_topic: str, stamped: bool) -> None: ...

    def capture_frame(self, topic: str, timeout: float) -> WirePayload: ...

    def avoid_snapshot(
        self, depth_topic: str, path: str, frames: int, timeout: float
    ) -> WirePayload: ...

    def watch(self, watch_id: int, topic: str, msg_type: str, throttle: float) -> WirePayload: ...

    def unwatch(self, watch_id: int) -> WirePayload: ...


def dispatch_request(
    node: BridgeNodeProtocol,
    op: str,
    req: WirePayload,
    watchdog: WatchdogState,
) -> WirePayload:
    """Dispatch one already-decoded bridge request or reject an unknown op."""
    if op == "ping":
        return {"pong": True}
    if op == "pose":
        return node.get_pose(_number(req, "timeout", 2.0, positive=True))
    if op == "map_cell":
        return node.map_cell(
            _number(req, "x"),
            _number(req, "y"),
            _number(req, "timeout", 3.0, positive=True),
        )
    if op == "map_identity":
        return node.map_identity(_number(req, "timeout", 3.0, positive=True))
    if op == "nav_send":
        return node.nav_send(
            _number(req, "x"),
            _number(req, "y"),
            _number(req, "yaw", 0.0),
            _text(req, "frame_id", "map"),
            _text(req, "tag", "", allow_empty=True),
        )
    if op == "nav_plan":
        return node.nav_plan(
            _number(req, "x"),
            _number(req, "y"),
            _number(req, "yaw", 0.0),
            _text(req, "frame_id", "map"),
            _number(req, "timeout", 5.0, positive=True),
        )
    if op == "drive_to_pose":
        return node.drive_to_pose(
            _number(req, "x"),
            _number(req, "y"),
            _number(req, "yaw", 0.0),
            tag=_text(req, "tag", "", allow_empty=True),
            cmd_vel_topic=_text(req, "cmd_vel_topic", "/cmd_vel"),
            stamped=_boolean(req, "stamped", False),
            max_linear=_number(req, "max_linear", 1.0, positive=True),
            max_angular=_number(req, "max_angular", 2.0, positive=True),
            tolerance=_number(req, "tolerance", 0.3, positive=True),
            odom_timeout_s=_number(req, "odom_timeout_s", 1.0, positive=True),
            timeout=_number(req, "timeout", 600.0, positive=True),
            avoidance=_avoidance(req),
        )
    if op == "nav_cancel":
        return node.nav_cancel()
    if op == "halt":
        return node.halt(
            _text(req, "cmd_vel_topic", "/cmd_vel"),
            _boolean(req, "stamped", False),
        )
    if op == "watchdog":
        normalized_watchdog = {
            "timeout": _number(req, "timeout", 0.0, positive=True),
            "cmd_vel_topic": _text(req, "cmd_vel_topic", "/cmd_vel"),
            "stamped": _boolean(req, "stamped", False),
        }
        result = watchdog.configure(normalized_watchdog)
        node.configure_pose_jump_guard(
            _number(req, "pose_jump_threshold_m", 5.0, positive=True),
            _number(req, "pose_jump_window_s", 2.0, positive=True),
            watchdog.cmd_vel_topic,
            watchdog.stamped,
        )
        # Pre-create the zero-velocity publisher now so DDS discovery is done
        # long before an emergency halt needs it.
        with node._halt_lock:
            node.ensure_halt_publisher(watchdog.cmd_vel_topic, watchdog.stamped)
        return result
    if op == "capture_frame":
        return node.capture_frame(
            _text(req, "topic"),
            _number(req, "timeout", 5.0, positive=True),
        )
    if op == "avoid_snapshot":
        return node.avoid_snapshot(
            _text(req, "depth_topic", "/depth"),
            _text(req, "path"),
            _integer(req, "frames", 5, minimum=1),
            _number(req, "timeout", 10.0, positive=True),
        )
    if op == "watch":
        return node.watch(
            _integer(req, "watch_id", minimum=1),
            _text(req, "topic"),
            _text(req, "msg_type"),
            _number(req, "throttle", 1.0, nonnegative=True),
        )
    if op == "unwatch":
        return node.unwatch(_integer(req, "watch_id", minimum=1))
    raise RuntimeError(f"unknown op '{op}'")
