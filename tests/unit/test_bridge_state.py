from __future__ import annotations

from types import SimpleNamespace

import pytest

from jenai.bridge._navigation_state import (
    NavigationGenerations,
    PoseJumpGuard,
    cancellation_is_confirmed,
    localization_halt_terminal,
    nav_result_status,
    navigation_active,
    wait_for_cancel_acknowledgement,
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


def test_pose_jump_guard_fails_closed_after_long_sample_gap_and_rearms() -> None:
    now = [10.0]
    guard = PoseJumpGuard(threshold_m=5.0, window_s=2.0, clock=lambda: now[0])
    guard.arm()
    assert guard.observe(0.0, 0.0) is None

    now[0] += 10.0
    jump = guard.observe(24.0, 0.0)
    assert jump is not None
    assert jump.distance_m == pytest.approx(24.0)
    assert jump.elapsed_s == pytest.approx(10.0)

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


def test_stale_generation_cannot_finish_or_disarm_new_goal() -> None:
    generations = NavigationGenerations()
    first = generations.begin("goal-a")
    second = generations.begin("goal-b")
    guard = PoseJumpGuard(threshold_m=5.0, window_s=2.0)
    guard.arm(second)

    assert generations.finish(first) is False
    assert generations.active == second
    assert guard.disarm(first) is False
    assert guard.observe(0.0, 0.0) is None
    jump = guard.observe(6.0, 0.0)
    assert jump is not None
    assert jump.token == second
    assert generations.finish(second) is True


class _CancelFuture:
    def __init__(self, response=None, error: Exception | None = None, *, completes=True) -> None:
        self._response = response
        self._error = error
        self._completes = completes

    def add_done_callback(self, callback) -> None:
        if self._completes:
            callback(self)

    def done(self) -> bool:
        return self._completes

    def result(self):
        if self._error is not None:
            raise self._error
        return self._response


def test_cancel_requires_positive_ros_acknowledgement() -> None:
    accepted = _CancelFuture(SimpleNamespace(goals_canceling=[object()]))
    rejected = _CancelFuture(SimpleNamespace(goals_canceling=[]))
    failed = _CancelFuture(error=RuntimeError("cancel service failed"))
    timed_out = _CancelFuture(completes=False)

    assert wait_for_cancel_acknowledgement(accepted, 0.001) is True
    assert wait_for_cancel_acknowledgement(rejected, 0.001) is False
    assert wait_for_cancel_acknowledgement(failed, 0.001) is False
    assert wait_for_cancel_acknowledgement(timed_out, 0.001) is False


def test_localization_halt_terminal_never_claims_unacknowledged_cancel() -> None:
    status, detail = localization_halt_terminal("jump", cancel_acknowledged=False)
    assert status == "localization_jump_halt_unconfirmed"
    assert "not acknowledged" in detail
    assert "zero velocity pulses were sent" in detail

    status, detail = localization_halt_terminal("jump", cancel_acknowledged=True)
    assert status == "localization_jump"
    assert "cancellation was acknowledged" in detail

    status, detail = localization_halt_terminal(
        "jump",
        cancel_acknowledged=False,
        error=RuntimeError("publisher failed"),
    )
    assert status == "localization_jump_halt_failed"
    assert "Emergency halt failed" in detail


def test_old_goal_ack_cannot_confirm_pending_current_goal_cancel() -> None:
    assert not cancellation_is_confirmed(
        has_owned_active=True,
        any_acknowledged=True,
        active_goal_acknowledged=False,
    )
    assert cancellation_is_confirmed(
        has_owned_active=True,
        any_acknowledged=True,
        active_goal_acknowledged=True,
    )
    assert cancellation_is_confirmed(
        has_owned_active=False,
        any_acknowledged=True,
        active_goal_acknowledged=False,
    )
