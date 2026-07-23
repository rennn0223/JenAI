from __future__ import annotations

import math
from typing import Any

import pytest

from jenai.bridge._drive_control import DirectDriveController, terminal_status_after_halt


@pytest.mark.parametrize("status", ["succeeded", "canceled", "failed"])
def test_terminal_status_never_hides_a_failed_final_halt(status: str) -> None:
    assert terminal_status_after_halt(status, halt_completed=True) == status
    assert terminal_status_after_halt(status, halt_completed=False) == "halt_failed"


def _avoidance(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "enabled": True,
        "stop_distance": 0.6,
        "slow_distance": 2.0,
        "floor_ref": 0.0,
        "floor_tol": 0.2,
        "detour_clearance": 0.5,
        "detour_beyond": 1.2,
        "max_replans": 4,
        "depth_timeout_s": 1.0,
    }
    config.update(overrides)
    return config


def _controller(*, avoidance: dict[str, Any] | None = None) -> DirectDriveController:
    return DirectDriveController(
        5.0,
        0.0,
        max_linear=1.0,
        max_angular=2.0,
        tolerance=0.3,
        odom_timeout_s=1.0,
        avoidance=avoidance,
    )


def test_controller_reaches_goal_and_generates_bounded_motion() -> None:
    controller = _controller()

    move = controller.step(now=1.0, elapsed=1.0, x=0.0, y=0.0, yaw=0.0, odom_updated_at=1.0)
    arrived = controller.step(now=2.0, elapsed=2.0, x=4.8, y=0.0, yaw=0.0, odom_updated_at=2.0)

    assert move.action == "move"
    assert move.linear == pytest.approx(1.0)
    assert move.angular == pytest.approx(0.0)
    assert arrived.action == "terminal" and arrived.status == "succeeded"


def test_controller_clamps_turn_rate_and_slows_while_turning() -> None:
    controller = _controller()

    tick = controller.step(now=1.0, elapsed=1.0, x=0.0, y=0.0, yaw=math.pi, odom_updated_at=1.0)

    assert tick.action == "move"
    assert abs(tick.angular) <= 2.0
    assert tick.linear == pytest.approx(0.2)


def test_avoidance_fails_closed_on_missing_or_stale_depth() -> None:
    controller = _controller(avoidance=_avoidance())

    waiting = controller.step(now=0.5, elapsed=0.5, x=0.0, y=0.0, yaw=0.0, odom_updated_at=0.5)
    failed = controller.step(now=1.1, elapsed=1.1, x=0.0, y=0.0, yaw=0.0, odom_updated_at=1.1)

    assert waiting.action == "zero" and waiting.zero_first
    assert failed.action == "terminal"
    assert failed.status == "sensor_unavailable" and failed.zero_first


@pytest.mark.parametrize(
    ("ranges", "angles"),
    [([], []), ([1.0], []), ([1.0, 2.0], [0.0])],
)
def test_fresh_but_invalid_scan_vectors_still_fail_closed(
    ranges: list[float], angles: list[float]
) -> None:
    controller = _controller(avoidance=_avoidance())

    tick = controller.step(
        now=2.0,
        elapsed=2.0,
        x=0.0,
        y=0.0,
        yaw=0.0,
        odom_updated_at=2.0,
        ranges=ranges,
        angles=angles,
        scan_updated_at=2.0,
    )

    assert tick.action == "terminal"
    assert tick.status == "sensor_unavailable" and tick.zero_first


def test_obstacle_stops_before_planning_a_detour() -> None:
    controller = _controller(avoidance=_avoidance())

    tick = controller.step(
        now=1.0,
        elapsed=0.5,
        x=0.0,
        y=0.0,
        yaw=0.0,
        odom_updated_at=1.0,
        ranges=[math.inf, 1.0, math.inf],
        angles=[0.5, 0.0, -0.5],
        scan_updated_at=1.0,
    )

    assert tick.action == "zero" and tick.zero_first
    assert tick.recoveries == 1 and tick.avoiding
    assert len(controller.waypoints) == 2


def test_replan_budget_ends_blocked_instead_of_grinding() -> None:
    controller = _controller(avoidance=_avoidance(max_replans=0))

    tick = controller.step(
        now=1.0,
        elapsed=0.5,
        x=0.0,
        y=0.0,
        yaw=0.0,
        odom_updated_at=1.0,
        ranges=[math.inf, 1.0, math.inf],
        angles=[0.5, 0.0, -0.5],
        scan_updated_at=1.0,
    )

    assert tick.action == "terminal"
    assert tick.status == "blocked" and tick.zero_first


def test_reaching_detour_waypoint_advances_without_publishing_old_command() -> None:
    controller = _controller(avoidance=_avoidance())
    controller.waypoints = [(1.0, 1.0), (2.0, 1.0)]

    tick = controller.step(
        now=1.0,
        elapsed=0.5,
        x=1.0,
        y=1.0,
        yaw=0.0,
        odom_updated_at=1.0,
        ranges=[math.inf],
        angles=[0.0],
        scan_updated_at=1.0,
    )

    assert tick.action == "wait"
    assert controller.waypoints == [(2.0, 1.0)]


@pytest.mark.parametrize("updated_at", [0.0, float("nan"), 3.0])
def test_controller_stops_on_stale_or_invalid_odometry(updated_at: float) -> None:
    controller = _controller()

    tick = controller.step(
        now=2.0,
        elapsed=2.0,
        x=0.0,
        y=0.0,
        yaw=0.0,
        odom_updated_at=updated_at,
    )

    assert tick.action == "terminal"
    assert tick.status == "odom_unavailable"
    assert tick.zero_first
