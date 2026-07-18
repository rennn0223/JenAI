"""Pure navigation-state decisions for the system-Python rclpy sidecar."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


def nav_result_status(status_code: int) -> str:
    """Map ROS GoalStatus codes onto JenAI's stable bridge protocol."""
    return {4: "succeeded", 5: "canceled", 6: "aborted"}.get(status_code, "failed")


def navigation_active(*, has_goal_handle: bool, nav_pending: bool, drive_active: bool) -> bool:
    """A not-yet-accepted goal is active too, so emergency stop cannot miss it."""
    return has_goal_handle or nav_pending or drive_active


@dataclass(frozen=True)
class PoseJump:
    """One implausible map-frame displacement observed during navigation."""

    distance_m: float
    elapsed_s: float
    threshold_m: float


class PoseJumpGuard:
    """Detect a large, near-instantaneous discontinuity in ``/amcl_pose``.

    This pure state object owns no ROS entities or stopping behavior. A long
    sample gap resets the baseline, avoiding false stops after ordinary travel
    during an AMCL outage.
    """

    def __init__(
        self,
        *,
        threshold_m: float = 5.0,
        window_s: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._threshold_m = 0.0
        self._window_s = 0.0
        self._armed = False
        self._tripped = False
        self._previous: tuple[float, float, float] | None = None
        self.configure(threshold_m=threshold_m, window_s=window_s)

    def configure(self, *, threshold_m: float, window_s: float) -> None:
        """Set conservative bounds and reset any observation in progress."""
        threshold_m = float(threshold_m)
        window_s = float(window_s)
        if not math.isfinite(threshold_m) or threshold_m <= 0:
            raise ValueError("pose-jump threshold must be a positive finite number")
        if not math.isfinite(window_s) or window_s <= 0:
            raise ValueError("pose-jump window must be a positive finite number")
        with self._lock:
            self._threshold_m = threshold_m
            self._window_s = window_s
            self._armed = False
            self._tripped = False
            self._previous = None

    def arm(self) -> None:
        """Start a fresh observation window for one navigation goal."""
        with self._lock:
            self._armed = True
            self._tripped = False
            self._previous = None

    def disarm(self) -> None:
        with self._lock:
            self._armed = False
            self._previous = None

    def observe(self, x: float, y: float) -> PoseJump | None:
        """Observe a map pose and return exactly once when the guard trips."""
        now = self._clock()
        x, y = float(x), float(y)
        with self._lock:
            if not self._armed or self._tripped:
                return None
            if not math.isfinite(x) or not math.isfinite(y):
                self._tripped = True
                return PoseJump(math.inf, 0.0, self._threshold_m)

            previous = self._previous
            self._previous = (x, y, now)
            if previous is None:
                return None
            px, py, observed_at = previous
            elapsed = now - observed_at
            if elapsed <= 0 or elapsed > self._window_s:
                return None
            distance = math.hypot(x - px, y - py)
            if distance <= self._threshold_m:
                return None
            self._tripped = True
            return PoseJump(distance, elapsed, self._threshold_m)
