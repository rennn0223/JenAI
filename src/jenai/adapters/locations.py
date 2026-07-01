from __future__ import annotations

import difflib
import tomllib
from pathlib import Path

from pydantic import ValidationError

from jenai.schemas import Location

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


def save_locations(locations: list[Location], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_to_toml(locations), encoding="utf-8")
    return path


def ensure_locations_file(path: Path) -> Path:
    """Create an empty (starter-commented) locations file if one doesn't exist."""
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_STARTER_CONTENT, encoding="utf-8")
    return path


def find_location(locations: list[Location], query: str, *, limit: int = 5) -> Location:
    normalized = query.strip().lower()
    if normalized:
        for location in locations:
            if location.name.strip().lower() == normalized:
                return location
            if any(alias.strip().lower() == normalized for alias in location.aliases):
                return location

    raise LocationNotFoundError(query, _fuzzy_candidates(locations, normalized, limit=limit))


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


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


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
