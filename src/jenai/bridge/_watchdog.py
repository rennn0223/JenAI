"""Pure client-liveness watchdog state shared by the rclpy bridge and tests."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from typing import Any


class WatchdogState:
    """Decide when a quiet bridge client requires an emergency halt.

    Disabled until configured with a positive timeout. Once expired, the
    watchdog retries at a short fixed cadence until the client sends another
    request; retries must not wait for the full liveness timeout again.
    """

    RETRY_S = 2.0

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.lock = threading.Lock()
        self._clock = clock
        self.last_rx = clock()
        self.last_halt: float | None = None
        self.timeout_s = 0.0
        self.cmd_vel_topic = "/cmd_vel"
        self.stamped = False

    def touch(self) -> None:
        with self.lock:
            self.last_rx = self._clock()
            self.last_halt = None

    def configure(self, req: dict[str, Any]) -> dict[str, Any]:
        timeout = req.get("timeout")
        cmd_vel_topic = req.get("cmd_vel_topic", "/cmd_vel")
        stamped = req.get("stamped", False)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("invalid watchdog request: timeout must be numeric")
        parsed_timeout = float(timeout)
        if not math.isfinite(parsed_timeout) or parsed_timeout <= 0.0:
            raise ValueError("invalid watchdog request: timeout must be positive and finite")
        if not isinstance(cmd_vel_topic, str) or not cmd_vel_topic.strip():
            raise ValueError("invalid watchdog request: cmd_vel_topic must be non-empty text")
        if type(stamped) is not bool:
            raise ValueError("invalid watchdog request: stamped must be a boolean")
        with self.lock:
            self.timeout_s = parsed_timeout
            self.cmd_vel_topic = cmd_vel_topic
            self.stamped = stamped
        return {"watchdog_s": self.timeout_s}

    def should_halt(self) -> bool:
        """Return true once on expiry, then once per retry interval."""
        with self.lock:
            now = self._clock()
            if self.timeout_s <= 0 or now - self.last_rx <= self.timeout_s:
                return False
            return self.last_halt is None or now - self.last_halt > self.RETRY_S

    def mark_halted(self) -> None:
        with self.lock:
            self.last_halt = self._clock()
