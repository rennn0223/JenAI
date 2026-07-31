from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts" / "isaac_nav_differential.py"


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
