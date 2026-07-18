from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "usability_study.py"
_SPEC = importlib.util.spec_from_file_location("usability_study", _SCRIPT)
assert _SPEC and _SPEC.loader
study = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(study)


def _trial(participant: str, condition: str, elapsed: float, *, success: bool = True) -> dict:
    return {
        "participant": participant,
        "experience": "novice",
        "condition": condition,
        "task": "discover_topic_type",
        "started_at": "2026-07-18T00:00:00+00:00",
        "elapsed_s": elapsed,
        "success": success,
        "errors": 0,
        "lookups": 0,
        "interventions": 0,
        "commands": 1,
    }


def test_generate_schedule_balances_condition_order() -> None:
    rows = study.generate_schedule(3, ("a", "b", "c"))

    first_conditions = [
        next(row["condition"] for row in rows if row["participant"] == participant)
        for participant in ("P01", "P02", "P03")
    ]

    assert first_conditions == ["manual", "slash", "natural"]
    assert len(rows) == 27


def test_start_and_finish_trial_uses_pseudonymous_metrics(tmp_path: Path) -> None:
    state = tmp_path / "active.json"
    output = tmp_path / "study.jsonl"
    study.start_trial(
        state,
        participant="P01",
        experience="novice",
        condition="slash",
        task="inspect_feedback",
        now_epoch=100.0,
    )

    trial = study.finish_trial(
        state,
        output,
        success=True,
        errors=1,
        lookups=2,
        commands=3,
        now_epoch=112.5,
    )

    assert trial["elapsed_s"] == 12.5
    assert trial["experience"] == "novice"
    assert trial["errors"] == 1
    assert not state.exists()
    assert json.loads(output.read_text(encoding="utf-8"))["participant"] == "P01"


def test_start_refuses_to_overwrite_active_trial(tmp_path: Path) -> None:
    state = tmp_path / "active.json"
    study.start_trial(
        state,
        participant="P01",
        experience="experienced",
        condition="manual",
        task="bounded_motion",
        now_epoch=1.0,
    )

    with pytest.raises(FileExistsError):
        study.start_trial(
            state,
            participant="P02",
            experience="novice",
            condition="natural",
            task="bounded_motion",
            now_epoch=2.0,
        )


def test_summary_keeps_failures_and_reports_paired_ratio() -> None:
    trials = [
        _trial("P01", "manual", 30.0),
        _trial("P01", "slash", 10.0),
        _trial("P01", "natural", 15.0),
        _trial("P02", "manual", 40.0),
        _trial("P02", "slash", 20.0, success=False),
        _trial("P02", "natural", 10.0),
        _trial("P03", "manual", 20.0),
        _trial("P03", "natural", 10.0),
        _trial("P03", "natural", 9.0),
    ]

    summary = study.summarize_trials(trials)

    assert summary["conditions"]["slash"]["success_rate"] == 0.5
    assert summary["paired"]["manual_vs_slash"] == {
        "pairs": 1,
        "median_speed_ratio": 3.0,
        "ambiguous_repeated_pairs": 0,
    }
    assert summary["paired"]["manual_vs_natural"] == {
        "pairs": 2,
        "median_speed_ratio": 3.0,
        "ambiguous_repeated_pairs": 1,
    }


def test_load_trials_rejects_missing_fields(tmp_path: Path) -> None:
    path = tmp_path / "study.jsonl"
    path.write_text('{"participant": "P01"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="missing fields"):
        study.load_trials(path)


def test_load_trials_rejects_non_boolean_success(tmp_path: Path) -> None:
    path = tmp_path / "study.jsonl"
    trial = _trial("P01", "manual", 4.0)
    trial["success"] = "false"
    path.write_text(json.dumps(trial) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="success must be a boolean"):
        study.load_trials(path)

    trial["success"] = False
    trial["experience"] = "expert"
    path.write_text(json.dumps(trial) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="experience must be one of"):
        study.load_trials(path)

    trial["experience"] = "experienced"
    trial["errors"] = 1.5
    path.write_text(json.dumps(trial) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="errors must be an integer"):
        study.load_trials(path)
