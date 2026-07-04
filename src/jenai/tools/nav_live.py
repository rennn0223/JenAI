from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from jenai.bridge import BridgeError, RosBridgeClient
from jenai.config.models import AppConfig
from jenai.schemas import RouteOutput


@dataclass(frozen=True)
class NavProgress:
    distance_remaining: float
    recoveries: int
    elapsed: float


async def navigate_live(
    bridge: RosBridgeClient,
    outgoing_action: dict,
    *,
    on_progress: Callable[[NavProgress], None] | None = None,
    timeout: float = 600.0,
    direct: bool = False,
    vehicle=None,
    avoidance: dict | None = None,
) -> RouteOutput:
    """Drive through the rclpy bridge with live feedback and cancellation.

    Unlike the CLI adapter (fire `ros2 action send_goal`, block, done), this
    streams distance-remaining while the robot moves and reacts to task
    cancellation (TUI Esc) by cancelling the goal — the robot actually
    stops instead of sailing on after the UI gave up.

    `direct=True` uses the Nav2-less odom→cmd_vel driver (open ground / a bare
    ground plane with no planner); it clamps to the vehicle's speed limits.
    """
    goal = outgoing_action.get("goal") or {}
    pose = goal.get("pose") or {}

    loop = asyncio.get_running_loop()
    result_future: asyncio.Future[str] = loop.create_future()
    # Events are matched by tag: after an Esc-cancel, the goal's terminal
    # "canceled" result can arrive while the NEXT navigation is already
    # listening — without the tag it would consume that stale result as its own.
    tag = uuid4().hex[:8]

    def _mine(event: dict) -> bool:
        return event.get("tag", "") in ("", tag)  # "" tolerates older bridges

    def _on_feedback(event: dict) -> None:
        if on_progress is not None and _mine(event):
            on_progress(
                NavProgress(
                    distance_remaining=float(event.get("distance_remaining", 0.0)),
                    recoveries=int(event.get("recoveries", 0)),
                    elapsed=float(event.get("elapsed", 0.0)),
                )
            )

    def _on_result(event: dict) -> None:
        if _mine(event) and not result_future.done():
            result_future.set_result(str(event.get("status", "failed")))

    async def _heartbeat() -> None:
        # Feed the bridge-side watchdog while we wait: if this client hangs or
        # dies instead, the bridge halts the robot on its own.
        while True:
            await asyncio.sleep(2.0)
            try:
                await bridge.ping()
            except BridgeError:
                return

    bridge.on_event("nav_feedback", _on_feedback)
    bridge.on_event("nav_result", _on_result)
    heartbeat = asyncio.create_task(_heartbeat())
    try:
        if direct:
            await bridge.drive_to_pose(
                x=float(pose.get("x", 0.0)),
                y=float(pose.get("y", 0.0)),
                yaw=float(pose.get("yaw", 0.0)),
                tag=tag,
                cmd_vel_topic=getattr(vehicle, "cmd_vel_topic", "/cmd_vel"),
                stamped=getattr(vehicle, "cmd_vel_stamped", False),
                max_linear=getattr(vehicle, "max_linear", 1.0),
                max_angular=getattr(vehicle, "max_angular", 2.0),
                timeout=timeout,
                avoidance=avoidance,
            )
        else:
            await bridge.nav_send(
                x=float(pose.get("x", 0.0)),
                y=float(pose.get("y", 0.0)),
                yaw=float(pose.get("yaw", 0.0)),
                frame_id=goal.get("frame_id", "map"),
                tag=tag,
            )
        status = await asyncio.wait_for(result_future, timeout)
        detail = {
            "succeeded": "Arrived at the goal.",
            "canceled": "Navigation canceled.",
            "aborted": "Nav2 aborted the goal (obstacle/planning failure?).",
            "rejected": "Nav2 rejected the goal.",
            "timed_out": "Navigation timed out before reaching the goal.",
        }.get(status, f"Navigation ended with status '{status}'.")
        execution = "succeeded" if status == "succeeded" else "failed"
    except BridgeError as exc:
        execution, detail = "unavailable", f"{exc} — the goal was NOT sent."
    except TimeoutError:
        await _cancel_quietly(bridge)
        execution, detail = "failed", f"Navigation timed out after {timeout:.0f}s (canceled)."
    except asyncio.CancelledError:
        # Esc in the TUI: stop the robot, then let the caller unwind normally.
        await _cancel_quietly(bridge)
        raise
    finally:
        heartbeat.cancel()
        bridge.off_event("nav_feedback", _on_feedback)
        bridge.off_event("nav_result", _on_result)

    return RouteOutput(
        input_text="",
        outgoing_action=outgoing_action,
        approval_status="approved",
        execution_status=execution,
        route_preview=detail,
    )


async def _cancel_quietly(bridge: RosBridgeClient) -> None:
    try:
        await asyncio.shield(bridge.nav_cancel())
    except (BridgeError, asyncio.CancelledError):
        pass


async def navigate_with_fallback(
    config: AppConfig,
    get_bridge: Callable[[], Awaitable[RosBridgeClient]],
    outgoing_action: dict,
    *,
    on_progress: Callable[[NavProgress], None] | None = None,
    on_gate: Callable[[str], None] | None = None,
) -> RouteOutput:
    """Execute a navigation action: live bridge (feedback + cancellation) when
    Nav2 is configured and ROS is present, otherwise the honest CLI adapter.

    This dispatch decides when a goal reaches real hardware — it lives here
    once so every surface (TUI, MCP, future callers) applies the same policy.
    That includes the Twin Gate: with `[twin] enabled = true` the goal is
    rehearsed in the digital twin first, and only a `pass` verdict reaches
    the robot. Gate progress streams to `on_gate` when given.
    """
    # Imported here: route_core pulls in the provider stack, which nav_live's
    # other callers (daemon, bridge tests) shouldn't need at import time.
    from jenai.tools.route_core import route_execute

    if config.twin.enabled:
        from jenai.twin import rehearse_goal

        report = await rehearse_goal(config.twin, outgoing_action, on_status=on_gate)
        if report.verdict != "pass":
            return RouteOutput(
                input_text="",
                outgoing_action=outgoing_action,
                approval_status="approved",
                execution_status="failed",
                route_preview=f"{report.summary} — the real robot was NOT moved.",
            )

    if config.route_adapter in ("nav2", "odom") and RosBridgeClient.available():
        try:
            bridge = await get_bridge()
            return await navigate_live(
                bridge,
                outgoing_action,
                on_progress=on_progress,
                direct=config.route_adapter == "odom",
                vehicle=config.vehicle,
                avoidance=config.avoidance.as_params(),
            )
        except BridgeError:
            pass  # bridge could not start — fall through to the CLI path
    return await route_execute(config, outgoing_action)
