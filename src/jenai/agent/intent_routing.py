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
_NEGATED_PATROL_TERMS = (
    " do not inspect ",
    " do not patrol ",
    " don't inspect ",
    " don't patrol ",
    " not inspect ",
    " not patrol ",
    "不要巡",
    "不要檢",
    "別巡",
    "別檢",
)
_INTERROGATIVE_PREFIXES = (
    "can ",
    "could ",
    "would ",
    "will ",
    "should ",
    "是否",
    "能否",
    "能不能",
    "可不可以",
)


def _contains_signal(normalized: str, term: str) -> bool:
    """Match English signals as words and CJK signals as bounded substrings."""

    return term in normalized if not term.isascii() else f" {term} " in normalized


def route_run_request(text: str) -> RunAgentRoute:
    """Select a narrow workflow Agent only for an explicit coverage mission.

    The three independent signals prevent a generic request such as "inspect
    pose" or "go to the lab" from being mistaken for a semantic-area patrol.
    Explicit ordered-waypoint language remains on the general route because it
    belongs to the waypoint patrol capability, not the coverage workflow.
    """

    normalized = f" {' '.join(text.casefold().split())} "
    stripped = normalized.strip()
    if (
        stripped.endswith(("?", "？", "嗎", "吗"))
        or stripped.startswith(_INTERROGATIVE_PREFIXES)
        or any(term in normalized for term in _NEGATED_PATROL_TERMS)
    ):
        return RunAgentRoute.GENERAL
    if any(term in normalized for term in _ORDERED_WAYPOINT_TERMS):
        return RunAgentRoute.GENERAL
    if (
        any(_contains_signal(normalized, term) for term in _PATROL_TERMS)
        and any(_contains_signal(normalized, term) for term in _COVERAGE_TERMS)
        and any(_contains_signal(normalized, term) for term in _SITE_TERMS)
    ):
        return RunAgentRoute.AREA_PATROL
    return RunAgentRoute.GENERAL
