"""Pure avoidance logic for the odom driver: floor filtering, stop-and-go
detour planning, and stuck detection.

Stdlib only (no rclpy, no jenai) so it is importable BOTH by the bridge —
which runs under system python and picks this up as a sibling module — and by
the venv test suite as ``jenai.bridge._avoidance``. Keeping the decision pure
(no ROS entities) is what makes the branch logic unit-testable; ros_bridge.py
owns only the I/O (depth → ranges, ranges → cmd_vel).
"""

from __future__ import annotations

import math


def scan_is_fresh(updated_at: float | None, *, now: float, timeout_s: float) -> bool:
    """Whether the latest depth scan is recent enough to command motion."""
    return updated_at is not None and 0.0 <= now - updated_at <= timeout_s


def corridor_nearest(
    ranges: list[float],
    angles: list[float],
    *,
    heading_err: float,
    half_width: float = 0.5,
) -> float:
    """Nearest return inside the corridor aligned with the current target."""
    candidates = (
        distance
        for distance, angle in zip(ranges, angles, strict=False)
        if math.isfinite(distance) and abs(distance * math.sin(angle - heading_err)) <= half_width
    )
    return min(candidates, default=math.inf)


def apply_floor_filter(ranges: list[float], floor_ref: float, tol: float) -> list[float]:
    """Treat returns at/beyond the ground-plane reference as CLEAR (inf).

    A down-pitched depth camera sees the floor as a uniform ring (live probe:
    every band reads one constant distance, 1.6 m in the working band) — fed
    raw, the robot thinks it is permanently surrounded. Anything closer than
    `floor_ref - tol` is a real obstacle standing above the floor; anything at
    or beyond it is ground. floor_ref <= 0 disables the filter. Honest limit:
    a flat wall at exactly the floor distance is filtered too — this is a
    ground-plane-scene tool, not a general segmentation.
    """
    if floor_ref <= 0:
        return ranges
    return [math.inf if r >= floor_ref - tol else r for r in ranges]


def plan_detour(
    x: float,
    y: float,
    yaw: float,
    gx: float,
    gy: float,
    ranges: list[float],
    angles: list[float],
    *,
    clearance: float = 0.5,
    beyond: float = 1.2,
) -> list[tuple[float, float]] | None:
    """Plan a stop-and-go local detour around the nearest obstacle cluster.

    Continuous reactive steering needs to keep SEEING the obstacle while
    rounding it — but a down-pitched depth camera only has a visibility
    window (live: a cube below camera height is visible ~1.8-2.5 m out and
    melts into the floor tolerance closer in). So instead: when the forward
    corridor is blocked, stop, estimate the obstacle's odom position and
    width from the current scan, and emit two odom-frame waypoints — one
    beside the obstacle, one past it near the original line — that the plain
    go-to-goal driver can follow blind. Memory of the sighting replaces
    continuous sight.

    Returns None when there is no finite obstacle in the scan or the goal is
    nearer than the obstacle (drive straight to goal instead).
    """
    n = len(ranges)
    finite = [i for i in range(n) if math.isfinite(ranges[i])]
    if not finite:
        return None
    i_min = min(finite, key=lambda i: ranges[i])
    d = ranges[i_min]
    if math.hypot(gx - x, gy - y) <= d:
        return None  # goal is before the obstacle — no detour needed
    # Contiguous cluster around the nearest return ≈ one obstacle's face.
    lo = i_min
    while lo - 1 >= 0 and ranges[lo - 1] < d + 0.5:
        lo -= 1
    hi = i_min
    while hi + 1 < n and ranges[hi + 1] < d + 0.5:
        hi += 1
    ang_c = (angles[lo] + angles[hi]) / 2.0
    half_ang = abs(angles[lo] - angles[hi]) / 2.0 + math.pi / 60  # + half sector slack
    half_w = max(0.2, d * math.sin(half_ang))
    # Obstacle centre (face distance + half width deep), in odom frame.
    oc = d + half_w
    ox = x + oc * math.cos(yaw + ang_c)
    oy = y + oc * math.sin(yaw + ang_c)
    # Detour side (+1 = robot-left): whichever flank of the cluster has more
    # room. A cluster reaching a scan edge leaves no flank there — go the
    # other way; a full tie breaks away from the cluster's centre bearing.
    left = min((ranges[i] for i in range(lo)), default=None)
    right = min((ranges[i] for i in range(hi + 1, n)), default=None)
    if left is None and right is None:
        side = 1.0 if ang_c <= 0 else -1.0
    elif left is None:
        side = -1.0
    elif right is None:
        side = 1.0
    elif left != right:
        side = 1.0 if left > right else -1.0
    else:
        side = 1.0 if ang_c <= 0 else -1.0
    # Waypoints relative to the obstacle→goal line: beside, then past-and-in.
    ux, uy = gx - ox, gy - oy
    ul = math.hypot(ux, uy) or 1.0
    ux, uy = ux / ul, uy / ul
    px, py = -uy * side, ux * side  # perpendicular, on the chosen side
    off = half_w + clearance
    w1 = (ox + px * off, oy + py * off)
    w2 = (ox + ux * beyond + px * off * 0.5, oy + uy * beyond + py * off * 0.5)
    return [w1, w2]


class StuckDetector:
    """Abort signal for a robot grinding against an obstacle it cannot round.

    The crawl floor keeps an Ackermann rolling so it can steer, but when the
    gap search stays boxed in, that same crawl just pushes into the obstacle
    until the drive times out (caught live: nose pinned against a cube for
    30 s at full "avoiding" tick rate). Feed every control tick; fires when
    the robot has been continuously blocked for ``window_s`` while moving
    less than ``min_move`` metres — the caller should then end the drive and
    report it honestly instead of grinding.
    """

    def __init__(self, *, window_s: float = 3.0, min_move: float = 0.05) -> None:
        self.window_s = window_s
        self.min_move = min_move
        self._anchor: tuple[float, float, float] | None = None  # (t, x, y)

    def update(self, t: float, x: float, y: float, blocked: bool) -> bool:
        if not blocked:
            self._anchor = None
            return False
        if self._anchor is None:
            self._anchor = (t, x, y)
            return False
        t0, x0, y0 = self._anchor
        if math.hypot(x - x0, y - y0) >= self.min_move:
            self._anchor = (t, x, y)  # still making progress while blocked
            return False
        return t - t0 >= self.window_s
