from __future__ import annotations

import math

import pytest

from jenai.bridge._occupancy import sample_occupancy_cell


def test_occupancy_cell_samples_row_major_world_coordinate() -> None:
    result = sample_occupancy_cell(
        [0, 0, 0, 0, 100, 0],
        width=3,
        height=2,
        resolution=0.5,
        origin_x=-1.0,
        origin_y=-2.0,
        x=-0.25,
        y=-1.25,
    )

    assert result["in_bounds"] is True
    assert result["free"] is False
    assert result["value"] == 100
    assert (result["cell_x"], result["cell_y"]) == (1, 1)


def test_occupancy_cell_applies_inverse_rotated_origin_pose() -> None:
    result = sample_occupancy_cell(
        [0, 100],
        width=2,
        height=1,
        resolution=1.0,
        origin_x=0.0,
        origin_y=0.0,
        origin_yaw=math.pi / 2,
        # Local cell (1, 0) centre (1.5, 0.5) rotated +90° into world.
        x=-0.5,
        y=1.5,
    )

    assert result["in_bounds"] is True
    assert result["value"] == 100
    assert result["free"] is False
    assert (result["cell_x"], result["cell_y"]) == (1, 0)


@pytest.mark.parametrize("value", [-1, 1, 65, 100])
def test_occupancy_cell_accepts_only_explicitly_free_zero(value: int) -> None:
    result = sample_occupancy_cell(
        [value],
        width=1,
        height=1,
        resolution=1.0,
        origin_x=0.0,
        origin_y=0.0,
        x=0.5,
        y=0.5,
    )

    assert result["free"] is False


def test_occupancy_cell_fails_closed_outside_map() -> None:
    result = sample_occupancy_cell(
        [0],
        width=1,
        height=1,
        resolution=1.0,
        origin_x=0.0,
        origin_y=0.0,
        x=-0.1,
        y=0.5,
    )

    assert result["in_bounds"] is False
    assert result["free"] is False
    assert result["value"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"width": 0},
        {"data": []},
        {"resolution": 0.0},
        {"resolution": math.inf},
        {"x": math.nan},
        {"origin_yaw": math.nan},
    ],
)
def test_occupancy_cell_rejects_malformed_grid(overrides: dict) -> None:
    values = {
        "data": [0],
        "width": 1,
        "height": 1,
        "resolution": 1.0,
        "origin_x": 0.0,
        "origin_y": 0.0,
        "x": 0.5,
        "y": 0.5,
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        sample_occupancy_cell(**values)
