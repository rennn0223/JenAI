from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from jenai.adapters.locations import LocationNotFoundError, find_location, load_locations
from jenai.bridge import BridgeError, RosBridgeClient
from jenai.config.models import AppConfig
from jenai.tools import ros2_core
from jenai.tools.nav_live import navigate_live
from jenai.tools.route_core import route_execute
from jenai.tools.vision_core import VisionError, analyze_image


def build_mcp_server(
    config: AppConfig,
    config_path: Path,
    *,
    allow_actions: bool = False,
) -> FastMCP:
    """Expose JenAI's robot tools as an MCP server (stdio transport).

    Read-only tools are always registered. `navigate_to` — the only tool that
    moves the robot — is registered ONLY when allow_actions is True: an MCP
    client's own permission prompt is the human gate, but the operator must
    first opt this server into actions at all. Every tool keeps JenAI's honest
    reporting: missing ROS/Nav2 reads as "unavailable", never fake success.
    """
    mcp = FastMCP(
        "jenai",
        instructions=(
            "Tools for a ROS2 mobile robot managed by JenAI. Read-only inspection is "
            "always available; navigation exists only when the operator started the "
            "server with --allow-actions."
        ),
    )
    # One lazily-started rclpy bridge shared by pose/camera/navigation tools.
    bridge = RosBridgeClient()

    async def _bridge_ready() -> RosBridgeClient:
        if not bridge.running:
            await bridge.start()
        return bridge

    def _locations_or_error() -> tuple[list, str | None]:
        locations_path = config.resolved_locations_path(config_path)
        if locations_path is None or not locations_path.exists():
            return [], "No locations file is configured (locations.toml)."
        return load_locations(locations_path), None

    @mcp.tool()
    async def ros_topics() -> str:
        """List ROS2 topics currently on the graph, with a kind hint each."""
        out = await ros2_core.ros_topics(config)
        if not out.topics:
            return "No topics on the graph (is ROS2 running?)."
        return "\n".join(f"{t.name} ({t.kind_hint})" for t in out.topics)

    @mcp.tool()
    async def ros_topic_info(topic: str) -> str:
        """Show a topic's message type, publishers, and subscribers."""
        out = await ros2_core.ros_topic_info(config, topic)
        return (
            f"type: {out.message_type}\npublishers: {out.publisher_count}\n"
            f"subscribers: {out.subscriber_count}"
        )

    @mcp.tool()
    async def ros_echo(topic: str, count: int = 3) -> str:
        """Snapshot up to `count` recent messages from a topic."""
        out = await ros2_core.ros_echo(config, topic, limit=count)
        if not out.messages:
            return "No messages received."
        return "\n---\n".join(json.dumps(m, ensure_ascii=False, default=str) for m in out.messages)

    @mcp.tool()
    async def list_locations() -> str:
        """List the robot's saved named locations (for navigate_to)."""
        locations, error = _locations_or_error()
        if error:
            return error
        if not locations:
            return "No locations saved yet."
        return "\n".join(
            f"{loc.name} ({loc.pose.x:.2f}, {loc.pose.y:.2f}, {loc.frame_id})"
            f"{' aka ' + ', '.join(loc.aliases) if loc.aliases else ''}"
            for loc in locations
        )

    @mcp.tool()
    async def robot_pose() -> str:
        """The robot's current position (x, y, yaw) from AMCL or odometry."""
        try:
            client = await _bridge_ready()
            pose = await client.get_pose(timeout=3.0)
        except BridgeError as exc:
            return f"unavailable: {exc}"
        return (
            f"x={pose.x:.3f} y={pose.y:.3f} yaw={pose.yaw:.3f} "
            f"({pose.frame_id}, from {pose.source})"
        )

    @mcp.tool()
    async def camera_look(topic: str = "/camera/image_raw") -> str:
        """Capture one camera frame and describe it with the vision model."""
        try:
            client = await _bridge_ready()
            frame = await client.capture_frame(topic, timeout=5.0)
        except BridgeError as exc:
            return f"unavailable: {exc}"
        try:
            output = await analyze_image(config, str(frame))
        except VisionError as exc:
            return f"vision error: {exc}"
        finally:
            frame.unlink(missing_ok=True)
        parts = [output.summary]
        if output.objects:
            parts.append("objects: " + ", ".join(output.objects))
        if output.anomalies:
            parts.append("anomalies: " + ", ".join(output.anomalies))
        return "\n".join(parts)

    if allow_actions:

        @mcp.tool()
        async def navigate_to(location: str) -> str:
            """Navigate the robot to a saved location BY NAME (see list_locations).

            This MOVES THE ROBOT. Only present because the operator started the
            server with --allow-actions.
            """
            locations, error = _locations_or_error()
            if error:
                return error
            try:
                target = find_location(locations, location)
            except LocationNotFoundError as exc:
                hint = ", ".join(c.name for c in exc.candidates) or "no close matches"
                return f"Unknown location '{location}' (near: {hint})."
            action = {"goal": target.model_dump(mode="json")}
            if config.route_adapter == "nav2" and RosBridgeClient.available():
                try:
                    client = await _bridge_ready()
                    output = await navigate_live(client, action)
                except BridgeError:
                    output = await route_execute(config, action)
            else:
                output = await route_execute(config, action)
            return f"{output.execution_status}: {output.route_preview}"

    return mcp
