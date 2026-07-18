from __future__ import annotations

import pytest

from jenai.bridge._navigation_state import (
    PoseJumpGuard,
    nav_result_status,
    navigation_active,
)
from jenai.bridge._watchdog import WatchdogState


@pytest.mark.parametrize(
    ("code", "expected"),
    [(4, "succeeded"), (5, "canceled"), (6, "aborted"), (0, "failed"), (99, "failed")],
)
def test_nav_result_status_is_stable(code: int, expected: str) -> None:
    assert nav_result_status(code) == expected


def test_navigation_pending_counts_as_active() -> None:
    assert navigation_active(has_goal_handle=False, nav_pending=True, drive_active=False)
    assert navigation_active(has_goal_handle=True, nav_pending=False, drive_active=False)
    assert navigation_active(has_goal_handle=False, nav_pending=False, drive_active=True)
    assert not navigation_active(has_goal_handle=False, nav_pending=False, drive_active=False)


def test_watchdog_is_disabled_until_configured_and_uses_requested_transport() -> None:
    now = [100.0]
    state = WatchdogState(clock=lambda: now[0])

    now[0] = 1_000.0
    assert not state.should_halt()
    assert state.configure({"timeout": 6.0, "cmd_vel_topic": "/base/cmd_vel", "stamped": True}) == {
        "watchdog_s": 6.0
    }
    assert state.cmd_vel_topic == "/base/cmd_vel"
    assert state.stamped is True


def test_watchdog_expires_retries_and_resets_when_client_returns() -> None:
    now = [100.0]
    state = WatchdogState(clock=lambda: now[0])
    state.configure({"timeout": 6.0})

    now[0] = 106.0
    assert not state.should_halt()  # the boundary itself is still alive
    now[0] = 106.01
    assert state.should_halt()

    state.mark_halted()
    now[0] = 108.01
    assert not state.should_halt()
    now[0] = 108.02
    assert state.should_halt()

    state.touch()
    assert not state.should_halt()
    now[0] = 114.03
    assert state.should_halt()


def test_pose_jump_guard_trips_once_for_near_instantaneous_discontinuity() -> None:
    now = [10.0]
    guard = PoseJumpGuard(threshold_m=5.0, window_s=2.0, clock=lambda: now[0])
    guard.arm()

    assert guard.observe(0.0, 0.0) is None
    now[0] += 0.5
    assert guard.observe(4.9, 0.0) is None
    now[0] += 0.1
    jump = guard.observe(10.1, 0.0)

    assert jump is not None
    assert jump.distance_m == pytest.approx(5.2)
    assert jump.elapsed_s == pytest.approx(0.1)
    assert guard.observe(30.0, 0.0) is None


def test_pose_jump_guard_resets_after_sparse_samples_and_when_rearmed() -> None:
    now = [10.0]
    guard = PoseJumpGuard(threshold_m=5.0, window_s=2.0, clock=lambda: now[0])
    guard.arm()
    assert guard.observe(0.0, 0.0) is None

    now[0] += 3.0
    assert guard.observe(20.0, 0.0) is None
    now[0] += 0.5
    assert guard.observe(22.0, 0.0) is None

    guard.disarm()
    now[0] += 0.1
    assert guard.observe(100.0, 0.0) is None
    guard.arm()
    assert guard.observe(100.0, 0.0) is None


def test_pose_jump_guard_fails_closed_on_non_finite_pose() -> None:
    guard = PoseJumpGuard()
    guard.arm()

    jump = guard.observe(float("nan"), 0.0)

    assert jump is not None
    assert jump.distance_m == float("inf")


@pytest.mark.parametrize(("threshold", "window"), [(0.0, 2.0), (5.0, 0.0), (float("inf"), 2.0)])
def test_pose_jump_guard_rejects_unsafe_configuration(threshold: float, window: float) -> None:
    with pytest.raises(ValueError):
        PoseJumpGuard(threshold_m=threshold, window_s=window)
