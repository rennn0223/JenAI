"""Thread-safe latest-value storage for long-lived ROS observations."""

from __future__ import annotations

import threading


class LatchedObservation[ObservationT]:
    """Retain the newest callback value and wait only for the first sample.

    ROS static-map publishers commonly use transient-local durability. Keeping
    one subscription and its latest value avoids repeatedly joining DDS late
    during a long workflow while still requiring at least one real sample.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._latest: ObservationT | None = None

    def observe(self, value: ObservationT) -> None:
        """Atomically replace the retained value and wake first-sample waiters."""

        with self._lock:
            self._latest = value
            self._ready.set()

    def wait(self, timeout: float) -> ObservationT:
        """Return the newest value or raise when no sample arrives in time."""

        if not self._ready.wait(timeout):
            raise TimeoutError("No observation received before the timeout")
        with self._lock:
            value = self._latest
        if value is None:  # Defensive: ``observe`` never signals without a value.
            raise RuntimeError("Observation became unavailable after readiness")
        return value
