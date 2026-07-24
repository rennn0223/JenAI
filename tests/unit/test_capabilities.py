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


def test_unregistered_quadruped_agent_has_no_motion_or_navigation_tools() -> None:
    config = _config()
    config.vehicle = VehicleProfile(type="quadruped", display_name="Nexuni prototype")

    supervisor = build_supervisor_agent(config)
    tool_names = {tool.name for tool in supervisor.tools}
    handoff_names = {handoff.name for handoff in supervisor.handoffs}

    assert "route_execute_tool" not in tool_names
    assert "explore_area_tool" not in tool_names
    assert "Motion" not in handoff_names
    assert "Navigation" not in handoff_names


def test_explicit_navigation_only_capability_does_not_expose_explore() -> None:
    config = _config()
    config.vehicle.capabilities = ["inspect_state", "navigate"]

    supervisor = build_supervisor_agent(config)
    navigation = next(handoff for handoff in supervisor.handoffs if handoff.name == "Navigation")
    supervisor_tools = {tool.name for tool in supervisor.tools}
    navigation_tools = {tool.name for tool in navigation.tools}

    assert "route_execute_tool" in supervisor_tools
    assert "route_execute_tool" in navigation_tools
    assert "explore_area_tool" not in supervisor_tools
    assert "explore_area_tool" not in navigation_tools


def test_registered_patrol_photo_capability_exposes_agent_tool() -> None:
    config = _config()

    supervisor = build_supervisor_agent(config)
    navigation = next(handoff for handoff in supervisor.handoffs if handoff.name == "Navigation")
    supervisor_tools = {tool.name for tool in supervisor.tools}
    navigation_tools = {tool.name for tool in navigation.tools}

    assert "patrol_area_tool" in supervisor_tools
    assert "patrol_area_tool" in navigation_tools


def test_registered_area_patrol_capability_exposes_workflow_tool() -> None:
    config = _config()

    supervisor = build_supervisor_agent(config)
    navigation = next(handoff for handoff in supervisor.handoffs if handoff.name == "Navigation")
    supervisor_tools = {tool.name for tool in supervisor.tools}
    navigation_tools = {tool.name for tool in navigation.tools}

    assert "area_patrol_workflow_tool" in supervisor_tools
    assert "area_patrol_workflow_tool" in navigation_tools
    assert "area_patrol" in {
        item.capability_id for item in build_robot_capability_card(config).capabilities
    }
