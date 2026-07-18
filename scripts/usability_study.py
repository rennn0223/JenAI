#!/usr/bin/env python3
"""Record and summarize the JenAI interface-efficiency study.

The recorder does not collect raw prompts, terminal output, camera data, or site information.
Free-form notes are optional and must be sanitized by the study operator.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CONDITIONS = ("manual", "slash", "natural")
EXPERIENCE_LEVELS = ("novice", "experienced")
DEFAULT_TASKS = ("discover_topic_type", "inspect_feedback", "bounded_motion")
REQUIRED_TRIAL_FIELDS = {
    "participant",
    "experience",
    "condition",
    "task",
    "started_at",
    "elapsed_s",
    "success",
    "errors",
    "lookups",
    "interventions",
    "commands",
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _validate_condition(value: str) -> str:
    if value not in CONDITIONS:
        raise ValueError(f"condition must be one of: {', '.join(CONDITIONS)}")
    return value


def _validate_experience(value: str) -> str:
    if value not in EXPERIENCE_LEVELS:
        raise ValueError(f"experience must be one of: {', '.join(EXPERIENCE_LEVELS)}")
    return value


def generate_schedule(
    participants: int, tasks: tuple[str, ...] = DEFAULT_TASKS
) -> list[dict[str, str]]:
    """Return a balanced three-condition schedule with deterministic participant IDs."""

    if participants < 1:
        raise ValueError("participants must be at least 1")
    if not tasks or any(not task.strip() for task in tasks):
        raise ValueError("tasks must contain non-empty names")

    orders = (
        CONDITIONS,
        ("slash", "natural", "manual"),
        ("natural", "manual", "slash"),
    )
    rows: list[dict[str, str]] = []
    for index in range(participants):
        participant = f"P{index + 1:02d}"
        order = orders[index % len(orders)]
        for period, condition in enumerate(order, start=1):
            for task_index in range(len(tasks)):
                # Rotate task order with the condition period so the first task is not fixed.
                rotated = tasks[(task_index + period - 1) % len(tasks)]
                rows.append(
                    {
                        "participant": participant,
                        "period": str(period),
                        "condition": condition,
                        "task_order": str(task_index + 1),
                        "task": rotated,
                    }
                )
    return rows


def write_schedule(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("participant", "period", "condition", "task_order", "task"),
        )
        writer.writeheader()
        writer.writerows(rows)


def start_trial(
    state_path: Path,
    *,
    participant: str,
    experience: str,
    condition: str,
    task: str,
    now_epoch: float | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Persist the active timer; refuse to overwrite a forgotten trial by default."""

    _validate_condition(condition)
    _validate_experience(experience)
    if state_path.exists() and not force:
        raise FileExistsError(f"active trial already exists: {state_path}")
    if not participant.strip() or not task.strip():
        raise ValueError("participant and task must be non-empty")

    state = {
        "participant": participant.strip(),
        "experience": experience,
        "condition": condition,
        "task": task.strip(),
        "started_at": _utc_now(),
        "started_epoch": time.time() if now_epoch is None else now_epoch,
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state


def finish_trial(
    state_path: Path,
    output_path: Path,
    *,
    success: bool,
    errors: int = 0,
    lookups: int = 0,
    interventions: int = 0,
    commands: int = 0,
    notes: str = "",
    now_epoch: float | None = None,
) -> dict[str, Any]:
    """Finish the active timer and append one validated, pseudonymous JSONL record."""

    if not state_path.is_file():
        raise FileNotFoundError(f"no active trial: {state_path}")
    counts = (errors, lookups, interventions, commands)
    if any(value < 0 for value in counts):
        raise ValueError("count metrics cannot be negative")

    state = json.loads(state_path.read_text(encoding="utf-8"))
    ended_epoch = time.time() if now_epoch is None else now_epoch
    elapsed_s = ended_epoch - float(state["started_epoch"])
    if elapsed_s < 0:
        raise ValueError("finish time cannot precede start time")

    trial = {
        "participant": state["participant"],
        "experience": _validate_experience(state["experience"]),
        "condition": _validate_condition(state["condition"]),
        "task": state["task"],
        "started_at": state["started_at"],
        "finished_at": _utc_now(),
        "elapsed_s": round(elapsed_s, 3),
        "success": bool(success),
        "errors": errors,
        "lookups": lookups,
        "interventions": interventions,
        "commands": commands,
        "notes": notes.strip(),
    }
    validate_trial(trial)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(trial, ensure_ascii=False) + "\n")
    state_path.unlink()
    return trial


def validate_trial(trial: dict[str, Any], *, line_number: int | None = None) -> dict[str, Any]:
    missing = REQUIRED_TRIAL_FIELDS - trial.keys()
    prefix = f"line {line_number}: " if line_number is not None else ""
    if missing:
        raise ValueError(f"{prefix}missing fields: {', '.join(sorted(missing))}")
    _validate_condition(str(trial["condition"]))
    _validate_experience(str(trial["experience"]))
    if not isinstance(trial["success"], bool):
        raise ValueError(f"{prefix}success must be a boolean")
    if isinstance(trial["elapsed_s"], bool) or not isinstance(trial["elapsed_s"], (int, float)):
        raise ValueError(f"{prefix}elapsed_s must be a number")
    if trial["elapsed_s"] < 0:
        raise ValueError(f"{prefix}elapsed_s cannot be negative")
    for field in ("errors", "lookups", "interventions", "commands"):
        if isinstance(trial[field], bool) or not isinstance(trial[field], int):
            raise ValueError(f"{prefix}{field} must be an integer")
        if trial[field] < 0:
            raise ValueError(f"{prefix}{field} cannot be negative")
    return trial


def load_trials(path: Path) -> list[dict[str, Any]]:
    trials: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            trial = json.loads(line)
            if not isinstance(trial, dict):
                raise ValueError(f"line {line_number}: trial must be a JSON object")
            trials.append(validate_trial(trial, line_number=line_number))
    if not trials:
        raise ValueError("study file contains no trials")
    return trials


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def summarize_trials(trials: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize conditions and paired successful-task speed ratios without hiding failures."""

    summary: dict[str, Any] = {"conditions": {}, "paired": {}}
    for condition in CONDITIONS:
        group = [trial for trial in trials if trial["condition"] == condition]
        elapsed = [float(trial["elapsed_s"]) for trial in group]
        if not group:
            continue
        successful = [trial for trial in group if bool(trial["success"])]
        summary["conditions"][condition] = {
            "trials": len(group),
            "participants": len({trial["participant"] for trial in group}),
            "successes": len(successful),
            "success_rate": len(successful) / len(group),
            "median_time_s": statistics.median(elapsed),
            "p95_time_s": _p95(elapsed),
            "mean_errors": statistics.mean(int(trial["errors"]) for trial in group),
            "mean_lookups": statistics.mean(int(trial["lookups"]) for trial in group),
            "mean_interventions": statistics.mean(int(trial["interventions"]) for trial in group),
            "mean_commands": statistics.mean(int(trial["commands"]) for trial in group),
        }

    indexed: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for trial in trials:
        key = (trial["participant"], trial["task"], trial["condition"])
        indexed.setdefault(key, []).append(trial)
    participant_tasks = {(trial["participant"], trial["task"]) for trial in trials}
    for condition in ("slash", "natural"):
        ratios: list[float] = []
        ambiguous_repeated_pairs = 0
        for participant, task in participant_tasks:
            manual_group = indexed.get((participant, task, "manual"), [])
            candidate_group = indexed.get((participant, task, condition), [])
            if len(manual_group) > 1 or len(candidate_group) > 1:
                ambiguous_repeated_pairs += 1
                continue
            if len(manual_group) != 1 or len(candidate_group) != 1:
                continue
            manual, candidate = manual_group[0], candidate_group[0]
            if not manual["success"] or not candidate["success"]:
                continue
            candidate_time = float(candidate["elapsed_s"])
            if candidate_time > 0:
                ratios.append(float(manual["elapsed_s"]) / candidate_time)
        summary["paired"][f"manual_vs_{condition}"] = {
            "pairs": len(ratios),
            "median_speed_ratio": statistics.median(ratios) if ratios else None,
            "ambiguous_repeated_pairs": ambiguous_repeated_pairs,
        }
    return summary


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# JenAI usability study summary",
        "",
        "| Condition | Trials | Participants | Success | Median s | P95 s | "
        "Errors | Lookups | Interventions | Commands |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for condition in CONDITIONS:
        row = summary["conditions"].get(condition)
        if not row:
            continue
        lines.append(
            f"| {condition} | {row['trials']} | {row['participants']} | "
            f"{row['success_rate']:.1%} | {row['median_time_s']:.2f} | {row['p95_time_s']:.2f} | "
            f"{row['mean_errors']:.2f} | {row['mean_lookups']:.2f} | "
            f"{row['mean_interventions']:.2f} | {row['mean_commands']:.2f} |"
        )
    lines.extend(["", "## Paired successful-task comparison", ""])
    for name, row in summary["paired"].items():
        ratio = row["median_speed_ratio"]
        rendered = "n/a" if ratio is None else f"{ratio:.2f}×"
        lines.append(
            f"- `{name}`: {row['pairs']} pairs, median manual/candidate time ratio {rendered}; "
            f"{row['ambiguous_repeated_pairs']} repeated participant-task pairs excluded"
        )
    lines.extend(
        [
            "",
            "> Ratios exclude failed tasks and therefore must be reported together "
            "with success rate.",
            "> Do not claim an efficiency improvement from the ratio alone.",
        ]
    )
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    schedule = subparsers.add_parser("schedule", help="create a balanced study schedule")
    schedule.add_argument("--participants", type=int, default=6)
    schedule.add_argument("--out", type=Path, default=Path("usability-schedule.csv"))

    start = subparsers.add_parser("start", help="start one timed trial")
    start.add_argument("--participant", required=True)
    start.add_argument("--experience", required=True, choices=EXPERIENCE_LEVELS)
    start.add_argument("--condition", required=True, choices=CONDITIONS)
    start.add_argument("--task", required=True)
    start.add_argument("--state", type=Path, default=Path(".usability-active.json"))
    start.add_argument("--force", action="store_true")

    finish = subparsers.add_parser("finish", help="finish and append one trial")
    finish.add_argument("--state", type=Path, default=Path(".usability-active.json"))
    finish.add_argument("--out", type=Path, default=Path("usability-study.jsonl"))
    outcome = finish.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--success", action="store_true")
    outcome.add_argument("--failed", action="store_true")
    finish.add_argument("--errors", type=int, default=0)
    finish.add_argument("--lookups", type=int, default=0)
    finish.add_argument("--interventions", type=int, default=0)
    finish.add_argument("--commands", type=int, default=0)
    finish.add_argument("--notes", default="")

    summary = subparsers.add_parser("summary", help="render the JSONL study summary")
    summary.add_argument("--input", type=Path, required=True)
    summary.add_argument("--out", type=Path)
    summary.add_argument("--json", action="store_true")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "schedule":
        rows = generate_schedule(args.participants)
        write_schedule(args.out, rows)
        print(f"Wrote {len(rows)} scheduled trials to {args.out}")
        return
    if args.command == "start":
        state = start_trial(
            args.state,
            participant=args.participant,
            experience=args.experience,
            condition=args.condition,
            task=args.task,
            force=args.force,
        )
        print(f"Started {state['participant']} {state['condition']} {state['task']}")
        return
    if args.command == "finish":
        trial = finish_trial(
            args.state,
            args.out,
            success=args.success and not args.failed,
            errors=args.errors,
            lookups=args.lookups,
            interventions=args.interventions,
            commands=args.commands,
            notes=args.notes,
        )
        print(f"Recorded {trial['elapsed_s']:.3f}s to {args.out}")
        return

    summary = summarize_trials(load_trials(args.input))
    rendered = (
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
        if args.json
        else render_markdown(summary)
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
        print(f"Wrote summary to {args.out}")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
