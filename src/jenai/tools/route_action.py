"""Canonical normalization for route actions shown and executed by JenAI."""

from __future__ import annotations

import json
from typing import Any

ROUTE_ACTION_MAX_JSON_LAYERS = 2


def unwrap_route_action(action: dict[str, Any]) -> dict[str, Any]:
    """Unwrap one preview-response envelope, rejecting deeper wrappers."""
    if "goal" in action or "outgoing_action" not in action:
        return action

    inner = action["outgoing_action"]
    if not isinstance(inner, dict):
        raise ValueError("outgoing_action wrapper must contain a JSON object")
    if "goal" not in inner and "outgoing_action" in inner:
        raise ValueError("more than one outgoing_action wrapper is not allowed")
    return inner


def normalize_route_action(value: object) -> dict[str, Any]:
    """Decode and unwrap exactly the bounded route object eligible for execution.

    Agents may pass an object directly, JSON-encode it once, or quote that JSON
    once more. No deeper JSON-string or outgoing_action nesting is accepted.
    Goal/pose semantics remain the navigation gateway's responsibility.
    """
    parsed = value
    for _ in range(ROUTE_ACTION_MAX_JSON_LAYERS):
        if not isinstance(parsed, str):
            break
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError as exc:
            raise ValueError(f"not valid JSON: {exc}") from exc

    if isinstance(parsed, str):
        raise ValueError(f"exceeds {ROUTE_ACTION_MAX_JSON_LAYERS} allowed JSON string layer(s)")
    if not isinstance(parsed, dict):
        raise ValueError("must decode to a JSON object")
    return unwrap_route_action(parsed)
