from __future__ import annotations

from jenai.capabilities import build_robot_capability_card
from jenai.config.models import VehicleProfile
from jenai.config.store import build_minimal_config


def test_reference_differential_ugv_has_registered_navigation_capabilities() -> None:
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="model",
        api_key_env="",
    )
    config.vehicle = VehicleProfile(type="diff", display_name="Isaac Carter")

    card = build_robot_capability_card(config)

    capability_ids = {capability.capability_id for capability in card.capabilities}
    assert {
        "navigate",
        "explore_known_locations",
        "patrol_photo",
        "dock_approach",
    } <= capability_ids
    assert not any("No platform-specific motion capability" in item for item in card.limitations)
