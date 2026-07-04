"""Robot-facing slash-command handlers for the JenAI TUI.

Mixin for JenAITuiApp: everything that touches ROS — topic inspection,
driving, navigation (with the live rclpy bridge), locations, and camera
vision. Approval plumbing and app state come from the host class.
"""

from __future__ import annotations

import asyncio
import json
import re

from rich.markup import escape

from jenai.adapters.locations import (
    LocationNotFoundError,
    LocationsFileError,
    append_location,
    find_location,
    load_locations_tolerant,
)
from jenai.bridge import BridgeError, RosBridgeClient
from jenai.schemas import (
    ApprovalRequest,
    EffectScope,
    Location,
    Pose2D,
    RiskLevel,
    RunStatus,
    ToolCallCategory,
    ToolCallRecord,
)
from jenai.tools.drive_core import extract_drive_command
from jenai.tools.mission_core import parse_mission
from jenai.tools.nav_live import navigate_with_fallback
from jenai.tools.perception import PerceptionLoop
from jenai.tools.ros2_core import (
    ros_echo,
    ros_pub_validate,
    ros_schema,
    ros_topic_info,
    ros_topics,
)
from jenai.tools.route_core import route_preview
from jenai.tools.safety import arm_watchdog, halt_robot
from jenai.tools.skills import find_dock, parse_patrol
from jenai.tools.vision_core import VisionError, analyze_image, capture_and_analyze
from jenai.tui.panels import MUTED, OutputPanel, TimelineItem, _is_number
from jenai.tui.widgets import ApprovalCard


class RobotCommandsMixin:
    async def _request_direct_approval(self, ctx, tool_call, pending: dict, approval) -> None:
        """The one approval pipeline for direct (non-agent) actuating commands:
        record the tool call, honor session auto-approval (auto_key falls back
        to the execution kind), otherwise raise the card and park the action."""
        self.run_store.add_tool_call(ctx.run, tool_call)
        if pending.get("auto_key", pending["kind"]) in self._auto_approved:
            await self._execute_direct(pending)
            return
        self.run_store.add_interruption(ctx.run, approval)
        self.run_store.set_status(ctx.run, RunStatus.AWAITING_APPROVAL)
        self._pending_direct_approvals[approval.tool_call_id] = pending
        await self._mount_event(ApprovalCard(approval))
        self._scroll_to_bottom()

    async def _show_ros_topics(self, _: str = "") -> None:
        output = await ros_topics(self.config)
        if not output.topics:
            await self._mount_event(TimelineItem("warn", "No topics found."))
            return
        rows = [f"{item.name}  [#9c9689]({item.kind_hint})[/]" for item in output.topics]
        await self._mount_event(OutputPanel("ROS2 topics", "\n".join(rows)))

    async def _show_ros_topic_info(self, arg: str) -> None:
        if not arg:
            await self._mount_event(TimelineItem("warn", "Usage: /ros topic-info <topic>"))
            return

        output = await ros_topic_info(self.config, arg)
        if not output.message_type:
            await self._mount_event(TimelineItem("warn", output.summary))
            return

        lines = [
            f"Message type: [bold #f2ede1]{output.message_type}[/]",
            f"Publishers ({output.publisher_count}): {', '.join(output.publishers) or '—'}",
            f"Subscribers ({output.subscriber_count}): {', '.join(output.subscribers) or '—'}",
        ]
        await self._mount_event(OutputPanel(f"Topic info: {arg}", "\n".join(lines)))

    async def _show_ros_schema(self, arg: str) -> None:
        if not arg:
            await self._mount_event(TimelineItem("warn", "Usage: /ros schema <topic>"))
            return

        output = await ros_schema(self.config, arg)
        lines = [f"Message type: [bold #f2ede1]{output.message_type}[/]", ""]
        for field in output.field_summary:
            lines.append(
                f"[bold #f2ede1]{field.field_name}[/] ({field.field_type}): {field.description}"
            )
        await self._mount_event(OutputPanel(f"Schema: {arg}", "\n".join(lines)))

    async def _show_ros_echo(self, arg: str) -> None:
        parts = arg.split()
        if not parts:
            await self._mount_event(TimelineItem("warn", "Usage: /ros echo <topic> [count]"))
            return
        topic = parts[0]
        limit = 1
        if len(parts) > 1 and parts[1].isdigit():
            limit = max(1, int(parts[1]))

        output = await ros_echo(self.config, topic, limit=limit)
        if not output.messages:
            await self._mount_event(TimelineItem("warn", output.summary))
            return
        rendered = "\n\n".join(
            json.dumps(msg, ensure_ascii=False, indent=2) for msg in output.messages
        )
        await self._mount_event(OutputPanel(f"Echo: {topic}", rendered))

    async def _show_ros_pub(self, arg: str) -> None:
        parts = arg.split(maxsplit=1)
        if len(parts) != 2:
            await self._mount_event(TimelineItem("warn", "Usage: /ros pub <topic> <json payload>"))
            return

        topic, payload_json = parts
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            await self._mount_event(TimelineItem("error", f"Invalid JSON payload: {exc}"))
            return

        validation = await ros_pub_validate(topic, payload)
        if not validation.ok:
            message = validation.error.message if validation.error else "Validation failed."
            await self._mount_event(TimelineItem("error", message))
            return

        ctx = self._new_run_context(f"/ros pub {arg}")
        tool_call = ToolCallRecord(
            tool_name="ros_pub_execute_tool",
            category=ToolCallCategory.ROS2,
            input_summary=f"publish to {topic}",
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
        )
        pending = {
            "kind": "ros_pub",
            "ctx": ctx,
            "topic": topic,
            "message_type": validation.message_type,
            "payload": payload,
        }
        approval = ApprovalRequest(
            run_id=ctx.run.run_id,
            tool_call_id=tool_call.tool_call_id,
            title=f"Publish to {topic}",
            summary=f"Send a {validation.message_type} message to {topic}.",
            raw_action=f'ros2 topic pub --once {topic} {validation.message_type} "{payload}"',
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
            justification="Requested via /ros pub.",
        )
        await self._request_direct_approval(ctx, tool_call, pending, approval)

    async def _show_ros_drive(self, arg: str) -> None:
        # /ros drive <topic> <json payload> [seconds]
        parts = arg.split()
        if len(parts) < 2:
            await self._mount_event(
                TimelineItem("warn", "Usage: /ros drive <topic> <json payload> [seconds]")
            )
            return
        duration = 1.0
        if len(parts) >= 3 and _is_number(parts[-1]):
            duration = float(parts[-1])
            payload_json = " ".join(parts[1:-1])
        else:
            payload_json = " ".join(parts[1:])
        topic = parts[0]
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            await self._mount_event(TimelineItem("error", f"Invalid JSON payload: {exc}"))
            return

        validation = await ros_pub_validate(topic, payload)
        if not validation.ok:
            message = validation.error.message if validation.error else "Validation failed."
            await self._mount_event(TimelineItem("error", message))
            return

        ctx = self._new_run_context(f"/ros drive {arg}")
        tool_call = ToolCallRecord(
            tool_name="ros_drive_execute_tool",
            category=ToolCallCategory.ROS2,
            input_summary=f"drive {topic} for {duration}s",
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
        )
        pending = {
            "kind": "drive",
            "ctx": ctx,
            "topic": topic,
            "message_type": validation.message_type,
            "payload": payload,
            "duration": duration,
        }
        approval = ApprovalRequest(
            run_id=ctx.run.run_id,
            tool_call_id=tool_call.tool_call_id,
            tool_name="ros_drive_execute_tool",
            title=f"Drive {topic} for {duration}s",
            summary=f"Publish a {validation.message_type} to {topic} for {duration}s, then stop.",
            raw_action=f"ros2 topic pub --rate 10 {topic} … for {duration}s, then zero-stop",
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
            justification="Requested via /ros drive.",
        )
        await self._request_direct_approval(ctx, tool_call, pending, approval)

    async def _show_drive(self, arg: str) -> None:
        # Natural-language driving: "前進兩秒", "turn left", "slowly reverse".
        if not arg:
            await self._mount_event(
                TimelineItem("warn", "Usage: /drive <plain language>, e.g. /drive 前進兩秒")
            )
            return

        intent = await extract_drive_command(self.config, arg)
        if intent is None:
            await self._mount_event(
                TimelineItem("warn", f"Could not understand '{arg}' as a drive command.")
            )
            return

        topic = self.config.vehicle.cmd_vel_topic
        message_type = "geometry_msgs/msg/Twist"
        ctx = self._new_run_context(f"/drive {arg}")
        tool_call = ToolCallRecord(
            tool_name="ros_drive_execute_tool",
            category=ToolCallCategory.ROS2,
            input_summary=intent.description,
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
        )
        pending = {
            "kind": "drive",
            "ctx": ctx,
            "topic": topic,
            "message_type": message_type,
            "payload": intent.to_payload(),
            "duration": intent.duration_s,
        }
        approval = ApprovalRequest(
            run_id=ctx.run.run_id,
            tool_call_id=tool_call.tool_call_id,
            tool_name="ros_drive_execute_tool",
            title=f"Drive: {intent.description}",
            summary=f"Interpreted '{arg}' as: {intent.description}.",
            raw_action=(
                f"{topic} linear.x={intent.linear_x:g} angular.z={intent.angular_z:g} "
                f"for {intent.duration_s:g}s (continuous, then stop)"
            ),
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
            justification=f"Requested via /drive: {arg}",
        )
        await self._request_direct_approval(ctx, tool_call, pending, approval)

    async def _show_mission(self, arg: str) -> None:
        # /mission kitchen, drive turn left, lobby  → a supervised multi-step run.
        if not arg:
            await self._mount_event(
                TimelineItem("warn", "Usage: /mission <place>, <place>, … (or 'drive <motion>')")
            )
            return
        steps = parse_mission(arg)
        if not steps:
            await self._mount_event(TimelineItem("warn", "No mission steps recognized."))
            return

        plan = " → ".join(f"{s.kind} {s.target}" for s in steps)
        ctx = self._new_run_context(f"/mission {arg}")
        tool_call = ToolCallRecord(
            tool_name="mission",
            category=ToolCallCategory.ROS2,
            input_summary=plan,
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
        )
        pending = {
            "kind": "mission",
            "ctx": ctx,
            "steps": steps,
            "locations": self._load_locations(),
        }
        approval = ApprovalRequest(
            run_id=ctx.run.run_id,
            tool_call_id=tool_call.tool_call_id,
            tool_name="mission",
            title=f"Run mission · {len(steps)} steps",
            summary=f"The robot will carry out: {plan}.",
            raw_action=plan,
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
            justification=f"Requested via /mission: {arg}",
        )
        await self._request_direct_approval(ctx, tool_call, pending, approval)

    async def _show_patrol(self, arg: str) -> None:
        # /patrol A, B, C x3 photo → loop the waypoints, optional VLM report.
        spec = parse_patrol(arg) if arg else None
        if spec is None:
            await self._mount_event(
                TimelineItem("warn", "Usage: /patrol <place>, <place>, … [xN] [photo]")
            )
            return

        plan = spec.describe()
        total = len(spec.points) * spec.loops
        ctx = self._new_run_context(f"/patrol {arg}")
        tool_call = ToolCallRecord(
            tool_name="patrol",
            category=ToolCallCategory.ROS2,
            input_summary=plan,
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
        )
        pending = {
            "kind": "patrol",
            "ctx": ctx,
            "spec": spec,
            "locations": self._load_locations(),
        }
        approval = ApprovalRequest(
            run_id=ctx.run.run_id,
            tool_call_id=tool_call.tool_call_id,
            tool_name="patrol",
            title=f"Patrol · {total} waypoints",
            summary=f"The robot will patrol: {plan}.",
            raw_action=plan,
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
            justification=f"Requested via /patrol: {arg}",
        )
        await self._request_direct_approval(ctx, tool_call, pending, approval)

    async def _show_dock(self, _: str = "") -> None:
        # /dock → navigate to the location tagged 'dock' (or named like one).
        dock = find_dock(self._load_locations())
        if dock is None:
            await self._mount_event(
                TimelineItem(
                    "warn",
                    "No dock location found. Tag one in locations.toml "
                    '(tags = ["dock"]) or save it: /loc add here Dock',
                )
            )
            return

        ctx = self._new_run_context("/dock")
        tool_call = ToolCallRecord(
            tool_name="route_execute_tool",
            category=ToolCallCategory.ROUTE,
            input_summary=f"return to dock '{dock.name}'",
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
        )
        # A dock run reuses the route execution pipeline (kind), but keeps its
        # own approval identity (auto_key): remembering /route must not
        # silently auto-approve /dock, nor the reverse.
        pending = {
            "kind": "route",
            "auto_key": "dock",
            "ctx": ctx,
            "outgoing_action": {"goal": dock.model_dump(mode="json")},
        }
        approval = ApprovalRequest(
            run_id=ctx.run.run_id,
            tool_call_id=tool_call.tool_call_id,
            tool_name="route_execute_tool",
            title=f"Return to dock · {dock.name}",
            summary=(
                f"The robot will navigate to '{dock.name}' "
                f"({dock.pose.x:.2f}, {dock.pose.y:.2f})."
            ),
            raw_action=f"goto {dock.name}",
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
            justification="Requested via /dock",
        )
        await self._request_direct_approval(ctx, tool_call, pending, approval)

    # -- Route / locations ----------------------------------------------------

    async def _show_route(self, arg: str) -> None:
        if not arg:
            await self._mount_event(
                TimelineItem("warn", "Usage: /route <natural language request>")
            )
            return

        locations = self._load_locations()
        output = await route_preview(self.config, locations, arg)
        if not output.outgoing_action:
            await self._mount_event(TimelineItem("warn", output.route_preview))
            return

        ctx = self._new_run_context(f"/route {arg}")
        tool_call = ToolCallRecord(
            tool_name="route_execute_tool",
            category=ToolCallCategory.ROUTE,
            input_summary=output.route_preview,
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
        )
        pending = {"kind": "route", "ctx": ctx, "outgoing_action": output.outgoing_action}
        approval = ApprovalRequest(
            run_id=ctx.run.run_id,
            tool_call_id=tool_call.tool_call_id,
            title="Send navigation route",
            summary=output.route_preview,
            raw_action=str(output.outgoing_action),
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
            justification="Requested via /route.",
        )
        await self._request_direct_approval(ctx, tool_call, pending, approval)

    async def _show_report(self, arg: str = "") -> None:
        from jenai.state.reports import (
            list_patrol_logs,
            load_patrol_log,
            render_patrol_markdown,
            summarize_patrol,
        )

        logs = list_patrol_logs(self.config_path)
        if not logs:
            await self._mount_event(
                TimelineItem("warn", "No patrol logs yet — finish a /patrol first.")
            )
            return
        if arg.strip() == "list":
            rows = [f"{i}. [#9c9689]{p.name}[/]" for i, p in enumerate(logs[:10], start=1)]
            await self._mount_event(OutputPanel("Patrol logs (newest first)", "\n".join(rows)))
            return
        log = load_patrol_log(logs[0])
        if log is None:
            await self._mount_event(TimelineItem("error", f"Log unreadable: {logs[0]}"))
            return
        await self._mount_event(
            OutputPanel(f"Patrol report · {logs[0].name}", escape(render_patrol_markdown(log)))
        )
        digest = await summarize_patrol(self.config, log)
        if digest:
            await self._mount_event(TimelineItem("assistant", escape(digest)))
        else:
            # Honest: the deterministic body above IS the report; the LLM
            # paragraph is a bonus, never a dependency.
            await self._mount_event(
                TimelineItem("warn", "LLM digest unavailable — deterministic report shown above.")
            )

    async def _show_loc_list(self, _: str = "") -> None:
        locations = self._load_locations()
        if not locations:
            await self._mount_event(
                TimelineItem("warn", "No locations configured. Add entries to locations.toml.")
            )
            return
        rows = [
            f"[bold #f2ede1]{loc.name}[/] · {', '.join(loc.aliases) or 'no aliases'}"
            for loc in locations
        ]
        await self._mount_event(OutputPanel("Locations", "\n".join(rows)))

    async def _show_loc_add(self, arg: str) -> None:
        name = arg.strip()
        if name.lower().startswith("gps "):
            await self._loc_add_gps(name[4:].strip())
            return
        if name.lower().startswith("here "):  # "/loc add here Kitchen" and "/loc add Kitchen"
            name = name[5:].strip()
        elif name.lower() == "here":  # bare "/loc add here" has no name to save
            name = ""
        if not name or name.startswith("<"):
            await self._mount_event(
                TimelineItem(
                    "warn",
                    "Usage: [bold #f2ede1]/loc add here <name>[/] · "
                    "[bold #f2ede1]/loc add gps <name> <lat> <lon>[/]",
                )
            )
            return

        locations_path = self.config.resolved_locations_path(self.config_path)
        if locations_path is None:
            await self._mount_event(
                TimelineItem("warn", "No locations_path is configured — add one to the config.")
            )
            return

        try:
            bridge = await self._get_bridge()
            pose = await bridge.get_pose(timeout=3.0)
        except BridgeError as exc:
            await self._mount_event(
                TimelineItem("warn", f"Could not read the robot's position: {exc}")
            )
            return

        location = Location(
            name=name,
            frame_id=pose.frame_id,
            pose=Pose2D(x=round(pose.x, 3), y=round(pose.y, 3), yaw=round(pose.yaw, 3)),
        )
        try:
            await asyncio.to_thread(append_location, location, locations_path)
        except LocationsFileError as exc:
            await self._mount_event(TimelineItem("warn", str(exc)))
            return

        note = ""
        if pose.source == "/odom":
            note = (
                f"\n[{MUTED}]Caution: pose came from /odom (no localization) — coordinates are "
                "in the odom frame and drift over time. Start AMCL for map-frame poses.[/]"
            )
        await self._mount_event(
            TimelineItem(
                "success",
                f"Saved [bold #f2ede1]{name}[/] at x={location.pose.x} y={location.pose.y} "
                f"yaw={location.pose.yaw} ({pose.frame_id}, from {pose.source}) · "
                f"try [bold #f2ede1]/route from here to {name}[/]{note}",
            )
        )

    async def _loc_add_gps(self, arg: str) -> None:
        """`/loc add gps <name> <lat> <lon>` — campus lat/lon into map metres."""
        from jenai.adapters.locations import gps_to_map_xy

        numbers = re.findall(r"-?\d+(?:\.\d+)?", arg)
        name = re.split(r"-?\d+(?:\.\d+)?", arg, maxsplit=1)[0].strip().rstrip(",= ")
        if not name or len(numbers) < 2:
            await self._mount_event(
                TimelineItem(
                    "warn", "Usage: [bold #f2ede1]/loc add gps <name> <lat> <lon>[/]"
                )
            )
            return
        lat, lon = float(numbers[0]), float(numbers[1])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            await self._mount_event(TimelineItem("warn", f"({lat}, {lon}) 不是合法經緯度。"))
            return
        datum = self.config.map_datum
        if not datum.configured:
            await self._mount_event(
                TimelineItem(
                    "warn",
                    "GPS 地點需要先設定地圖基準點 —— 在 config 加:\n"
                    "[bold #f2ede1][map_datum][/]\n"
                    "[bold #f2ede1]lat = <map 原點的緯度>[/]\n"
                    "[bold #f2ede1]lon = <map 原點的經度>[/]\n"
                    "[bold #f2ede1]yaw_deg = <map +x 相對正東的角度,對齊 ENU 則為 0>[/]\n"
                    "(建圖起點的 GPS 讀值即可;沒有基準點,經緯度換不成 map 座標 —— 不猜。)",
                )
            )
            return
        locations_path = self.config.resolved_locations_path(self.config_path)
        if locations_path is None:
            await self._mount_event(
                TimelineItem("warn", "No locations_path is configured — add one to the config.")
            )
            return
        x, y = gps_to_map_xy(datum, lat, lon)
        location = Location(
            name=name,
            pose=Pose2D(x=round(x, 3), y=round(y, 3), yaw=0.0),
            description=f"gps {lat}, {lon}",
        )
        try:
            await asyncio.to_thread(append_location, location, locations_path)
        except LocationsFileError as exc:
            await self._mount_event(TimelineItem("warn", str(exc)))
            return
        await self._mount_event(
            TimelineItem(
                "success",
                f"Saved [bold #f2ede1]{name}[/] at x={location.pose.x} y={location.pose.y} "
                f"(map,自 GPS {lat}, {lon} 換算) · 實地驗證第一次導航,基準點誤差會整批平移",
            )
        )

    async def _show_loc_show(self, arg: str) -> None:
        if not arg:
            await self._mount_event(TimelineItem("warn", "Usage: /loc show <name>"))
            return

        locations = self._load_locations()
        try:
            location = find_location(locations, arg)
        except LocationNotFoundError as exc:
            if exc.candidates:
                names = ", ".join(loc.name for loc in exc.candidates)
                await self._mount_event(
                    TimelineItem("warn", f"Location '{arg}' not found. Did you mean: {names}?")
                )
            else:
                await self._mount_event(TimelineItem("warn", f"Location '{arg}' not found."))
            return

        lines = [
            f"Name: [bold #f2ede1]{location.name}[/]",
            f"Aliases: {', '.join(location.aliases) or '(none)'}",
            f"Frame: {location.frame_id}",
            f"Pose: x={location.pose.x}, y={location.pose.y}, yaw={location.pose.yaw}",
            f"Tags: {', '.join(location.tags) or '(none)'}",
        ]
        if location.description:
            lines.append(f"Description: {location.description}")
        await self._mount_event(OutputPanel(f"Location: {location.name}", "\n".join(lines)))

    # -- Approval decisions ---------------------------------------------------

    async def _show_vision(self, arg: str) -> None:
        # Accept "/vision image <path>", "/vision <path>", and "/vision camera [topic]".
        parts = arg.split(maxsplit=1)
        if parts and parts[0] == "camera":
            topic = parts[1].strip() if len(parts) > 1 else self.config.vehicle.camera_topic
            await self._show_vision_camera(topic)
            return
        if parts and parts[0] == "image":
            path = parts[1].strip() if len(parts) > 1 else ""
        else:
            path = arg.strip()
        if not path:
            await self._mount_event(
                TimelineItem("warn", "Usage: /vision image <path> · /vision camera [topic]")
            )
            return

        await self._analyze_and_render(path)

    async def _show_vision_camera(self, topic: str) -> None:
        """Grab one frame from a camera topic and run it through the VLM."""
        self._spinner_label = f"Capturing {topic}"

        def _on_captured() -> None:
            self._spinner_label = "Analyzing frame"

        try:
            bridge = await self._get_bridge()
            output = await capture_and_analyze(
                self.config, bridge, topic, timeout=5.0, on_captured=_on_captured
            )
        except BridgeError as exc:
            await self._mount_event(
                TimelineItem(
                    "warn",
                    f"Could not capture from [bold #f2ede1]{topic}[/]: {exc}\n"
                    f"[{MUTED}]List image topics with /ros topics.[/]",
                )
            )
            return
        except VisionError as exc:
            await self._mount_event(TimelineItem("error", str(exc)))
            return
        await self._render_vision_output(output, source_label=topic)

    async def _analyze_and_render(self, path: str, *, source_label: str | None = None) -> None:
        try:
            output = await analyze_image(self.config, path)
        except VisionError as exc:
            await self._mount_event(TimelineItem("error", str(exc)))
            return
        await self._render_vision_output(output, source_label=source_label)

    async def _render_vision_output(self, output, *, source_label: str | None = None) -> None:
        lines = [output.summary]
        if output.objects:
            lines.append(f"[bold #f2ede1]Objects:[/] {', '.join(output.objects)}")
        if output.anomalies:
            lines.append(f"[bold #f2ede1]Anomalies:[/] {', '.join(output.anomalies)}")
        if output.next_action_suggestions:
            lines.append(
                "[bold #f2ede1]Suggested next:[/] " + "; ".join(output.next_action_suggestions)
            )
        title = source_label or output.source
        await self._mount_event(OutputPanel(f"Vision: {title}", "\n".join(lines)))

    async def _get_bridge(self) -> RosBridgeClient:
        """Start (or reuse) the rclpy bridge; raises BridgeError when ROS is absent."""
        if self._bridge is None:
            self._bridge = RosBridgeClient()
            # Register the dead-client watchdog config once; every (re)spawn
            # arms it inside start(), so a hung or killed TUI can never leave
            # the robot driving unsupervised — even after a bridge crash.
            await arm_watchdog(self.config, self._bridge)
        if not self._bridge.running:
            await self._bridge.start()
        return self._bridge

    async def _show_perception(self, arg: str) -> None:
        """/perception start [topic] [hz] · stop · status — continuous camera→VLM.

        Perception only OBSERVES: suggested actions are rendered for the
        human (or matched by daemon rules) and always go through the existing
        approval machinery — nothing here actuates.
        """
        sub, _, rest = arg.strip().partition(" ")
        sub = sub.strip().lower()

        if sub == "start":
            loop = getattr(self, "_perception", None)
            if loop is not None and loop.running:
                await self._mount_event(
                    TimelineItem(
                        "warn", "Perception loop already running — /perception stop first."
                    )
                )
                return
            topic = None
            hz = 1.0
            for token in rest.split():
                if token.startswith("/"):
                    topic = token
                elif _is_number(token):
                    hz = max(0.05, float(token))
            try:
                bridge = await self._get_bridge()
            except BridgeError as exc:
                await self._mount_event(
                    TimelineItem("warn", f"Perception unavailable (no ROS bridge): {exc}")
                )
                return

            async def _on_analysis(analysis) -> None:
                parts = [escape(analysis.scene_context or "(no description)")]
                if analysis.affordances:
                    tags = " ".join(f"#{escape(a)}" for a in analysis.affordances)
                    parts.append(f"[#9c9689]{tags}[/]")
                if analysis.suggested_action:
                    note = (
                        " [#9c9689](suggestion only — needs approval)[/]"
                        if analysis.requires_approval
                        else ""
                    )
                    parts.append(f"[bold #f2ede1]→ {escape(analysis.suggested_action)}[/]{note}")
                parts.append(f"[#9c9689]{analysis.confidence:.0%}[/]")
                await self._mount_event(TimelineItem("muted", "👁 " + " · ".join(parts)))
                self._scroll_to_bottom()

            async def _on_status(message: str) -> None:
                await self._mount_event(TimelineItem("warn", escape(message)))
                self._scroll_to_bottom()

            self._perception = PerceptionLoop(
                self.config,
                bridge,
                topic=topic,
                hz=hz,
                on_analysis=_on_analysis,
                on_status=_on_status,
            )
            await self._perception.start()
            await self._mount_event(
                TimelineItem(
                    "success",
                    f"Perception loop started · {self._perception.topic} @ {hz:g}Hz "
                    "(/perception stop to end)",
                )
            )
            return

        if sub == "stop":
            loop = getattr(self, "_perception", None)
            if loop is None or not loop.running:
                await self._mount_event(TimelineItem("warn", "Perception loop is not running."))
                return
            frames = loop.frames
            await loop.stop()
            await self._mount_event(
                TimelineItem("success", f"Perception loop stopped ({frames} frames analyzed).")
            )
            return

        loop = getattr(self, "_perception", None)
        if loop is not None and loop.running:
            latest = loop.latest
            detail = f" · last: {escape(latest.scene_context)}" if latest is not None else ""
            await self._mount_event(
                TimelineItem(
                    "muted",
                    f"Perception running · {loop.topic} · {loop.frames} frames{detail}",
                )
            )
        else:
            await self._mount_event(
                TimelineItem("muted", "Perception idle. Usage: /perception start [topic] [hz]")
            )

    async def _show_stop(self, _: str = "") -> None:
        """EMERGENCY STOP — no approval gate: stopping is always safe."""
        self._spinner_label = "STOPPING"
        try:
            bridge = await self._get_bridge()
            message = await halt_robot(self.config, bridge)
        except BridgeError as exc:
            await self._mount_event(
                TimelineItem("warn", f"Stop unavailable (no ROS bridge): {exc}")
            )
            return
        await self._mount_event(TimelineItem("success", message))

    async def _execute_route_action(self, outgoing_action: dict):
        """Execute a navigation action: live bridge (feedback + Esc cancel) when
        Nav2 is configured and ROS is present, otherwise the honest CLI adapter."""

        def _progress(p) -> None:
            self._spinner_label = (
                f"Navigating · {p.distance_remaining:.1f} m left · {p.elapsed:.0f}s"
                + (f" · {p.recoveries} recoveries" if p.recoveries else "")
            )

        def _gate(message: str) -> None:
            self._spinner_label = message

        return await navigate_with_fallback(
            self.config, self._get_bridge, outgoing_action, on_progress=_progress, on_gate=_gate
        )

    def _load_locations(self) -> list[Location]:
        locations, _error = load_locations_tolerant(self._locations_path())
        return locations
