"""Pure direct-drive control decisions; no ROS imports or side effects."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

if __package__:
    from ._avoidance import (
        apply_floor_filter,
        corridor_nearest,
        plan_detour,
        scan_is_fresh,
    )
else:  # pragma: no cover - exercised by the system-Python sidecar
    from _avoidance import (  # type: ignore[no-redef]
        apply_floor_filter,
        corridor_nearest,
        plan_detour,
        scan_is_fresh,
    )

TickAction = Literal["move", "wait", "zero", "terminal"]


def terminal_status_after_halt(status: str, *, halt_completed: bool) -> str:
    """Never preserve a successful/ordinary terminal status when braking failed."""
    return status if halt_completed else "halt_failed"


def direct_drive_terminal_status(
    *, cancelled: bool, elapsed: float, timeout: float, odom_ready: bool, odom_timeout: float
) -> str | None:
    """Return the first terminal lifecycle condition for a direct-drive loop."""
    if cancelled:
        return "canceled"
    if elapsed > timeout:
        return "timed_out"
    if not odom_ready and elapsed > odom_timeout:
        return "odom_unavailable"
    return None


@dataclass(frozen=True, slots=True)
class DriveTick:
    """One control-loop decision produced from an odometry/depth snapshot."""

    action: TickAction
    distance_remaining: float
    linear: float = 0.0
    angular: float = 0.0
    status: str | None = None
    zero_first: bool = False
    recoveries: int = 0
    avoiding: bool = False


class DirectDriveController:
    """Stateful go-to-goal and stop-and-go detour policy.

    ROS owns sampling and publishing.  This class owns only deterministic
    decisions, so every branch can be checked without Isaac Sim or ``rclpy``.
    """

    def __init__(
        self,
        goal_x: float,
        goal_y: float,
        *,
        max_linear: float,
        max_angular: float,
        tolerance: float,
        odom_timeout_s: float,
        avoidance: dict[str, Any] | None = None,
    ) -> None:
        self.goal_x = goal_x
        self.goal_y = goal_y
        self.max_linear = max_linear
        self.max_angular = max_angular
        self.tolerance = tolerance
        self.odom_timeout_s = odom_timeout_s
        self.avoidance = avoidance if avoidance and avoidance.get("enabled") else None
        self.waypoints: list[tuple[float, float]] = []
        self.replans = 0

    def step(
        self,
        *,
        now: float,
        elapsed: float,
        x: float,
        y: float,
        yaw: float,
        odom_updated_at: float,
        ranges: list[float] | None = None,
        angles: list[float] | None = None,
        scan_updated_at: float | None = None,
    ) -> DriveTick:
        """Advance the deterministic drive policy by one sensor snapshot."""
        distance = math.hypot(self.goal_x - x, self.goal_y - y)
        readiness = self._readiness_tick(
            now=now,
            elapsed=elapsed,
            distance=distance,
            odom_updated_at=odom_updated_at,
            ranges=ranges,
            angles=angles,
            scan_updated_at=scan_updated_at,
        )
        if readiness is not None:
            return readiness
        if self._advance_reached_waypoint(x, y):
            return self._tick("wait", distance)

        target_x, target_y = self.waypoints[0] if self.waypoints else (self.goal_x, self.goal_y)
        bearing = math.atan2(target_y - y, target_x - x)
        heading_error = _wrapped_angle(bearing - yaw)
        obstacle_tick, nearest = self._obstacle_tick(
            x=x,
            y=y,
            yaw=yaw,
            distance=distance,
            heading_error=heading_error,
            ranges=ranges,
            angles=angles,
        )
        if obstacle_tick is not None:
            return obstacle_tick
        return self._motion_tick(
            target_x=target_x,
            target_y=target_y,
            x=x,
            y=y,
            distance=distance,
            heading_error=heading_error,
            nearest=nearest,
        )

    def _readiness_tick(
        self,
        *,
        now: float,
        elapsed: float,
        distance: float,
        odom_updated_at: float,
        ranges: list[float] | None,
        angles: list[float] | None,
        scan_updated_at: float | None,
    ) -> DriveTick | None:
        """Fail closed until odometry and configured obstacle sensing are usable."""
        odom_age = now - odom_updated_at
        if not math.isfinite(odom_age) or odom_age < 0.0 or odom_age > self.odom_timeout_s:
            return self._tick("terminal", distance, status="odom_unavailable", zero_first=True)
        if not self.waypoints and distance <= self.tolerance:
            return self._tick("terminal", distance, status="succeeded")
        if self.avoidance is None:
            return None

        depth_timeout = float(self.avoidance.get("depth_timeout_s", 1.0))
        scan_valid = (
            ranges is not None
            and angles is not None
            and len(ranges) > 0
            and len(ranges) == len(angles)
        )
        if scan_valid and scan_is_fresh(scan_updated_at, now=now, timeout_s=depth_timeout):
            return None
        if elapsed > depth_timeout:
            return self._tick("terminal", distance, status="sensor_unavailable", zero_first=True)
        return self._tick("zero", distance, zero_first=True)

    def _advance_reached_waypoint(self, x: float, y: float) -> bool:
        """Consume one reached detour without publishing its stale command."""
        waypoint_tolerance = max(0.35, self.tolerance)
        if not self.waypoints or self._distance_to(self.waypoints[0], x, y) > waypoint_tolerance:
            return False
        self.waypoints.pop(0)
        return True

    def _obstacle_tick(
        self,
        *,
        x: float,
        y: float,
        yaw: float,
        distance: float,
        heading_error: float,
        ranges: list[float] | None,
        angles: list[float] | None,
    ) -> tuple[DriveTick | None, float]:
        """Apply the bounded detour/replan policy and report nearest clearance."""
        avoid = self.avoidance
        if avoid is None or not ranges or not angles:
            return None, math.inf
        filtered = apply_floor_filter(
            ranges,
            float(avoid.get("floor_ref", 0.0)),
            float(avoid.get("floor_tol", 0.2)),
        )
        nearest = corridor_nearest(filtered, angles, heading_err=heading_error)
        stop_distance = float(avoid["stop_distance"])
        threshold = stop_distance if self.waypoints else float(avoid["slow_distance"])
        if nearest > threshold:
            return None, nearest

        self.replans += 1
        if self.replans > int(avoid.get("max_replans", 4)):
            return self._tick("terminal", distance, status="blocked", zero_first=True), nearest
        detour = plan_detour(
            x,
            y,
            yaw,
            self.goal_x,
            self.goal_y,
            filtered,
            angles,
            clearance=float(avoid.get("detour_clearance", 0.5)),
            beyond=float(avoid.get("detour_beyond", 1.2)),
        )
        if detour:
            self.waypoints = detour
        elif nearest <= stop_distance:
            return self._tick("terminal", distance, status="blocked", zero_first=True), nearest
        return self._tick("zero", distance, zero_first=True), nearest

    def _motion_tick(
        self,
        *,
        target_x: float,
        target_y: float,
        x: float,
        y: float,
        distance: float,
        heading_error: float,
        nearest: float,
    ) -> DriveTick:
        """Convert a clear target into bounded linear and angular velocity."""
        angular = _clamp(1.5 * heading_error, self.max_angular)
        alignment = max(0.2, math.cos(heading_error))
        target_distance = math.hypot(target_x - x, target_y - y)
        proximity = 1.0
        avoid = self.avoidance
        if avoid is not None and math.isfinite(nearest):
            stop_distance = float(avoid["stop_distance"])
            slow_distance = float(avoid["slow_distance"])
            proximity = (nearest - stop_distance) / max(1e-3, slow_distance - stop_distance)
            proximity = max(0.3, min(1.0, proximity))
        linear = alignment * min(self.max_linear, 0.8 * target_distance + 0.2) * proximity
        return self._tick("move", distance, linear=linear, angular=angular)

    def _tick(
        self,
        action: TickAction,
        distance: float,
        *,
        linear: float = 0.0,
        angular: float = 0.0,
        status: str | None = None,
        zero_first: bool = False,
    ) -> DriveTick:
        return DriveTick(
            action=action,
            distance_remaining=distance,
            linear=linear,
            angular=angular,
            status=status,
            zero_first=zero_first,
            recoveries=self.replans,
            avoiding=bool(self.waypoints),
        )

    @staticmethod
    def _distance_to(target: tuple[float, float], x: float, y: float) -> float:
        return math.hypot(target[0] - x, target[1] - y)


def _wrapped_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))
