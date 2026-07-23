"""Live-bridge navigation (feedback/cancel) + navigate_with_fallback dispatch."""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from jenai.bridge import BridgeError, RosBridgeClient
from jenai.config.models import AppConfig, VehicleProfile
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


@dataclass(frozen=True, slots=True)
class _HaltOutcome:
    """What is known after a best-effort emergency halt request."""

    delivered: bool
    nav_cancel_acknowledged: bool


NavigationAction = dict[str, Any]


def _route_output(
    outgoing_action: NavigationAction,
    execution_status: str,
    detail: str,
) -> RouteOutput:
    """Build the canonical approved navigation result."""
    return RouteOutput(
        input_text="",
        outgoing_action=outgoing_action,
        approval_status="approved",
        execution_status=execution_status,
        route_preview=detail,
    )


async def _navigation_plan_failure(
    bridge: RosBridgeClient,
    goal: dict[str, Any],
    pose: dict[str, Any],
) -> tuple[str, str] | None:
    """Return a fail-closed outcome when Nav2 cannot plan without motion."""
    try:
        plan = await bridge.nav_plan(
            x=float(pose.get("x", 0.0)),
            y=float(pose.get("y", 0.0)),
            yaw=float(pose.get("yaw", 0.0)),
            frame_id=str(goal.get("frame_id", "map")),
        )
    except BridgeError as exc:
        return "unavailable", f"Read-only Nav2 planning failed: {exc} — the goal was NOT sent."
    if plan.feasible:
        return None
    reason = plan.error_name
    if plan.error_message:
        reason = f"{reason}: {plan.error_message}"
    return (
        "failed",
        f"Nav2 preflight found no safe path ({reason}) — the goal was NOT sent.",
    )


def _navigation_outcome(terminal: dict[str, Any]) -> tuple[str, str]:
    """Translate one terminal bridge event into the public route outcome."""
    status = str(terminal.get("status", "failed"))
    if terminal.get("reason"):
        detail = str(terminal["reason"])
    else:
        detail = {
            "succeeded": "Arrived at the goal.",
            "canceled": "Navigation canceled.",
            "aborted": "Nav2 aborted the goal (obstacle/planning failure?).",
            "rejected": "Nav2 rejected the goal.",
            "timed_out": "Navigation timed out before reaching the goal.",
            "sensor_unavailable": "Fresh depth data was unavailable; the robot stopped.",
            "odom_unavailable": "Fresh odometry was unavailable; the robot stopped.",
        }.get(status, f"Navigation ended with status '{status}'.")
    execution = "succeeded" if status == "succeeded" else "failed"
    return execution, detail


def _finite_pose_value(payload: dict[str, Any], field: str) -> float | None:
    """Read one finite numeric pose field without coercing booleans or text."""
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _normalized_frame(frame_id: str) -> str:
    """ROS accepts both ``map`` and ``/map``; compare their canonical names."""
    return frame_id.strip().lstrip("/")


async def _verify_nav2_arrival(
    bridge: RosBridgeClient,
    terminal: dict[str, Any],
    goal: dict[str, Any],
    vehicle: VehicleProfile | None,
) -> tuple[str, str]:
    """Independently verify a Nav2 success against JenAI's endpoint contract.

    Nav2's ``SUCCEEDED`` status means its own goal checker accepted the pose;
    it does not prove that the profile used by JenAI was met.  Prefer the last
    pose in Nav2 feedback because a latched ``/amcl_pose`` sample can be stale
    after a short final movement.  Missing evidence falls back to a fresh pose
    query for compatibility with older bridges, but malformed evidence fails
    closed instead of being silently trusted.
    """
    observed = terminal.get("final_pose")
    evidence_source = "Nav2 feedback"
    if observed is None:
        try:
            pose = await bridge.get_pose(timeout=2.0)
        except (AttributeError, BridgeError) as exc:
            halt = await _halt_quietly(bridge)
            return (
                "endpoint_mismatch",
                "Nav2 reported success, but JenAI could not obtain a terminal pose "
                f"({exc}); success was not accepted. {_halt_detail(halt)}",
            )
        observed = {
            "x": pose.x,
            "y": pose.y,
            "yaw": pose.yaw,
            "frame_id": pose.frame_id,
        }
        evidence_source = pose.source

    if not isinstance(observed, dict):
        halt = await _halt_quietly(bridge)
        return (
            "failed",
            "Nav2 reported success, but its terminal-pose evidence was malformed; "
            f"success was not accepted. {_halt_detail(halt)}",
        )

    x = _finite_pose_value(observed, "x")
    y = _finite_pose_value(observed, "y")
    yaw = _finite_pose_value(observed, "yaw")
    frame_id = observed.get("frame_id")
    if (
        x is None
        or y is None
        or yaw is None
        or not isinstance(frame_id, str)
        or not frame_id.strip()
    ):
        halt = await _halt_quietly(bridge)
        return (
            "failed",
            "Nav2 reported success, but its terminal pose was incomplete or non-finite; "
            f"success was not accepted. {_halt_detail(halt)}",
        )

    expected_frame = str(goal.get("frame_id", "map"))
    if _normalized_frame(frame_id) != _normalized_frame(expected_frame):
        halt = await _halt_quietly(bridge)
        return (
            "failed",
            "Nav2 reported success, but JenAI cannot compare terminal pose frame "
            f"'{frame_id}' with goal frame '{expected_frame}'; success was not accepted. "
            f"{_halt_detail(halt)}",
        )

    goal_pose = goal.get("pose") or {}
    goal_x = float(goal_pose.get("x", 0.0))
    goal_y = float(goal_pose.get("y", 0.0))
    goal_yaw = float(goal_pose.get("yaw", 0.0))
    position_error = math.hypot(x - goal_x, y - goal_y)
    yaw_error = abs(math.atan2(math.sin(yaw - goal_yaw), math.cos(yaw - goal_yaw)))
    position_tolerance = getattr(vehicle, "arrival_position_tolerance_m", 0.25)
    yaw_tolerance = getattr(vehicle, "arrival_yaw_tolerance_rad", 0.25)

    if position_error > position_tolerance or yaw_error > yaw_tolerance:
        halt = await _halt_quietly(bridge)
        return (
            "endpoint_mismatch",
            "Nav2 reported success, but JenAI rejected the endpoint: "
            f"position error {position_error:.3f} m (limit {position_tolerance:.3f} m), "
            f"yaw error {yaw_error:.3f} rad (limit {yaw_tolerance:.3f} rad). "
            f"{_halt_detail(halt)}",
        )

    return (
        "succeeded",
        "Arrived at the goal; endpoint verified from "
        f"{evidence_source} (position error {position_error:.3f} m, "
        f"yaw error {yaw_error:.3f} rad).",
    )


def _goal_pose_error(outgoing_action: NavigationAction) -> str | None:
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
    if "yaw" in pose:
        yaw = pose["yaw"]
        if isinstance(yaw, bool) or not isinstance(yaw, (int, float)):
            return "goal.pose.yaw is not a number"
        if not math.isfinite(yaw):
            return "goal.pose.yaw is not finite"
    return None


async def _bridge_heartbeat(bridge: RosBridgeClient) -> None:
    """Feed the sidecar watchdog until the bridge becomes unavailable."""
    while True:
        await asyncio.sleep(2.0)
        try:
            await bridge.ping()
        except BridgeError:
            return


async def navigate_live(
    bridge: RosBridgeClient,
    outgoing_action: NavigationAction,
    *,
    on_progress: Callable[[NavProgress], None] | None = None,
    timeout: float = 600.0,
    direct: bool = False,
    vehicle: VehicleProfile | None = None,
    avoidance: dict[str, Any] | None = None,
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

    if not direct:
        planning_failure = await _navigation_plan_failure(bridge, goal, pose)
        if planning_failure is not None:
            execution, detail = planning_failure
            return _route_output(outgoing_action, execution, detail)

    loop = asyncio.get_running_loop()
    result_future: asyncio.Future[dict[str, Any]] = loop.create_future()
    # Events are matched by tag: after an Esc-cancel, the goal's terminal
    # "canceled" result can arrive while the NEXT navigation is already
    # listening — without the tag it would consume that stale result as its own.
    tag = uuid4().hex[:8]

    def _mine(event: dict[str, Any]) -> bool:
        return event.get("tag", "") in ("", tag)  # "" tolerates older bridges

    def _on_feedback(event: dict[str, Any]) -> None:
        if on_progress is not None and _mine(event):
            on_progress(
                NavProgress(
                    distance_remaining=float(event.get("distance_remaining", 0.0)),
                    recoveries=int(event.get("recoveries", 0)),
                    elapsed=float(event.get("elapsed", 0.0)),
                )
            )

    def _on_result(event: dict[str, Any]) -> None:
        if _mine(event) and not result_future.done():
            result_future.set_result(event)

    bridge.on_event("nav_feedback", _on_feedback)
    bridge.on_event("nav_result", _on_result)
    heartbeat = asyncio.create_task(_bridge_heartbeat(bridge))
    try:
        # Once dispatch begins, a transport failure cannot prove that Nav2 or
        # the direct driver did not accept the request. Treat the outcome as
        # ambiguous and brake; never claim "NOT sent" after bytes may have
        # crossed the process boundary.
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
                odom_timeout_s=getattr(vehicle, "odom_timeout_s", 1.0),
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
        execution, detail = _navigation_outcome(terminal)
        if execution == "succeeded" and not direct:
            execution, detail = await _verify_nav2_arrival(bridge, terminal, goal, vehicle)
    except BridgeError as exc:
        halt = await _halt_quietly(bridge)
        execution = "unavailable"
        detail = (
            f"The bridge failed after navigation dispatch began ({exc}); goal acceptance "
            f"is unknown. {_halt_detail(halt)} Do not assume that no movement occurred."
        )
    except TimeoutError:
        halt = await _halt_quietly(bridge)
        execution = "failed"
        detail = f"Navigation timed out after {timeout:.0f}s. {_halt_detail(halt)}"
    except asyncio.CancelledError:
        # Esc in the TUI: cancel Nav2 AND pulse zero velocity before unwinding.
        # Preserve whether the action server confirmed the cancellation; a
        # delivered halt with no acknowledgement is still not reported as one.
        halt = await _halt_quietly(bridge)
        raise NavigationCancelled(nav_cancel_acknowledged=halt.nav_cancel_acknowledged) from None
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        bridge.off_event("nav_feedback", _on_feedback)
        bridge.off_event("nav_result", _on_result)

    return _route_output(outgoing_action, execution, detail)


async def _halt_quietly(bridge: RosBridgeClient) -> _HaltOutcome:
    """Brake and cancel without converting an unavailable halt into success."""
    try:
        acknowledged = bool(await asyncio.shield(bridge.halt()))
    except (BridgeError, asyncio.CancelledError):
        return _HaltOutcome(delivered=False, nav_cancel_acknowledged=False)
    return _HaltOutcome(delivered=True, nav_cancel_acknowledged=acknowledged)


def _halt_detail(outcome: _HaltOutcome) -> str:
    if not outcome.delivered:
        return "Emergency halt could not be delivered or confirmed."
    if outcome.nav_cancel_acknowledged:
        return "Emergency zero-velocity halt was delivered and Nav2 cancellation acknowledged."
    return (
        "Emergency zero-velocity halt was delivered, but active Nav2 cancellation was not "
        "acknowledged."
    )


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
    outgoing_action: NavigationAction,
    *,
    on_progress: Callable[[NavProgress], None] | None = None,
    on_gate: Callable[[str], None] | None = None,
    on_gate_report: Callable[[GateReport], None] | None = None,
) -> RouteOutput:
    """Execute navigation through the supervised live bridge.

    Navigation fails closed when the bridge is unavailable.  JenAI never
    downgrades an approved goal to the unsupervised ``ros2 action`` CLI path:
    terminating that client cannot prove that the Nav2 server cancelled its
    accepted goal, and it bypasses the bridge watchdog and live feedback.

    This dispatch decides when a goal reaches real hardware — it lives here
    once so every surface (TUI, MCP, future callers) applies the same policy.
    That includes the Twin Gate: with `[twin] enabled = true` on an isolated
    ROS domain, the goal is rehearsed in the digital twin first, and only a
    `pass` verdict reaches the robot. When the Twin and target share a domain,
    rehearsal is explicitly skipped so the same target is never commanded
    twice. Gate progress streams to `on_gate` when given.
    """
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
    if twin_shares_target and config.deployment_mode == "physical":
        return RouteOutput(
            input_text="",
            outgoing_action=outgoing_action,
            approval_status="approved",
            execution_status="blocked",
            route_preview=(
                "Twin Gate isolation is invalid for physical deployment: the Twin and target "
                f"share ROS_DOMAIN_ID={config.twin.domain_id}. The goal was NOT sent."
            ),
        )
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
            "Simulation-only Twin rehearsal skipped because Twin and target share "
            f"ROS_DOMAIN_ID={config.twin.domain_id}; sending one simulated target goal."
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
        except BridgeError as exc:
            return RouteOutput(
                input_text="",
                outgoing_action=outgoing_action,
                approval_status="approved",
                execution_status="unavailable",
                route_preview=f"{exc} — the goal was NOT sent; no unsafe fallback was used.",
            )
    return RouteOutput(
        input_text="",
        outgoing_action=outgoing_action,
        approval_status="approved",
        execution_status="unavailable",
        route_preview=(
            "The supervised ROS bridge is unavailable for the configured route adapter — "
            "the goal was NOT sent."
        ),
    )
