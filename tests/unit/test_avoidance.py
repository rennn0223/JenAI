from __future__ import annotations

import math

from jenai.bridge._avoidance import (
    StuckDetector,
    apply_floor_filter,
    corridor_nearest,
    plan_detour,
    scan_is_fresh,
)

# 15 sectors spanning ±45° (hfov 90): angles left(+)→right(-), like the bridge.
ANGLES = [math.radians(90 * (0.5 - (i + 0.5) / 15)) for i in range(15)]
INF = math.inf


def test_floor_filter_clears_ground_ring_keeps_obstacle() -> None:
    """Live-caught: a down-pitched camera reads the floor as a uniform 1.6 m
    ring, so the robot thought it was permanently surrounded. The filter turns
    the ring into clear space and keeps the cube standing above the floor."""
    live_scan = [1.6, 1.6, 1.6, 1.6, 1.1, 1.6, 1.6, 1.6, 1.6]
    filtered = apply_floor_filter(live_scan, floor_ref=1.6, tol=0.2)
    assert filtered[4] == 1.1  # the cube survives
    assert all(math.isinf(r) for i, r in enumerate(filtered) if i != 4)
    assert apply_floor_filter(live_scan, floor_ref=0.0, tol=0.2) == live_scan  # off = passthrough


def test_detour_rounds_obstacle_dead_ahead() -> None:
    """Cube cluster dead ahead at 2 m, goal 8 m out on the same line → two
    waypoints: one clearly beside the obstacle, one past it near the line."""
    ranges = [INF] * 15
    ranges[6] = ranges[7] = ranges[8] = 2.0
    wps = plan_detour(0.0, 0.0, 0.0, 8.0, 0.0, ranges, ANGLES, clearance=0.5)
    assert wps is not None and len(wps) == 2
    (x1, y1), (x2, y2) = wps
    assert abs(y1) >= 0.6  # half-width + clearance, not a shave
    assert x2 > x1  # second leg moves past the obstacle toward the goal
    assert abs(y2) < abs(y1)  # and starts rejoining the original line
    assert math.copysign(1, y1) == math.copysign(1, y2)  # same side, no S-curve


def test_detour_picks_the_open_side() -> None:
    ranges = [INF] * 15
    ranges[6] = ranges[7] = ranges[8] = 2.0
    for blocked_side, sign in ((range(9, 15), 1.0), (range(0, 6), -1.0)):
        r = list(ranges)
        for i in blocked_side:
            r[i] = 1.8
        wps = plan_detour(0.0, 0.0, 0.0, 8.0, 0.0, r, ANGLES)
        assert wps is not None
        # right flank blocked → go left, and vice versa
        assert math.copysign(1.0, wps[0][1]) == sign


def test_detour_none_when_goal_is_before_obstacle() -> None:
    ranges = [INF] * 15
    ranges[7] = 2.0
    assert plan_detour(0.0, 0.0, 0.0, 1.0, 0.0, ranges, ANGLES) is None


def test_detour_none_on_clear_scan() -> None:
    assert plan_detour(0.0, 0.0, 0.0, 8.0, 0.0, [INF] * 15, ANGLES) is None


def test_detour_respects_vehicle_yaw() -> None:
    """Obstacle bearing is camera-relative: with the car facing +y, a dead-
    ahead cluster is at odom (0, 2), and the detour must sidestep in x."""
    ranges = [INF] * 15
    ranges[7] = 2.0
    wps = plan_detour(0.0, 0.0, math.pi / 2, 0.0, 8.0, ranges, ANGLES)
    assert wps is not None
    assert abs(wps[0][0]) >= 0.6  # lateral in odom-x now
    assert wps[1][1] > 2.0  # second waypoint is past the obstacle in +y


def test_stuck_detector_fires_only_when_blocked_and_stationary() -> None:
    """Live-caught: blocked + boxed in, the crawl floor ground the nose
    against the cube for 30 s until the drive timed out. The detector ends
    that honestly: blocked AND <5 cm of motion for the window → abort."""
    d = StuckDetector(window_s=3.0, min_move=0.05)
    assert d.update(0.0, 0.0, 0.0, blocked=False) is False  # free driving
    assert d.update(1.0, 0.0, 0.0, blocked=True) is False  # anchor set
    assert d.update(2.0, 0.5, 0.0, blocked=True) is False  # blocked but moving
    assert d.update(4.9, 0.51, 0.0, blocked=True) is False  # window not elapsed
    assert d.update(5.1, 0.52, 0.0, blocked=True) is True  # pinned 3.1 s → fire


def test_stuck_detector_resets_when_path_clears() -> None:
    d = StuckDetector(window_s=3.0, min_move=0.05)
    d.update(0.0, 0.0, 0.0, blocked=True)
    assert d.update(2.9, 0.0, 0.0, blocked=True) is False
    d.update(3.0, 0.0, 0.0, blocked=False)  # obstacle cleared → reset
    assert d.update(6.5, 0.0, 0.0, blocked=True) is False  # fresh anchor


def test_scan_freshness_fails_closed_without_or_after_updates() -> None:
    assert scan_is_fresh(None, now=10.0, timeout_s=1.0) is False
    assert scan_is_fresh(9.1, now=10.0, timeout_s=1.0) is True
    assert scan_is_fresh(9.0, now=10.0, timeout_s=1.0) is True
    assert scan_is_fresh(8.99, now=10.0, timeout_s=1.0) is False


def test_corridor_rotates_with_target_heading() -> None:
    ranges = [1.0, 1.0]
    angles = [0.0, 0.8]

    assert corridor_nearest(ranges, angles, heading_err=0.0, half_width=0.2) == 1.0
    assert corridor_nearest(ranges, angles, heading_err=0.8, half_width=0.2) == 1.0
    assert math.isinf(
        corridor_nearest([1.0], [0.0], heading_err=0.8, half_width=0.2)
    )
