"""Pure translation of Nav2 path-planning results into wire evidence."""

from __future__ import annotations

import math
from typing import Any

WirePayload = dict[str, Any]

_ERROR_NAMES = {
    0: "NONE",
    200: "UNKNOWN",
    201: "INVALID_PLANNER",
    202: "TF_ERROR",
    203: "START_OUTSIDE_MAP",
    204: "GOAL_OUTSIDE_MAP",
    205: "START_OCCUPIED",
    206: "GOAL_OCCUPIED",
    207: "TIMEOUT",
    208: "NO_VALID_PATH",
}


def path_plan_payload(wrapped: Any) -> WirePayload:
    """Convert one ComputePathToPose result into bounded wire evidence."""
    result = wrapped.result
    poses = result.path.poses
    path_length = sum(
        math.hypot(
            current.pose.position.x - previous.pose.position.x,
            current.pose.position.y - previous.pose.position.y,
        )
        for previous, current in zip(poses, poses[1:], strict=False)
    )
    error_code = int(result.error_code)
    return {
        "feasible": error_code == 0 and bool(poses),
        "pose_count": len(poses),
        "path_length_m": path_length,
        "planning_time_s": result.planning_time.sec + result.planning_time.nanosec / 1_000_000_000,
        "error_code": error_code,
        "error_name": _ERROR_NAMES.get(error_code, f"ERROR_{error_code}"),
        "error_message": str(result.error_msg),
    }
