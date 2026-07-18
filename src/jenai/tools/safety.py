"""Emergency stop — the one place every surface (TUI, WebUI, MCP, daemon)
comes to halt the robot, so the halt semantics can never drift between them."""

from __future__ import annotations

from jenai.bridge import RosBridgeClient
from jenai.config.models import AppConfig


async def halt_robot(config: AppConfig, bridge: RosBridgeClient) -> str:
    """Cancel any Nav2 goal and pulse zero velocity on the vehicle's cmd_vel.

    Raises BridgeError when ROS/the bridge is unavailable — callers report
    that honestly rather than pretending the robot stopped.
    """
    vehicle = config.vehicle
    nav_canceled = await bridge.halt(
        cmd_vel_topic=vehicle.cmd_vel_topic, stamped=vehicle.cmd_vel_stamped
    )
    if nav_canceled:
        return "Robot halted (navigation goal canceled, zero velocity sent)."
    return "Robot halted (zero velocity sent)."


async def arm_watchdog(config: AppConfig, bridge: RosBridgeClient, timeout_s: float = 6.0) -> None:
    """Arm the bridge-side dead-client watchdog with this vehicle's settings."""
    vehicle = config.vehicle
    await bridge.configure_safety(
        watchdog_s=timeout_s,
        cmd_vel_topic=vehicle.cmd_vel_topic,
        stamped=vehicle.cmd_vel_stamped,
        pose_jump_threshold_m=vehicle.pose_jump_threshold_m,
        pose_jump_window_s=vehicle.pose_jump_window_s,
    )
