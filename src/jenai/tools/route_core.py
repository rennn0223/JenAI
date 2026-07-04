"""Route parsing ('from A to B') + execution through the route adapter."""

from __future__ import annotations

import asyncio
import re

from jenai.adapters.locations import LocationNotFoundError, find_location
from jenai.adapters.route_adapter import get_route_adapter
from jenai.config.models import AppConfig
from jenai.providers.chat import ask_json
from jenai.schemas import Location, RouteOutput

# English `to`/`from` require word boundaries so they don't match inside words
# (e.g. the "to" in "photo"); Chinese 從/到 have no such boundaries and match as-is.
_SPLIT_PATTERN = re.compile(
    r"(?:從|\bfrom\b)\s*(.+?)\s*(?:到|\bto\b)\s*(.+)", re.IGNORECASE
)


def _extract_via_regex(text: str) -> tuple[str, str] | None:
    match = _SPLIT_PATTERN.search(text)
    if not match:
        return None
    start, goal = match.group(1).strip(), match.group(2).strip()
    if not start or not goal:
        return None
    return start, goal


async def _extract_via_llm(config: AppConfig, text: str) -> tuple[str, str] | None:
    prompt = (
        "Extract the start and goal location names from this navigation request. "
        'Respond with ONLY JSON: {"start": "...", "goal": "..."}. '
        "If either is missing, use an empty string for that field.\n\n"
        f"Request: {text}"
    )
    parsed = await ask_json(config, prompt, binding="route")
    if not isinstance(parsed, dict):
        return None
    start, goal = str(parsed.get("start", "")).strip(), str(parsed.get("goal", "")).strip()
    if not start or not goal:
        return None
    return start, goal


async def route_preview(config: AppConfig, locations: list[Location], text: str) -> RouteOutput:
    extraction = _extract_via_regex(text) or await _extract_via_llm(config, text)
    if extraction is None:
        return RouteOutput(
            input_text=text,
            route_preview=(
                "Could not determine a start and goal location. "
                "Please specify both, e.g. 'from X to Y'."
            ),
        )

    start_query, goal_query = extraction
    resolved_start: Location | None = None
    resolved_goal: Location | None = None
    candidate_matches: list[Location] = []

    # Only the GOAL is required. Nav2 navigates from the robot's *current* pose,
    # so the adapter uses the goal alone — a start we can't resolve is therefore
    # not fatal (we simply omit it) and we never imply travel from a place we do
    # not actually send.
    try:
        resolved_start = find_location(locations, start_query)
    except LocationNotFoundError:
        resolved_start = None

    try:
        resolved_goal = find_location(locations, goal_query)
    except LocationNotFoundError as exc:
        candidate_matches.extend(exc.candidates)
        hint = (
            "Did you mean: " + ", ".join(loc.name for loc in candidate_matches) + "?"
            if candidate_matches
            else "No close matches found."
        )
        return RouteOutput(
            input_text=text,
            resolved_start=resolved_start,
            candidate_matches=candidate_matches,
            route_preview=f"Could not resolve goal '{goal_query}'. {hint}",
        )

    outgoing_action: dict = {"goal": resolved_goal.model_dump(mode="json")}
    if resolved_start is not None:
        outgoing_action["start"] = resolved_start.model_dump(mode="json")
    return RouteOutput(
        input_text=text,
        resolved_start=resolved_start,
        resolved_goal=resolved_goal,
        route_preview=f"Navigate to {resolved_goal.name} (from the robot's current position).",
        outgoing_action=outgoing_action,
    )


async def route_execute(config: AppConfig, outgoing_action: dict) -> RouteOutput:
    adapter = get_route_adapter(config.route_adapter)
    # resolve() may block (e.g. the Nav2 adapter waits on `ros2 action send_goal`),
    # so run it off the event loop to keep the TUI responsive.
    result = await asyncio.to_thread(adapter.resolve, outgoing_action)
    return RouteOutput(
        input_text="",
        outgoing_action=outgoing_action,
        approval_status="approved",
        execution_status=result.execution_status,
        route_preview=result.detail,
    )
