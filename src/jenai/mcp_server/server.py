"""FastMCP stdio server exposing robot tools (read-only; --allow-actions to move)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from jenai.adapters.locations import (
    LocationNotFoundError,
    find_location,
    load_locations_tolerant,
)
from jenai.adapters.ros2_adapter import Ros2NotAvailableError
from jenai.bridge import BridgeError, RosBridgeClient
from jenai.config.models import AppConfig
from jenai.schemas import Location
from jenai.state.audit import AuditStore
from jenai.tools import ros2_core
from jenai.tools.navigation_gateway import NavigationGateway
from jenai.tools.safety import arm_watchdog, halt_robot
from jenai.tools.vision_core import VisionError, capture_and_analyze


class _ServerResources:
    """Own the state shared by MCP tools for exactly one server instance."""

    def __init__(self, config: AppConfig, config_path: Path) -> None:
        self.config = config
        self.config_path = config_path
        self._bridge = RosBridgeClient()
        self._bridge_lock = asyncio.Lock()
        self._safety_registered = False
        self.navigation = NavigationGateway(
            config,
            get_bridge=self.bridge,
            audit_store=AuditStore.best_effort(config_path.parent / "audit.sqlite3"),
        )

    async def bridge(self) -> RosBridgeClient:
        """Return the shared bridge after installing its fail-safe watchdog."""
        async with self._bridge_lock:
            if not self._safety_registered:
                # Register once; start() arms the watchdog on every (re)spawn,
                # so a killed MCP client can never leave the robot driving.
                await arm_watchdog(self.config, self._bridge)
                self._safety_registered = True
            await self._bridge.start()
        return self._bridge

    def locations(self) -> tuple[list[Location], str | None]:
        path = self.config.resolved_locations_path(self.config_path)
        return load_locations_tolerant(path)


def _register_ros_tools(mcp: FastMCP, resources: _ServerResources) -> None:
    config = resources.config

    @mcp.tool()
    async def ros_topics() -> str:
        """List ROS2 topics currently on the graph, with a kind hint each."""
        try:
            out = await ros2_core.ros_topics(config)
        except Ros2NotAvailableError as exc:
            return f"unavailable: {exc}"
        if not out.topics:
            return "No topics on the graph (is ROS2 running?)."
        return "\n".join(f"{t.name} ({t.kind_hint})" for t in out.topics)

    @mcp.tool()
    async def ros_topic_info(topic: str) -> str:
        """Show a topic's message type, publishers, and subscribers."""
        try:
            out = await ros2_core.ros_topic_info(config, topic)
        except Ros2NotAvailableError as exc:
            return f"unavailable: {exc}"
        return (
            f"type: {out.message_type}\npublishers: {out.publisher_count}\n"
            f"subscribers: {out.subscriber_count}"
        )

    @mcp.tool()
    async def ros_echo(topic: str, count: int = 3) -> str:
        """Snapshot up to `count` recent messages from a topic."""
        try:
            out = await ros2_core.ros_echo(config, topic, limit=count)
        except Ros2NotAvailableError as exc:
            return f"unavailable: {exc}"
        if not out.messages:
            return "No messages received."
        return "\n---\n".join(json.dumps(m, ensure_ascii=False, default=str) for m in out.messages)

    @mcp.tool()
    async def list_locations() -> str:
        """List the robot's saved named locations (for navigate_to)."""
        locations, error = resources.locations()
        if error:
            return error
        if not locations:
            return "No locations saved yet."
        return "\n".join(
            f"{loc.name} ({loc.pose.x:.2f}, {loc.pose.y:.2f}, {loc.frame_id})"
            f"{' aka ' + ', '.join(loc.aliases) if loc.aliases else ''}"
            for loc in locations
        )


def _register_robot_tools(mcp: FastMCP, resources: _ServerResources) -> None:
    config = resources.config

    @mcp.tool()
    async def stop() -> str:
        """EMERGENCY STOP: cancel navigation and command zero velocity.

        Always available (even read-only servers) — stopping is always safe.
        """
        try:
            client = await resources.bridge()
            return await halt_robot(config, client)
        except BridgeError as exc:
            return f"unavailable: {exc}"

    @mcp.tool()
    async def robot_pose() -> str:
        """The robot's current position (x, y, yaw) from AMCL or odometry."""
        try:
            client = await resources.bridge()
            pose = await client.get_pose(timeout=3.0)
        except BridgeError as exc:
            return f"unavailable: {exc}"
        return (
            f"x={pose.x:.3f} y={pose.y:.3f} yaw={pose.yaw:.3f} "
            f"({pose.frame_id}, from {pose.source})"
        )

    @mcp.tool()
    async def camera_look(topic: str = "") -> str:
        """Capture one camera frame and describe it with the vision model.
        Omit `topic` to use the vehicle's configured camera."""
        try:
            client = await resources.bridge()
            output = await capture_and_analyze(
                config, client, topic or config.vehicle.camera_topic, timeout=5.0
            )
        except BridgeError as exc:
            return f"unavailable: {exc}"
        except VisionError as exc:
            return f"vision error: {exc}"
        parts = [output.summary]
        if output.objects:
            parts.append("objects: " + ", ".join(output.objects))
        if output.anomalies:
            parts.append("anomalies: " + ", ".join(output.anomalies))
        if output.next_action_suggestions:
            parts.append("suggested next: " + "; ".join(output.next_action_suggestions))
        return "\n".join(parts)


def _register_navigation_tool(mcp: FastMCP, resources: _ServerResources) -> None:
    # One goal at a time: MCP clients retry after their own tool timeouts and
    # can issue parallel calls. The lock prevents silent goal preemption.
    nav_busy = asyncio.Lock()

    @mcp.tool()
    async def navigate_to(location: str) -> str:
        """Navigate the robot to a saved location BY NAME (see list_locations).

        This MOVES THE ROBOT. Only present because the operator started the
        server with --allow-actions.
        """
        if nav_busy.locked():
            return "busy: a navigation goal is already in progress — one goal at a time."
        async with nav_busy:
            locations, error = resources.locations()
            if error:
                return error
            try:
                target = find_location(locations, location)
            except LocationNotFoundError as exc:
                hint = ", ".join(c.name for c in exc.candidates) or "no close matches"
                return f"Unknown location '{location}' (near: {hint})."
            action = {"goal": target.model_dump(mode="json")}
            output = await resources.navigation.execute(action)
            return f"{output.execution_status}: {output.route_preview}"


def build_mcp_server(
    config: AppConfig,
    config_path: Path,
    *,
    allow_actions: bool = False,
) -> FastMCP:
    """Build a read-only MCP server, optionally exposing guarded navigation."""
    mcp = FastMCP(
        "jenai",
        instructions=(
            "Tools for a ROS2 mobile robot managed by JenAI. Read-only inspection is "
            "always available; navigation exists only when the operator started the "
            "server with --allow-actions."
        ),
    )
    resources = _ServerResources(config, config_path)
    _register_ros_tools(mcp, resources)
    _register_robot_tools(mcp, resources)
    if allow_actions:
        _register_navigation_tool(mcp, resources)

    return mcp
