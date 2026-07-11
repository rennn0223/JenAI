"""Pure navigation-state decisions for the system-Python rclpy sidecar."""

from __future__ import annotations


def nav_result_status(status_code: int) -> str:
    """Map ROS GoalStatus codes onto JenAI's stable bridge protocol."""
    return {4: "succeeded", 5: "canceled", 6: "aborted"}.get(status_code, "failed")


def navigation_active(*, has_goal_handle: bool, nav_pending: bool, drive_active: bool) -> bool:
    """A not-yet-accepted goal is active too, so emergency stop cannot miss it."""
    return has_goal_handle or nav_pending or drive_active
