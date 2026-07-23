from __future__ import annotations

import asyncio
from pathlib import Path

from jenai.config.store import build_minimal_config
from jenai.daemon.engine import Rule, RuleEngine
from jenai.schemas import SceneAnalysis
from jenai.tools.perception import PerceptionLoop, parse_scene_analysis


def _config():
    return build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )


# -- affordance parsing --------------------------------------------------------


def test_parse_scene_analysis_full_reply() -> None:
    analysis = parse_scene_analysis(
        {
            "scene_context": "A corridor with a box in the middle.",
            "objects": ["box", "door"],
            "affordances": ["Path Blocked", "door_open"],
            "suggested_action": "navigate around the box",
            "confidence": "0.85",  # numeric string — models do this
            "requires_approval": True,
        }
    )

    assert analysis is not None
    assert analysis.scene_context.startswith("A corridor")
    assert analysis.objects == ["box", "door"]
    assert analysis.affordances == ["path_blocked", "door_open"]  # normalized
    assert analysis.suggested_action == "navigate around the box"
    assert analysis.confidence == 0.85
    assert analysis.requires_approval is True
    assert analysis.ts > 0


def test_parse_scene_analysis_degrades_safely() -> None:
    # Strings where lists were asked; garbage confidence; missing approval flag.
    analysis = parse_scene_analysis(
        {
            "scene_context": "open floor",
            "objects": "chair",
            "affordances": "path_clear",
            "confidence": "high",
            # requires_approval absent
        }
    )

    assert analysis.objects == ["chair"]
    assert analysis.affordances == ["path_clear"]
    assert analysis.confidence == 0.0  # unparseable → 0, never a crash
    assert analysis.requires_approval is True  # unknown → keep the human gate

    assert parse_scene_analysis(None) is None
    assert parse_scene_analysis("not a dict") is None

    clamped = parse_scene_analysis({"confidence": 7})
    assert clamped.confidence == 1.0


# -- rule matching --------------------------------------------------------------


def _affordance_rule(**overrides) -> Rule:
    base = dict(
        name="blocked",
        topic="@perception",
        affordance="path_blocked",
        cooldown_s=0.0,
        action="notify",
    )
    base.update(overrides)
    return Rule.model_validate(base)


def test_affordance_rule_fires_on_membership() -> None:
    rule = _affordance_rule()
    engine = RuleEngine([rule])
    data = SceneAnalysis(affordances=["path_blocked", "door_open"], confidence=0.9).model_dump()

    decision = engine.handle_event(rule, data, now=0.0)

    assert decision.fired
    assert decision.reason == "notify"


def test_affordance_rule_does_not_fire_when_absent_or_malformed() -> None:
    rule = _affordance_rule()
    engine = RuleEngine([rule])

    clear = SceneAnalysis(affordances=["path_clear"]).model_dump()
    assert not engine.handle_event(rule, clear, now=0.0).fired

    # affordances missing entirely / wrong type → never fire
    assert not engine.handle_event(rule, {}, now=1.0).fired
    assert not engine.handle_event(rule, {"affordances": "path_blocked"}, now=2.0).fired


def test_affordance_rule_respects_min_confidence_and_cooldown() -> None:
    rule = _affordance_rule(min_confidence=0.6, cooldown_s=100.0)
    engine = RuleEngine([rule])
    low = SceneAnalysis(affordances=["path_blocked"], confidence=0.3).model_dump()
    high = SceneAnalysis(affordances=["path_blocked"], confidence=0.9).model_dump()

    assert not engine.handle_event(rule, low, now=0.0).fired  # confidence gate
    assert engine.handle_event(rule, high, now=1.0).fired
    followup = engine.handle_event(rule, high, now=2.0)  # cooldown gate
    assert not followup.fired and "cooldown" in followup.reason


def test_affordance_goto_keeps_existing_safety_gating() -> None:
    # A perception rule that moves the robot obeys the SAME gates as numeric
    # rules: auto_approve AND nav2 — perception gets no shortcut.
    rule = _affordance_rule(action="goto Dock", auto_approve=False)
    engine = RuleEngine([rule], nav_allowed=True)
    data = SceneAnalysis(affordances=["path_blocked"], confidence=1.0).model_dump()

    decision = engine.handle_event(rule, data, now=0.0)

    assert decision.fired and decision.navigate_to is None
    assert "auto_approve" in decision.reason


def test_rule_validator_accepts_affordance_only_condition() -> None:
    rule = _affordance_rule()
    assert rule.fld == "affordances"  # default field for perception rules

    import pytest

    with pytest.raises(ValueError):
        Rule.model_validate({"name": "x", "topic": "@perception", "action": "notify"})


# -- the loop -------------------------------------------------------------------


def test_perception_loop_ticks_and_stops(monkeypatch, tmp_path: Path) -> None:
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"fake")

    class FakeBridge:
        def __init__(self) -> None:
            self.captures = 0

        async def capture_frame(self, topic, timeout=5.0):
            self.captures += 1
            frame.write_bytes(b"fake")  # recreate: the loop unlinks after use
            return frame

    async def fake_vision(config, prompt, data_url, *, binding="vision"):
        return {
            "scene_context": "hallway",
            "affordances": ["path_clear"],
            "confidence": 0.9,
            "requires_approval": False,
        }

    monkeypatch.setattr("jenai.tools.perception.ask_vision_json", fake_vision)

    async def run() -> None:
        bridge = FakeBridge()
        seen: list[SceneAnalysis] = []

        async def on_analysis(analysis: SceneAnalysis) -> None:
            seen.append(analysis)

        loop = PerceptionLoop(_config(), bridge, topic="/rgb", hz=20.0, on_analysis=on_analysis)
        await loop.start()
        assert loop.running
        for _ in range(100):
            await asyncio.sleep(0.02)
            if len(seen) >= 3:
                break
        await loop.stop()
        assert not loop.running

        assert len(seen) >= 3  # it ticked repeatedly
        assert seen[0].affordances == ["path_clear"]
        assert loop.latest is not None and loop.frames == len(seen)
        captures_at_stop = bridge.captures
        await asyncio.sleep(0.2)
        assert bridge.captures == captures_at_stop  # truly stopped

    asyncio.run(run())


def test_perception_loop_reports_errors_once_per_streak(monkeypatch, tmp_path: Path) -> None:
    from jenai.bridge import BridgeError

    class DeadBridge:
        async def capture_frame(self, topic, timeout=5.0):
            raise BridgeError("no camera")

    async def run() -> None:
        statuses: list[str] = []

        async def on_status(message: str) -> None:
            statuses.append(message)

        loop = PerceptionLoop(_config(), DeadBridge(), topic="/rgb", hz=20.0, on_status=on_status)
        await loop.start()
        await asyncio.sleep(0.3)
        await loop.stop()

        assert len(statuses) == 1  # one report per failure streak, not per tick
        assert "camera unavailable" in statuses[0]

    asyncio.run(run())
