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
    with pytest.raises(RuleError, match="below/above/equals/affordance"):
        load_rules(rules_file)


def test_example_rules_file_parses() -> None:
    rules = load_rules(Path(__file__).parents[2] / "rules.example.toml")
    assert len(rules) == 3
    assert all(not r.auto_approve for r in rules)  # example must ship safe
    perception = [r for r in rules if r.topic == "@perception"]
    assert len(perception) == 1 and perception[0].affordance == "path_blocked"


def test_daemon_navigate_failure_is_reported_not_silent(tmp_path: Path, monkeypatch) -> None:
    """A fire-and-forget nav task that dies (malformed locations.toml) must
    surface through on_status — the rule fired, the robot didn't move, and the
    operator has to learn why."""
    import asyncio
    import contextlib
    import sys

    from jenai.bridge import RosBridgeClient
    from jenai.bridge import client as client_module
    from jenai.config.store import build_minimal_config
    from jenai.daemon.runner import run_daemon

    monkeypatch.setattr(
        client_module, "_BRIDGE_SCRIPT", Path(__file__).parent / "fake_bridge.py"
    )
    monkeypatch.setenv("JENAI_BRIDGE_PYTHON", sys.executable)
    monkeypatch.setattr(RosBridgeClient, "available", staticmethod(lambda: True))

    config = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )
    config.route_adapter = "nav2"  # allow goto rules
    config_path = tmp_path / "config.toml"
    (tmp_path / "locations.toml").write_text("not = [valid toml", encoding="utf-8")

    # fake_bridge's watch op immediately emits {"percentage": 0.42}.
    rule = Rule(
        name="low-battery",
        topic="/battery",
        msg_type="sensor_msgs/msg/BatteryState",
        fld="percentage",
        below=0.5,
        action="goto Dock",
        auto_approve=True,
        cooldown_s=0.0,
    )

    statuses: list[str] = []

    async def run() -> None:
        task = asyncio.create_task(
            run_daemon(
                config,
                config_path,
                [rule],
                on_decision=lambda d: None,
                on_status=statuses.append,
            )
        )
        for _ in range(100):  # wait for the nav task's failure report
            await asyncio.sleep(0.05)
            if any("navigation failed" in s for s in statuses):
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())

    assert any("navigation failed" in s for s in statuses)


def test_halt_action_fires_without_auto_approve() -> None:
    # Stopping is always safe: no auto_approve, no nav2 requirement.
    rule = Rule(
        name="estop",
        topic="/e_stop",
        msg_type="std_msgs/msg/Bool",
        fld="data",
        equals=True,
        action="halt",
    )
    engine = RuleEngine([rule], nav_allowed=False)

    decision = engine.handle_event(rule, {"data": True}, now=0.0)

    assert decision.fired and decision.halt
    assert decision.navigate_to is None


def test_action_validator_accepts_halt_rejects_junk() -> None:
    Rule(
        name="ok", topic="/t", msg_type="std_msgs/msg/Bool", fld="data",
        equals=True, action="halt",
    )
    with pytest.raises(ValueError):
        Rule(
            name="bad", topic="/t", msg_type="std_msgs/msg/Bool", fld="data",
            equals=True, action="explode",
        )


# --- fault injection: the autonomous goto path (A3/A4) ----------------------


_DOCK_LOCATIONS = """\
[[locations]]
name = "Dock"
tags = ["dock"]

[locations.pose]
x = 1.0
y = 2.0
yaw = 0.0
"""


def _goto_rule() -> Rule:
    return Rule(
        name="low-battery",
        topic="/battery",
        msg_type="sensor_msgs/msg/BatteryState",
        fld="percentage",
        below=0.5,
        action="goto Dock",
        auto_approve=True,
        cooldown_s=0.0,
    )


def _run_daemon_until(
    tmp_path, monkeypatch, *, marker: str, locations: str | None, mutate_config=None
) -> list[str]:
    """Drive run_daemon with the fake bridge until `marker` shows in statuses."""
    import asyncio
    import contextlib
    import sys

    from jenai.bridge import RosBridgeClient
    from jenai.bridge import client as client_module
    from jenai.config.store import build_minimal_config
    from jenai.daemon.runner import run_daemon

    monkeypatch.setattr(
        client_module, "_BRIDGE_SCRIPT", Path(__file__).parent / "fake_bridge.py"
    )
    monkeypatch.setenv("JENAI_BRIDGE_PYTHON", sys.executable)
    monkeypatch.setattr(RosBridgeClient, "available", staticmethod(lambda: True))

    config = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )
    config.route_adapter = "nav2"
    if mutate_config is not None:
        mutate_config(config)
    config_path = tmp_path / "config.toml"
    if locations is not None:
        (tmp_path / "locations.toml").write_text(locations, encoding="utf-8")

    statuses: list[str] = []

    async def run() -> None:
        task = asyncio.create_task(
            run_daemon(
                config,
                config_path,
                [_goto_rule()],
                on_decision=lambda d: None,
                on_status=statuses.append,
            )
        )
        for _ in range(100):
            await asyncio.sleep(0.05)
            if any(marker in s for s in statuses):
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
    return statuses


def test_daemon_twin_refer_blocks_autonomous_goto(tmp_path: Path, monkeypatch) -> None:
    """Autonomous path has no human to refer to: anything short of a clean
    twin pass must keep the robot parked, and say so."""
    from types import SimpleNamespace

    import jenai.daemon.runner as runner_module

    moved: list[dict] = []

    async def fake_rehearse(twin, action, on_status=None):
        return SimpleNamespace(verdict="refer", summary="G2 timeout in the twin")

    async def fake_navigate(bridge, action):
        moved.append(action)
        return SimpleNamespace(execution_status="succeeded", route_preview="")

    monkeypatch.setattr(runner_module, "rehearse_goal", fake_rehearse)
    monkeypatch.setattr(runner_module, "navigate_live", fake_navigate)

    def enable_twin(cfg) -> None:
        cfg.twin.enabled = True

    statuses = _run_daemon_until(
        tmp_path,
        monkeypatch,
        marker="NOT moved",
        locations=_DOCK_LOCATIONS,
        mutate_config=enable_twin,
    )
    assert any("NOT moved" in s for s in statuses)
    assert moved == []  # the gate held: navigate_live was never reached


def test_daemon_goto_unknown_location_is_reported(tmp_path: Path, monkeypatch) -> None:
    statuses = _run_daemon_until(
        tmp_path,
        monkeypatch,
        marker="unknown location",
        locations='[[locations]]\nname = "Lab"\n\n[locations.pose]\nx = 0.0\ny = 0.0\nyaw = 0.0\n',
    )
    assert any("unknown location" in s for s in statuses)


def test_daemon_goto_without_locations_file_is_reported(tmp_path: Path, monkeypatch) -> None:
    statuses = _run_daemon_until(tmp_path, monkeypatch, marker="no locations file", locations=None)
    assert any("no locations file" in s for s in statuses)


def test_load_rules_missing_file_and_bad_toml_raise_rule_error(tmp_path: Path) -> None:
    import pytest

    from jenai.daemon.engine import RuleError, load_rules

    with pytest.raises(RuleError, match="not found"):
        load_rules(tmp_path / "nope.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("not = [valid", encoding="utf-8")
    with pytest.raises(RuleError, match="not valid TOML"):
        load_rules(bad)


def test_perception_rule_non_numeric_confidence_does_not_fire() -> None:
    """A VLM that answers confidence='high' must count as 0.0 — below any
    threshold — not crash the engine or fire the rule."""
    from jenai.daemon.engine import condition_met

    rule = _rule(
        topic="@perception",
        below=None,
        affordance="path_blocked",
        min_confidence=0.6,
    )
    data = {"affordances": ["path_blocked"], "confidence": "high"}
    assert condition_met(rule, None, data) is False


def _halt_rule() -> Rule:
    return Rule(
        name="estop-battery",
        topic="/battery",
        msg_type="sensor_msgs/msg/BatteryState",
        fld="percentage",
        below=0.5,
        action="halt",
        cooldown_s=0.0,
    )


def test_daemon_halt_rule_halts_and_reports(tmp_path: Path, monkeypatch) -> None:
    """The halt action must reach the bridge and its outcome must be spoken."""
    import asyncio
    import contextlib
    import sys

    from jenai.bridge import RosBridgeClient
    from jenai.bridge import client as client_module
    from jenai.config.store import build_minimal_config
    from jenai.daemon.runner import run_daemon

    monkeypatch.setattr(
        client_module, "_BRIDGE_SCRIPT", Path(__file__).parent / "fake_bridge.py"
    )
    monkeypatch.setenv("JENAI_BRIDGE_PYTHON", sys.executable)
    monkeypatch.setattr(RosBridgeClient, "available", staticmethod(lambda: True))

    config = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )
    statuses: list[str] = []

    async def run() -> None:
        task = asyncio.create_task(
            run_daemon(
                config,
                tmp_path / "config.toml",
                [_halt_rule()],
                on_decision=lambda d: None,
                on_status=statuses.append,
            )
        )
        for _ in range(100):
            await asyncio.sleep(0.05)
            if any("estop-battery" in s for s in statuses):
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert any("estop-battery" in s for s in statuses)


def test_daemon_halt_failure_is_reported_not_silent(tmp_path: Path, monkeypatch) -> None:
    """If even the halt fails, the operator must hear it immediately."""
    import jenai.daemon.runner as runner_module
    from jenai.bridge import BridgeError

    async def broken_halt(config, bridge):
        raise BridgeError("halt pipe broke")

    monkeypatch.setattr(runner_module, "halt_robot", broken_halt)

    import asyncio
    import contextlib
    import sys

    from jenai.bridge import RosBridgeClient
    from jenai.bridge import client as client_module
    from jenai.config.store import build_minimal_config
    from jenai.daemon.runner import run_daemon

    monkeypatch.setattr(
        client_module, "_BRIDGE_SCRIPT", Path(__file__).parent / "fake_bridge.py"
    )
    monkeypatch.setenv("JENAI_BRIDGE_PYTHON", sys.executable)
    monkeypatch.setattr(RosBridgeClient, "available", staticmethod(lambda: True))

    config = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )
    statuses: list[str] = []

    async def run() -> None:
        task = asyncio.create_task(
            run_daemon(
                config,
                tmp_path / "config.toml",
                [_halt_rule()],
                on_decision=lambda d: None,
                on_status=statuses.append,
            )
        )
        for _ in range(100):
            await asyncio.sleep(0.05)
            if any("halt failed" in s for s in statuses):
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert any("halt failed" in s for s in statuses)
