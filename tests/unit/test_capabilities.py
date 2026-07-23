from __future__ import annotations

from jenai.agent.specialists import build_supervisor_agent
from jenai.capabilities import (
    CapabilityMaturity,
    build_robot_capability_card,
    capability_prompt,
)
from jenai.config.models import VehicleProfile
from jenai.config.store import build_minimal_config


def _config():
    return build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="model",
        api_key_env="",
    )


def test_reference_ackermann_card_exposes_registered_contracts_and_limits() -> None:
    config = _config()

    card = build_robot_capability_card(config)

    by_id = {capability.capability_id: capability for capability in card.capabilities}
    assert all(item.summary_zh.strip() for item in card.capabilities)
    assert all(config.vehicle.type in item.supported_platforms for item in card.capabilities)
    assert all(len(item.limitations) == len(item.limitations_zh) for item in card.capabilities)
    assert by_id["navigate"].maturity == CapabilityMaturity.IMPLEMENTED_UNVALIDATED
    assert by_id["navigate"].completion_evidence == ["nav2_result", "terminal_pose"]
    assert by_id["dock_approach"].success_outcome == "arrived_unverified"
    assert "charging feedback is unavailable" in " ".join(card.limitations).lower()


def test_unregistered_quadruped_does_not_inherit_ackermann_motion_claims() -> None:
    config = _config()
    config.vehicle = VehicleProfile(type="quadruped", display_name="Nexuni prototype")

    card = build_robot_capability_card(config)

    capability_ids = {capability.capability_id for capability in card.capabilities}
    assert capability_ids == {"inspect_state", "emergency_stop"}
    assert "navigate" not in capability_ids


def test_capability_prompt_is_authoritative_context_for_the_llm() -> None:
    config = _config()
    config.vehicle.display_name = "JenAI Warehouse UGV"

    prompt = capability_prompt(config)
    supervisor = build_supervisor_agent(config)

    assert "JenAI Warehouse UGV" in prompt
    assert "Do not invent unregistered capabilities" in prompt
    assert "dock_approach" in prompt
    assert isinstance(supervisor.instructions, str)
    assert prompt in supervisor.instructions
