from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from jenai.adapters.locations import load_locations_snapshot, save_locations
from jenai.config.models import AppConfig, PatrolAreaProfile, SiteProfile
from jenai.schemas import Location, Pose2D
from jenai.site_assets import (
    SiteAssetError,
    bind_navigation_action,
    fingerprint_locations_file,
    load_site_patrol_areas,
    validate_site_assets,
)


def _active_site(**updates) -> SiteProfile:
    values = {
        "site_id": "warehouse",
        "display_name": "Warehouse",
        "active": True,
        "locations_sha256": "b" * 64,
        "validated": True,
        "map_sha256": "a" * 64,
    }
    values.update(updates)
    return SiteProfile(**values)


def test_active_site_migrates_legacy_locations_path_into_profile() -> None:
    config = AppConfig(
        locations_path="locations.toml",
        site=_active_site(),
    )

    assert config.site.locations_path == "locations.toml"
    assert config.resolved_locations_path(Path("/tmp/config.toml")) == Path("/tmp/locations.toml")


def test_active_site_rejects_conflicting_or_missing_location_binding() -> None:
    with pytest.raises(ValidationError, match="conflicts"):
        AppConfig(
            locations_path="other.toml",
            site=_active_site(locations_path="site-locations.toml"),
        )

    with pytest.raises(ValidationError, match="must bind a locations_path"):
        AppConfig(site=_active_site())


def test_active_site_without_locations_content_identity_is_not_execution_ready() -> None:
    site = SiteProfile(
        site_id="warehouse",
        display_name="Warehouse",
        active=True,
        validated=True,
        map_sha256="a" * 64,
        locations_path="locations.toml",
    )

    assert site.active is True
    assert site.execution_ready is False


def test_site_asset_references_are_versioned_and_normalized() -> None:
    site = _active_site(
        locations_path=" locations.toml ",
        validated_routes=[" map_left_down ", "map_left_down", "dock"],
        default_patrol=[" map_left_down ", "map_left_down"],
        dock_location=" dock ",
        validation_evidence=[" artifacts/hil.json "],
    )

    assert site.locations_path == "locations.toml"
    assert site.validated_routes == ["map_left_down", "dock"]
    assert site.default_patrol == ["map_left_down"]
    assert site.dock_location == "dock"
    assert site.validation_evidence == ["artifacts/hil.json"]


def test_site_assets_reject_unknown_or_unvalidated_default_patrol(tmp_path: Path) -> None:
    locations_path = tmp_path / "locations.toml"
    locations = [
        Location(name="A", pose=Pose2D(x=1.0, y=0.0, yaw=0.0)),
        Location(name="Dock", pose=Pose2D(x=0.0, y=0.0, yaw=0.0)),
    ]
    save_locations(locations, locations_path)
    digest = fingerprint_locations_file(locations_path)

    for default_patrol, expected in ((["Unknown"], "unknown"), (["A"], "validated_routes")):
        config = AppConfig(
            locations_path="locations.toml",
            site=_active_site(
                locations_path="locations.toml",
                locations_sha256=digest,
                validated_routes=["Dock"],
                default_patrol=default_patrol,
                home_location="Dock",
                dock_location="Dock",
            ),
        )
        with pytest.raises(SiteAssetError, match=expected):
            validate_site_assets(config, tmp_path / "config.toml")


def test_locations_fingerprint_is_content_bound(tmp_path: Path) -> None:
    path = tmp_path / "locations.toml"
    path.write_text("[[locations]]\nname='Dock'\n", encoding="utf-8")
    first = fingerprint_locations_file(path)

    path.write_text("[[locations]]\nname='Warehouse'\n", encoding="utf-8")

    assert fingerprint_locations_file(path) != first
    assert len(first) == 64


def test_navigation_action_is_bound_to_validated_location_content(tmp_path: Path) -> None:
    locations_path = tmp_path / "locations.toml"
    dock = Location(
        name="Dock",
        frame_id="map",
        pose=Pose2D(x=1.0, y=2.0, yaw=0.0),
        tags=["dock"],
    )
    save_locations([dock], locations_path)
    digest = fingerprint_locations_file(locations_path)
    config = AppConfig(
        locations_path="locations.toml",
        site=_active_site(
            locations_path="locations.toml",
            locations_sha256=digest,
            validated_routes=["Dock"],
            dock_location="Dock",
        ),
    )
    bound = bind_navigation_action(
        config, tmp_path / "config.toml", {"goal": dock.model_dump(mode="json")}
    )
    assert bound["capability_id"] == "dock_approach"

    tampered = {"goal": dock.model_dump(mode="json")}
    tampered["goal"]["pose"]["x"] = 9.0
    with pytest.raises(SiteAssetError, match="does not match"):
        bind_navigation_action(config, tmp_path / "config.toml", tampered)


def test_navigation_binding_can_reuse_the_reviewed_locations_snapshot(tmp_path: Path) -> None:
    locations_path = tmp_path / "locations.toml"
    reviewed = Location(
        name="Reviewed Goal",
        frame_id="map",
        pose=Pose2D(x=1.0, y=2.0, yaw=0.25),
    )
    save_locations([reviewed], locations_path)
    snapshot = load_locations_snapshot(locations_path)
    config = AppConfig(
        locations_path="locations.toml",
        site=_active_site(
            locations_path="locations.toml",
            locations_sha256=snapshot.sha256,
            validated_routes=[reviewed.name],
        ),
    )
    save_locations(
        [reviewed.model_copy(update={"pose": Pose2D(x=9.0, y=9.0, yaw=0.25)})],
        locations_path,
    )

    bound = bind_navigation_action(
        config,
        tmp_path / "config.toml",
        {"capability_id": "navigate", "goal": reviewed.model_dump(mode="json")},
        locations_snapshot=snapshot,
    )

    assert bound["goal"] == reviewed.model_dump(mode="json")
    with pytest.raises(SiteAssetError, match="Locations identity mismatch"):
        bind_navigation_action(
            config,
            tmp_path / "config.toml",
            {"capability_id": "navigate", "goal": reviewed.model_dump(mode="json")},
        )


def test_navigation_binding_rejects_snapshot_from_another_locations_path(
    tmp_path: Path,
) -> None:
    expected_path = tmp_path / "locations.toml"
    other_path = tmp_path / "other.toml"
    goal = Location(name="Goal", pose=Pose2D(x=1.0, y=2.0, yaw=0.0))
    save_locations([goal], expected_path)
    save_locations([goal], other_path)
    snapshot = load_locations_snapshot(other_path)
    config = AppConfig(
        locations_path="locations.toml",
        site=_active_site(
            locations_path="locations.toml",
            locations_sha256=snapshot.sha256,
            validated_routes=[goal.name],
        ),
    )

    with pytest.raises(SiteAssetError, match="different locations path"):
        bind_navigation_action(
            config,
            tmp_path / "config.toml",
            {"capability_id": "navigate", "goal": goal.model_dump(mode="json")},
            locations_snapshot=snapshot,
        )


def test_patrol_area_profiles_are_typed_and_unique() -> None:
    area = PatrolAreaProfile(
        area_id=" equipment ",
        display_name=" Equipment Zone ",
        inspection_locations=[" Inspection A ", "Inspection A", "Inspection B"],
        optional_inspection_locations=[" Optional A ", "Optional A"],
    )

    assert area.area_id == "equipment"
    assert area.display_name == "Equipment Zone"
    assert area.inspection_locations == ["Inspection A", "Inspection B"]
    assert area.optional_inspection_locations == ["Optional A"]

    with pytest.raises(ValidationError, match="both required and optional"):
        PatrolAreaProfile(
            area_id="conflict",
            display_name="Conflict",
            inspection_locations=["Inspection A"],
            optional_inspection_locations=["Inspection A"],
        )

    with pytest.raises(ValidationError, match="duplicate patrol area"):
        SiteProfile(
            patrol_areas=[
                area,
                PatrolAreaProfile(
                    area_id="EQUIPMENT",
                    display_name="Duplicate",
                    inspection_locations=["Inspection C"],
                ),
            ]
        )


def test_site_patrol_areas_resolve_only_validated_location_assets(tmp_path: Path) -> None:
    locations_path = tmp_path / "locations.toml"
    inspection = Location(
        name="Inspection A",
        frame_id="map",
        pose=Pose2D(x=1.0, y=2.0, yaw=0.0),
    )
    optional = Location(
        name="Optional View",
        frame_id="map",
        pose=Pose2D(x=1.5, y=2.5, yaw=0.0),
    )
    home = Location(
        name="Home",
        frame_id="map",
        pose=Pose2D(x=0.0, y=0.0, yaw=0.0),
    )
    save_locations([inspection, optional, home], locations_path)
    config = AppConfig(
        locations_path="locations.toml",
        site=_active_site(
            locations_path="locations.toml",
            locations_sha256=fingerprint_locations_file(locations_path),
            validated_routes=["Inspection A", "Optional View", "Home"],
            home_location="Home",
            patrol_areas=[
                PatrolAreaProfile(
                    area_id="equipment",
                    display_name="Equipment Zone",
                    inspection_locations=["Inspection A"],
                    optional_inspection_locations=["Optional View"],
                )
            ],
        ),
    )

    areas = load_site_patrol_areas(config, tmp_path / "config.toml", "equipment")

    assert len(areas) == 1
    assert areas[0].area_id == "equipment"
    assert areas[0].inspection_points[0].location == "Inspection A"
    assert areas[0].inspection_points[0].required is True
    assert areas[0].inspection_points[1].location == "Optional View"
    assert areas[0].inspection_points[1].required is False

    config.site.patrol_areas[0].inspection_locations = ["Missing"]
    with pytest.raises(SiteAssetError, match="unknown location"):
        load_site_patrol_areas(config, tmp_path / "config.toml", "equipment")
