"""Authoritative robot capability facts exposed to users and the Agent."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jenai.config.models import AppConfig
from jenai.schemas import TaskOutcome


class CapabilityMaturity(StrEnum):
    """How strongly JenAI may claim that a capability exists."""

    IMPLEMENTED_VALIDATED = "implemented_validated"
    IMPLEMENTED_UNVALIDATED = "implemented_unvalidated"
    INTERFACE_ONLY = "interface_only"
    CONCEPT = "concept"


class CapabilityContract(BaseModel):
    """One registered high-level behavior and its completion contract."""

    model_config = ConfigDict(extra="forbid")

    capability_id: str
    summary: str
    summary_zh: str
    interface_name: str
    supported_platforms: list[Literal["ackermann", "diff", "quadruped"]] = Field(
        default_factory=list
    )
    risk_level: Literal["p0", "p1", "p2"]
    requires_approval: bool
    maturity: CapabilityMaturity
    completion_evidence: list[str] = Field(default_factory=list)
    success_outcome: TaskOutcome = TaskOutcome.SUCCEEDED
    limitations: list[str] = Field(default_factory=list)
    limitations_zh: list[str] = Field(default_factory=list)


class RobotCapabilityCard(BaseModel):
    """Machine-readable identity and registered capability claims for one robot."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    robot_id: str
    display_name: str
    description: str
    platform_type: str
    deployment_mode: str
    capabilities: list[CapabilityContract] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


_CATALOG: dict[str, CapabilityContract] = {
    "inspect_state": CapabilityContract(
        capability_id="inspect_state",
        summary="Read current pose, LaserScan availability, and Nav2 readiness.",
        summary_zh="讀取目前姿態、LaserScan 可用性與 Nav2 就緒狀態。",
        interface_name="ros_state_tool",
        supported_platforms=["ackermann", "diff", "quadruped"],
        risk_level="p0",
        requires_approval=False,
        maturity=CapabilityMaturity.IMPLEMENTED_VALIDATED,
        completion_evidence=["live_pose", "laser_summary", "nav2_checks"],
    ),
    "emergency_stop": CapabilityContract(
        capability_id="emergency_stop",
        summary="Cancel active navigation and deliver a zero-velocity halt.",
        summary_zh="取消進行中的導航並送出零速度停止。",
        interface_name="bridge.halt",
        supported_platforms=["ackermann", "diff", "quadruped"],
        risk_level="p0",
        requires_approval=False,
        maturity=CapabilityMaturity.IMPLEMENTED_VALIDATED,
        completion_evidence=["halt_delivery", "nav_cancel_acknowledgement"],
    ),
    "bounded_drive": CapabilityContract(
        capability_id="bounded_drive",
        summary="Perform one approved, time-bounded motion and verify feedback.",
        summary_zh="執行一次經核准、時間有上限的移動並驗證回授。",
        interface_name="ros_drive_verified_tool",
        supported_platforms=["ackermann", "diff"],
        risk_level="p1",
        requires_approval=True,
        maturity=CapabilityMaturity.IMPLEMENTED_UNVALIDATED,
        completion_evidence=["baseline_odom", "automatic_stop", "post_action_odom"],
    ),
    "navigate": CapabilityContract(
        capability_id="navigate",
        summary="Navigate to a registered map location through Nav2.",
        summary_zh="透過 Nav2 導航至已登錄的地圖位置。",
        interface_name="route_execute_tool",
        supported_platforms=["ackermann", "diff"],
        risk_level="p1",
        requires_approval=True,
        maturity=CapabilityMaturity.IMPLEMENTED_UNVALIDATED,
        completion_evidence=["nav2_result", "terminal_pose"],
    ),
    "explore_known_locations": CapabilityContract(
        capability_id="explore_known_locations",
        summary="Visit bounded, registered locations with deterministic limits.",
        summary_zh="在次數與時間上限內巡訪已登錄地點。",
        interface_name="explore_area_tool",
        supported_platforms=["ackermann", "diff"],
        risk_level="p1",
        requires_approval=True,
        maturity=CapabilityMaturity.IMPLEMENTED_UNVALIDATED,
        completion_evidence=["per_goal_result", "stop_reason"],
        limitations=["This is known-location exploration, not frontier SLAM."],
        limitations_zh=["這是已知地點探索，不是 frontier SLAM。"],
    ),
    "patrol_photo": CapabilityContract(
        capability_id="patrol_photo",
        summary="Visit registered patrol points and preserve image observations.",
        summary_zh="巡訪已登錄巡邏點並保存影像觀測。",
        interface_name="patrol",
        supported_platforms=["ackermann", "diff"],
        risk_level="p1",
        requires_approval=True,
        maturity=CapabilityMaturity.IMPLEMENTED_UNVALIDATED,
        completion_evidence=["per_goal_result", "captured_observation", "patrol_report"],
    ),
    "dock_approach": CapabilityContract(
        capability_id="dock_approach",
        summary="Navigate to the registered Dock approach pose.",
        summary_zh="導航至已登錄的 Dock 接近姿態。",
        interface_name="dock",
        supported_platforms=["ackermann", "diff"],
        risk_level="p1",
        requires_approval=True,
        maturity=CapabilityMaturity.IMPLEMENTED_UNVALIDATED,
        completion_evidence=["nav2_result", "terminal_pose"],
        success_outcome=TaskOutcome.ARRIVED_UNVERIFIED,
        limitations=["Charging engagement and charging state are not verified."],
        limitations_zh=["尚未驗證實際接合充電座與充電狀態。"],
    ),
}


def _registered_capability_ids(config: AppConfig) -> tuple[str, ...]:
    configured = config.vehicle.capabilities
    if configured is not None:
        return tuple(dict.fromkeys(configured))
    return tuple(
        capability_id
        for capability_id, contract in _CATALOG.items()
        if config.vehicle.type in contract.supported_platforms
    )


def build_robot_capability_card(config: AppConfig) -> RobotCapabilityCard:
    """Build the single capability claim used by the UI, Agent, and reports."""

    capability_ids = _registered_capability_ids(config)
    unknown = sorted(set(capability_ids) - _CATALOG.keys())
    if unknown:
        raise ValueError(f"Unknown robot capabilities: {', '.join(unknown)}")

    limitations = list(config.vehicle.limitations)
    if "dock_approach" in capability_ids:
        limitations.append("Dock is an approach-only capability; charging feedback is unavailable.")
    if config.vehicle.type not in {"ackermann", "diff"} and config.vehicle.capabilities is None:
        limitations.append(
            "No platform-specific motion capability is registered for this vehicle type."
        )

    return RobotCapabilityCard(
        robot_id=config.vehicle.robot_id,
        display_name=config.vehicle.display_name,
        description=config.vehicle.description,
        platform_type=config.vehicle.type,
        deployment_mode=config.deployment_mode,
        capabilities=[_CATALOG[capability_id] for capability_id in capability_ids],
        limitations=list(dict.fromkeys(limitations)),
    )


def capability_prompt(config: AppConfig) -> str:
    """Render concise authoritative facts for LLM reasoning, without live-state claims."""

    card = build_robot_capability_card(config)
    capabilities = "\n".join(
        "- "
        f"{item.capability_id}: {item.summary} "
        f"[maturity={item.maturity}; success={item.success_outcome}; "
        f"evidence={','.join(item.completion_evidence) or 'none'}]"
        for item in card.capabilities
    )
    limitations = "\n".join(f"- {item}" for item in card.limitations) or "- None registered."
    return (
        "ROBOT CAPABILITY CARD (authoritative configured facts)\n"
        f"Identity: {card.display_name} ({card.robot_id})\n"
        f"Platform: {card.platform_type}; deployment={card.deployment_mode}\n"
        f"Description: {card.description}\n"
        "Registered capabilities:\n"
        f"{capabilities}\n"
        "Known limitations:\n"
        f"{limitations}\n"
        "Reason freely about the user's goal, but use live tools for current state. "
        "Do not invent unregistered capabilities, coordinates, observations, or success. "
        "State inferences as inferences and missing evidence as unverified."
    )
