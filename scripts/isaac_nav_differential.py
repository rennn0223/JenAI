#!/usr/bin/env python3
"""Isaac Nav2／JenAI 差分觀測工具。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from jenai.acceptance.nav_differential_runner import (
    DIFFERENTIAL_EXECUTION_CONFIRMATION,
    DifferentialCaptureOptions,
    DifferentialMode,
    ResetPolicy,
    capture_navigation_differential,
    load_and_compare,
    load_and_validate_live_preflight,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "以相同 canonical goal 觀測 bridge→Nav2 與 "
            "NavigationGateway→Nav2；不調整導航參數或 production 行為。"
        )
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    capture = subcommands.add_parser("capture", help="保存一場 R1 或 R2 的觀測 artifact")
    capture.add_argument("--mode", choices=[item.value for item in DifferentialMode], required=True)
    capture.add_argument("--location", required=True, help="active Site Profile 的儲存地點")
    capture.add_argument("--pair-id", required=True, help="這組 R1/R2 共用的 pair ID")
    capture.add_argument(
        "--simulation-epoch",
        required=True,
        help="同一輪 Isaac Play epoch 的操作員指定識別；Replay 後必須更換",
    )
    capture.add_argument(
        "--reset-policy",
        choices=[item.value for item in ResetPolicy],
        required=True,
    )
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--config", type=Path)
    capture.add_argument(
        "--scene",
        type=Path,
        help="live execution 必須提供存在的 absolute USD path",
    )
    capture.add_argument(
        "--live-scene-sha256",
        help=(
            "操作員從目前 Isaac Stage root layer 另行擷取的 64 字元小寫 SHA-256；--execute 時必填"
        ),
    )
    capture.add_argument(
        "--expected-git-sha",
        help="第二輪 Code Review 通過且要實際驗證的 commit SHA；--execute 時必填",
    )
    capture.add_argument("--calibration", type=Path, help="已驗證的 T_map_world JSON")
    capture.add_argument("--ground-truth-topic")
    capture.add_argument(
        "--ground-truth-type",
        default="geometry_msgs/msg/PoseStamped",
    )
    capture.add_argument("--execute", action="store_true", help="允許模擬車移動")
    capture.add_argument(
        "--live-preflight",
        action="store_true",
        help="執行完整 live T0/T1 gate，但禁止轉送 navigation goal",
    )
    capture.add_argument(
        "--confirm",
        default="",
        help=f"--execute 必須精確提供：{DIFFERENTIAL_EXECUTION_CONFIRMATION}",
    )

    validate_preflight = subcommands.add_parser(
        "validate-preflight",
        help="離線重建一份無移動 live-preflight artifact",
    )
    validate_preflight.add_argument("--artifact", type=Path, required=True)

    compare = subcommands.add_parser("compare", help="離線比較一組 R1/R2 artifact")
    compare.add_argument("--r1", type=Path, required=True)
    compare.add_argument("--r2", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    return parser


def _capture(args: argparse.Namespace) -> int:
    options = DifferentialCaptureOptions(
        output=args.output,
        expected_source_root=_REPOSITORY_ROOT,
        expected_git_sha=args.expected_git_sha,
        location=args.location,
        pair_id=args.pair_id,
        mode=DifferentialMode(args.mode),
        simulation_epoch=args.simulation_epoch,
        reset_policy=ResetPolicy(args.reset_policy),
        config_path=args.config,
        scene_path=args.scene,
        live_scene_sha256=args.live_scene_sha256,
        calibration_path=args.calibration,
        ground_truth_topic=args.ground_truth_topic,
        ground_truth_type=args.ground_truth_type,
        execute=args.execute,
        live_preflight=args.live_preflight,
        confirmation=args.confirm,
    )
    artifact = asyncio.run(capture_navigation_differential(options))
    print(f"{artifact['overall']}: {options.output}")
    return 0 if artifact["overall"] in {"preflight_only", "captured"} else 1


def _validate_preflight(args: argparse.Namespace) -> int:
    report = load_and_validate_live_preflight(args.artifact)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("valid") else 1


def _compare(args: argparse.Namespace) -> int:
    report = load_and_compare(args.r1, args.r2, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("included") else 1


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "capture":
            return _capture(args)
        if args.command == "validate-preflight":
            return _validate_preflight(args)
        return _compare(args)
    except (ValueError, FileExistsError, OSError) as exc:
        print(f"錯誤：{exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
