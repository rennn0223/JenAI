"""Live-bridge navigation (feedback/cancel) + navigate_with_fallback dispatch."""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from jenai.bridge import BridgeError, RosBridgeClient
from jenai.config.models import AppConfig, VehicleProfile
from jenai.schemas import GateReport, NavigationAttemptEvidence, RouteOutput


@dataclass(frozen=True)
class NavProgress:
    distance_remaining: float
    recoveries: int
    elapsed: float


@dataclass
class _NavigationEventCollector:
    """Own tag matching and terminal/feedback delivery for one navigation."""

    result_future: asyncio.Future[dict[str, Any]]
    on_progress: Callable[[NavProgress], None] | None = None
    endpoint_stall_radius_m: float | None = None
    endpoint_stall_timeout_s: float = 0.0
    tag: str = ""
    endpoint_entered_at: float | None = None

    def __post_init__(self) -> None:
        if not self.tag:
            self.tag = uuid4().hex[:8]

    def _matches(self, event: dict[str, Any]) -> bool:
        """Accept only events belonging to this dispatched goal generation."""
        return event.get("tag") == self.tag

    def record_feedback(self, event: dict[str, Any]) -> None:
        if not self._matches(event):
            return
        progress = NavProgress(
            distance_remaining=float(event.get("distance_remaining", 0.0)),
            recoveries=int(event.get("recoveries", 0)),
            elapsed=float(event.get("elapsed", 0.0)),
        )
        radius = self.endpoint_stall_radius_m
        if radius is not None and 0.0 < progress.distance_remaining <= radius:
            if self.endpoint_entered_at is None:
                self.endpoint_entered_at = self.result_future.get_loop().time()
        else:
            self.endpoint_entered_at = None
        if self.on_progress is not None:
            self.on_progress(progress)

    def endpoint_stalled(self) -> bool:
        """Whether close-range feedback has remained terminal-free too long."""
        entered_at = self.endpoint_entered_at
        return (
            entered_at is not None
            and self.result_future.get_loop().time() - entered_at >= self.endpoint_stall_timeout_s
        )

    def record_result(self, event: dict[str, Any]) -> None:
        if self._matches(event) and not self.result_future.done():
            self.result_future.set_result(event)


class _EndpointStalled(TimeoutError):
    """A Nav2 action stayed near its target without reaching a terminal state."""


async def _wait_for_navigation_result(
    collector: _NavigationEventCollector,
    timeout: float,
) -> dict[str, Any]:
    """Wait for a terminal result while enforcing the close-range stall bound."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        if collector.endpoint_stalled():
            raise _EndpointStalled
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError
        try:
            return await asyncio.wait_for(
                asyncio.shield(collector.result_future),
                min(0.25, remaining),
            )
        except TimeoutError:
            continue


class NavigationCancelled(asyncio.CancelledError):
    """Task cancellation with the bridge's Nav2 acknowledgement attached.

    This remains an asyncio.CancelledError so existing TUI and daemon callers
    keep their normal cancellation behavior. Acceptance callers can additionally
    distinguish "the Python task stopped" from "Nav2 confirmed cancellation".
    """

    def __init__(self, *, nav_cancel_acknowledged: bool) -> None:
        super().__init__("Navigation task canceled.")
        self.nav_cancel_acknowledged = nav_cancel_acknowledged


class _NavigationDispatchSuppressed(RuntimeError):
    """A cancellation fence prevented the motion command from being sent."""


@dataclass(frozen=True, slots=True)
class _HaltOutcome:
    """What is known after a best-effort emergency halt request."""

    delivered: bool
    nav_cancel_acknowledged: bool


@dataclass(frozen=True, slots=True)
class _NavigationAttemptResult:
    """One dispatched goal outcome and whether an endpoint retry is safe."""

    execution: str
    detail: str
    tag: str
    endpoint_retry_allowed: bool = False
    halt_delivered: bool | None = None
    nav_cancel_acknowledged: bool | None = None
    terminal_status: str | None = None
    terminal_observed: bool = False
    endpoint_pose_observed: bool = False
    position_error_m: float | None = None


NavigationAction = dict[str, Any]
NavigationFailureScope = Literal["waypoint_local", "navigation_system"]


def _route_output(
    outgoing_action: NavigationAction,
    execution_status: str,
    detail: str,
    *,
    navigation_attempts: list[NavigationAttemptEvidence] | None = None,
    failure_scope: NavigationFailureScope | None = None,
) -> RouteOutput:
    """Build the canonical approved navigation result."""
    return RouteOutput(
        input_text="",
        outgoing_action=outgoing_action,
        approval_status="approved",
        execution_status=execution_status,
        route_preview=detail,
        navigation_attempts=navigation_attempts or [],
        failure_scope=failure_scope,
    )


async def _navigation_plan_failure(
    bridge: RosBridgeClient,
    goal: dict[str, Any],
    pose: dict[str, Any],
) -> tuple[str, str, NavigationFailureScope] | None:
    """Return a fail-closed outcome when Nav2 cannot plan without motion."""
    try:
        plan = await bridge.nav_plan(
            x=float(pose.get("x", 0.0)),
            y=float(pose.get("y", 0.0)),
            yaw=float(pose.get("yaw", 0.0)),
            frame_id=str(goal.get("frame_id", "map")),
        )
    except BridgeError as exc:
        return (
            "unavailable",
            f"Read-only Nav2 planning failed: {exc} — the goal was NOT sent.",
            "navigation_system",
        )
    if plan.feasible:
        return None
    waypoint_local_errors = {"GOAL_OCCUPIED", "GOAL_OUTSIDE_MAP", "NO_VALID_PATH"}
    failure_scope: NavigationFailureScope = (
        "waypoint_local" if plan.error_name in waypoint_local_errors else "navigation_system"
    )
    reason = plan.error_name
    if plan.error_message:
        reason = f"{reason}: {plan.error_message}"
    return (
        "failed",
        f"Nav2 preflight found no safe path ({reason}) — the goal was NOT sent.",
        failure_scope,
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
) -> tuple[str, str, float | None]:
    """Independently verify a Nav2 success against JenAI's endpoint contract.

    Nav2's ``SUCCEEDED`` status means its own goal checker accepted the pose;
    it does not prove that the profile used by JenAI was met.  Wait briefly for
    the localization estimate to settle, then query the bridge again.  The last
    feedback pose can precede the physical stop and must not be accepted as
    postcondition evidence.  Missing or malformed evidence fails closed.
    """
    await asyncio.sleep(0.5)
    expected_frame = str(goal.get("frame_id", "map"))
    base_frame = getattr(vehicle, "robot_base_frame", "base_link")
    try:
        pose = await bridge.get_pose(
            timeout=4.0,
            fresh=True,
            frame_id=expected_frame,
            base_frame=base_frame,
        )
    except (AttributeError, BridgeError) as exc:
        halt = await _halt_quietly(bridge, vehicle)
        return (
            "endpoint_mismatch",
            "Nav2 reported success, but JenAI could not obtain a post-stop pose "
            f"({exc}); success was not accepted. {_halt_detail(halt, cancellation_expected=False)}",
            None,
        )
    observed = {
        "x": pose.x,
        "y": pose.y,
        "yaw": pose.yaw,
        "frame_id": pose.frame_id,
    }
    evidence_source = f"post-stop {pose.source}"

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
        halt = await _halt_quietly(bridge, vehicle)
        return (
            "failed",
            "Nav2 reported success, but its terminal pose was incomplete or non-finite; "
            f"success was not accepted. {_halt_detail(halt, cancellation_expected=False)}",
            None,
        )

    if _normalized_frame(frame_id) != _normalized_frame(expected_frame):
        halt = await _halt_quietly(bridge, vehicle)
        return (
            "failed",
            "Nav2 reported success, but JenAI cannot compare terminal pose frame "
            f"'{frame_id}' with goal frame '{expected_frame}'; success was not accepted. "
            f"{_halt_detail(halt, cancellation_expected=False)}",
            None,
        )

    goal_pose = goal.get("pose") or {}
    goal_x = float(goal_pose.get("x", 0.0))
    goal_y = float(goal_pose.get("y", 0.0))
    goal_yaw = float(goal_pose.get("yaw", 0.0))
    position_error = math.hypot(x - goal_x, y - goal_y)
    yaw_error = abs(math.atan2(math.sin(yaw - goal_yaw), math.cos(yaw - goal_yaw)))
    position_tolerance = getattr(vehicle, "arrival_position_tolerance_m", 0.05)
    yaw_tolerance = getattr(vehicle, "arrival_yaw_tolerance_rad", 0.15)

    if position_error > position_tolerance or yaw_error > yaw_tolerance:
        halt = await _halt_quietly(bridge, vehicle)
        return (
            "endpoint_mismatch",
            "Nav2 reported success, but JenAI rejected the endpoint: "
            f"position error {position_error:.3f} m (limit {position_tolerance:.3f} m), "
            f"yaw error {yaw_error:.3f} rad (limit {yaw_tolerance:.3f} rad). "
            f"{_halt_detail(halt, cancellation_expected=False)}",
            position_error,
        )

    return (
        "succeeded",
        "Arrived at the goal; endpoint verified from "
        f"{evidence_source} (position error {position_error:.3f} m, "
        f"yaw error {yaw_error:.3f} rad).",
        position_error,
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


async def _dispatch_navigation(
    bridge: RosBridgeClient,
    goal: dict[str, Any],
    pose: dict[str, Any],
    collector: _NavigationEventCollector,
    *,
    direct: bool,
    vehicle: VehicleProfile | None,
    avoidance: dict[str, Any] | None,
    timeout: float,
    is_cancelled: Callable[[], bool] | None,
) -> None:
    """Dispatch exactly one goal through Nav2 or the explicit direct driver."""
    if is_cancelled is not None and is_cancelled():
        raise _NavigationDispatchSuppressed
    if direct:
        await bridge.drive_to_pose(
            x=float(pose.get("x", 0.0)),
            y=float(pose.get("y", 0.0)),
            yaw=float(pose.get("yaw", 0.0)),
            tag=collector.tag,
            cmd_vel_topic=getattr(vehicle, "cmd_vel_topic", "/cmd_vel"),
            stamped=getattr(vehicle, "cmd_vel_stamped", False),
            max_linear=getattr(vehicle, "max_linear", 1.0),
            max_angular=getattr(vehicle, "max_angular", 2.0),
            odom_timeout_s=getattr(vehicle, "odom_timeout_s", 1.0),
            timeout=timeout,
            avoidance=avoidance,
        )
        return
    await bridge.nav_send(
        x=float(pose.get("x", 0.0)),
        y=float(pose.get("y", 0.0)),
        yaw=float(pose.get("yaw", 0.0)),
        frame_id=goal.get("frame_id", "map"),
        tag=collector.tag,
    )


async def _accepted_navigation_outcome(
    bridge: RosBridgeClient,
    terminal: dict[str, Any],
    goal: dict[str, Any],
    vehicle: VehicleProfile | None,
    *,
    direct: bool,
) -> tuple[str, str, float | None]:
    """Translate the bridge result and independently verify Nav2 success."""
    execution, detail = _navigation_outcome(terminal)
    if execution == "succeeded" and not direct:
        return await _verify_nav2_arrival(bridge, terminal, goal, vehicle)
    return execution, detail, None


async def _run_navigation_attempt(
    bridge: RosBridgeClient,
    goal: dict[str, Any],
    pose: dict[str, Any],
    *,
    on_progress: Callable[[NavProgress], None] | None,
    timeout: float,
    direct: bool,
    vehicle: VehicleProfile | None,
    avoidance: dict[str, Any] | None,
    is_cancelled: Callable[[], bool] | None,
) -> _NavigationAttemptResult:
    """Dispatch one goal, own its handlers, and classify its terminal state."""
    endpoint_stall_radius_m: float | None = None
    endpoint_stall_timeout_s = 0.0
    if vehicle is not None and not direct and vehicle.nav_endpoint_retry_limit > 0:
        endpoint_stall_radius_m = vehicle.nav_endpoint_stall_radius_m
        endpoint_stall_timeout_s = vehicle.nav_endpoint_stall_timeout_s
    collector = _NavigationEventCollector(
        asyncio.get_running_loop().create_future(),
        on_progress,
        endpoint_stall_radius_m=endpoint_stall_radius_m,
        endpoint_stall_timeout_s=endpoint_stall_timeout_s,
    )
    bridge.on_event("nav_feedback", collector.record_feedback)
    bridge.on_event("nav_result", collector.record_result)
    heartbeat = asyncio.create_task(_bridge_heartbeat(bridge))
    try:
        await _dispatch_navigation(
            bridge,
            goal,
            pose,
            collector,
            direct=direct,
            vehicle=vehicle,
            avoidance=avoidance,
            timeout=timeout,
            is_cancelled=is_cancelled,
        )
        terminal = await _wait_for_navigation_result(collector, timeout)
        execution, detail, position_error = await _accepted_navigation_outcome(
            bridge, terminal, goal, vehicle, direct=direct
        )
        return _NavigationAttemptResult(
            execution=execution,
            detail=detail,
            tag=collector.tag,
            terminal_status=str(terminal.get("status") or "unknown"),
            terminal_observed=True,
            endpoint_pose_observed=position_error is not None,
            position_error_m=position_error,
        )
    except _NavigationDispatchSuppressed:
        return _NavigationAttemptResult(
            execution="cancelled",
            detail="Navigation dispatch cancelled before a motion command was sent.",
            tag=collector.tag,
        )
    except _EndpointStalled:
        halt = await _halt_quietly(bridge, vehicle)
        retry_allowed = halt.delivered and halt.nav_cancel_acknowledged
        return _NavigationAttemptResult(
            execution="failed",
            detail=(
                "Navigation remained near the endpoint without a terminal Nav2 result. "
                f"{_halt_detail(halt)}"
            ),
            tag=collector.tag,
            endpoint_retry_allowed=retry_allowed,
            halt_delivered=halt.delivered,
            nav_cancel_acknowledged=halt.nav_cancel_acknowledged,
        )
    except BridgeError as exc:
        halt = await _halt_quietly(bridge, vehicle)
        return _NavigationAttemptResult(
            execution="unavailable",
            detail=(
                f"The bridge failed after navigation dispatch began ({exc}); goal acceptance "
                f"is unknown. {_halt_detail(halt)} Do not assume that no movement occurred."
            ),
            tag=collector.tag,
            halt_delivered=halt.delivered,
            nav_cancel_acknowledged=halt.nav_cancel_acknowledged,
        )
    except TimeoutError:
        halt = await _halt_quietly(bridge, vehicle)
        return _NavigationAttemptResult(
            execution="failed",
            detail=f"Navigation timed out after {timeout:.0f}s. {_halt_detail(halt)}",
            tag=collector.tag,
            halt_delivered=halt.delivered,
            nav_cancel_acknowledged=halt.nav_cancel_acknowledged,
        )
    except asyncio.CancelledError:
        halt = await _halt_quietly(bridge, vehicle)
        raise NavigationCancelled(nav_cancel_acknowledged=halt.nav_cancel_acknowledged) from None
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        bridge.off_event("nav_feedback", collector.record_feedback)
        bridge.off_event("nav_result", collector.record_result)


async def navigate_live(
    bridge: RosBridgeClient,
    outgoing_action: NavigationAction,
    *,
    on_progress: Callable[[NavProgress], None] | None = None,
    timeout: float = 600.0,
    direct: bool = False,
    vehicle: VehicleProfile | None = None,
    endpoint_retry_limit: int | None = None,
    avoidance: dict[str, Any] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> RouteOutput:
    """Drive through the supervised bridge with live feedback and cancellation.

    The final cancellation check occurs immediately before the motion command.
    """
    goal = outgoing_action.get("goal") or {}
    pose = goal.get("pose") or {}

    if not direct:
        planning_failure = await _navigation_plan_failure(bridge, goal, pose)
        if is_cancelled is not None and is_cancelled():
            return _route_output(
                outgoing_action,
                "cancelled",
                "Navigation dispatch cancelled after plan preflight.",
            )
        if planning_failure is not None:
            execution, detail, failure_scope = planning_failure
            return _route_output(outgoing_action, execution, detail, failure_scope=failure_scope)

    if is_cancelled is not None and is_cancelled():
        return _route_output(
            outgoing_action,
            "cancelled",
            "Navigation dispatch cancelled before a motion command was sent.",
        )

    retry_limit = (
        endpoint_retry_limit
        if endpoint_retry_limit is not None
        else vehicle.nav_endpoint_retry_limit
        if vehicle is not None and not direct
        else 0
    )
    attempt_timeout = timeout
    first_stall_detail: str | None = None
    navigation_attempts: list[NavigationAttemptEvidence] = []
    for attempt in range(retry_limit + 1):
        result = await _run_navigation_attempt(
            bridge,
            goal,
            pose,
            on_progress=on_progress,
            timeout=attempt_timeout,
            direct=direct,
            vehicle=vehicle,
            avoidance=avoidance,
            is_cancelled=is_cancelled,
        )
        navigation_attempts.append(
            NavigationAttemptEvidence(
                attempt=attempt + 1,
                tag=result.tag,
                execution_status=result.execution,
                detail=result.detail,
                endpoint_retry_allowed=result.endpoint_retry_allowed,
                halt_delivered=result.halt_delivered,
                nav_cancel_acknowledged=result.nav_cancel_acknowledged,
                terminal_status=result.terminal_status,
                terminal_observed=result.terminal_observed,
                endpoint_pose_observed=result.endpoint_pose_observed,
                position_error_m=result.position_error_m,
            )
        )
        if result.execution == "succeeded":
            detail = result.detail
            if attempt > 0:
                detail = (
                    f"Initial attempt: {first_stall_detail} "
                    f"Endpoint recovery retry {attempt}/{retry_limit} succeeded. {detail}"
                )
            return _route_output(
                outgoing_action,
                result.execution,
                detail,
                navigation_attempts=navigation_attempts,
            )

        if result.endpoint_retry_allowed and attempt < retry_limit and vehicle is not None:
            first_stall_detail = result.detail
            attempt_timeout = min(timeout, vehicle.nav_endpoint_retry_timeout_s)
            continue

        detail = result.detail
        if attempt > 0:
            detail = (
                f"Endpoint recovery retry {attempt}/{retry_limit} failed. "
                f"Initial attempt: {first_stall_detail} Final attempt: {detail}"
            )
        return _route_output(
            outgoing_action,
            result.execution,
            detail,
            navigation_attempts=navigation_attempts,
            failure_scope=(
                None
                if result.execution == "cancelled"
                else "waypoint_local"
                if result.endpoint_retry_allowed
                else "navigation_system"
            ),
        )

    raise AssertionError("navigation retry loop exhausted without an outcome")


async def _halt_quietly(
    bridge: RosBridgeClient,
    vehicle: VehicleProfile | None = None,
) -> _HaltOutcome:
    """Brake on the configured actuator topic and retain cancellation evidence."""
    try:
        acknowledged = bool(
            await asyncio.shield(
                bridge.halt(
                    cmd_vel_topic=getattr(vehicle, "cmd_vel_topic", "/cmd_vel"),
                    stamped=getattr(vehicle, "cmd_vel_stamped", False),
                )
            )
        )
    except (BridgeError, asyncio.CancelledError):
        return _HaltOutcome(delivered=False, nav_cancel_acknowledged=False)
    return _HaltOutcome(delivered=True, nav_cancel_acknowledged=acknowledged)


def _halt_detail(
    outcome: _HaltOutcome,
    *,
    cancellation_expected: bool = True,
) -> str:
    if not outcome.delivered:
        return "Emergency zero-velocity command could not be published or confirmed."
    if not cancellation_expected:
        return "Post-stop zero-velocity command was published."
    if outcome.nav_cancel_acknowledged:
        return "Emergency zero-velocity command was published and Nav2 cancellation acknowledged."
    return (
        "Emergency zero-velocity command was published, but active Nav2 cancellation was not "
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


async def _twin_gate_outcome(
    config: AppConfig,
    outgoing_action: NavigationAction,
    *,
    on_gate: Callable[[str], None] | None,
    on_gate_report: Callable[[GateReport], None] | None,
    is_cancelled: Callable[[], bool] | None,
) -> RouteOutput | None:
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
        if is_cancelled is not None and is_cancelled():
            return _route_output(
                outgoing_action,
                "cancelled",
                "Navigation dispatch cancelled after Twin preflight.",
            )
        if on_gate_report is not None:
            on_gate_report(report)
        if report.verdict != "pass":
            return RouteOutput(
                input_text="",
                outgoing_action=outgoing_action,
                approval_status="approved",
                execution_status=("blocked" if report.verdict == "block" else "referred"),
                route_preview=f"{report.summary} — the real robot was NOT moved.",
            )
    elif twin_shares_target and on_gate is not None:
        on_gate(
            "Simulation-only Twin rehearsal skipped because Twin and target share "
            f"ROS_DOMAIN_ID={config.twin.domain_id}; sending one simulated target goal."
        )
    return None


async def navigate_with_fallback(
    config: AppConfig,
    get_bridge: Callable[[], Awaitable[RosBridgeClient]],
    outgoing_action: NavigationAction,
    *,
    on_progress: Callable[[NavProgress], None] | None = None,
    on_gate: Callable[[str], None] | None = None,
    on_gate_report: Callable[[GateReport], None] | None = None,
    endpoint_retry_limit: int | None = None,
    is_cancelled: Callable[[], bool] | None = None,
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

    if is_cancelled is not None and is_cancelled():
        return _route_output(
            outgoing_action,
            "cancelled",
            "Navigation dispatch cancelled before live bridge acquisition.",
        )

    gate_outcome = await _twin_gate_outcome(
        config,
        outgoing_action,
        on_gate=on_gate,
        on_gate_report=on_gate_report,
        is_cancelled=is_cancelled,
    )
    if gate_outcome is not None:
        return gate_outcome

    if config.route_adapter in ("nav2", "odom") and RosBridgeClient.available():
        try:
            bridge = await get_bridge()
            if is_cancelled is not None and is_cancelled():
                return _route_output(
                    outgoing_action,
                    "cancelled",
                    "Navigation dispatch cancelled after target bridge acquisition.",
                )
            return await navigate_live(
                bridge,
                outgoing_action,
                on_progress=on_progress,
                timeout=config.vehicle.nav_timeout_s,
                direct=config.route_adapter == "odom",
                vehicle=config.vehicle,
                avoidance=config.avoidance.as_params(),
                endpoint_retry_limit=endpoint_retry_limit,
                is_cancelled=is_cancelled,
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
