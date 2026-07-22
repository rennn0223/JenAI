"""Route parsing ('from A to B') + execution through the route adapter."""

from __future__ import annotations

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
# Destination-only phrasings ("去X", "go to X"). Tried only after the from-to
# pattern fails, so 到/to acting as the from-to separator is never shadowed.
_GOAL_ONLY_PATTERN = re.compile(
    r"(?:去|前往|到|\bgo to\b|\bnavigate to\b|\btake me to\b|\bto\b)\s*(.+)", re.IGNORECASE
)

# The TUI may bypass the LLM only for a narrow, explicit one-destination
# command.  Negated, interrogative and multi-stop wording deliberately falls
# back to the agent: a false negative costs latency; a false positive could
# move a robot contrary to the user's intent.
_EXPLICIT_ROUTE_VERB = (
    r"(?:前往|導航到|移動到|開到|回到|去到|去|"
    r"go\s+to|navigate\s+to|drive\s+to|move\s+to|return(?:\s+back)?\s+to)"
)
_ROUTE_NEGATION = re.compile(
    r"(?:不要|別|不准|禁止|避免|do\s+not|don't|dont|must\s+not|avoid)", re.IGNORECASE
)
_ROUTE_QUESTION = re.compile(
    r"(?:嗎\s*[？?]?\s*$|能不能|可不可以|怎麼|如何|"
    r"\b(?:can|could|should|would|how)\b[^.!。！]*[?？]?\s*$)",
    re.IGNORECASE,
)
_MULTI_STOP_ROUTE = re.compile(r"(?:從.+到|\bfrom\b.+\bto\b)", re.IGNORECASE)


def explicit_route_goal(locations: list[Location], text: str) -> Location | None:
    """Return one saved goal for an unambiguous imperative route request.

    This is the natural-language reflex path used before the general agent. It
    accepts only a navigation verb immediately followed by an exact saved name
    or alias.  The normal approval card and route adapter still own execution;
    this function only removes unnecessary model planning from commands such
    as ``請前往 map_left_down，抵達後回報結果``.
    """

    request = " ".join(text.strip().split())
    if (
        not request
        or _ROUTE_NEGATION.search(request)
        or _ROUTE_QUESTION.search(request)
        or _MULTI_STOP_ROUTE.search(request)
    ):
        return None

    matches: list[Location] = []
    for location in locations:
        terms = [location.name, *location.aliases]
        for term in sorted({item.strip() for item in terms if item.strip()}, key=len, reverse=True):
            pattern = re.compile(
                rf"{_EXPLICIT_ROUTE_VERB}\s*{re.escape(term)}"
                r"(?=$|[\s,，。.!！?？、;；])",
                re.IGNORECASE,
            )
            if pattern.search(request):
                matches.append(location)
                break

    unique = {location.id: location for location in matches}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _extract_via_regex(text: str) -> tuple[str, str] | None:
    # An empty start is valid — only the goal is required (see route_preview).
    match = _SPLIT_PATTERN.search(text)
    if match:
        start, goal = match.group(1).strip(), match.group(2).strip()
        if start and goal:
            return start, goal
    match = _GOAL_ONLY_PATTERN.search(text)
    if match:
        goal = match.group(1).strip()
        if goal:
            return "", goal
    return None


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
    # Only the goal is required: the prompt above tells the model to leave a
    # missing start empty, so rejecting an empty start here would make every
    # destination-only request ("去A") unparseable by design.
    if not goal:
        return None
    return start, goal


async def route_preview(config: AppConfig, locations: list[Location], text: str) -> RouteOutput:
    extraction = _extract_via_regex(text)
    if extraction is None:
        # A bare known-location name ("map_right_down") is unambiguously the
        # goal — agents pass exactly this form. Deterministic, no provider.
        try:
            find_location(locations, text)
            extraction = ("", text.strip())
        except LocationNotFoundError:
            extraction = await _extract_via_llm(config, text)
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
    if start_query:
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
    # Every adapter is async. In particular the Nav2 CLI fallback owns a native
    # subprocess group, so Esc and /stop kill and reap send_goal before unwinding.
    result = await adapter.resolve(outgoing_action)
    return RouteOutput(
        input_text="",
        outgoing_action=outgoing_action,
        approval_status="approved",
        execution_status=result.execution_status,
        route_preview=result.detail,
    )
