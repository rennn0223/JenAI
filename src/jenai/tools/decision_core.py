"""M6 decision brain: context snapshot → ONE bounded action (thesis core).

The cognitive step of the autonomous loop (perceive → snapshot → **decide** →
rehearse → act → feed back). Deliberately tiny and strict:

- The action space is CLOSED (`ACTIONS`): every decision is a discrete choice
  among already-implemented, already-gated primitives — which is what makes a
  decision rehearsable in the twin, measurable against a gold label (E1), and
  auditable after the fact. No free-form output ever reaches actuation.
- Invalid/unparseable model output degrades to `refer_to_human`, never to a
  guessed action: when the brain is unsure, the robot stands still and asks.
- `decide()` is ONE ask_json call with no retries: latency and failure modes
  stay predictable; retrying is the caller's policy decision, not hidden here.

`jenai eval` (decision_eval.py) measures this function against labeled
scenario families — decision quality is a number here, not a feeling.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from jenai.config.models import AppConfig
from jenai.providers.chat import ask_json

ACTIONS = ("navigate_to", "patrol", "dock", "wait", "capture_and_report", "refer_to_human")


class ContextSnapshot(BaseModel):
    """Everything the brain sees for one decision — structured, compact text.

    Keep this SMALL on purpose: an 8B edge model decides far more reliably
    over six labeled fields than over a page of prose (Inner-Monologue-style
    feedback, structured instead of free text).
    """

    model_config = ConfigDict(extra="forbid")

    pose: str = "unknown"  # "x=1.2 y=-3.4 yaw=0.1 (map)" or "unknown"
    battery: float | None = None  # 0..1, None = no battery topic
    scene: str = ""  # VLM scene summary / affordances, "" = no camera
    task: str = "idle"  # current task state, e.g. "patrol 2/6 points done"
    request: str = ""  # standing user instruction, if any
    locations: list[str] = []  # known location names (navigate_to targets)


class Decision(BaseModel):
    """One discrete choice from the closed action set — the ONLY thing that
    may reach actuation.

    `action` is constrained to ACTIONS at validation time, so a hallucinated
    verb fails parsing and degrades to refer_to_human upstream. extra="ignore"
    tolerates models that emit extra keys; unknown *values* still fail.
    """

    model_config = ConfigDict(extra="ignore")

    action: Literal[*ACTIONS]
    target: str | None = None  # location name for navigate_to/patrol
    reason: str = ""


_PROMPT = (
    "You are the decision core of an unmanned ground vehicle. Given the "
    "situation snapshot, choose the SINGLE best next action.\n"
    "Actions: navigate_to(target=known location) | patrol | dock | wait | "
    "capture_and_report | refer_to_human.\n"
    "Rules: choose dock when battery is critically low; finish the current "
    "task step before starting new ones when reasonable; when the situation "
    "is ambiguous, risky, or the request is unclear — refer_to_human (asking "
    "is always safe, wrong movement is not). navigate_to target MUST be one "
    "of the known locations.\n"
    'Respond ONLY JSON: {"action": "...", "target": "... or null", '
    '"reason": "one short sentence"}\n\n'
    "Snapshot:\n{snapshot}\n"
)


def _refer(reason: str) -> Decision:
    return Decision(action="refer_to_human", target=None, reason=reason)


async def decide(config: AppConfig, snapshot: ContextSnapshot) -> Decision:
    """One bounded decision. NEVER raises and never returns a free-form action:
    anything the model gets wrong becomes an honest refer_to_human."""
    parsed = await ask_json(
        config,
        _PROMPT.replace("{snapshot}", snapshot.model_dump_json(indent=1)),
        binding="plan",
    )
    if not isinstance(parsed, dict):
        return _refer("model returned no usable decision")
    try:
        decision = Decision.model_validate(parsed)
    except ValidationError:
        return _refer(f"model chose an action outside the bounded set: {parsed.get('action')!r}")
    if decision.action in ("navigate_to",) and (
        not decision.target or decision.target not in snapshot.locations
    ):
        # A hallucinated destination must not become motion toward nowhere.
        return _refer(f"unknown navigation target {decision.target!r}")
    return decision
