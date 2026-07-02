from __future__ import annotations

from pathlib import Path

import pytest

from jenai.daemon.engine import (
    Rule,
    RuleEngine,
    RuleError,
    condition_met,
    extract_field,
    load_rules,
)


def _rule(**overrides) -> Rule:
    base = {
        "name": "battery-low",
        "topic": "/battery_state",
        "msg_type": "sensor_msgs/msg/BatteryState",
        "fld": "percentage",
        "below": 0.25,
        "cooldown_s": 600,
    }
    base.update(overrides)
    return Rule.model_validate(base)


def test_extract_field_dotted_path() -> None:
    data = {"pose": {"pose": {"position": {"x": 3.5}}}}
    assert extract_field(data, "pose.pose.position.x") == 3.5
    assert extract_field(data, "pose.missing.x") is None


def test_condition_met_below_above_equals() -> None:
    assert condition_met(_rule(below=0.25), 0.1)
    assert not condition_met(_rule(below=0.25), 0.5)
    assert condition_met(_rule(below=None, above=40.0), 55.0)
    assert condition_met(_rule(below=None, equals=True), True)
    assert not condition_met(_rule(below=0.25), None)
    assert not condition_met(_rule(below=0.25), "not-a-number")


def test_engine_fires_once_then_cooldown() -> None:
    engine = RuleEngine([_rule()])
    rule = engine.rules[0]

    first = engine.handle_event(rule, {"percentage": 0.1}, now=1000.0)
    assert first.fired and first.reason == "notify"

    during_cooldown = engine.handle_event(rule, {"percentage": 0.1}, now=1100.0)
    assert not during_cooldown.fired and "cooldown" in during_cooldown.reason

    after = engine.handle_event(rule, {"percentage": 0.1}, now=1700.0)
    assert after.fired


def test_goto_requires_auto_approve_and_nav2() -> None:
    rule = _rule(action="goto Dock")
    engine = RuleEngine([rule], nav_allowed=True)
    decision = engine.handle_event(rule, {"percentage": 0.1}, now=0.0)
    assert decision.fired and decision.navigate_to is None
    assert "auto_approve" in decision.reason

    approved = _rule(action="goto Dock", auto_approve=True)
    engine = RuleEngine([approved], nav_allowed=False)
    decision = engine.handle_event(approved, {"percentage": 0.1}, now=0.0)
    assert decision.fired and decision.navigate_to is None
    assert "route_adapter" in decision.reason

    engine = RuleEngine([approved], nav_allowed=True)
    decision = engine.handle_event(approved, {"percentage": 0.1}, now=0.0)
    assert decision.navigate_to == "Dock"


def test_load_rules_from_toml(tmp_path: Path) -> None:
    rules_file = tmp_path / "rules.toml"
    rules_file.write_text(
        """
[[rules]]
name = "battery"
topic = "/battery_state"
msg_type = "sensor_msgs/msg/BatteryState"
field = "percentage"
below = 0.3
action = "goto Dock"
""",
        encoding="utf-8",
    )
    rules = load_rules(rules_file)
    assert rules[0].fld == "percentage"
    assert rules[0].action == "goto Dock"
    assert rules[0].auto_approve is False  # safe default


def test_load_rules_rejects_conditionless_rule(tmp_path: Path) -> None:
    rules_file = tmp_path / "rules.toml"
    rules_file.write_text(
        """
[[rules]]
name = "broken"
topic = "/x"
msg_type = "std_msgs/msg/Bool"
field = "data"
""",
        encoding="utf-8",
    )
    with pytest.raises(RuleError, match="below/above/equals"):
        load_rules(rules_file)


def test_example_rules_file_parses() -> None:
    rules = load_rules(Path(__file__).parents[2] / "rules.example.toml")
    assert len(rules) == 2
    assert all(not r.auto_approve for r in rules)  # example must ship safe
