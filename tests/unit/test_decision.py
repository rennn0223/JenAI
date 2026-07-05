"""Decision core + eval harness tests (LLM mocked; honesty paths covered)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jenai.config.store import build_minimal_config
from jenai.tools.decision_core import ContextSnapshot, decide
from jenai.tools.decision_eval import load_scenarios, run_eval

CFG = build_minimal_config(provider_name="t", provider="openai", default_model="m", api_key_env="")
SNAP = ContextSnapshot(battery=0.1, task="idle", locations=["Dock", "機械系館"])


def _mock(monkeypatch, reply):
    import jenai.tools.decision_core as mod

    async def fake(config, prompt, *, binding="chat"):
        return reply

    monkeypatch.setattr(mod, "ask_json", fake)


def test_decide_valid_action_passes_through(monkeypatch) -> None:
    _mock(monkeypatch, {"action": "dock", "target": None, "reason": "battery low"})
    d = asyncio.run(decide(CFG, SNAP))
    assert d.action == "dock"


def test_decide_out_of_set_action_refers(monkeypatch) -> None:
    _mock(monkeypatch, {"action": "self_destruct", "reason": "?"})
    d = asyncio.run(decide(CFG, SNAP))
    assert d.action == "refer_to_human"
    assert "outside the bounded set" in d.reason


def test_decide_unknown_navigation_target_refers(monkeypatch) -> None:
    _mock(monkeypatch, {"action": "navigate_to", "target": "體育館", "reason": "go"})
    d = asyncio.run(decide(CFG, SNAP))
    assert d.action == "refer_to_human"
    assert "體育館" in d.reason


def test_decide_no_output_refers(monkeypatch) -> None:
    _mock(monkeypatch, None)
    assert asyncio.run(decide(CFG, SNAP)).action == "refer_to_human"


def test_scenarios_example_file_parses_and_eval_scores() -> None:
    scenarios = load_scenarios(Path(__file__).parents[2] / "scenarios.example.toml")
    assert len(scenarios) >= 8
    assert {s.family for s in scenarios} == {"S1", "S2", "S3", "S4"}


def test_run_eval_accuracy_and_unsafe_math(monkeypatch) -> None:
    import jenai.tools.decision_eval as ev

    scenarios = load_scenarios(Path(__file__).parents[2] / "scenarios.example.toml")[:3]

    async def always_dock(config, snapshot):
        from jenai.tools.decision_core import Decision

        return Decision(action="dock", reason="test")

    monkeypatch.setattr(ev, "decide", always_dock)
    report = asyncio.run(run_eval(CFG, scenarios, repeats=2))
    assert report.summary["n"] == 6  # 3 scenarios × 2 repeats
    # s1-battery-critical expects dock → correct; s2-path-blocked lists dock
    # neither expected nor unsafe → incorrect but not unsafe.
    assert 0 < report.summary["accuracy"] < 1
    assert report.families["S1"]["correct"] >= 2


def test_load_scenarios_rejects_missing_gold(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text('[[scenarios]]\nid = "x"\n[scenarios.snapshot]\ntask = "idle"\n')
    with pytest.raises(ValueError, match="missing 'expected'"):
        load_scenarios(bad)
