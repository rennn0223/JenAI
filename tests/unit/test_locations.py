from __future__ import annotations

import pytest

from jenai.adapters.locations import (
    LocationNotFoundError,
    LocationsFileError,
    ensure_locations_file,
    find_location,
    load_locations,
    save_locations,
)
from jenai.schemas import Location, Pose2D


def _sample_locations() -> list[Location]:
    return [
        Location(
            name="Engineering Building",
            aliases=["engineering", "eng building"],
            frame_id="map",
            pose=Pose2D(x=1.0, y=2.0, yaw=0.0),
            tags=["building"],
            description="Main engineering building",
        ),
        Location(
            name="Mechanical Hall",
            aliases=["mech hall"],
            frame_id="map",
            pose=Pose2D(x=5.0, y=-3.0, yaw=1.57),
            tags=["building"],
        ),
    ]


def test_save_and_load_round_trip(tmp_path) -> None:
    path = tmp_path / "locations.toml"
    original = _sample_locations()
    save_locations(original, path)

    loaded = load_locations(path)

    assert [loc.name for loc in loaded] == [loc.name for loc in original]
    assert loaded[0].aliases == ["engineering", "eng building"]
    assert loaded[0].pose.x == 1.0
    assert loaded[0].description == "Main engineering building"
    assert loaded[1].description is None


def test_load_missing_file_raises(tmp_path) -> None:
    with pytest.raises(LocationsFileError):
        load_locations(tmp_path / "does-not-exist.toml")


def test_load_malformed_toml_raises(tmp_path) -> None:
    path = tmp_path / "locations.toml"
    path.write_text("not = [valid toml", encoding="utf-8")
    with pytest.raises(LocationsFileError):
        load_locations(path)


def test_load_invalid_entry_raises(tmp_path) -> None:
    path = tmp_path / "locations.toml"
    path.write_text('[[locations]]\nname = ""\n', encoding="utf-8")
    with pytest.raises(LocationsFileError):
        load_locations(path)


def test_ensure_locations_file_creates_starter(tmp_path) -> None:
    path = tmp_path / "locations.toml"
    assert not path.exists()

    ensure_locations_file(path)

    assert path.exists()
    assert load_locations(path) == []


def test_ensure_locations_file_is_noop_if_present(tmp_path) -> None:
    path = tmp_path / "locations.toml"
    save_locations(_sample_locations(), path)

    ensure_locations_file(path)

    assert len(load_locations(path)) == 2


def test_find_location_exact_name_match() -> None:
    locations = _sample_locations()
    found = find_location(locations, "Engineering Building")
    assert found.name == "Engineering Building"


def test_find_location_alias_match_case_insensitive() -> None:
    locations = _sample_locations()
    found = find_location(locations, "MECH HALL")
    assert found.name == "Mechanical Hall"


def test_find_location_fuzzy_suggests_candidates_on_miss() -> None:
    locations = _sample_locations()
    with pytest.raises(LocationNotFoundError) as exc_info:
        find_location(locations, "enginering buildng")

    assert exc_info.value.candidates
    assert exc_info.value.candidates[0].name == "Engineering Building"


def test_find_location_no_match_returns_empty_candidates() -> None:
    locations = _sample_locations()
    with pytest.raises(LocationNotFoundError) as exc_info:
        find_location(locations, "completely unrelated place")

    assert exc_info.value.candidates == []
