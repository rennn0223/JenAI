"""Registry assembling the agent's tool set."""

from __future__ import annotations

from dataclasses import dataclass

from jenai.schemas import EffectScope, RiskLevel


@dataclass(frozen=True)
class ToolRiskInfo:
    risk_level: RiskLevel
    effect_scope: EffectScope
    needs_approval: bool
    description: str


TOOL_RISK_REGISTRY: dict[str, ToolRiskInfo] = {}


def register_tool(name: str, info: ToolRiskInfo) -> None:
    TOOL_RISK_REGISTRY[name] = info
