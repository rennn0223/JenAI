"""Rebuild the E2 paired A/B/C dataset from a valid legacy full-twin run.

The legacy protocol executed condition C only. Conditions A (no gate) and B
(goal-coordinate forbidden-zone rule) are deterministic, non-driving policies,
so they can be evaluated on the exact same targets without rerunning Nav2.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from e2_ablation import (
    CONDITION_NAMES,
    HOME,
    SCHEMA_VERSION,
    ZONE,
    _git_revision,
    segment_intersects_zone,
    static_verdict,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rebuild(legacy_dir: Path, out_dir: Path) -> dict:
    targets_path = legacy_dir / "targets.json"
    trials_path = legacy_dir / "trials.jsonl"
    targets = json.loads(targets_path.read_text(encoding="utf-8"))
    trials = [
        json.loads(line) for line in trials_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(targets) != len(trials):
        raise ValueError(f"target/trial count mismatch:{len(targets)} != {len(trials)}")
    by_key = {(row["class"], row["x"], row["y"]): row for row in trials}
    if len(by_key) != len(trials):
        raise ValueError("legacy trials contain duplicate class/x/y keys")
    if any(row.get("homed") is not True for row in trials):
        raise ValueError("legacy run contains a trial without a successful HOME reset")
    if any(row.get("verdict") not in {"pass", "block", "refer"} for row in trials):
        raise ValueError("legacy run contains an invalid verdict")

    run_id = f"e2-paired-reanalysis-{datetime.now():%Y%m%dT%H%M%S}"
    rows = []
    for index, target in enumerate(targets, start=1):
        target_id = f"T{index:03d}"
        key = (target["class"], target["x"], target["y"])
        if key not in by_key:
            raise ValueError(f"missing legacy trial for {key}")
        legacy = by_key[key]
        geometry_confirmed = (
            segment_intersects_zone(HOME["x"], HOME["y"], target["x"], target["y"])
            if target["class"] == "zone_crossing"
            else None
        )
        common = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "target_id": target_id,
            "class": target["class"],
            "x": target["x"],
            "y": target["y"],
            "yaw": target["yaw"],
            "valid": True,
            "derived": True,
            "confirmatory": target["class"] != "zone_crossing",
            "geometry_confirmed": geometry_confirmed,
        }
        rows.append(
            common
            | {
                "condition": "A",
                "condition_name": CONDITION_NAMES["A"],
                "verdict": "pass",
                "reason": "",
                "elapsed_s": 0.0,
                "criteria": {},
            }
        )
        verdict, reason, criteria = static_verdict(target)
        rows.append(
            common
            | {
                "condition": "B",
                "condition_name": CONDITION_NAMES["B"],
                "verdict": verdict,
                "reason": reason,
                "elapsed_s": 0.0,
                "criteria": criteria,
            }
        )
        rows.append(
            common
            | {
                "condition": "C",
                "condition_name": CONDITION_NAMES["C"],
                "verdict": legacy["verdict"],
                "reason": legacy.get("reason", ""),
                "elapsed_s": legacy["elapsed_s"],
                "criteria": legacy.get("criteria", {}),
                "homed": True,
                "derived": False,
                "legacy_ts": legacy.get("ts"),
            }
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    out_trials = out_dir / "trials.jsonl"
    if out_trials.exists() and out_trials.stat().st_size:
        raise ValueError(f"output already exists:{out_trials}")
    out_trials.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    (out_dir / "targets.json").write_text(
        json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_revision": _git_revision(),
        "method": "paired offline reanalysis; A/B deterministic, C observed",
        "legacy_dir": str(legacy_dir.resolve()),
        "legacy_targets_sha256": _sha256(targets_path),
        "legacy_trials_sha256": _sha256(trials_path),
        "home": HOME,
        "forbidden_zone": ZONE,
    }
    (out_dir / "run.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"run_id": run_id, "targets": len(targets), "rows": len(rows)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("legacy_dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = rebuild(args.legacy_dir, args.out)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
