"""Auditable Isaac Sim / ROS2 hardware-in-the-loop acceptance runner.

This module deliberately has no scheduled entry point.  A live run can move
the simulated vehicle, so it requires both ``execute=True`` and an exact
operator confirmation.  The ordinary hosted CI suite only tests this runner
with fakes; a manually dispatched self-hosted workflow is the sole automated
path that may reach a live Isaac graph.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import os
import platform
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from jenai import __version__
from jenai.adapters.locations import find_location, load_locations
from jenai.bridge import BridgeError, RosBridgeClient
from jenai.config.models import AppConfig
from jenai.config.store import default_config_path, load_config
from jenai.doctor import run_doctor
from jenai.tools.navigation_gateway import NavigationGateway
from jenai.tools.safety import arm_watchdog, halt_robot

EXECUTION_CONFIRMATION = "I-CONFIRM-ISAAC-SIM-MAY-MOVE"
REQUIRED_NAV_CHECKS = {"ros2_cli", "map", "localization", "laser", "nav2", "cmd_vel"}

# A LaserScan may legitimately contain positive infinity for directions with no
# return. The gate therefore allows two transient blank scans in a ten-scan
# sample and only requires one quarter of all bins to contain finite returns.
# Those deliberately conservative limits still reject a rotating RTX LiDAR
# wedge converted frame-by-frame (observed: 30% blank scans and 18.5% finite
# coverage), which is not a usable full scan for AMCL.
SCAN_TOPIC = "/scan"
SCAN_MESSAGE_TYPE = "sensor_msgs/msg/LaserScan"
SCAN_SAMPLE_COUNT = 10
SCAN_SAMPLE_TIMEOUT_S = 8.0
SCAN_MAX_ALL_INF_SAMPLE_RATIO = 0.20
SCAN_MIN_FINITE_BIN_COVERAGE = 0.25


@dataclass(frozen=True)
class IsaacHilOptions:
    """Inputs that define one reproducible live acceptance run."""

    output: Path
    goals: tuple[str, ...]
    cancel_goal: str | None = None
    execute: bool = False
    confirmation: str = ""
    target: Literal["isaac-sim"] = "isaac-sim"
    cancel_after_s: float = 2.0
    settle_s: float = 2.0
    max_stop_drift_m: float = 0.05
    require_twin: bool = False
    overwrite: bool = False
    config_path: Path | None = None

    def validate(self) -> None:
        if self.require_twin and not self.execute:
            raise ValueError("--require-twin is meaningful only with --execute.")
        if self.execute and self.confirmation != EXECUTION_CONFIRMATION:
            raise ValueError("Live execution requires --confirm " + EXECUTION_CONFIRMATION)
        if not self.goals:
            raise ValueError("At least one --goal is required.")
        if self.cancel_after_s <= 0 or self.settle_s <= 0:
            raise ValueError("cancel_after_s and settle_s must be positive.")
        if self.max_stop_drift_m < 0 or not math.isfinite(self.max_stop_drift_m):
            raise ValueError("max_stop_drift_m must be finite and non-negative.")
        if self.output.exists() and not self.overwrite:
            raise FileExistsError(
                f"Refusing to overwrite existing acceptance artifact: {self.output}"
            )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _config_fingerprint(path: Path) -> str:
    """Identify the tested config without copying credentials into evidence."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_state() -> tuple[str | None, bool | None]:
    """Return the tested Git revision and whether tracked files differ from it."""
    repository = Path(__file__).resolve().parents[3]
    revision = os.environ.get("GITHUB_SHA") or os.environ.get("JENAI_SOURCE_REVISION")
    try:
        if revision is None:
            completed = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repository,
                check=False,
                capture_output=True,
                text=True,
                timeout=3.0,
            )
            if completed.returncode == 0:
                revision = completed.stdout.strip() or None
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        dirty = status.returncode == 0 and bool(status.stdout.strip())
        return revision, dirty if status.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return revision, None


def _check(check_id: str, status: str, *, detail: str = "", **evidence: Any) -> dict:
    return {
        "id": check_id,
        "status": status,
        "detail": detail,
        "evidence": evidence,
    }


def _evaluate_start_pose(pose: Any, config: AppConfig, *, check_id: str = "start_pose") -> dict:
    """Fail closed when the localized start cannot satisfy map-frame zones."""
    coordinates = (pose.x, pose.y, pose.yaw)
    evidence = {
        "pose": {
            "x": pose.x,
            "y": pose.y,
            "yaw": pose.yaw,
            "frame_id": pose.frame_id,
            "source": pose.source,
        },
        "configured_forbidden_zones": [zone.name for zone in config.twin.forbidden_zones],
    }
    if not all(math.isfinite(value) for value in coordinates):
        return _check(
            check_id,
            "fail",
            detail="The localized start pose contains a non-finite value.",
            **evidence,
        )
    if config.twin.forbidden_zones and pose.frame_id != "map":
        return _check(
            check_id,
            "fail",
            detail=("Forbidden zones use the map frame, but the start pose is not map-localized."),
            **evidence,
        )
    hit = next(
        (zone for zone in config.twin.forbidden_zones if zone.contains(pose.x, pose.y)),
        None,
    )
    if hit is not None:
        return _check(
            check_id,
            "fail",
            detail=f"Start pose is inside forbidden zone {hit.name!r}; reset before execution.",
            forbidden_zone=hit.model_dump(mode="json"),
            **evidence,
        )
    return _check(
        check_id,
        "pass",
        detail=(
            f"Finite localized start is outside {len(config.twin.forbidden_zones)} "
            "configured forbidden zone(s)."
        ),
        **evidence,
    )


async def _inspect_start_pose(config: AppConfig) -> dict:
    """Read one pose through the production bridge without sending commands."""
    bridge = RosBridgeClient()
    try:
        await bridge.start()
        pose = await bridge.get_pose(timeout=3.0)
        return _evaluate_start_pose(pose, config)
    except Exception as exc:
        return _check(
            "start_pose",
            "fail",
            detail=f"Could not inspect a localized start pose: {type(exc).__name__}: {exc}",
        )
    finally:
        await bridge.stop()


def _summarize_scan_message(message: Any) -> dict[str, int]:
    """Reduce one LaserScan to counters; never retain its raw ranges."""
    ranges = message.get("ranges") if isinstance(message, dict) else None
    if not isinstance(ranges, (list, tuple)):
        return {
            "bins": 0,
            "finite_bins": 0,
            "positive_inf_bins": 0,
            "nan_bins": 0,
            "negative_inf_bins": 0,
            "malformed_bins": 1,
        }

    summary = {
        "bins": len(ranges),
        "finite_bins": 0,
        "positive_inf_bins": 0,
        "nan_bins": 0,
        "negative_inf_bins": 0,
        "malformed_bins": 0,
    }
    for raw_value in ranges:
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            summary["malformed_bins"] += 1
            continue
        if math.isnan(value):
            summary["nan_bins"] += 1
        elif math.isinf(value):
            key = "positive_inf_bins" if value > 0 else "negative_inf_bins"
            summary[key] += 1
        else:
            summary["finite_bins"] += 1
    return summary


def _evaluate_scan_quality(
    samples: list[dict[str, int]],
    *,
    sample_target: int = SCAN_SAMPLE_COUNT,
    elapsed_s: float,
    topic_available: bool,
    error: str | None = None,
    max_all_inf_sample_ratio: float = SCAN_MAX_ALL_INF_SAMPLE_RATIO,
    min_finite_bin_coverage: float = SCAN_MIN_FINITE_BIN_COVERAGE,
) -> dict:
    """Build an artifact-safe verdict from per-message counters."""
    sample_count = len(samples)
    bins = [sample["bins"] for sample in samples]
    total_bins = sum(bins)
    finite_bins = sum(sample["finite_bins"] for sample in samples)
    positive_inf_bins = sum(sample["positive_inf_bins"] for sample in samples)
    nan_bins = sum(sample["nan_bins"] for sample in samples)
    negative_inf_bins = sum(sample["negative_inf_bins"] for sample in samples)
    malformed_bins = sum(sample["malformed_bins"] for sample in samples)
    empty_samples = sum(count == 0 for count in bins)
    all_inf_samples = sum(
        sample["bins"] > 0 and sample["positive_inf_bins"] == sample["bins"] for sample in samples
    )
    all_inf_sample_ratio = all_inf_samples / sample_count if sample_count else 1.0
    finite_bin_coverage = finite_bins / total_bins if total_bins else 0.0

    failures: list[str] = []
    if not topic_available:
        failures.append("The scan topic could not be subscribed to.")
    if sample_count < sample_target:
        failures.append(f"Received {sample_count}/{sample_target} required scan samples.")
    if empty_samples:
        failures.append(f"{empty_samples} scan sample(s) had no range bins.")
    if nan_bins or negative_inf_bins or malformed_bins:
        failures.append(
            "Scan ranges contained invalid values "
            f"(NaN={nan_bins}, -inf={negative_inf_bins}, malformed={malformed_bins})."
        )
    if all_inf_sample_ratio > max_all_inf_sample_ratio:
        failures.append(
            f"All-infinite scan ratio {all_inf_sample_ratio:.1%} exceeds "
            f"{max_all_inf_sample_ratio:.1%}."
        )
    if finite_bin_coverage < min_finite_bin_coverage:
        failures.append(
            f"Finite-bin coverage {finite_bin_coverage:.1%} is below {min_finite_bin_coverage:.1%}."
        )

    return _check(
        "scan_quality",
        "fail" if failures else "pass",
        detail=(
            " ".join(failures)
            if failures
            else (
                f"Received {sample_count} usable full-scan samples with "
                f"{finite_bin_coverage:.1%} finite-bin coverage."
            )
        ),
        topic=SCAN_TOPIC,
        message_type=SCAN_MESSAGE_TYPE,
        topic_available=topic_available,
        error=error,
        elapsed_s=round(elapsed_s, 3),
        sample_target=sample_target,
        samples_received=sample_count,
        range_bins={
            "minimum_per_sample": min(bins, default=0),
            "maximum_per_sample": max(bins, default=0),
            "total": total_bins,
        },
        finite_bins=finite_bins,
        positive_inf_bins=positive_inf_bins,
        nan_bins=nan_bins,
        negative_inf_bins=negative_inf_bins,
        malformed_bins=malformed_bins,
        empty_samples=empty_samples,
        all_inf_samples=all_inf_samples,
        all_inf_sample_ratio=round(all_inf_sample_ratio, 6),
        finite_bin_coverage=round(finite_bin_coverage, 6),
        thresholds={
            "max_all_inf_sample_ratio": max_all_inf_sample_ratio,
            "min_finite_bin_coverage": min_finite_bin_coverage,
        },
    )


async def _inspect_scan_quality(
    *,
    sample_count: int = SCAN_SAMPLE_COUNT,
    timeout_s: float = SCAN_SAMPLE_TIMEOUT_S,
    max_all_inf_sample_ratio: float = SCAN_MAX_ALL_INF_SAMPLE_RATIO,
    min_finite_bin_coverage: float = SCAN_MIN_FINITE_BIN_COVERAGE,
) -> dict:
    """Sample the live LaserScan before any navigation goal can be sent."""
    bridge = RosBridgeClient()
    samples: list[dict[str, int]] = []
    complete = asyncio.Event()
    watch_id: int | None = None
    topic_available = False
    error: str | None = None
    started = time.perf_counter()

    def on_scan(message: dict) -> None:
        if len(samples) >= sample_count:
            return
        samples.append(_summarize_scan_message(message))
        if len(samples) >= sample_count:
            complete.set()

    try:
        await bridge.start()
        watch_id = await bridge.watch(
            SCAN_TOPIC,
            SCAN_MESSAGE_TYPE,
            on_scan,
            throttle=0.0,
        )
        topic_available = True
        try:
            await asyncio.wait_for(complete.wait(), timeout_s)
        except TimeoutError:
            error = f"Timed out after {timeout_s:.1f}s waiting for scan samples."
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if watch_id is not None:
            with contextlib.suppress(BridgeError):
                await bridge.unwatch(watch_id)
        await bridge.stop()

    return _evaluate_scan_quality(
        samples,
        sample_target=sample_count,
        elapsed_s=time.perf_counter() - started,
        topic_available=topic_available,
        error=error,
        max_all_inf_sample_ratio=max_all_inf_sample_ratio,
        min_finite_bin_coverage=min_finite_bin_coverage,
    )


def _overall(checks: list[dict], *, executed: bool) -> str:
    if any(item["status"] == "fail" for item in checks):
        return "fail"
    if not executed:
        return "preflight_pass"
    if any(item["status"] == "skip" for item in checks):
        return "pass_with_skips"
    return "pass"


def _execution_config(config: AppConfig, target: str, ambient_domain: str) -> AppConfig:
    """Return the config used to command the target, without mutating disk.

    When Isaac Sim itself is the target, rehearsing in the same graph would
    command it twice and could be mistaken for deployment isolation.  The live
    route checks therefore bypass only the *optional twin rehearsal* in memory;
    watchdog, NavigationGateway, Nav2 and hard stop remain active.  The artifact
    records this fact and the independent Twin check stays skipped/failed.
    """
    if (
        target == "isaac-sim"
        and config.twin.enabled
        and str(config.twin.domain_id) == ambient_domain
    ):
        return config.model_copy(update={"twin": config.twin.model_copy(update={"enabled": False})})
    return config


def _doctor_checks(
    config_path: Path,
    *,
    attempts: int = 3,
    retry_delay_s: float = 0.5,
) -> tuple[list[dict], bool, dict]:
    history: list[dict] = []
    items: list[dict] = []
    doctor = None
    failed: list[dict] = []
    missing: list[str] = []
    passed = False
    for attempt in range(1, max(1, attempts) + 1):
        doctor = run_doctor(config_path)
        items = [item.model_dump(mode="json") for item in doctor.items]
        by_name = {
            item["check_name"]: item for item in items if item["check_name"] in REQUIRED_NAV_CHECKS
        }
        missing = sorted(REQUIRED_NAV_CHECKS - set(by_name))
        failed = [item for item in by_name.values() if item["status"] != "pass"]
        passed = not failed and not missing
        history.append(
            {
                "attempt": attempt,
                "doctor_overall": doctor.overall,
                "non_passing_required": failed,
                "missing_required": missing,
            }
        )
        if passed or attempt >= max(1, attempts):
            break
        time.sleep(max(0.0, retry_delay_s))
    assert doctor is not None
    return (
        items,
        passed,
        {
            "doctor_overall": doctor.overall,
            "required_checks": sorted(REQUIRED_NAV_CHECKS),
            "non_passing_required": failed,
            "missing_required": missing,
            "attempts": history,
        },
    )


async def _run_live(
    config: AppConfig,
    locations,
    options: IsaacHilOptions,
) -> list[dict]:
    checks: list[dict] = []
    bridge = RosBridgeClient()
    ambient_domain = os.environ.get("ROS_DOMAIN_ID", "0").strip() or "0"
    execution_config = _execution_config(config, options.target, ambient_domain)
    gateway = NavigationGateway(execution_config, get_bridge=lambda: _ready_bridge(bridge))

    try:
        await arm_watchdog(execution_config, bridge)
        await bridge.start()
        pose = await bridge.get_pose(timeout=3.0)
        checks.append(
            _check(
                "live_bridge",
                "pass",
                detail="ROS bridge started with watchdog armed.",
                pose={
                    "x": pose.x,
                    "y": pose.y,
                    "yaw": pose.yaw,
                    "frame_id": pose.frame_id,
                    "source": pose.source,
                },
            )
        )

        start_pose_check = _evaluate_start_pose(pose, config, check_id="start_pose_recheck")
        checks.append(start_pose_check)
        if start_pose_check["status"] != "pass":
            return checks

        if config.twin.enabled:
            isolated = str(config.twin.domain_id) != ambient_domain
            if isolated:
                checks.append(
                    _check(
                        "twin_isolation",
                        "pass",
                        detail="Twin and target ROS domains are distinct.",
                        target_domain=ambient_domain,
                        twin_domain=config.twin.domain_id,
                    )
                )
            else:
                status = "fail" if options.require_twin else "skip"
                checks.append(
                    _check(
                        "twin_isolation",
                        status,
                        detail=(
                            "Pure-simulation target shares the configured Twin domain; "
                            "this run does not claim deployment isolation or a Twin verdict."
                        ),
                        target_domain=ambient_domain,
                        twin_domain=config.twin.domain_id,
                    )
                )
        else:
            status = "fail" if options.require_twin else "skip"
            checks.append(
                _check(
                    "twin_isolation",
                    status,
                    detail="Twin Gate is disabled; no live Twin verdict was produced.",
                )
            )
        if options.require_twin and not execution_config.twin.enabled:
            return checks

        for goal_name in options.goals:
            goal = find_location(locations, goal_name)
            action = {"goal": goal.model_dump(mode="json")}
            progress: list[dict] = []
            gate_reports: list[dict] = []
            started = time.perf_counter()
            result = await gateway.execute(
                action,
                on_progress=lambda item, progress=progress: progress.append(
                    {
                        "distance_remaining": item.distance_remaining,
                        "recoveries": item.recoveries,
                        "elapsed": item.elapsed,
                    }
                ),
                on_gate_report=lambda report, reports=gate_reports: reports.append(
                    report.model_dump(mode="json")
                ),
            )
            checks.append(
                _check(
                    f"route:{goal_name}",
                    "pass" if result.execution_status == "succeeded" else "fail",
                    detail=result.route_preview,
                    execution_status=result.execution_status,
                    elapsed_s=round(time.perf_counter() - started, 3),
                    progress_samples=progress,
                    gate_reports=gate_reports,
                )
            )
            if execution_config.twin.enabled:
                report = gate_reports[-1] if gate_reports else None
                checks.append(
                    _check(
                        f"twin_verdict:{goal_name}",
                        "pass" if report and report["verdict"] == "pass" else "fail",
                        detail=(
                            f"Twin Gate returned {report['verdict']}."
                            if report
                            else "Twin Gate did not return a structured verdict."
                        ),
                        report=report,
                    )
                )

        if options.cancel_goal:
            # Cancellation must exercise the target Nav2 goal, not merely stop
            # an in-progress rehearsal in a separate Twin domain.
            cancel_config = execution_config.model_copy(
                update={"twin": execution_config.twin.model_copy(update={"enabled": False})}
            )
            cancel_gateway = NavigationGateway(
                cancel_config, get_bridge=lambda: _ready_bridge(bridge)
            )
            checks.append(
                await _run_cancel_and_stop(
                    cancel_gateway,
                    bridge,
                    cancel_config,
                    find_location(locations, options.cancel_goal),
                    options,
                )
            )
    except Exception as exc:
        # The broad catch is intentional at this evidence boundary: unexpected
        # failures must be serialized into the artifact before the process exits.
        checks.append(
            _check(
                "live_exception",
                "fail",
                detail=f"{type(exc).__name__}: {exc}",
            )
        )
    finally:
        with contextlib.suppress(BridgeError):
            await halt_robot(execution_config, bridge)
        await bridge.stop()
    return checks


async def _ready_bridge(bridge: RosBridgeClient) -> RosBridgeClient:
    if not bridge.running:
        await bridge.start()
    return bridge


async def _run_cancel_and_stop(
    gateway: NavigationGateway,
    bridge: RosBridgeClient,
    config: AppConfig,
    goal,
    options: IsaacHilOptions,
) -> dict:
    progress: list[dict] = []

    def on_progress(item) -> None:
        progress.append(
            {
                "distance_remaining": item.distance_remaining,
                "recoveries": item.recoveries,
                "elapsed": item.elapsed,
            }
        )

    action = {"goal": goal.model_dump(mode="json")}
    task = asyncio.create_task(gateway.execute(action, on_progress=on_progress))
    started = time.perf_counter()
    try:
        await asyncio.sleep(options.cancel_after_s)
        if task.done():
            result = await task
            return _check(
                f"cancel_stop:{goal.name}",
                "fail",
                detail="Navigation completed before cancellation could be exercised.",
                execution_status=result.execution_status,
                progress_samples=progress,
            )

        pose_before = await bridge.get_pose(timeout=3.0)
        task.cancel()
        canceled = False
        try:
            await task
        except asyncio.CancelledError:
            canceled = True
        halt_message = await halt_robot(config, bridge)
        await asyncio.sleep(options.settle_s)
        pose_after = await bridge.get_pose(timeout=3.0)
        drift = math.hypot(pose_after.x - pose_before.x, pose_after.y - pose_before.y)
        passed = canceled and drift <= options.max_stop_drift_m
        return _check(
            f"cancel_stop:{goal.name}",
            "pass" if passed else "fail",
            detail=(
                f"Cancellation propagated and post-stop drift was {drift:.4f} m."
                if passed
                else f"Cancel/stop acceptance failed; post-stop drift was {drift:.4f} m."
            ),
            task_cancelled=canceled,
            halt_message=halt_message,
            drift_m=round(drift, 6),
            max_stop_drift_m=options.max_stop_drift_m,
            elapsed_s=round(time.perf_counter() - started, 3),
            progress_samples=progress,
        )
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def run_isaac_hil(options: IsaacHilOptions) -> dict:
    """Run preflight or live acceptance and always persist one JSON artifact."""
    options.validate()
    config_path = options.config_path or default_config_path()
    started_at = _utc_now()
    source_revision, source_dirty = _source_state()
    checks: list[dict] = []
    artifact: dict[str, Any] = {
        "schema_version": 1,
        "run_id": f"isaac-hil-{datetime.now():%Y%m%dT%H%M%S}-{uuid4().hex[:6]}",
        "started_at": started_at,
        "target": options.target,
        "execution_requested": options.execute,
        "environment": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "jenai_version": __version__,
            "source_revision": source_revision,
            "source_dirty": source_dirty,
            "ros_domain_id": os.environ.get("ROS_DOMAIN_ID", "0"),
        },
        "requested_goals": list(options.goals),
        "cancel_goal": options.cancel_goal,
        "checks": checks,
    }

    try:
        config = load_config(config_path)
        locations_path = config.resolved_locations_path(config_path)
        if locations_path is None:
            raise ValueError("No locations_path is configured.")
        locations = load_locations(locations_path)
        requested = set(options.goals)
        if options.cancel_goal:
            requested.add(options.cancel_goal)
        for name in sorted(requested):
            find_location(locations, name)

        artifact["configuration"] = {
            "config_file": config_path.name,
            "sha256": _config_fingerprint(config_path),
            "route_adapter": config.route_adapter,
            "vehicle_type": config.vehicle.type,
            "cmd_vel_topic": config.vehicle.cmd_vel_topic,
            "twin_enabled": config.twin.enabled,
            "twin_domain_id": config.twin.domain_id,
            "locations_file": locations_path.name,
        }
        doctor_items, doctor_ok, doctor_evidence = _doctor_checks(config_path)
        artifact["doctor"] = doctor_items
        checks.append(
            _check(
                "preflight",
                "pass" if doctor_ok else "fail",
                detail=(
                    "Required ROS/Nav2 checks passed."
                    if doctor_ok
                    else "One or more required ROS/Nav2 checks did not pass."
                ),
                **doctor_evidence,
            )
        )
        scan_quality_ok = False
        start_pose_ok = False
        if doctor_ok:
            scan_quality_check = await _inspect_scan_quality()
            checks.append(scan_quality_check)
            scan_quality_ok = scan_quality_check["status"] == "pass"
            if scan_quality_ok:
                start_pose_check = await _inspect_start_pose(config)
                checks.append(start_pose_check)
                start_pose_ok = start_pose_check["status"] == "pass"
        if options.execute and doctor_ok and scan_quality_ok and start_pose_ok:
            checks.extend(await _run_live(config, locations, options))
        elif options.execute:
            checks.append(
                _check(
                    "live_execution",
                    "fail",
                    detail="Live execution was withheld because preflight failed.",
                )
            )
    except Exception as exc:
        checks.append(_check("setup", "fail", detail=f"{type(exc).__name__}: {exc}"))
    finally:
        artifact["finished_at"] = _utc_now()
        artifact["overall"] = _overall(checks, executed=options.execute)
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return artifact
