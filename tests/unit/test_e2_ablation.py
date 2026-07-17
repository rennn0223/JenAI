"""Regression tests for the thesis E2 experiment protocol."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parents[2] / "scripts" / "e2_ablation.py"
_SPEC = importlib.util.spec_from_file_location("e2_ablation", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
e2 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(e2)


def test_zone_crossing_requires_real_segment_intersection() -> None:
    assert e2.segment_intersects_zone(e2.HOME["x"], e2.HOME["y"], -8.0, -15.0)
    assert not e2.segment_intersects_zone(e2.HOME["x"], e2.HOME["y"], 5.0, 5.0)


def test_static_condition_only_blocks_goal_inside_zone() -> None:
    verdict, _, criteria = e2.static_verdict({"x": -7.0, "y": -10.0})
    assert verdict == "block"
    assert criteria == {"G3": "fail"}
    verdict, _, criteria = e2.static_verdict({"x": 0.5, "y": -1.5})
    assert verdict == "pass"
    assert criteria == {"G3": "pass"}



def test_offline_conditions_do_not_start_ros(tmp_path) -> None:
    targets = [{"target_id": "T001", "class": "normal", "x": 0.5, "y": -1.5, "yaw": 0.0}]
    out = tmp_path / "trials.jsonl"
    done = asyncio.run(
        e2.run_trials(targets, out, run_id="offline", conditions=("A", "B"))
    )
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert done == 2
    assert [row["condition"] for row in rows] == ["A", "B"]
    assert all(row["verdict"] == "pass" for row in rows)

def test_go_home_retries_before_accepting_trial() -> None:
    class Client:
        def __init__(self) -> None:
            self.statuses = iter(["aborted", "succeeded"])
            self.callback = None
            self.halts = 0

        def on_event(self, name, callback) -> None:
            self.callback = callback

        def off_event(self, name, callback) -> None:
            self.callback = None

        async def nav_send(self, x, y, yaw, tag) -> None:
            status = next(self.statuses)
            asyncio.get_running_loop().call_soon(
                self.callback, {"tag": tag, "status": status}
            )

        async def halt(self) -> None:
            self.halts += 1

    client = Client()
    homed, attempts = asyncio.run(e2.go_home(client, attempts=3, timeout_s=0.1))
    assert homed
    assert attempts == 2
    assert client.halts == 1


def test_e4_excludes_provider_errors_from_latency(monkeypatch, tmp_path) -> None:
    from jenai.tools import decision_core
    from jenai.tools.decision_core import Decision

    e4_path = Path(__file__).parents[2] / "scripts" / "e4_bench.py"
    spec = importlib.util.spec_from_file_location("e4_bench", e4_path)
    assert spec is not None and spec.loader is not None
    e4 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e4)
    calls = 0

    async def decide(config, snapshot):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("provider unavailable")
        return Decision(action="refer_to_human", reason="test")

    monkeypatch.setattr(decision_core, "decide", decide)
    out = tmp_path / "e4.jsonl"
    latencies, errors = asyncio.run(e4.run(object(), 2, out, run_id="run-test"))
    rows = [__import__("json").loads(line) for line in out.read_text().splitlines()]
    assert len(latencies) == 1
    assert errors == 1
    assert [row["success"] for row in rows] == [False, True]


def test_legacy_reanalysis_builds_paired_rows(tmp_path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    target = {"class": "normal", "x": 1.0, "y": 2.0, "yaw": 0.0}
    (legacy / "targets.json").write_text(json.dumps([target]), encoding="utf-8")
    trial = {
        "ts": "2026-01-01T00:00:00",
        "class": "normal",
        "x": 1.0,
        "y": 2.0,
        "verdict": "pass",
        "reason": "",
        "elapsed_s": 1.2,
        "criteria": {"G5": "pass"},
        "homed": True,
    }
    (legacy / "trials.jsonl").write_text(json.dumps(trial) + "\n", encoding="utf-8")
    out = tmp_path / "paired"
    script = Path(__file__).parents[2] / "scripts" / "e2_reanalyze.py"
    subprocess.run(
        [sys.executable, str(script), str(legacy), "--out", str(out)],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = [json.loads(line) for line in (out / "trials.jsonl").read_text().splitlines()]
    assert [row["condition"] for row in rows] == ["A", "B", "C"]
    assert all(row["confirmatory"] for row in rows)
    assert rows[2]["derived"] is False
