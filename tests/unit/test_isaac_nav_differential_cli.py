from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts" / "isaac_nav_differential.py"


def test_validate_preflight_cli_fails_closed_for_invalid_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "invalid.json"
    artifact.write_text("{}", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(CLI), "validate-preflight", "--artifact", str(artifact)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1, completed.stderr
    assert json.loads(completed.stdout) == {
        "schema_version": 1,
        "valid": False,
        "failures": ["artifact_load"],
    }


def test_capture_cli_does_not_offer_artifact_overwrite() -> None:
    completed = subprocess.run(
        [sys.executable, str(CLI), "capture", "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--overwrite" not in completed.stdout
