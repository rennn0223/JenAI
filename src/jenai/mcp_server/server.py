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
from jenai.capabilities import has_registered_capability
from jenai.config.models import AppConfig
from jenai.schemas import Location
from jenai.state.audit import AuditStore
from jenai.task_results import navigation_receipt_text
from jenai.tools import ros2_core
from jenai.tools.navigation_gateway import NavigationGateway
from jenai.tools.safety import (
    NavigationCancelStatus,
    arm_watchdog,
    halt_robot_with_receipt,
)
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
            config_path=config_path,
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


async def _ros_topics(resources: _ServerResources) -> str:
    try:
        out = await ros2_core.ros_topics(resources.config)
    except Ros2NotAvailableError as exc:
        return f"unavailable: {exc}"
    if not out.topics:
        return "No topics on the graph (is ROS2 running?)."
    return "\n".join(f"{topic.name} ({topic.kind_hint})" for topic in out.topics)


async def _ros_topic_info(resources: _ServerResources, topic: str) -> str:
    try:
        out = await ros2_core.ros_topic_info(resources.config, topic)
    except Ros2NotAvailableError as exc:
        return f"unavailable: {exc}"
    return (
        f"type: {out.message_type}\npublishers: {out.publisher_count}\n"
        f"subscribers: {out.subscriber_count}"
    )


async def _ros_echo(resources: _ServerResources, topic: str, count: int) -> str:
    try:
        out = await ros2_core.ros_echo(resources.config, topic, limit=count)
    except Ros2NotAvailableError as exc:
        return f"unavailable: {exc}"
    if not out.messages:
        return "No messages received."
    return "\n---\n".join(
        json.dumps(message, ensure_ascii=False, default=str) for message in out.messages
    )


def _list_locations(resources: _ServerResources) -> str:
    locations, error = resources.locations()
    if error:
        return error
    if not locations:
        return "No locations saved yet."
    return "\n".join(
        f"{location.name} ({location.pose.x:.2f}, {location.pose.y:.2f}, {location.frame_id})"
        f"{' aka ' + ', '.join(location.aliases) if location.aliases else ''}"
        for location in locations
    )


async def _stop_robot(resources: _ServerResources) -> str:
    try:
        client = await resources.bridge()
        receipt = await halt_robot_with_receipt(resources.config, client)
        if receipt.navigation_cancel_status is NavigationCancelStatus.UNCONFIRMED:
            return f"unconfirmed: {receipt.message}"
        return receipt.message
    except BridgeError as exc:
        return f"unavailable: {exc}"


async def _robot_pose(resources: _ServerResources) -> str:
    try:
        client = await resources.bridge()
        pose = await client.get_pose(timeout=3.0)
    except BridgeError as exc:
        return f"unavailable: {exc}"
    return f"x={pose.x:.3f} y={pose.y:.3f} yaw={pose.yaw:.3f} ({pose.frame_id}, from {pose.source})"


async def _camera_look(resources: _ServerResources, topic: str) -> str:
    config = resources.config
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


def _register_ros_tools(mcp: FastMCP, resources: _ServerResources) -> None:
    @mcp.tool()
    async def ros_topics() -> str:
        """List ROS2 topics currently on the graph, with a kind hint each."""
        return await _ros_topics(resources)

    @mcp.tool()
    async def ros_topic_info(topic: str) -> str:
        """Show a topic's message type, publishers, and subscribers."""
        return await _ros_topic_info(resources, topic)

    @mcp.tool()
    async def ros_echo(topic: str, count: int = 3) -> str:
        """Snapshot up to `count` recent messages from a topic."""
        return await _ros_echo(resources, topic, count)

    @mcp.tool()
    async def list_locations() -> str:
        """List the robot's saved named locations (for navigate_to)."""
        return _list_locations(resources)


def _register_robot_tools(mcp: FastMCP, resources: _ServerResources) -> None:
    @mcp.tool()
    async def stop() -> str:
        """EMERGENCY STOP: cancel navigation and command zero velocity.

        Always available (even read-only servers) — stopping is always safe.
        """
        return await _stop_robot(resources)

    @mcp.tool()
    async def robot_pose() -> str:
        """The robot's current position (x, y, yaw) from AMCL or odometry."""
        return await _robot_pose(resources)

    @mcp.tool()
    async def camera_look(topic: str = "") -> str:
        """Capture one camera frame and describe it with the vision model.
        Omit `topic` to use the vehicle's configured camera."""
        return await _camera_look(resources, topic)


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
            return navigation_receipt_text(output)


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
    if allow_actions and has_registered_capability(config, "navigate"):
        _register_navigation_tool(mcp, resources)

    return mcp
