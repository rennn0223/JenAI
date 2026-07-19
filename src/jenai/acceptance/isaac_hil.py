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
from collections.abc import Callable
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
from jenai.tools.nav_live import NavigationCancelled
from jenai.tools.navigation_gateway import NavigationGateway
from jenai.tools.safety import arm_watchdog, halt_robot

EXECUTION_CONFIRMATION = "I-CONFIRM-ISAAC-SIM-MAY-MOVE"
REQUIRED_NAV_CHECKS = {"ros2_cli", "map", "localization", "laser", "nav2", "cmd_vel"}

# A LaserScan may legitimately contain positive infinity for directions with no
# return. The gate therefore allows two transient blank scans in a ten-scan
# sample and only requires one quarter of all bins to contain finite returns.
# Those deliberately conservative limits still reject a rotating RTX LiDAR
# wedge converted frame-by-frame (observed: 30% blank scans and 18.5% finite
# coverage), which is not a usable wide-field LaserScan for AMCL.
SCAN_TOPIC = "/scan"
SCAN_MESSAGE_TYPE = "sensor_msgs/msg/LaserScan"
SCAN_SAMPLE_COUNT = 10
SCAN_SAMPLE_TIMEOUT_S = 8.0
SCAN_MAX_ALL_INF_SAMPLE_RATIO = 0.20
SCAN_MIN_FINITE_BIN_COVERAGE = 0.25
SCAN_MIN_ANGULAR_SPAN_RAD = 3.0
SCAN_MIN_RANGE_BINS = 180
SCAN_GEOMETRY_TOLERANCE_RAD = 0.02
SCAN_MAX_SCAN_TIME_S = 1.0

# Live Nav2 feedback can arrive hundreds of times per second. Evidence needs a
# representative trace rather than a lossless transport log.
PROGRESS_SAMPLE_INTERVAL_S = 0.5
PROGRESS_DISTANCE_JUMP_M = 1.0
PROGRESS_SAMPLE_LIMIT = 512


class _ProgressSampler:
    """Thin and bound progress evidence without affecting navigation."""

    def __init__(
        self,
        *,
        interval_s: float = PROGRESS_SAMPLE_INTERVAL_S,
        distance_jump_m: float = PROGRESS_DISTANCE_JUMP_M,
        max_samples: int = PROGRESS_SAMPLE_LIMIT,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if interval_s < 0 or not math.isfinite(interval_s):
            raise ValueError("interval_s must be finite and non-negative")
        if distance_jump_m < 0 or not math.isfinite(distance_jump_m):
            raise ValueError("distance_jump_m must be finite and non-negative")
        if max_samples < 2:
            raise ValueError("max_samples must preserve the first and final samples")
        self._interval_s = interval_s
        self._distance_jump_m = distance_jump_m
        self._max_samples = max_samples
        self._clock = clock
        self._samples: list[dict[str, Any]] = []
        self._important: list[bool] = []
        self._last_observed: dict[str, Any] | None = None
        self._last_emitted_at: float | None = None

    def record(self, item: Any) -> None:
        """Observe feedback, retaining periodic or materially changed values."""
        payload = {
            "distance_remaining": item.distance_remaining,
            "recoveries": item.recoveries,
            "elapsed": item.elapsed,
        }
        status = getattr(item, "status", None)
        if status is not None:
            payload["status"] = str(status)

        now = self._clock()
        important = self._is_important(payload)
        self._last_observed = payload
        due = self._last_emitted_at is None or now - self._last_emitted_at >= self._interval_s
        if important or due:
            self._append(payload, now, important=important)

    def finish(self, *, status: str) -> list[dict[str, Any]]:
        """Return evidence with the latest feedback and terminal status last."""
        payload = dict(self._last_observed or {})
        payload["status"] = status
        if not self._samples or self._samples[-1] != payload:
            self._append(payload, self._clock(), important=True)
        return [dict(sample) for sample in self._samples]

    def _is_important(self, payload: dict[str, Any]) -> bool:
        previous = self._last_observed
        if previous is None:
            return True
        if payload.get("status") != previous.get("status"):
            return True
        if payload["recoveries"] != previous["recoveries"]:
            return True
        previous_distance = float(previous["distance_remaining"])
        distance = float(payload["distance_remaining"])
        if math.isfinite(previous_distance) and math.isfinite(distance):
            return abs(distance - previous_distance) >= self._distance_jump_m
        return math.isfinite(previous_distance) != math.isfinite(distance)

    def _append(self, payload: dict[str, Any], now: float, *, important: bool) -> None:
        self._samples.append(dict(payload))
        self._important.append(important)
        self._last_emitted_at = now
        if len(self._samples) <= self._max_samples:
            return

        # Preserve both endpoints and prefer dropping the oldest routine point.
        # Important changes are only thinned if a pathological stream fills the
        # entire bound with them; the bound remains absolute in every case.
        remove_at = next(
            (index for index in range(1, len(self._samples) - 1) if not self._important[index]),
            1,
        )
        del self._samples[remove_at]
        del self._important[remove_at]


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


def _scan_float(message: dict[str, Any], key: str) -> float | None:
    raw_value = message.get(key)
    if isinstance(raw_value, bool):
        return None
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _scan_stamp_ns(message: dict[str, Any]) -> int | None:
    header = message.get("header")
    stamp = header.get("stamp") if isinstance(header, dict) else None
    if not isinstance(stamp, dict):
        return None
    seconds = stamp.get("sec")
    nanoseconds = stamp.get("nanosec")
    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, int)
        or isinstance(nanoseconds, bool)
        or not isinstance(nanoseconds, int)
        or seconds < 0
        or not 0 <= nanoseconds < 1_000_000_000
    ):
        return None
    return seconds * 1_000_000_000 + nanoseconds


def _summarize_scan_message(message: Any) -> dict[str, Any]:
    """Reduce one LaserScan to metadata and counters; never retain raw ranges."""
    scan = message if isinstance(message, dict) else {}
    header = scan.get("header")
    frame_id = header.get("frame_id") if isinstance(header, dict) else ""
    frame_id = frame_id.strip() if isinstance(frame_id, str) else ""
    angle_min = _scan_float(scan, "angle_min")
    angle_max = _scan_float(scan, "angle_max")
    angle_increment = _scan_float(scan, "angle_increment")
    range_min = _scan_float(scan, "range_min")
    range_max = _scan_float(scan, "range_max")
    scan_time = _scan_float(scan, "scan_time")
    angle_span = angle_max - angle_min if angle_min is not None and angle_max is not None else None
    range_bounds_valid = (
        range_min is not None and range_max is not None and 0 <= range_min < range_max
    )
    ranges = scan.get("ranges")
    summary: dict[str, Any] = {
        "bins": len(ranges) if isinstance(ranges, (list, tuple)) else 0,
        "finite_bins": 0,
        "positive_inf_bins": 0,
        "nan_bins": 0,
        "negative_inf_bins": 0,
        "malformed_bins": 0 if isinstance(ranges, (list, tuple)) else 1,
        "out_of_range_bins": 0,
        "angle_span": angle_span,
        "angle_increment": angle_increment,
        "range_min": range_min,
        "range_max": range_max,
        "range_bounds_valid": range_bounds_valid,
        "scan_time": scan_time,
        "frame_id": frame_id,
        "stamp_ns": _scan_stamp_ns(scan),
        "geometry_error": None,
    }
    if not isinstance(ranges, (list, tuple)):
        return summary

    if angle_span is not None and angle_increment is not None and angle_increment > 0:
        expected_span = max(0, len(ranges) - 1) * angle_increment
        summary["geometry_error"] = abs(angle_span - expected_span)

    for raw_value in ranges:
        if isinstance(raw_value, bool):
            summary["malformed_bins"] += 1
            continue
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
            if range_bounds_valid and not range_min <= value <= range_max:
                summary["out_of_range_bins"] += 1
    return summary


def _evaluate_scan_quality(
    samples: list[dict[str, Any]],
    *,
    sample_target: int = SCAN_SAMPLE_COUNT,
    elapsed_s: float,
    topic_available: bool,
    error: str | None = None,
    max_all_inf_sample_ratio: float = SCAN_MAX_ALL_INF_SAMPLE_RATIO,
    min_finite_bin_coverage: float = SCAN_MIN_FINITE_BIN_COVERAGE,
) -> dict:
    """Build an artifact-safe verdict from per-message metadata and counters."""
    sample_count = len(samples)
    bins = [sample["bins"] for sample in samples]
    total_bins = sum(bins)
    finite_bins = sum(sample["finite_bins"] for sample in samples)
    positive_inf_bins = sum(sample["positive_inf_bins"] for sample in samples)
    nan_bins = sum(sample["nan_bins"] for sample in samples)
    negative_inf_bins = sum(sample["negative_inf_bins"] for sample in samples)
    malformed_bins = sum(sample["malformed_bins"] for sample in samples)
    out_of_range_bins = sum(sample["out_of_range_bins"] for sample in samples)
    valid_finite_bins = finite_bins - out_of_range_bins
    empty_samples = sum(count == 0 for count in bins)
    undersized_samples = sum(count < SCAN_MIN_RANGE_BINS for count in bins)
    all_inf_samples = sum(
        sample["bins"] > 0 and sample["positive_inf_bins"] == sample["bins"] for sample in samples
    )
    all_inf_sample_ratio = all_inf_samples / sample_count if sample_count else 1.0
    finite_bin_coverage = valid_finite_bins / total_bins if total_bins else 0.0

    angle_spans = [sample["angle_span"] for sample in samples if sample["angle_span"] is not None]
    increments = [
        sample["angle_increment"] for sample in samples if sample["angle_increment"] is not None
    ]
    scan_times = [sample["scan_time"] for sample in samples if sample["scan_time"] is not None]
    geometry_errors = [
        sample["geometry_error"] for sample in samples if sample["geometry_error"] is not None
    ]
    invalid_angle_samples = sum(
        sample["angle_span"] is None or sample["angle_span"] < SCAN_MIN_ANGULAR_SPAN_RAD
        for sample in samples
    )
    invalid_increment_samples = sum(
        sample["angle_increment"] is None or sample["angle_increment"] <= 0 for sample in samples
    )
    invalid_geometry_samples = sum(
        sample["geometry_error"] is None or sample["geometry_error"] > SCAN_GEOMETRY_TOLERANCE_RAD
        for sample in samples
    )
    invalid_range_bound_samples = sum(not sample["range_bounds_valid"] for sample in samples)
    invalid_scan_time_samples = sum(
        sample["scan_time"] is None or not 0 < sample["scan_time"] <= SCAN_MAX_SCAN_TIME_S
        for sample in samples
    )
    frame_ids = sorted({sample["frame_id"] for sample in samples if sample["frame_id"]})
    missing_frame_samples = sum(not sample["frame_id"] for sample in samples)
    stamps = [sample["stamp_ns"] for sample in samples]
    missing_stamp_samples = sum(stamp is None for stamp in stamps)
    nonadvancing_stamp_pairs = sum(
        previous is not None and current is not None and current <= previous
        for previous, current in zip(stamps, stamps[1:], strict=False)
    )

    failures: list[str] = []
    if not topic_available:
        failures.append("The scan topic could not be subscribed to.")
    if sample_count < sample_target:
        failures.append(f"Received {sample_count}/{sample_target} required scan samples.")
    if empty_samples:
        failures.append(f"{empty_samples} scan sample(s) had no range bins.")
    if undersized_samples:
        failures.append(
            f"{undersized_samples} scan sample(s) had fewer than {SCAN_MIN_RANGE_BINS} bins."
        )
    if nan_bins or negative_inf_bins or malformed_bins or out_of_range_bins:
        failures.append(
            "Scan ranges contained invalid values "
            f"(NaN={nan_bins}, -inf={negative_inf_bins}, malformed={malformed_bins}, "
            f"out_of_range={out_of_range_bins})."
        )
    if invalid_angle_samples:
        failures.append(
            f"{invalid_angle_samples} scan sample(s) did not cover at least "
            f"{SCAN_MIN_ANGULAR_SPAN_RAD:.1f} rad."
        )
    if invalid_increment_samples:
        failures.append(
            f"{invalid_increment_samples} scan sample(s) lacked a positive finite angle increment."
        )
    if invalid_geometry_samples:
        failures.append(
            f"{invalid_geometry_samples} scan sample(s) had inconsistent angle/bin geometry."
        )
    if invalid_range_bound_samples:
        failures.append(f"{invalid_range_bound_samples} scan sample(s) had invalid range bounds.")
    if invalid_scan_time_samples:
        failures.append(
            f"{invalid_scan_time_samples} scan sample(s) had an unreasonable scan_time."
        )
    if missing_frame_samples or len(frame_ids) != 1:
        failures.append(
            "Scan frame_id was missing or changed across samples "
            f"(missing={missing_frame_samples}, frames={frame_ids})."
        )
    if missing_stamp_samples or nonadvancing_stamp_pairs:
        failures.append(
            "Scan timestamps were missing or did not strictly advance "
            f"(missing={missing_stamp_samples}, nonadvancing={nonadvancing_stamp_pairs})."
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

    timestamp_span_s = None
    if stamps and all(stamp is not None for stamp in stamps):
        timestamp_span_s = (stamps[-1] - stamps[0]) / 1_000_000_000

    return _check(
        "scan_quality",
        "fail" if failures else "pass",
        detail=(
            " ".join(failures)
            if failures
            else (
                f"Received {sample_count} usable wide-field LaserScan samples with "
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
        valid_finite_bins=valid_finite_bins,
        positive_inf_bins=positive_inf_bins,
        nan_bins=nan_bins,
        negative_inf_bins=negative_inf_bins,
        malformed_bins=malformed_bins,
        out_of_range_bins=out_of_range_bins,
        empty_samples=empty_samples,
        undersized_samples=undersized_samples,
        all_inf_samples=all_inf_samples,
        all_inf_sample_ratio=round(all_inf_sample_ratio, 6),
        finite_bin_coverage=round(finite_bin_coverage, 6),
        metadata={
            "frame_ids": frame_ids,
            "missing_frame_samples": missing_frame_samples,
            "missing_stamp_samples": missing_stamp_samples,
            "nonadvancing_stamp_pairs": nonadvancing_stamp_pairs,
            "timestamp_span_s": timestamp_span_s,
            "angle_span_rad": {
                "minimum": min(angle_spans, default=None),
                "maximum": max(angle_spans, default=None),
                "invalid_samples": invalid_angle_samples,
            },
            "angle_increment_rad": {
                "minimum": min(increments, default=None),
                "maximum": max(increments, default=None),
                "invalid_samples": invalid_increment_samples,
            },
            "geometry_error_rad": {
                "maximum": max(geometry_errors, default=None),
                "invalid_samples": invalid_geometry_samples,
            },
            "invalid_range_bound_samples": invalid_range_bound_samples,
            "scan_time_s": {
                "minimum": min(scan_times, default=None),
                "maximum": max(scan_times, default=None),
                "invalid_samples": invalid_scan_time_samples,
            },
        },
        thresholds={
            "max_all_inf_sample_ratio": max_all_inf_sample_ratio,
            "min_finite_bin_coverage": min_finite_bin_coverage,
            "min_angular_span_rad": SCAN_MIN_ANGULAR_SPAN_RAD,
            "min_range_bins": SCAN_MIN_RANGE_BINS,
            "geometry_tolerance_rad": SCAN_GEOMETRY_TOLERANCE_RAD,
            "max_scan_time_s": SCAN_MAX_SCAN_TIME_S,
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
    samples: list[dict[str, Any]] = []
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
            progress = _ProgressSampler()
            gate_reports: list[dict] = []
            started = time.perf_counter()
            result = await gateway.execute(
                action,
                on_progress=progress.record,
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
                    progress_samples=progress.finish(status=result.execution_status),
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
    progress = _ProgressSampler()

    action = {"goal": goal.model_dump(mode="json")}
    task = asyncio.create_task(gateway.execute(action, on_progress=progress.record))
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
                progress_samples=progress.finish(status=result.execution_status),
            )

        pose_before = await bridge.get_pose(timeout=3.0)
        task.cancel()
        canceled = False
        nav_cancel_acknowledged = False
        try:
            await task
        except NavigationCancelled as exc:
            canceled = True
            nav_cancel_acknowledged = exc.nav_cancel_acknowledged
        except asyncio.CancelledError:
            # A bare cancellation proves only that the local asyncio task
            # stopped. It carries no evidence that Nav2 canceled its goal.
            canceled = True
        halt_message = await halt_robot(config, bridge)
        await asyncio.sleep(options.settle_s)
        pose_after = await bridge.get_pose(timeout=3.0)
        drift = math.hypot(pose_after.x - pose_before.x, pose_after.y - pose_before.y)
        passed = canceled and nav_cancel_acknowledged and drift <= options.max_stop_drift_m
        if passed:
            detail = (
                "Task cancellation propagated, Nav2 acknowledged the active-goal "
                f"cancel, and post-stop drift was {drift:.4f} m."
            )
        else:
            failures = []
            if not canceled:
                failures.append("task cancellation did not propagate")
            if not nav_cancel_acknowledged:
                failures.append("Nav2 did not acknowledge active-goal cancellation")
            if drift > options.max_stop_drift_m:
                failures.append(
                    f"post-stop drift {drift:.4f} m exceeded {options.max_stop_drift_m:.4f} m"
                )
            detail = "Cancel/stop acceptance failed: " + "; ".join(failures) + "."
        return _check(
            f"cancel_stop:{goal.name}",
            "pass" if passed else "fail",
            detail=detail,
            task_cancelled=canceled,
            nav_cancel_acknowledged=nav_cancel_acknowledged,
            halt_message=halt_message,
            drift_m=round(drift, 6),
            max_stop_drift_m=options.max_stop_drift_m,
            elapsed_s=round(time.perf_counter() - started, 3),
            progress_samples=progress.finish(
                status=(
                    "canceled"
                    if canceled and nav_cancel_acknowledged
                    else "cancel_unacknowledged"
                    if canceled
                    else "cancel_not_propagated"
                )
            ),
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
