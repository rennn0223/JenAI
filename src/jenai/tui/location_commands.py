"""Saved-location slash commands for the JenAI TUI.

The host App supplies rendering, config and bridge lifecycle methods.
Keeping location persistence here prevents robot_commands from mixing
map data management with navigation and perception execution.
"""

from __future__ import annotations

import asyncio
import math
import re

from jenai.adapters.locations import (
    LocationNotFoundError,
    LocationsFileError,
    append_location,
    find_location,
    remove_location,
    rename_location,
    update_location_pose,
)
from jenai.bridge import BridgeError, PoseInfo
from jenai.schemas import Location, Pose2D
from jenai.tui.host_contract import TuiHostContract
from jenai.tui.panels import MUTED, OutputPanel, TimelineItem


def _format_location_row(location: Location) -> str:
    aliases = ", ".join(location.aliases)
    suffix = f" · {aliases}" if aliases else ""
    return f"[bold #f2ede1]{location.name}[/]{suffix}"


class LocationCommandsMixin(TuiHostContract):
    async def _show_loc_list(self, _: str = "") -> None:
        locations = self._load_locations()
        if not locations:
            await self._mount_event(
                TimelineItem("warn", "No locations configured. Add entries to locations.toml.")
            )
            return
        rows = [_format_location_row(loc) for loc in locations]
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

        pose = await self._read_current_pose()
        if pose is None:
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

    async def _read_current_pose(self) -> PoseInfo | None:
        """Robot pose fit for saving into locations, or None (a warn was shown)."""
        try:
            bridge = await self._get_bridge()
            pose = await bridge.get_pose(timeout=3.0)
        except BridgeError as exc:
            await self._mount_event(
                TimelineItem("warn", f"Could not read the robot's position: {exc}")
            )
            return None
        if not all(math.isfinite(v) for v in (pose.x, pose.y, pose.yaw)):
            # Pose2D rejects NaN/inf; pre-check so the user gets a diagnosis
            # instead of a raw pydantic ValidationError from the generic net.
            await self._mount_event(
                TimelineItem(
                    "warn",
                    f"Robot pose from {pose.source} is not finite "
                    f"(x={pose.x}, y={pose.y}, yaw={pose.yaw}) — location not saved. "
                    "Check localization (AMCL / odometry) and try again.",
                )
            )
            return None
        return pose

    async def _show_loc_rm(self, arg: str) -> None:
        name = arg.strip()
        if not name:
            await self._mount_event(TimelineItem("warn", "Usage: [bold #f2ede1]/loc rm <name>[/]"))
            return
        locations_path = self.config.resolved_locations_path(self.config_path)
        if locations_path is None:
            await self._mount_event(
                TimelineItem("warn", "No locations_path is configured — add one to the config.")
            )
            return
        try:
            removed = await asyncio.to_thread(remove_location, name, locations_path)
        except LocationsFileError as exc:
            await self._mount_event(TimelineItem("warn", str(exc)))
            return
        await self._mount_event(
            TimelineItem(
                "success",
                f"Removed [bold #f2ede1]{removed.name}[/] "
                f"(was x={removed.pose.x} y={removed.pose.y} yaw={removed.pose.yaw})",
            )
        )

    async def _show_loc_rename(self, arg: str) -> None:
        # Names may contain spaces, so "old -> new" is the unambiguous form;
        # the bare two-token form covers the common case.
        if "->" in arg:
            old, _, new = arg.partition("->")
            old, new = old.strip(), new.strip()
        else:
            parts = arg.split()
            old, new = (parts[0], parts[1]) if len(parts) == 2 else ("", "")
        if not old or not new:
            await self._mount_event(
                TimelineItem(
                    "warn",
                    "Usage: [bold #f2ede1]/loc rename <old> <new>[/] "
                    "(names with spaces: [bold #f2ede1]/loc rename old name -> new name[/])",
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
            renamed = await asyncio.to_thread(rename_location, old, new, locations_path)
        except LocationsFileError as exc:
            await self._mount_event(TimelineItem("warn", str(exc)))
            return
        await self._mount_event(
            TimelineItem(
                "success",
                f"Renamed [bold #f2ede1]{old}[/] → [bold #f2ede1]{renamed.name}[/]",
            )
        )

    async def _show_loc_move(self, arg: str) -> None:
        name = arg.strip()
        if not name:
            await self._mount_event(
                TimelineItem(
                    "warn",
                    "Usage: [bold #f2ede1]/loc move <name>[/] — re-save an existing "
                    "location at the robot's current position",
                )
            )
            return
        locations_path = self.config.resolved_locations_path(self.config_path)
        if locations_path is None:
            await self._mount_event(
                TimelineItem("warn", "No locations_path is configured — add one to the config.")
            )
            return
        pose = await self._read_current_pose()
        if pose is None:
            return
        try:
            updated = await asyncio.to_thread(
                update_location_pose,
                name,
                Pose2D(x=round(pose.x, 3), y=round(pose.y, 3), yaw=round(pose.yaw, 3)),
                pose.frame_id,
                locations_path,
            )
        except LocationsFileError as exc:
            await self._mount_event(TimelineItem("warn", str(exc)))
            return
        await self._mount_event(
            TimelineItem(
                "success",
                f"Moved [bold #f2ede1]{updated.name}[/] to x={updated.pose.x} "
                f"y={updated.pose.y} yaw={updated.pose.yaw} "
                f"({pose.frame_id}, from {pose.source})",
            )
        )

    async def _loc_add_gps(self, arg: str) -> None:
        """`/loc add gps <name> <lat> <lon>` — campus lat/lon into map metres."""
        from jenai.adapters.locations import gps_to_map_xy

        numbers = re.findall(r"-?\d+(?:\.\d+)?", arg)
        name = re.split(r"-?\d+(?:\.\d+)?", arg, maxsplit=1)[0].strip().rstrip(",= ")
        if not name or len(numbers) < 2:
            await self._mount_event(
                TimelineItem("warn", "Usage: [bold #f2ede1]/loc add gps <name> <lat> <lon>[/]")
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
