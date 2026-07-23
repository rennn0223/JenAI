"""locations.toml load/save/fuzzy-search + GPS→map-frame conversion."""

from __future__ import annotations

import difflib
import math
import os
import tomllib
from pathlib import Path

from pydantic import ValidationError

from jenai.config.models import MapDatum
from jenai.schemas import Location, Pose2D
from jenai.secure_files import PRIVATE_FILE_MODE, atomic_write_text

_EARTH_RADIUS_M = 6378137.0  # WGS-84 equatorial
_DOCK_TAG = "dock"
_DOCK_NAMES = frozenset({"dock", "充電站", "充电站", "charging station"})


def gps_to_map_xy(datum: MapDatum, lat: float, lon: float) -> tuple[float, float]:
    """lat/lon → map-frame metres via a local ENU tangent plane at the datum.

    Equirectangular approximation — centimetre-class error at campus scale
    (< a few km), far below Nav2 goal tolerance. `datum.yaw_deg` is the
    bearing of map +x measured CCW from east, so a SLAM map that wasn't
    built axis-aligned to ENU still lands correctly.
    """
    if datum.lat is None or datum.lon is None:
        raise ValueError("map datum requires both lat and lon")
    east = math.radians(lon - datum.lon) * _EARTH_RADIUS_M * math.cos(math.radians(datum.lat))
    north = math.radians(lat - datum.lat) * _EARTH_RADIUS_M
    theta = math.radians(datum.yaw_deg)
    x = east * math.cos(theta) + north * math.sin(theta)
    y = -east * math.sin(theta) + north * math.cos(theta)
    return x, y


_STARTER_CONTENT = """\
# JenAI locations file
#
# Add locations like:
# [[locations]]
# name = "Engineering Building"
# aliases = ["engineering", "eng building"]
# frame_id = "map"
# tags = ["building"]
#
# [locations.pose]
# x = 1.0
# y = 2.0
# yaw = 0.0
"""


class LocationsFileError(Exception):
    """Raised when a locations file cannot be read or validated."""


class LocationNotFoundError(Exception):
    """Raised when a location query has no exact/alias match.

    Carries fuzzy `candidates` so the caller can ask the user to confirm
    rather than guessing, per F11/F12's "找不到位置時不亂猜" requirement.
    """

    def __init__(self, query: str, candidates: list[Location]) -> None:
        super().__init__(f"Location '{query}' was not found.")
        self.query = query
        self.candidates = candidates


def load_locations(path: Path) -> list[Location]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise LocationsFileError(f"Locations file not found: {path}") from exc
    except OSError as exc:
        raise LocationsFileError(f"Could not read locations file: {path}") from exc

    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise LocationsFileError(f"Locations file is not valid TOML: {path}") from exc
    except UnicodeDecodeError as exc:
        raise LocationsFileError(f"Locations file must be UTF-8: {path}") from exc

    entries = data.get("locations", [])
    try:
        return [Location.model_validate(entry) for entry in entries]
    except ValidationError as exc:
        raise LocationsFileError(f"Locations file has invalid entries: {exc}") from exc


def load_locations_tolerant(path: Path | None) -> tuple[list[Location], str | None]:
    """Load locations for display/lookup flows: create a starter file when
    missing and map failures to a message instead of an exception.

    Returns (locations, error_message) — error_message is None on success.
    The shared form of the loader every surface (CLI, TUI, WebUI, MCP) needs,
    so error handling can't drift between copies.
    """
    if path is None:
        return [], "No locations file is configured (locations.toml)."
    try:
        ensure_locations_file(path)
        return load_locations(path), None
    except LocationsFileError as exc:
        return [], str(exc)


def save_locations(locations: list[Location], path: Path) -> Path:
    """Persist all locations privately; a failed replacement keeps the old file."""
    return atomic_write_text(path, _to_toml(locations))


def ensure_locations_file(path: Path) -> Path:
    """Create an empty (starter-commented) locations file if one doesn't exist."""
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise LocationsFileError(f"Locations path must be a regular file: {path}")
        try:
            os.chmod(path, PRIVATE_FILE_MODE)
        except OSError as exc:
            raise LocationsFileError(f"Could not secure locations file: {path}") from exc
        return path
    return atomic_write_text(path, _STARTER_CONTENT)


def append_location(location: Location, path: Path) -> list[Location]:
    """Add one location to the file, refusing names/aliases that already exist."""
    locations = load_locations(path) if path.exists() else []
    taken = {loc.name.strip().lower() for loc in locations}
    for loc in locations:
        taken.update(alias.strip().lower() for alias in loc.aliases)
    if location.name.strip().lower() in taken:
        raise LocationsFileError(f"A location named '{location.name}' already exists.")
    locations.append(location)
    save_locations(locations, path)
    return locations


def _find_index(locations: list[Location], name: str) -> int:
    """Exact (case-insensitive) name match — destructive ops must never fuzzy-guess."""
    normalized = name.strip().lower()
    for index, location in enumerate(locations):
        if location.name.strip().lower() == normalized:
            return index
    known = ", ".join(location.name for location in locations)
    raise LocationsFileError(f"No location named '{name}'." + (f" Known: {known}" if known else ""))


def remove_location(name: str, path: Path) -> Location:
    """Delete one location by exact name; returns the removed entry."""
    locations = load_locations(path) if path.exists() else []
    removed = locations.pop(_find_index(locations, name))
    save_locations(locations, path)
    return removed


def rename_location(old: str, new: str, path: Path) -> Location:
    """Rename by exact name, refusing collisions with other names/aliases."""
    locations = load_locations(path) if path.exists() else []
    index = _find_index(locations, old)
    new_name = new.strip()
    if not new_name:
        raise LocationsFileError("New name must not be empty.")
    taken: set[str] = set()
    for i, location in enumerate(locations):
        if i == index:
            continue
        taken.add(location.name.strip().lower())
        taken.update(alias.strip().lower() for alias in location.aliases)
    if new_name.lower() in taken:
        raise LocationsFileError(f"A location named '{new_name}' already exists.")
    locations[index] = locations[index].model_copy(update={"name": new_name})
    save_locations(locations, path)
    return locations[index]


def update_location_pose(name: str, pose: Pose2D, frame_id: str, path: Path) -> Location:
    """Re-point an existing location at a new pose (e.g. the robot's current one)."""
    locations = load_locations(path) if path.exists() else []
    index = _find_index(locations, name)
    locations[index] = locations[index].model_copy(update={"pose": pose, "frame_id": frame_id})
    save_locations(locations, path)
    return locations[index]


def find_location(locations: list[Location], query: str, *, limit: int = 5) -> Location:
    normalized = query.strip().lower()
    # Tool-calling models sometimes preserve an English article when turning a
    # request such as "go to the dock" into the location argument. Prefer the
    # literal query first (a location may genuinely be named "The Lab"), then
    # try the article-free form. This is lookup-only: destructive operations
    # still require an exact saved name through ``_find_index``.
    lookup_forms = [normalized]
    article, separator, remainder = normalized.partition(" ")
    if separator and article in {"a", "an", "the"} and remainder.strip():
        lookup_forms.append(remainder.strip())

    for lookup in lookup_forms:
        if not lookup:
            continue
        for location in locations:
            if location.name.strip().lower() == lookup:
                return location
            if any(alias.strip().lower() == lookup for alias in location.aliases):
                return location

    fuzzy_query = lookup_forms[-1]
    raise LocationNotFoundError(query, _fuzzy_candidates(locations, fuzzy_query, limit=limit))


def find_dock(locations: list[Location]) -> Location | None:
    """Return the location registered as the site's Dock approach."""
    for location in locations:
        if any(tag.strip().lower() == _DOCK_TAG for tag in location.tags):
            return location
    for location in locations:
        names = (location.name, *location.aliases)
        if any(name.strip().lower() in _DOCK_NAMES for name in names):
            return location
    return None


def _fuzzy_candidates(
    locations: list[Location], normalized_query: str, *, limit: int
) -> list[Location]:
    if not normalized_query:
        return []

    by_key: dict[str, Location] = {}
    for location in locations:
        for candidate in (location.name, *location.aliases):
            key = candidate.strip().lower()
            if key:
                by_key.setdefault(key, location)

    matches = difflib.get_close_matches(normalized_query, list(by_key), n=limit, cutoff=0.6)

    seen_ids: set[str] = set()
    candidates: list[Location] = []
    for match in matches:
        location = by_key[match]
        if location.id not in seen_ids:
            seen_ids.add(location.id)
            candidates.append(location)
    return candidates


_TOML_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _quote(value: str) -> str:
    """Emit a TOML basic string, escaping control characters.

    TOML basic strings forbid literal control characters (newlines, tabs,
    etc.); without escaping them the written file would fail to reload via
    tomllib and silently drop every saved location.
    """
    out = []
    for ch in value:
        if ch in _TOML_ESCAPES:
            out.append(_TOML_ESCAPES[ch])
        elif ch < "\x20" or ch == "\x7f":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_quote(v) for v in values) + "]"


def _to_toml(locations: list[Location]) -> str:
    lines: list[str] = []
    for location in locations:
        lines.append("[[locations]]")
        lines.append(f"id = {_quote(location.id)}")
        lines.append(f"name = {_quote(location.name)}")
        lines.append(f"aliases = {_toml_array(location.aliases)}")
        lines.append(f"frame_id = {_quote(location.frame_id)}")
        lines.append(f"tags = {_toml_array(location.tags)}")
        if location.description:
            lines.append(f"description = {_quote(location.description)}")
        lines.append("")
        lines.append("[locations.pose]")
        lines.append(f"x = {location.pose.x}")
        lines.append(f"y = {location.pose.y}")
        lines.append(f"yaw = {location.pose.yaw}")
        lines.append("")
    return "\n".join(lines) + "\n"
