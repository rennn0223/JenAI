from __future__ import annotations

from pathlib import Path

import pytest

from jenai.adapters.locations import save_locations
from jenai.config.models import PatrolAreaProfile, SiteProfile
from jenai.config.store import build_minimal_config
from jenai.schemas import Location, Pose2D
from jenai.site_assets import fingerprint_locations_file
from jenai.site_profiles import (
    SiteProfileDocumentError,
    activate_site_profile,
    build_site_profile_draft,
    deactivate_site_profile,
    load_site_profile_document,
    write_site_profile_draft,
)


def _locations(path: Path) -> None:
    save_locations(
        [
            Location(name="Inspection A", pose=Pose2D(x=1.0, y=2.0, yaw=0.0)),
            Location(name="Home", pose=Pose2D(x=0.0, y=0.0, yaw=0.0)),
        ],
        path,
    )


def _imported_profile(**updates: object) -> SiteProfile:
    values: dict[str, object] = {
        "site_id": "warehouse",
        "display_name": "Warehouse",
        "version": "1",
        "active": True,
        "validated": True,
        "map_sha256": "a" * 64,
        "locations_sha256": "0" * 64,
        "locations_path": "locations.toml",
        "validated_routes": ["Inspection A", "Home"],
        "home_location": "Home",
        "patrol_areas": [
            PatrolAreaProfile(
                area_id="inspection",
                display_name="Inspection",
                inspection_locations=["Inspection A"],
            )
        ],
    }
    values.update(updates)
    return SiteProfile.model_validate(values)


def test_activation_recomputes_location_identity_and_validates_all_references(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    locations_path = tmp_path / "locations.toml"
    _locations(locations_path)
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="model",
        api_key_env="",
    )

    activated = activate_site_profile(
        config,
        config_path,
        _imported_profile(),
    )

    assert activated.site.active is True
    assert activated.site.validated is True
    assert activated.site.locations_sha256 == fingerprint_locations_file(locations_path)
    assert activated.site.locations_sha256 != "0" * 64
    assert activated.site.home_location == "Home"
    assert activated.site.patrol_areas[0].area_id == "inspection"


def test_activation_rejects_unknown_patrol_reference_without_mutating_base(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    _locations(tmp_path / "locations.toml")
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="model",
        api_key_env="",
    )
    imported = _imported_profile(
        patrol_areas=[
            PatrolAreaProfile(
                area_id="unsafe",
                display_name="Unsafe",
                inspection_locations=["Missing"],
            )
        ]
    )

    with pytest.raises(SiteProfileDocumentError, match="unknown location"):
        activate_site_profile(config, config_path, imported)

    assert config.site.active is False
    assert config.site.site_id == "unbound"


def test_profile_document_is_strict_and_deactivation_preserves_binding(
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "site.toml"
    profile_path.write_text(
        """
[site]
site_id = "warehouse"
display_name = "Warehouse"
version = "1"
map_sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
locations_path = "locations.toml"
validated_routes = ["Inspection A", "Home"]
home_location = "Home"

[[site.patrol_areas]]
area_id = "inspection"
display_name = "Inspection"
inspection_locations = ["Inspection A"]
""".strip(),
        encoding="utf-8",
    )

    profile = load_site_profile_document(profile_path)

    assert profile.site_id == "warehouse"
    assert profile.patrol_areas[0].inspection_locations == ["Inspection A"]

    invalid = tmp_path / "invalid.toml"
    invalid.write_text("[site]\nsite_id='x'\n[extra]\nvalue=1\n", encoding="utf-8")
    with pytest.raises(SiteProfileDocumentError, match="exactly one"):
        load_site_profile_document(invalid)

    _locations(tmp_path / "locations.toml")
    base = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="model",
        api_key_env="",
    )
    active = activate_site_profile(base, tmp_path / "config.toml", profile)
    inactive = deactivate_site_profile(active)

    assert inactive.site.active is False
    assert inactive.site.validated is True
    assert inactive.site.site_id == "warehouse"
    assert inactive.site.locations_sha256 == active.site.locations_sha256


def test_site_profile_draft_is_inactive_private_and_refuses_overwrite(
    tmp_path: Path,
) -> None:
    locations = [
        Location(
            name="Dock",
            tags=["dock"],
            pose=Pose2D(x=0.0, y=0.0, yaw=0.0),
        ),
        Location(
            name="Inspection A",
            pose=Pose2D(x=1.0, y=2.0, yaw=0.0),
        ),
    ]
    draft = build_site_profile_draft(
        site_id="warehouse",
        display_name="Warehouse",
        map_sha256="b" * 64,
        map_frame="map",
        locations_path="locations.toml",
        locations=locations,
        dock_location="Dock",
        reference_scene="Isaac Sim Warehouse",
    )

    assert draft.active is False
    assert draft.validated is False
    assert draft.home_location == "Dock"
    assert draft.validated_routes == ["Dock", "Inspection A"]
    assert [area.inspection_locations for area in draft.patrol_areas] == [["Inspection A"]]

    output = tmp_path / "site-profile.toml"
    write_site_profile_draft(draft, output)
    text = output.read_text(encoding="utf-8")

    assert output.stat().st_mode & 0o777 == 0o600
    assert "\nactive =" not in text
    assert "\nvalidated =" not in text
    assert "locations_sha256" not in text
    assert load_site_profile_document(output).map_sha256 == "b" * 64

    with pytest.raises(SiteProfileDocumentError, match="already exists"):
        write_site_profile_draft(draft, output)
