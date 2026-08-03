"""Command handler for the observation-only Motion Safety Gate CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from jenai.acceptance.motion_safety import (
    assemble_motion_readiness_artifact,
    load_and_validate_motion_readiness,
    write_bytes_create_once,
    write_motion_readiness_artifact,
)
from jenai.acceptance.motion_safety_capture import (
    IsaacMotionReadinessCollector,
    load_and_validate_blocked_collection,
    load_blocked_collection_artifact,
)
from jenai.acceptance.motion_safety_isaac import (
    IsaacRosReadOnlyEvidenceSource,
    RepositoryIsaacReadOnlyTransport,
    prepare_reviewed_stage_export_bundle,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assemble or independently validate no-motion readiness Evidence."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser(
        "capture", help="collect bounded no-motion Evidence through an Isaac probe"
    )
    capture.add_argument("--config", type=Path, required=True)
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--timeout-s", type=float, default=5.0)
    prepare = commands.add_parser(
        "prepare-stage-export",
        help="freeze the reviewed source closure for the in-Isaac Stage exporter",
    )
    prepare.add_argument("--config", type=Path, required=True)
    prepare.add_argument("--source-bundle", type=Path, required=True)
    assemble = commands.add_parser("assemble", help="derive and persist a readiness artifact")
    assemble.add_argument("--evidence", type=Path, required=True)
    assemble.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser("validate", help="rebuild a readiness verdict offline")
    validate.add_argument("--artifact", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "prepare-stage-export":
        prepared = prepare_reviewed_stage_export_bundle(args.config, args.source_bundle)
        print(json.dumps(prepared, sort_keys=True))
        return 0
    if args.command == "capture":
        process_timeout_s = args.timeout_s * 6.0 + 0.1
        transport = RepositoryIsaacReadOnlyTransport(
            config_path=args.config,
            timeout_s=process_timeout_s,
        )
        outcome = asyncio.run(
            IsaacMotionReadinessCollector(
                source=IsaacRosReadOnlyEvidenceSource(transport),
                operation_timeout_s=process_timeout_s + 0.1,
            ).collect()
        )
        payload = outcome.artifact
        write_bytes_create_once(
            args.output,
            (payload.model_dump_json(indent=2) + "\n").encode(),
        )
        print(
            json.dumps(
                {
                    "capture": str(args.output),
                    "status": outcome.status,
                    "failures": [failure.model_dump(mode="json") for failure in outcome.failures],
                },
                sort_keys=True,
            )
        )
        return 0 if outcome.status == "captured" else 3
    if args.command == "assemble":
        try:
            blocked_artifact = load_blocked_collection_artifact(args.evidence)
        except ValueError:
            blocked_artifact = None
        if blocked_artifact is not None:
            write_bytes_create_once(
                args.output,
                (blocked_artifact.model_dump_json(indent=2) + "\n").encode(),
            )
            report = load_and_validate_blocked_collection(args.output)
            print(report.model_dump_json(indent=2))
            return 3 if report.valid else 2
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
    try:
        load_blocked_collection_artifact(args.artifact)
    except ValueError:
        report = load_and_validate_motion_readiness(args.artifact)
    else:
        report = load_and_validate_blocked_collection(args.artifact)
    print(report.model_dump_json(indent=2))
    if not report.valid:
        return 2
    return 0 if report.decision == "PASS" else 3
