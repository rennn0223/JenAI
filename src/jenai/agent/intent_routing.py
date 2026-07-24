"""Conservative pre-routing for high-confidence robot workflow requests.

This module never executes a capability and never parses arbitrary mission
details. It only narrows the Agent's candidate tool set when the user has
unambiguously requested one existing, high-level workflow. Ambiguous text
continues through the general LLM supervisor.
"""

from __future__ import annotations

from enum import StrEnum


class RunAgentRoute(StrEnum):
    """The smallest Agent graph that may safely interpret a request."""

    GENERAL = "general"
    AREA_PATROL = "area_patrol"


_PATROL_TERMS = (
    "patrol",
    "inspect",
    "inspection",
    "survey",
    "巡邏",
    "巡檢",
    "巡查",
    "檢查",
)
_COVERAGE_TERMS = (
    "all",
    "whole",
    "entire",
    "every",
    "required",
    "全部",
    "所有",
    "每個",
    "必巡",
    "整個",
    "完整",
)
_SITE_TERMS = (
    "site profile",
    "configured site",
    "semantic area",
    "patrol area",
    "laboratory",
    "lab",
    "場域",
    "區域",
    "實驗室",
)
_ORDERED_WAYPOINT_TERMS = (
    " followed by ",
    " then ",
    " in this order",
    "依序",
    "照順序",
)


def route_run_request(text: str) -> RunAgentRoute:
    """Select a narrow workflow Agent only for an explicit coverage mission.

    The three independent signals prevent a generic request such as "inspect
    pose" or "go to the lab" from being mistaken for a semantic-area patrol.
    Explicit ordered-waypoint language remains on the general route because it
    belongs to the waypoint patrol capability, not the coverage workflow.
    """

    normalized = f" {' '.join(text.casefold().split())} "
    if any(term in normalized for term in _ORDERED_WAYPOINT_TERMS):
        return RunAgentRoute.GENERAL
    if (
        any(term in normalized for term in _PATROL_TERMS)
        and any(term in normalized for term in _COVERAGE_TERMS)
        and any(term in normalized for term in _SITE_TERMS)
    ):
        return RunAgentRoute.AREA_PATROL
    return RunAgentRoute.GENERAL
