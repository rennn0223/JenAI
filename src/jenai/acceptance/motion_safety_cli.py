"""Command handler for the observation-only Motion Safety Gate CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jenai.acceptance.motion_safety import (
    assemble_motion_readiness_artifact,
    load_and_validate_motion_readiness,
    write_motion_readiness_artifact,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assemble or independently validate no-motion readiness Evidence."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    assemble = commands.add_parser("assemble", help="derive and persist a readiness artifact")
    assemble.add_argument("--evidence", type=Path, required=True)
    assemble.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser("validate", help="rebuild a readiness verdict offline")
    validate.add_argument("--artifact", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "assemble":
        artifact = assemble_motion_readiness_artifact(args.evidence)
        write_motion_readiness_artifact(artifact, args.output)
        validation = load_and_validate_motion_readiness(args.output)
        print(
            json.dumps(
                {
                    "artifact": str(args.output),
                    "decision": (
                        artifact.result.decision
                        if artifact.result and validation.valid
                        else "BLOCK"
                    ),
                    "valid": validation.valid,
                    "failures": validation.failures,
                },
                sort_keys=True,
            )
        )
        if not validation.valid:
            return 2
        return 0 if artifact.result and artifact.result.decision == "PASS" else 3
    report = load_and_validate_motion_readiness(args.artifact)
    print(report.model_dump_json(indent=2))
    if not report.valid:
        return 2
    return 0 if report.decision == "PASS" else 3
