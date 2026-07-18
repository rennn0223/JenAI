from __future__ import annotations

import importlib.util
import json
from collections import Counter
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
    rows = study.generate_schedule(6, ("a", "b", "c"), seed=7)
    orders: list[tuple[str, ...]] = []
    for participant in (f"P{index:02d}" for index in range(1, 7)):
        periods = sorted(
            (row for row in rows if row["participant"] == participant and row["task_order"] == "1"),
            key=lambda row: row["period"],
        )
        orders.append(tuple(row["condition"] for row in periods))

    first_counts = Counter(order[0] for order in orders)
    transitions = Counter(
        pair for order in orders for pair in ((order[0], order[1]), (order[1], order[2]))
    )

    assert set(orders) == set(study.WILLIAMS_ORDERS)
    assert first_counts == {condition: 2 for condition in study.CONDITIONS}
    assert all(count == 2 for count in transitions.values())
    assert all(row["allocation_seed"] == "7" for row in rows)
    assert len(rows) == 54


def test_generate_schedule_requires_complete_williams_blocks() -> None:
    for participants in (1, 5, 7, 11):
        with pytest.raises(ValueError, match="positive multiple of 6"):
            study.generate_schedule(participants)


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


def test_force_start_archives_abandoned_trial_with_reason(tmp_path: Path) -> None:
    state = tmp_path / "active.json"
    study.start_trial(
        state,
        participant="P01",
        experience="experienced",
        condition="manual",
        task="bounded_motion",
        now_epoch=1.0,
    )

    with pytest.raises(ValueError, match="force_reason is required"):
        study.start_trial(
            state,
            participant="P02",
            experience="novice",
            condition="natural",
            task="bounded_motion",
            force=True,
            now_epoch=2.0,
        )

    study.start_trial(
        state,
        participant="P02",
        experience="novice",
        condition="natural",
        task="bounded_motion",
        force=True,
        force_reason="operator stopped the previous trial",
        now_epoch=2.0,
    )

    archives = list(tmp_path.glob("active.abandoned-*.json"))
    assert len(archives) == 1
    abandoned = json.loads(archives[0].read_text(encoding="utf-8"))
    assert abandoned["participant"] == "P01"
    assert abandoned["abandon_reason"] == "operator stopped the previous trial"
    assert state.is_file()


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
    assert summary["conditions"]["slash"]["median_time_s"] == 15.0
    assert summary["conditions"]["slash"]["median_success_time_s"] == 10.0
    assert summary["conditions"]["slash"]["p95_success_time_s"] == 10.0
    assert summary["conditions"]["slash"]["failure_reasons"] == {"unclassified_legacy": 1}
    assert summary["paired"]["manual_vs_slash"] == {
        "pairs": 1,
        "median_speed_ratio": 3.0,
        "ambiguous_repeated_pairs": 0,
        "missing_pairs": 1,
        "failed_pairs": 1,
        "zero_duration_pairs": 0,
    }
    assert summary["paired"]["manual_vs_natural"] == {
        "pairs": 2,
        "median_speed_ratio": 3.0,
        "ambiguous_repeated_pairs": 1,
        "missing_pairs": 0,
        "failed_pairs": 0,
        "zero_duration_pairs": 0,
    }
    rendered = study.render_markdown(summary)
    assert "`slash`: unclassified_legacy=1" in rendered


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


def test_failed_trial_requires_structured_failure_reason(tmp_path: Path) -> None:
    state = tmp_path / "active.json"
    output = tmp_path / "study.jsonl"
    study.start_trial(
        state,
        participant="P01",
        experience="novice",
        condition="natural",
        task="bounded_motion",
        now_epoch=1.0,
    )

    with pytest.raises(ValueError, match="failed trials require failure_reason"):
        study.finish_trial(state, output, success=False, now_epoch=2.0)

    trial = study.finish_trial(
        state,
        output,
        success=False,
        failure_reason="timeout",
        now_epoch=3.0,
    )

    assert trial["failure_reason"] == "timeout"
    assert trial["success"] is False


def test_start_rejects_identifying_participant_name(tmp_path: Path) -> None:
    state = tmp_path / "active.json"
    with pytest.raises(ValueError, match="pseudonym"):
        study.start_trial(
            state,
            participant="Lin Shu-Jen",
            experience="novice",
            condition="slash",
            task="inspect_feedback",
        )
