from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from jenai.config.models import AppConfig, SiteProfile


def _active_site(**updates) -> SiteProfile:
    values = {
        "site_id": "warehouse",
        "display_name": "Warehouse",
        "active": True,
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


def test_site_asset_references_are_versioned_and_normalized() -> None:
    site = _active_site(
        locations_path=" locations.toml ",
        validated_routes=[" map_left_down ", "map_left_down", "dock"],
        dock_location=" dock ",
        validation_evidence=[" artifacts/hil.json "],
    )

    assert site.locations_path == "locations.toml"
    assert site.validated_routes == ["map_left_down", "dock"]
    assert site.dock_location == "dock"
    assert site.validation_evidence == ["artifacts/hil.json"]
