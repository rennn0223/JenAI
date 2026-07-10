"""Pure emergency-stop sequencing shared by the ROS sidecar and tests."""

from __future__ import annotations

from collections.abc import Callable


def halt_in_order(
    send_zero: Callable[[int], None],
    cancel_navigation: Callable[[], bool],
    *,
    pulses: int,
) -> bool:
    """Command zero before and after the potentially slow cancel round-trip."""
    count = max(1, pulses)
    send_zero(min(2, count))
    canceled = bool(cancel_navigation())
    send_zero(count)
    return canceled
