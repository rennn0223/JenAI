"""Live-bridge navigation (feedback/cancel) + navigate_with_fallback dispatch."""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from jenai.bridge import BridgeError, RosBridgeClient
from jenai.config.models import AppConfig
from jenai.schemas import GateReport, RouteOutput


@dataclass(frozen=True)
class NavProgress:
    distance_remaining: float
    recoveries: int
    elapsed: float


class NavigationCancelled(asyncio.CancelledError):
    """Task cancellation with the bridge's Nav2 acknowledgement attached.

    This remains an asyncio.CancelledError so existing TUI and daemon callers
    keep their normal cancellation behavior. Acceptance callers can additionally
    distinguish "the Python task stopped" from "Nav2 confirmed cancellation".
    """

    def __init__(self, *, nav_cancel_acknowledged: bool) -> None:
        super().__init__("Navigation task canceled.")
        self.nav_cancel_acknowledged = nav_cancel_acknowledged


def _goal_pose_error(outgoing_action: dict) -> str | None:
    """Why this action must not reach any adapter, or None when it is sound.

    Fail closed: a goal whose pose is missing or non-numeric would otherwise
    default to the map origin (0, 0) — an LLM-fabricated action once drove the
    robot there while honestly reporting "succeeded". Every entry point funnels
    through navigate_with_fallback, so this single check floors them all.
    """
    goal = outgoing_action.get("goal")
    if not isinstance(goal, dict):
        return "goal is missing or not an object"
    pose = goal.get("pose")
    if not isinstance(pose, dict):
        return "goal.pose is missing or not an object"
    for axis in ("x", "y"):
        value = pose.get(axis)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"goal.pose.{axis} is missing or not a number"
        if not math.isfinite(value):
            return f"goal.pose.{axis} is not finite"
    return None


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
    result_future: asyncio.Future[dict] = loop.create_future()
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
            result_future.set_result(event)

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
        terminal = await asyncio.wait_for(result_future, timeout)
        status = str(terminal.get("status", "failed"))
        if status.startswith("localization_jump") and terminal.get("reason"):
            detail = str(terminal["reason"])
        else:
            detail = {
                "succeeded": "Arrived at the goal.",
                "canceled": "Navigation canceled.",
                "aborted": "Nav2 aborted the goal (obstacle/planning failure?).",
                "rejected": "Nav2 rejected the goal.",
                "timed_out": "Navigation timed out before reaching the goal.",
                "sensor_unavailable": "Fresh depth data was unavailable; the robot stopped.",
            }.get(status, f"Navigation ended with status '{status}'.")
        execution = "succeeded" if status == "succeeded" else "failed"
    except BridgeError as exc:
        execution, detail = "unavailable", f"{exc} — the goal was NOT sent."
    except TimeoutError:
        cancel_acknowledged = await _cancel_quietly(bridge)
        cancel_detail = (
            "Nav2 cancellation acknowledged."
            if cancel_acknowledged
            else "Nav2 cancellation was not acknowledged."
        )
        execution = "failed"
        detail = f"Navigation timed out after {timeout:.0f}s. {cancel_detail}"
    except asyncio.CancelledError:
        # Esc in the TUI: ask Nav2 to stop, then unwind as a normal cancelled
        # task while preserving whether the action server confirmed the cancel.
        cancel_acknowledged = await _cancel_quietly(bridge)
        raise NavigationCancelled(nav_cancel_acknowledged=cancel_acknowledged) from None
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


async def _cancel_quietly(bridge: RosBridgeClient) -> bool:
    """Best-effort cancel that never turns a missing acknowledgement into success."""
    try:
        return bool(await asyncio.shield(bridge.nav_cancel()))
    except (BridgeError, asyncio.CancelledError):
        return False


def _twin_shares_target_domain(config: AppConfig) -> bool:
    """Whether a Twin rehearsal would command the target ROS graph itself.

    ROS_DOMAIN_ID defaults to zero when it is unset. Compare numerically so
    equivalent spellings such as ``0`` and ``00`` cannot bypass the guard.
    An invalid ambient value is left to ROS to reject, but is still compared
    textually so this helper never turns configuration parsing into movement.
    """
    ambient = os.environ.get("ROS_DOMAIN_ID", "0").strip() or "0"
    try:
        return config.twin.domain_id == int(ambient)
    except ValueError:
        return str(config.twin.domain_id) == ambient


async def navigate_with_fallback(
    config: AppConfig,
    get_bridge: Callable[[], Awaitable[RosBridgeClient]],
    outgoing_action: dict,
    *,
    on_progress: Callable[[NavProgress], None] | None = None,
    on_gate: Callable[[str], None] | None = None,
    on_gate_report: Callable[[GateReport], None] | None = None,
) -> RouteOutput:
    """Execute a navigation action: live bridge (feedback + cancellation) when
    Nav2 is configured and ROS is present, otherwise the honest CLI adapter.

    This dispatch decides when a goal reaches real hardware — it lives here
    once so every surface (TUI, MCP, future callers) applies the same policy.
    That includes the Twin Gate: with `[twin] enabled = true` on an isolated
    ROS domain, the goal is rehearsed in the digital twin first, and only a
    `pass` verdict reaches the robot. When the Twin and target share a domain,
    rehearsal is explicitly skipped so the same target is never commanded
    twice. Gate progress streams to `on_gate` when given.
    """
    # Imported here: route_core pulls in the provider stack, which nav_live's
    # other callers (daemon, bridge tests) shouldn't need at import time.
    from jenai.tools.route_core import route_execute

    pose_error = _goal_pose_error(outgoing_action)
    if pose_error is not None:
        return RouteOutput(
            input_text="",
            outgoing_action=outgoing_action,
            approval_status="approved",
            execution_status="failed",
            route_preview=(
                f"Malformed navigation action ({pose_error}) — nothing was sent. "
                "Pass route_preview_tool's outgoing_action through unchanged."
            ),
        )

    twin_shares_target = config.twin.enabled and _twin_shares_target_domain(config)
    if config.twin.enabled and not twin_shares_target:
        from jenai.twin import rehearse_goal

        report = await rehearse_goal(config.twin, outgoing_action, on_status=on_gate)
        if on_gate_report is not None:
            on_gate_report(report)
        if report.verdict != "pass":
            return RouteOutput(
                input_text="",
                outgoing_action=outgoing_action,
                approval_status="approved",
                # Preserve the gate's three-valued verdict instead of
                # flattening both outcomes into a generic navigation failure.
                # Callers still treat every non-success status as "do not
                # continue", while the operator can now distinguish a hard
                # safety block from an inconclusive result that needs review.
                execution_status=("blocked" if report.verdict == "block" else "referred"),
                route_preview=f"{report.summary} — the real robot was NOT moved.",
            )
    elif twin_shares_target and on_gate is not None:
        on_gate(
            "Twin rehearsal skipped because Twin and target share "
            f"ROS_DOMAIN_ID={config.twin.domain_id}; sending one target goal."
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
