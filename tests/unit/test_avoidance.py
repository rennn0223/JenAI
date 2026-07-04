from __future__ import annotations

import math

from jenai.bridge._avoidance import follow_the_gap

# 9 sectors spanning ±45° (hfov 90): angles left(+)→right(-).
ANGLES = [math.radians(45 - (i + 0.5) * 90 / 9) for i in range(9)]
CLEAR = [10.0] * 9  # nothing anywhere
KW = {"stop_distance": 0.6, "slow_distance": 2.0, "hfov_deg": 90.0}


def test_clear_path_seeks_goal_unchanged() -> None:
    steer, scale, blocked = follow_the_gap(0.3, CLEAR, ANGLES, **KW)
    assert steer == 0.3 and scale == 1.0 and blocked is False


def test_empty_scan_is_pure_seeking() -> None:
    steer, scale, blocked = follow_the_gap(-0.2, [], [], **KW)
    assert steer == -0.2 and scale == 1.0 and blocked is False


def test_obstacle_dead_ahead_steers_to_a_clear_side() -> None:
    # Center 3 sectors blocked at 1.0 m, sides clear → steer off-center.
    ranges = [10, 10, 10, 1.0, 1.0, 1.0, 10, 10, 10]
    steer, scale, blocked = follow_the_gap(0.0, ranges, ANGLES, **KW)
    assert blocked is True
    assert abs(steer) > math.radians(15)  # not straight ahead
    assert 0.0 < scale < 1.0  # slowed by the 1.0 m clearance


def test_gap_choice_biased_toward_goal_bearing() -> None:
    # Both sides clear; goal is to the RIGHT → pick a right (negative) gap.
    ranges = [10, 10, 10, 1.0, 1.0, 1.0, 10, 10, 10]
    steer, _, _ = follow_the_gap(math.radians(-40), ranges, ANGLES, **KW)
    assert steer < 0  # chose the goal-side gap, not the mirror


def test_too_close_stops_forward() -> None:
    ranges = [10, 10, 10, 0.4, 0.4, 0.4, 10, 10, 10]  # 0.4 < stop_distance
    steer, scale, blocked = follow_the_gap(0.0, ranges, ANGLES, **KW)
    assert blocked is True and scale == 0.0 and abs(steer) > 0


def test_boxed_in_aims_at_clearest_sector() -> None:
    # Everything within slow_distance, no true gap; rightmost is least-bad.
    ranges = [0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.9]
    steer, scale, blocked = follow_the_gap(0.0, ranges, ANGLES, **KW)
    assert blocked is True
    assert steer == ANGLES[8]  # the 1.9 m sector (max range)


def test_speed_scales_with_clearance() -> None:
    near = follow_the_gap(0.0, [10, 10, 10, 0.8, 0.8, 0.8, 10, 10, 10], ANGLES, **KW)[1]
    far = follow_the_gap(0.0, [10, 10, 10, 1.8, 1.8, 1.8, 10, 10, 10], ANGLES, **KW)[1]
    assert far > near  # more clearance → faster
