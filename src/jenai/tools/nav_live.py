from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from jenai.bridge import BridgeError, RosBridgeClient
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
) -> RouteOutput:
    """Drive Nav2 through the rclpy bridge with live feedback and cancellation.

    Unlike the CLI adapter (fire `ros2 action send_goal`, block, done), this
    streams distance-remaining while the robot moves and reacts to task
    cancellation (TUI Esc) by cancelling the Nav2 goal — the robot actually
    stops instead of sailing on after the UI gave up.
    """
    goal = outgoing_action.get("goal") or {}
    pose = goal.get("pose") or {}

    loop = asyncio.get_running_loop()
    result_future: asyncio.Future[str] = loop.create_future()

    def _on_feedback(event: dict) -> None:
        if on_progress is not None:
            on_progress(
                NavProgress(
                    distance_remaining=float(event.get("distance_remaining", 0.0)),
                    recoveries=int(event.get("recoveries", 0)),
                    elapsed=float(event.get("elapsed", 0.0)),
                )
            )

    def _on_result(event: dict) -> None:
        if not result_future.done():
            result_future.set_result(str(event.get("status", "failed")))

    bridge.on_event("nav_feedback", _on_feedback)
    bridge.on_event("nav_result", _on_result)
    try:
        await bridge.nav_send(
            x=float(pose.get("x", 0.0)),
            y=float(pose.get("y", 0.0)),
            yaw=float(pose.get("yaw", 0.0)),
            frame_id=goal.get("frame_id", "map"),
        )
        status = await asyncio.wait_for(result_future, timeout)
        detail = {
            "succeeded": "Arrived at the goal.",
            "canceled": "Navigation canceled.",
            "aborted": "Nav2 aborted the goal (obstacle/planning failure?).",
            "rejected": "Nav2 rejected the goal.",
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
