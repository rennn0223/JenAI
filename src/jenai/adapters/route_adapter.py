from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Protocol

from jenai.adapters import ros2_adapter

logger = logging.getLogger(__name__)


@dataclass
class RouteSendResult:
    execution_status: str
    detail: str


class RouteAdapter(Protocol):
    """Anything that can take a resolved navigation action and (try to) send it."""

    def resolve(self, outgoing_action: dict) -> RouteSendResult: ...


class NullRouteAdapter:
    """No navigation backend is connected.

    Reports honestly that the goal was NOT sent — it never fakes success. Wire a
    real adapter (e.g. Nav2 / a `/goal_pose` publisher) to actually navigate.
    """

    def resolve(self, outgoing_action: dict) -> RouteSendResult:
        logger.info("NullRouteAdapter: no navigation backend; goal not sent: %s", outgoing_action)
        return RouteSendResult(
            execution_status="unavailable",
            detail=(
                "No navigation backend is connected — the goal was NOT sent. "
                "Configure a real route_adapter (e.g. Nav2) to enable navigation."
            ),
        )


class Nav2RouteAdapter:
    """Send a navigation goal to Nav2 via its NavigateToPose action.

    Honest: if the `/navigate_to_pose` action server is not running it reports
    `unavailable` (never fakes success). Enable with `route_adapter = "nav2"`.
    """

    ACTION = "/navigate_to_pose"
    ACTION_TYPE = "nav2_msgs/action/NavigateToPose"

    def resolve(self, outgoing_action: dict) -> RouteSendResult:
        goal = outgoing_action.get("goal") or {}
        pose = goal.get("pose") or {}
        x, y = float(pose.get("x", 0.0)), float(pose.get("y", 0.0))
        yaw = float(pose.get("yaw", 0.0))
        frame = goal.get("frame_id", "map")

        if not ros2_adapter.is_available():
            return RouteSendResult("unavailable", "ros2 not on PATH — goal NOT sent.")
        try:
            if not ros2_adapter.action_available(self.ACTION):
                return RouteSendResult(
                    "unavailable",
                    "Nav2 (/navigate_to_pose) is not running — the goal was NOT sent.",
                )
            goal_yaml = json.dumps(
                {
                    "pose": {
                        "header": {"frame_id": frame},
                        "pose": {
                            "position": {"x": x, "y": y, "z": 0.0},
                            # yaw -> quaternion about z
                            "orientation": {"z": math.sin(yaw / 2), "w": math.cos(yaw / 2)},
                        },
                    }
                }
            )
            ok, detail = ros2_adapter.action_send_goal(self.ACTION, self.ACTION_TYPE, goal_yaml)
            return RouteSendResult("succeeded" if ok else "failed", detail)
        except ros2_adapter.Ros2AdapterError as exc:
            return RouteSendResult("failed", str(exc))


def get_route_adapter(adapter_name: str) -> RouteAdapter:
    """Map the config's route_adapter name to an implementation.

    Unknown names fall back to the honest NullRouteAdapter (reports
    unavailable) rather than guessing at hardware.
    """
    # "stub"/"none" mean "no backend wired" — honest, non-faking null adapter.
    if adapter_name in ("stub", "none", ""):
        return NullRouteAdapter()
    if adapter_name == "nav2":
        return Nav2RouteAdapter()
    raise NotImplementedError(f"Route adapter '{adapter_name}' is not implemented.")
