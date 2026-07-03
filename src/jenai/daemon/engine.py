from __future__ import annotations

import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class RuleError(Exception):
    """Raised when a rules file cannot be read or validated."""


class Rule(BaseModel):
    """One event-trigger rule: watch a topic field, fire an action on a threshold.

    Safety: `action` defaults to notify-only. A rule that moves the robot
    ("goto <location>") additionally requires `auto_approve = true` — an
    unattended daemon must never actuate on an implicit default. "halt"
    (emergency stop) needs no approval: stopping is always safe.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    topic: str  # ROS topic, or "@perception" to trigger on camera VLM analyses
    msg_type: str = ""  # unused for @perception rules
    fld: str = "affordances"  # dotted path into the message dict, e.g. "percentage"
    below: float | None = None
    above: float | None = None
    equals: Any | None = None
    # Perception condition: fires when this affordance (e.g. "path_blocked")
    # appears in the extracted list AND the analysis confidence reaches
    # min_confidence. Peer of the numeric thresholds above.
    affordance: str | None = None
    min_confidence: float = 0.0
    cooldown_s: float = 300.0
    action: str = "notify"
    auto_approve: bool = False
    throttle_s: float = 2.0

    @model_validator(mode="after")
    def _check(self) -> Rule:
        if (
            self.below is None
            and self.above is None
            and self.equals is None
            and self.affordance is None
        ):
            raise ValueError(f"rule '{self.name}' needs one of below/above/equals/affordance")
        if not (
            self.action in ("notify", "halt") or self.action.startswith("goto ")
        ):
            raise ValueError(
                f"rule '{self.name}': action must be 'notify', 'halt', or 'goto <location>'"
            )
        return self


def load_rules(path: Path) -> list[Rule]:
    """Parse a rules TOML file (see rules.example.toml); raises RuleError with
    a human-readable reason on any malformed or unsafe rule."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuleError(f"Rules file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise RuleError(f"Rules file is not valid TOML: {path}") from exc

    rules = []
    for entry in raw.get("rules", []):
        # TOML uses "field"; the model calls it fld to dodge pydantic's reserved names.
        if "field" in entry:
            entry = dict(entry)
            entry["fld"] = entry.pop("field")
        try:
            rules.append(Rule.model_validate(entry))
        except ValueError as exc:
            raise RuleError(str(exc)) from exc
    return rules


def extract_field(data: dict, dotted: str) -> Any | None:
    """Walk a dotted path ("pose.pose.position.x") into a message dict; None if absent."""
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def condition_met(rule: Rule, value: Any, data: dict | None = None) -> bool:
    """True when the extracted value crosses the rule's threshold.

    Missing or non-numeric values never fire — a sensor dropout must not
    trigger an action. Affordance rules fire on membership in the extracted
    list, gated by the analysis confidence when the rule demands one.
    """
    if rule.affordance is not None:
        if not isinstance(value, list) or rule.affordance not in value:
            return False
        if rule.min_confidence > 0:
            try:
                confidence = float((data or {}).get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            return confidence >= rule.min_confidence
        return True
    if value is None:
        return False
    if rule.equals is not None:
        return value == rule.equals
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    if rule.below is not None and number < rule.below:
        return True
    return rule.above is not None and number > rule.above


@dataclass
class Decision:
    rule: Rule
    value: Any
    fired: bool
    reason: str
    navigate_to: str | None = None  # location name, only when actually allowed to move
    halt: bool = False  # emergency stop — no approval gate, stopping is always safe


@dataclass
class RuleEngine:
    """Pure trigger logic: feed topic messages in, get decisions out.

    Owns cooldown bookkeeping; owns NO ROS or navigation. The runner wires
    bridge watches to handle_event and executes allowed decisions.
    """

    rules: list[Rule]
    nav_allowed: bool = False  # route_adapter == "nav2"
    _last_fired: dict[str, float] = field(default_factory=dict)

    def handle_event(self, rule: Rule, data: dict, now: float | None = None) -> Decision:
        """Evaluate one topic message against one rule.

        Returns a Decision that says what happened and why; `navigate_to` is
        set only when every safety gate passed (condition + cooldown +
        auto_approve + nav2 adapter). `now` is injectable for tests.
        """
        now = time.monotonic() if now is None else now
        value = extract_field(data, rule.fld)
        if not condition_met(rule, value, data):
            return Decision(rule, value, fired=False, reason="condition not met")

        last = self._last_fired.get(rule.name)
        if last is not None and now - last < rule.cooldown_s:
            remaining = rule.cooldown_s - (now - last)
            return Decision(rule, value, fired=False, reason=f"cooldown ({remaining:.0f}s left)")

        self._last_fired[rule.name] = now
        if rule.action == "halt":
            return Decision(rule, value, fired=True, reason="emergency stop", halt=True)
        if rule.action.startswith("goto "):
            target = rule.action[5:].strip()
            if not rule.auto_approve:
                return Decision(
                    rule,
                    value,
                    fired=True,
                    reason=f"would navigate to '{target}' — set auto_approve = true to allow",
                )
            if not self.nav_allowed:
                return Decision(
                    rule,
                    value,
                    fired=True,
                    reason=f"would navigate to '{target}' — route_adapter is not 'nav2'",
                )
            return Decision(
                rule, value, fired=True, reason=f"navigating to '{target}'", navigate_to=target
            )
        return Decision(rule, value, fired=True, reason="notify")
