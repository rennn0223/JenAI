"""Pure follow-the-gap steering law for the odom driver's reactive avoidance.

Stdlib only (no rclpy, no jenai) so it is importable BOTH by the bridge —
which runs under system python and picks this up as a sibling module — and by
the venv test suite as ``jenai.bridge._avoidance``. Keeping the decision pure
(no ROS entities) is what makes the branch logic unit-testable; ros_bridge.py
owns only the I/O (depth → ranges, ranges → cmd_vel).
"""

from __future__ import annotations

import math


def follow_the_gap(
    heading_err: float,
    ranges: list[float],
    angles: list[float],
    *,
    stop_distance: float,
    slow_distance: float,
    hfov_deg: float,
) -> tuple[float, float, bool]:
    """Blend go-to-goal with obstacle avoidance.

    Inputs: ``heading_err`` = goal bearing relative to robot forward (rad);
    ``ranges``/``angles`` = a pseudo-laserscan (nearest metres per sector, and
    each sector's angle, +left/-right). Returns
    ``(steer_angle, forward_scale, blocked)``:

    - ``steer_angle`` — direction to point, relative to robot forward.
    - ``forward_scale`` — 0..1 speed multiplier (0 = crawl-and-turn only).
    - ``blocked`` — an obstacle is within ``slow_distance`` ahead.

    When nothing is within ``slow_distance`` in the forward cone it returns the
    goal bearing unchanged (pure seeking → the robot rejoins its line to goal
    as the obstacle clears).
    """
    if not ranges:
        return heading_err, 1.0, False
    cone = math.radians(min(30.0, hfov_deg / 2.0))
    forward = [r for r, a in zip(ranges, angles, strict=False) if abs(a) <= cone]
    fwd_clear = min(forward) if forward else math.inf
    if fwd_clear >= slow_distance:
        return heading_err, 1.0, False  # clear ahead → seek the goal
    gaps = [(a, r) for r, a in zip(ranges, angles, strict=False) if r >= slow_distance]
    if gaps:
        # clear sector closest to the goal bearing: least detour, so goal
        # attraction snaps back to the line the moment it clears.
        steer = min(gaps, key=lambda ar: abs(ar[0] - heading_err))[0]
    else:
        # boxed in ahead and to the sides — aim at the single clearest sector.
        steer = max(zip(angles, ranges, strict=False), key=lambda ar: ar[1])[0]
    if fwd_clear <= stop_distance:
        return steer, 0.0, True  # too close → crawl-and-turn only
    scale = (fwd_clear - stop_distance) / max(1e-3, slow_distance - stop_distance)
    return steer, max(0.2, min(1.0, scale)), True
