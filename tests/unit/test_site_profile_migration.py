from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jenai.adapters.locations import save_locations
from jenai.bridge import RosBridgeClient
from jenai.config.models import AppConfig, SiteProfile
from jenai.config.store import build_minimal_config
from jenai.schemas import Location, Pose2D
from jenai.site_assets import fingerprint_locations_file
from jenai.site_profiles import (
    SiteProfileDocumentError,
    revalidate_active_site_profile,
)
from jenai.tools.navigation_gateway import NavigationGateway


def _legacy_config(tmp_path: Path) -> tuple[AppConfig, Path]:
    config_path = tmp_path / "config.toml"
    locations_path = tmp_path / "locations.toml"
    save_locations(
        [Location(name="Dock", pose=Pose2D(x=0.0, y=0.0, yaw=0.0))],
        locations_path,
    )
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="model",
        api_key_env="",
    )
    config.site = SiteProfile(
        site_id="legacy",
        display_name="Legacy site",
        version="1",
        active=True,
        validated=True,
        map_sha256="a" * 64,
        locations_path="locations.toml",
        validated_routes=["Dock"],
        home_location="Dock",
        dock_location="Dock",
    )
    return config, config_path


def test_legacy_profile_is_loadable_but_not_execution_ready(tmp_path: Path) -> None:
    config, _ = _legacy_config(tmp_path)

    loaded = AppConfig.model_validate(config.model_dump(mode="python"))

    assert loaded.site.active is True
    assert loaded.site.execution_ready is False


def test_navigation_fails_closed_before_opening_bridge_for_legacy_profile(tmp_path: Path) -> None:
    config, config_path = _legacy_config(tmp_path)

    async def bridge_must_not_start() -> RosBridgeClient:
        raise AssertionError("untrusted Site Profile must be blocked before ROS")

    result = asyncio.run(
        NavigationGateway(
            config,
            config_path=config_path,
            get_bridge=bridge_must_not_start,
        ).execute({"capability_id": "navigate", "goal": {}})
    )

    assert result.execution_status == "blocked"
    assert "not execution-ready" in result.route_preview


def test_revalidation_requires_same_live_map_and_binds_current_locations(
    tmp_path: Path,
) -> None:
    config, config_path = _legacy_config(tmp_path)

    repaired = revalidate_active_site_profile(
        config,
        config_path,
        observed_map_sha256="a" * 64,
        observed_map_frame="map",
    )

    assert repaired.site.execution_ready is True
    assert repaired.site.locations_sha256 == fingerprint_locations_file(tmp_path / "locations.toml")
    assert config.site.locations_sha256 is None


def test_revalidation_rejects_different_live_map_without_mutating_profile(
    tmp_path: Path,
) -> None:
    config, config_path = _legacy_config(tmp_path)

    with pytest.raises(SiteProfileDocumentError, match="does not match"):
        revalidate_active_site_profile(
            config,
            config_path,
            observed_map_sha256="b" * 64,
            observed_map_frame="map",
        )

    assert config.site.execution_ready is False
    assert config.site.locations_sha256 is None
