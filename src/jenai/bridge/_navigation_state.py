"""Pure navigation-state decisions for the system-Python rclpy sidecar."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


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
    token: object | None = None


@dataclass(frozen=True)
class NavigationGoalToken:
    """Identity for one goal across asynchronous ROS action callbacks."""

    generation: int
    tag: str


class NavigationGenerations:
    """Track the current goal without letting stale callbacks finish it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_generation = 0
        self._active: NavigationGoalToken | None = None

    def begin(self, tag: str) -> NavigationGoalToken:
        with self._lock:
            self._next_generation += 1
            token = NavigationGoalToken(self._next_generation, tag)
            self._active = token
            return token

    @property
    def active(self) -> NavigationGoalToken | None:
        with self._lock:
            return self._active

    def is_current(self, token: NavigationGoalToken) -> bool:
        with self._lock:
            return self._active == token

    def finish(self, token: NavigationGoalToken) -> bool:
        """Finish only ``token``; return False for a delayed older callback."""
        with self._lock:
            if self._active != token:
                return False
            self._active = None
            return True


def wait_for_cancel_acknowledgement(future: Any, timeout_s: float) -> bool:
    """Wait bounded for a ROS CancelGoal response that names a canceling goal."""
    done = threading.Event()
    try:
        future.add_done_callback(lambda _future: done.set())
        if not done.wait(timeout_s) or not future.done():
            return False
        response = future.result()
    except Exception:
        return False
    return bool(response is not None and getattr(response, "goals_canceling", ()))


def cancellation_is_confirmed(
    *,
    has_owned_active: bool,
    any_acknowledged: bool,
    active_goal_acknowledged: bool,
) -> bool:
    """Do not let an old/cross-process acknowledgement stand in for our active goal."""
    if has_owned_active:
        return active_goal_acknowledged
    return any_acknowledged


def localization_halt_terminal(
    reason: str,
    *,
    cancel_acknowledged: bool,
    error: Exception | None = None,
) -> tuple[str, str]:
    """Return a truthful terminal status/detail for a localization emergency."""
    if error is not None:
        return (
            "localization_jump_halt_failed",
            f"{reason} Emergency halt failed: {error}",
        )
    if not cancel_acknowledged:
        return (
            "localization_jump_halt_unconfirmed",
            f"{reason} Nav2 cancellation was not acknowledged; zero velocity pulses were sent.",
        )
    return (
        "localization_jump",
        f"{reason} Nav2 cancellation was acknowledged and zero velocity sent.",
    )


class PoseJumpGuard:
    """Detect a large, near-instantaneous discontinuity in ``/amcl_pose``.

    This pure state object owns no ROS entities or stopping behavior. A long
    sample gap does not reset the baseline: continuing an active Nav2 goal after
    an unbounded localization outage would silently admit simulator resets.
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
        self._token: object | None = None
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
            self._token = None

    def arm(self, token: object | None = None) -> None:
        """Start a fresh observation window for one navigation goal."""
        with self._lock:
            self._armed = True
            self._tripped = False
            self._previous = None
            self._token = token

    def disarm(self, token: object | None = None) -> bool:
        """Disarm the matching goal; a stale token cannot disarm a newer goal."""
        with self._lock:
            if token is not None and token != self._token:
                return False
            self._armed = False
            self._previous = None
            self._token = None
            return True

    def observe(self, x: float, y: float) -> PoseJump | None:
        """Observe a map pose and return exactly once when the guard trips."""
        now = self._clock()
        x, y = float(x), float(y)
        with self._lock:
            if not self._armed or self._tripped:
                return None
            if not math.isfinite(x) or not math.isfinite(y):
                self._tripped = True
                return PoseJump(math.inf, 0.0, self._threshold_m, self._token)

            previous = self._previous
            self._previous = (x, y, now)
            if previous is None:
                return None
            px, py, observed_at = previous
            elapsed = now - observed_at
            if elapsed <= 0:
                return None
            distance = math.hypot(x - px, y - py)
            # Keep the configured displacement ceiling across sparse updates.
            # While a goal is active, losing localization long enough to travel
            # farther than this bound is itself unsafe and must fail closed.
            if distance <= self._threshold_m:
                return None
            self._tripped = True
            return PoseJump(distance, elapsed, self._threshold_m, self._token)
