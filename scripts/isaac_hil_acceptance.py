#!/usr/bin/env python3
"""Manual/self-hosted entry point for auditable Isaac Sim acceptance."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

from jenai.acceptance import EXECUTION_CONFIRMATION, IsaacHilOptions, run_isaac_hil


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight or execute ROS2/Nav2 acceptance against a running Isaac Sim graph. "
            "Execution is opt-in and may move the simulated vehicle."
        )
    )
    parser.add_argument(
        "--goal",
        action="append",
        required=True,
        help="Saved goal name; repeatable",
    )
    parser.add_argument("--cancel-goal", help="Saved goal used to verify cancel + hard stop")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(f"artifacts/isaac-hil-{datetime.now():%Y%m%d-%H%M%S}.json"),
    )
    parser.add_argument("--execute", action="store_true", help="Allow live navigation checks")
    parser.add_argument(
        "--confirm",
        default="",
        help=f"Required with --execute: {EXECUTION_CONFIRMATION}",
    )
    parser.add_argument("--require-twin", action="store_true")
    parser.add_argument("--cancel-after", type=float, default=2.0)
    parser.add_argument("--settle", type=float, default=2.0)
    parser.add_argument("--max-stop-drift", type=float, default=0.05)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    options = IsaacHilOptions(
        output=args.output,
        goals=tuple(args.goal),
        cancel_goal=args.cancel_goal,
        execute=args.execute,
        confirmation=args.confirm,
        cancel_after_s=args.cancel_after,
        settle_s=args.settle,
        max_stop_drift_m=args.max_stop_drift,
        require_twin=args.require_twin,
        overwrite=args.overwrite,
        config_path=args.config,
    )
    try:
        artifact = asyncio.run(run_isaac_hil(options))
    except (ValueError, FileExistsError) as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"{artifact['overall']}: {options.output}")
    return 0 if artifact["overall"] in {"preflight_pass", "pass", "pass_with_skips"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
