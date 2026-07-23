"""Dependency-free OccupancyGrid sampling for the ROS sidecar and tests."""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Sequence
from typing import Any


def occupancy_grid_identity(
    data: Sequence[int],
    *,
    width: int,
    height: int,
    resolution: float,
    origin_x: float,
    origin_y: float,
    origin_yaw: float,
    frame_id: str,
) -> str:
    """Return the canonical SHA-256 identity of one OccupancyGrid.

    The versioned binary encoding covers map geometry, frame and every signed
    occupancy value. The digest therefore changes when the map content or its
    coordinate system changes, while remaining independent of timestamps and
    transport details.
    """
    numeric = (resolution, origin_x, origin_y, origin_yaw)
    if width <= 0 or height <= 0:
        raise ValueError("OccupancyGrid dimensions must be positive")
    if len(data) != width * height:
        raise ValueError("OccupancyGrid data length does not match its dimensions")
    if not all(math.isfinite(value) for value in numeric) or resolution <= 0:
        raise ValueError("OccupancyGrid geometry must be finite")
    normalized_frame = frame_id.strip()
    if not normalized_frame:
        raise ValueError("OccupancyGrid frame_id must not be blank")
    if any(type(value) is not int or not -1 <= value <= 100 for value in data):
        raise ValueError("OccupancyGrid cells must be integers from -1 to 100")

    frame = normalized_frame.encode("utf-8")
    digest = hashlib.sha256()
    digest.update(b"jenai:occupancy-grid:v1\0")
    digest.update(struct.pack("!I", len(frame)))
    digest.update(frame)
    digest.update(struct.pack("!II4d", width, height, resolution, origin_x, origin_y, origin_yaw))
    digest.update(bytes(value & 0xFF for value in data))
    return digest.hexdigest()


def sample_occupancy_cell(
    data: Sequence[int],
    *,
    width: int,
    height: int,
    resolution: float,
    origin_x: float,
    origin_y: float,
    origin_yaw: float = 0.0,
    x: float,
    y: float,
) -> dict[str, Any]:
    """Return a bounded, fail-closed summary for one world-coordinate cell."""
    numeric = (resolution, origin_x, origin_y, origin_yaw, x, y)
    if width <= 0 or height <= 0:
        raise ValueError("OccupancyGrid dimensions must be positive")
    if len(data) != width * height:
        raise ValueError("OccupancyGrid data length does not match its dimensions")
    if not all(math.isfinite(value) for value in numeric) or resolution <= 0:
        raise ValueError("OccupancyGrid geometry and query coordinates must be finite")

    # OccupancyGrid origin is a full pose. Convert the world point through the
    # inverse origin rotation before sampling the row-major local grid.
    delta_x = x - origin_x
    delta_y = y - origin_y
    cos_yaw = math.cos(origin_yaw)
    sin_yaw = math.sin(origin_yaw)
    cell_x = math.floor((cos_yaw * delta_x + sin_yaw * delta_y) / resolution)
    cell_y = math.floor((-sin_yaw * delta_x + cos_yaw * delta_y) / resolution)
    in_bounds = 0 <= cell_x < width and 0 <= cell_y < height
    if not in_bounds:
        return {
            "in_bounds": False,
            "free": False,
            "value": None,
            "cell_x": cell_x,
            "cell_y": cell_y,
            "width": width,
            "height": height,
            "resolution": resolution,
            "origin_x": origin_x,
            "origin_y": origin_y,
            "origin_yaw": origin_yaw,
        }

    value = int(data[cell_y * width + cell_x])
    return {
        "in_bounds": True,
        # OccupancyGrid values are -1 unknown and 0..100 occupancy. For an
        # execution preflight, only explicitly free (0) is acceptable.
        "free": value == 0,
        "value": value,
        "cell_x": cell_x,
        "cell_y": cell_y,
        "width": width,
        "height": height,
        "resolution": resolution,
        "origin_x": origin_x,
        "origin_y": origin_y,
        "origin_yaw": origin_yaw,
    }
