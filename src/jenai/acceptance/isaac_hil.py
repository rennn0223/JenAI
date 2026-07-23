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
from jenai.bridge import BridgeError, MapCellInfo, RosBridgeClient
from jenai.config.models import AppConfig
from jenai.config.store import default_config_path, load_config
from jenai.doctor import run_doctor
from jenai.schemas import Location
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

Check = dict[str, Any]


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


def _check(check_id: str, status: str, *, detail: str = "", **evidence: Any) -> Check:
    return {
        "id": check_id,
        "status": status,
        "detail": detail,
        "evidence": evidence,
    }


def _map_cell_evidence(cell: MapCellInfo) -> dict[str, Any]:
    return {
        "in_bounds": cell.in_bounds,
        "free": cell.free,
        "value": cell.value,
        "cell_x": cell.cell_x,
        "cell_y": cell.cell_y,
        "width": cell.width,
        "height": cell.height,
        "resolution": cell.resolution,
        "origin_x": cell.origin_x,
        "origin_y": cell.origin_y,
        "frame_id": cell.frame_id,
        "source": cell.source,
    }


def _evaluate_start_pose(
    pose: Any,
    config: AppConfig,
    *,
    check_id: str = "start_pose",
    map_cell: MapCellInfo | None = None,
) -> Check:
    """Fail closed on invalid localization, zones, or a non-free map cell."""
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
    if map_cell is not None:
        evidence["map_cell"] = _map_cell_evidence(map_cell)
    if not all(math.isfinite(value) for value in coordinates):
        return _check(
            check_id,
            "fail",
            detail="The localized start pose contains a non-finite value.",
            **evidence,
        )
    if pose.frame_id != "map":
        return _check(
            check_id,
            "fail",
            detail="HIL execution requires a map-localized start pose.",
            **evidence,
        )
    if map_cell is not None and map_cell.frame_id != "map":
        return _check(
            check_id,
            "fail",
            detail="The occupancy snapshot is not expressed in the map frame.",
            **evidence,
        )
    if map_cell is not None and not map_cell.in_bounds:
        return _check(
            check_id,
            "fail",
            detail="The localized start pose lies outside the static map bounds.",
            **evidence,
        )
    if map_cell is not None and not map_cell.free:
        return _check(
            check_id,
            "fail",
            detail=f"The localized start pose occupies static-map value {map_cell.value!r}.",
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
            "Finite map-localized start is free in the static map and outside "
            f"{len(config.twin.forbidden_zones)} configured forbidden zone(s)."
            if map_cell is not None
            else (
                f"Finite map-localized start is outside {len(config.twin.forbidden_zones)} "
                "configured forbidden zone(s)."
            )
        ),
        **evidence,
    )


async def _inspect_start_pose(config: AppConfig) -> Check:
    """Read one pose through the production bridge without sending commands."""
    bridge = RosBridgeClient()
    try:
        await bridge.start()
        pose = await bridge.get_pose(timeout=3.0)
        if pose.frame_id != "map":
            return _evaluate_start_pose(pose, config)
        map_cell = await bridge.map_cell(pose.x, pose.y, timeout=3.0)
        return _evaluate_start_pose(pose, config, map_cell=map_cell)
    except Exception as exc:
        return _check(
            "start_pose",
            "fail",
            detail=f"Could not inspect a localized start pose: {type(exc).__name__}: {exc}",
        )
    finally:
        await bridge.stop()


async def _inspect_route_plans(
    locations: list[Location],
    options: IsaacHilOptions,
    *,
    timeout_s: float = 5.0,
) -> list[Check]:
    """Prove each requested target is plannable without commanding motion."""
    requested_names = [*options.goals]
    if options.cancel_goal:
        requested_names.append(options.cancel_goal)
    goals: list[Location] = []
    seen: set[str] = set()
    for name in requested_names:
        goal = find_location(locations, name)
        if goal.id not in seen:
            seen.add(goal.id)
            goals.append(goal)

    bridge = RosBridgeClient()
    checks: list[Check] = []
    try:
        await bridge.start()
        for goal in goals:
            try:
                plan = await bridge.nav_plan(
                    goal.pose.x,
                    goal.pose.y,
                    goal.pose.yaw,
                    frame_id=goal.frame_id,
                    timeout=timeout_s,
                )
            except Exception as exc:
                checks.append(
                    _check(
                        f"plan:{goal.name}",
                        "fail",
                        detail=(
                            f"Could not compute a read-only Nav2 path: {type(exc).__name__}: {exc}"
                        ),
                    )
                )
                continue
            checks.append(
                _check(
                    f"plan:{goal.name}",
                    "pass" if plan.feasible else "fail",
                    detail=(
                        f"Nav2 produced a {plan.path_length_m:.3f} m path."
                        if plan.feasible
                        else f"Nav2 reported {plan.error_name}: {plan.error_message or 'no path'}."
                    ),
                    goal={
                        "x": goal.pose.x,
                        "y": goal.pose.y,
                        "yaw": goal.pose.yaw,
                        "frame_id": goal.frame_id,
                    },
                    feasible=plan.feasible,
                    pose_count=plan.pose_count,
                    path_length_m=round(plan.path_length_m, 6),
                    planning_time_s=round(plan.planning_time_s, 6),
                    error_code=plan.error_code,
                    error_name=plan.error_name,
                    error_message=plan.error_message,
                )
            )
    except Exception as exc:
        checks.append(
            _check(
                "route_planning",
                "fail",
                detail=(
                    f"Could not start the read-only planning bridge: {type(exc).__name__}: {exc}"
                ),
            )
        )
    finally:
        await bridge.stop()
    return checks


def _scan_float(message: dict[str, Any], key: str) -> float | None:
    raw_value = message.get(key)
    if raw_value is None or isinstance(raw_value, bool):
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
            if (
                range_bounds_valid
                and range_min is not None
                and range_max is not None
                and not range_min <= value <= range_max
            ):
                summary["out_of_range_bins"] += 1
    return summary


@dataclass(frozen=True)
class _ScanCounts:
    bins: tuple[int, ...]
    finite: int
    positive_inf: int
    nan: int
    negative_inf: int
    malformed: int
    out_of_range: int
    all_inf_samples: int

    @classmethod
    def collect(cls, samples: list[dict[str, Any]]) -> _ScanCounts:
        bins = tuple(sample["bins"] for sample in samples)
        return cls(
            bins=bins,
            finite=sum(sample["finite_bins"] for sample in samples),
            positive_inf=sum(sample["positive_inf_bins"] for sample in samples),
            nan=sum(sample["nan_bins"] for sample in samples),
            negative_inf=sum(sample["negative_inf_bins"] for sample in samples),
            malformed=sum(sample["malformed_bins"] for sample in samples),
            out_of_range=sum(sample["out_of_range_bins"] for sample in samples),
            all_inf_samples=sum(
                sample["bins"] > 0 and sample["positive_inf_bins"] == sample["bins"]
                for sample in samples
            ),
        )

    @property
    def total(self) -> int:
        return sum(self.bins)

    @property
    def valid_finite(self) -> int:
        return self.finite - self.out_of_range

    def all_inf_ratio(self, sample_count: int) -> float:
        return self.all_inf_samples / sample_count if sample_count else 1.0

    @property
    def finite_coverage(self) -> float:
        return self.valid_finite / self.total if self.total else 0.0


@dataclass(frozen=True)
class _ScanMetadata:
    angle_spans: tuple[float, ...]
    increments: tuple[float, ...]
    scan_times: tuple[float, ...]
    geometry_errors: tuple[float, ...]
    invalid_angles: int
    invalid_increments: int
    invalid_geometry: int
    invalid_range_bounds: int
    invalid_scan_times: int
    frame_ids: tuple[str, ...]
    missing_frames: int
    stamps: tuple[int | None, ...]
    missing_stamps: int
    nonadvancing_stamp_pairs: int

    @classmethod
    def collect(cls, samples: list[dict[str, Any]]) -> _ScanMetadata:
        stamps = tuple(sample["stamp_ns"] for sample in samples)
        return cls(
            angle_spans=tuple(
                sample["angle_span"] for sample in samples if sample["angle_span"] is not None
            ),
            increments=tuple(
                sample["angle_increment"]
                for sample in samples
                if sample["angle_increment"] is not None
            ),
            scan_times=tuple(
                sample["scan_time"] for sample in samples if sample["scan_time"] is not None
            ),
            geometry_errors=tuple(
                sample["geometry_error"]
                for sample in samples
                if sample["geometry_error"] is not None
            ),
            invalid_angles=sum(
                sample["angle_span"] is None or sample["angle_span"] < SCAN_MIN_ANGULAR_SPAN_RAD
                for sample in samples
            ),
            invalid_increments=sum(
                sample["angle_increment"] is None or sample["angle_increment"] <= 0
                for sample in samples
            ),
            invalid_geometry=sum(
                sample["geometry_error"] is None
                or sample["geometry_error"] > SCAN_GEOMETRY_TOLERANCE_RAD
                for sample in samples
            ),
            invalid_range_bounds=sum(not sample["range_bounds_valid"] for sample in samples),
            invalid_scan_times=sum(
                sample["scan_time"] is None or not 0 < sample["scan_time"] <= SCAN_MAX_SCAN_TIME_S
                for sample in samples
            ),
            frame_ids=tuple(
                sorted({sample["frame_id"] for sample in samples if sample["frame_id"]})
            ),
            missing_frames=sum(not sample["frame_id"] for sample in samples),
            stamps=stamps,
            missing_stamps=sum(stamp is None for stamp in stamps),
            nonadvancing_stamp_pairs=sum(
                previous is not None and current is not None and current <= previous
                for previous, current in zip(stamps, stamps[1:], strict=False)
            ),
        )

    @property
    def timestamp_span_s(self) -> float | None:
        if not self.stamps or any(stamp is None for stamp in self.stamps):
            return None
        first, last = self.stamps[0], self.stamps[-1]
        if first is None or last is None:
            return None
        return (last - first) / 1_000_000_000


def _scan_quality_failures(
    counts: _ScanCounts,
    metadata: _ScanMetadata,
    *,
    sample_count: int,
    sample_target: int,
    topic_available: bool,
    max_all_inf_sample_ratio: float,
    min_finite_bin_coverage: float,
) -> list[str]:
    failures: list[str] = []
    if not topic_available:
        failures.append("The scan topic could not be subscribed to.")
    if sample_count < sample_target:
        failures.append(f"Received {sample_count}/{sample_target} required scan samples.")
    empty_samples = sum(count == 0 for count in counts.bins)
    undersized_samples = sum(count < SCAN_MIN_RANGE_BINS for count in counts.bins)
    if empty_samples:
        failures.append(f"{empty_samples} scan sample(s) had no range bins.")
    if undersized_samples:
        failures.append(
            f"{undersized_samples} scan sample(s) had fewer than {SCAN_MIN_RANGE_BINS} bins."
        )
    if counts.nan or counts.negative_inf or counts.malformed or counts.out_of_range:
        failures.append(
            "Scan ranges contained invalid values "
            f"(NaN={counts.nan}, -inf={counts.negative_inf}, malformed={counts.malformed}, "
            f"out_of_range={counts.out_of_range})."
        )
    invalid_messages = (
        (metadata.invalid_angles, f"did not cover at least {SCAN_MIN_ANGULAR_SPAN_RAD:.1f} rad"),
        (metadata.invalid_increments, "lacked a positive finite angle increment"),
        (metadata.invalid_geometry, "had inconsistent angle/bin geometry"),
        (metadata.invalid_range_bounds, "had invalid range bounds"),
        (metadata.invalid_scan_times, "had an unreasonable scan_time"),
    )
    failures.extend(
        f"{count} scan sample(s) {message}." for count, message in invalid_messages if count
    )
    if metadata.missing_frames or len(metadata.frame_ids) != 1:
        failures.append(
            "Scan frame_id was missing or changed across samples "
            f"(missing={metadata.missing_frames}, frames={list(metadata.frame_ids)})."
        )
    if metadata.missing_stamps or metadata.nonadvancing_stamp_pairs:
        failures.append(
            "Scan timestamps were missing or did not strictly advance "
            f"(missing={metadata.missing_stamps}, "
            f"nonadvancing={metadata.nonadvancing_stamp_pairs})."
        )
    all_inf_ratio = counts.all_inf_ratio(sample_count)
    if all_inf_ratio > max_all_inf_sample_ratio:
        failures.append(
            f"All-infinite scan ratio {all_inf_ratio:.1%} exceeds {max_all_inf_sample_ratio:.1%}."
        )
    if counts.finite_coverage < min_finite_bin_coverage:
        failures.append(
            f"Finite-bin coverage {counts.finite_coverage:.1%} is below "
            f"{min_finite_bin_coverage:.1%}."
        )
    return failures


def _scan_quality_evidence(
    counts: _ScanCounts,
    metadata: _ScanMetadata,
    *,
    sample_target: int,
    elapsed_s: float,
    topic_available: bool,
    error: str | None,
    max_all_inf_sample_ratio: float,
    min_finite_bin_coverage: float,
) -> dict[str, Any]:
    sample_count = len(counts.bins)
    return {
        "topic": SCAN_TOPIC,
        "message_type": SCAN_MESSAGE_TYPE,
        "topic_available": topic_available,
        "error": error,
        "elapsed_s": round(elapsed_s, 3),
        "sample_target": sample_target,
        "samples_received": sample_count,
        "range_bins": {
            "minimum_per_sample": min(counts.bins, default=0),
            "maximum_per_sample": max(counts.bins, default=0),
            "total": counts.total,
        },
        "finite_bins": counts.finite,
        "valid_finite_bins": counts.valid_finite,
        "positive_inf_bins": counts.positive_inf,
        "nan_bins": counts.nan,
        "negative_inf_bins": counts.negative_inf,
        "malformed_bins": counts.malformed,
        "out_of_range_bins": counts.out_of_range,
        "empty_samples": sum(count == 0 for count in counts.bins),
        "undersized_samples": sum(count < SCAN_MIN_RANGE_BINS for count in counts.bins),
        "all_inf_samples": counts.all_inf_samples,
        "all_inf_sample_ratio": round(counts.all_inf_ratio(sample_count), 6),
        "finite_bin_coverage": round(counts.finite_coverage, 6),
        "metadata": {
            "frame_ids": list(metadata.frame_ids),
            "missing_frame_samples": metadata.missing_frames,
            "missing_stamp_samples": metadata.missing_stamps,
            "nonadvancing_stamp_pairs": metadata.nonadvancing_stamp_pairs,
            "timestamp_span_s": metadata.timestamp_span_s,
            "angle_span_rad": {
                "minimum": min(metadata.angle_spans, default=None),
                "maximum": max(metadata.angle_spans, default=None),
                "invalid_samples": metadata.invalid_angles,
            },
            "angle_increment_rad": {
                "minimum": min(metadata.increments, default=None),
                "maximum": max(metadata.increments, default=None),
                "invalid_samples": metadata.invalid_increments,
            },
            "geometry_error_rad": {
                "maximum": max(metadata.geometry_errors, default=None),
                "invalid_samples": metadata.invalid_geometry,
            },
            "invalid_range_bound_samples": metadata.invalid_range_bounds,
            "scan_time_s": {
                "minimum": min(metadata.scan_times, default=None),
                "maximum": max(metadata.scan_times, default=None),
                "invalid_samples": metadata.invalid_scan_times,
            },
        },
        "thresholds": {
            "max_all_inf_sample_ratio": max_all_inf_sample_ratio,
            "min_finite_bin_coverage": min_finite_bin_coverage,
            "min_angular_span_rad": SCAN_MIN_ANGULAR_SPAN_RAD,
            "min_range_bins": SCAN_MIN_RANGE_BINS,
            "geometry_tolerance_rad": SCAN_GEOMETRY_TOLERANCE_RAD,
            "max_scan_time_s": SCAN_MAX_SCAN_TIME_S,
        },
    }


def _evaluate_scan_quality(
    samples: list[dict[str, Any]],
    *,
    sample_target: int = SCAN_SAMPLE_COUNT,
    elapsed_s: float,
    topic_available: bool,
    error: str | None = None,
    max_all_inf_sample_ratio: float = SCAN_MAX_ALL_INF_SAMPLE_RATIO,
    min_finite_bin_coverage: float = SCAN_MIN_FINITE_BIN_COVERAGE,
) -> Check:
    """Build an artifact-safe verdict from per-message metadata and counters."""
    counts = _ScanCounts.collect(samples)
    metadata = _ScanMetadata.collect(samples)
    failures = _scan_quality_failures(
        counts,
        metadata,
        sample_count=len(samples),
        sample_target=sample_target,
        topic_available=topic_available,
        max_all_inf_sample_ratio=max_all_inf_sample_ratio,
        min_finite_bin_coverage=min_finite_bin_coverage,
    )
    detail = " ".join(failures) or (
        f"Received {len(samples)} usable wide-field LaserScan samples with "
        f"{counts.finite_coverage:.1%} finite-bin coverage."
    )
    evidence = _scan_quality_evidence(
        counts,
        metadata,
        sample_target=sample_target,
        elapsed_s=elapsed_s,
        topic_available=topic_available,
        error=error,
        max_all_inf_sample_ratio=max_all_inf_sample_ratio,
        min_finite_bin_coverage=min_finite_bin_coverage,
    )
    return _check("scan_quality", "fail" if failures else "pass", detail=detail, **evidence)


async def _inspect_scan_quality(
    *,
    sample_count: int = SCAN_SAMPLE_COUNT,
    timeout_s: float = SCAN_SAMPLE_TIMEOUT_S,
    max_all_inf_sample_ratio: float = SCAN_MAX_ALL_INF_SAMPLE_RATIO,
    min_finite_bin_coverage: float = SCAN_MIN_FINITE_BIN_COVERAGE,
) -> Check:
    """Sample the live LaserScan before any navigation goal can be sent."""
    bridge = RosBridgeClient()
    samples: list[dict[str, Any]] = []
    complete = asyncio.Event()
    watch_id: int | None = None
    topic_available = False
    error: str | None = None
    started = time.perf_counter()

    def on_scan(message: dict[str, Any]) -> None:
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


def _overall(checks: list[Check], *, executed: bool) -> str:
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
) -> tuple[list[Check], bool, dict[str, Any]]:
    history: list[Check] = []
    items: list[Check] = []
    doctor = None
    failed: list[Check] = []
    missing: list[str] = []
    passed = False
    required_checks = set(REQUIRED_NAV_CHECKS)
    for attempt in range(1, max(1, attempts) + 1):
        doctor = run_doctor(config_path)
        items = [item.model_dump(mode="json") for item in doctor.items]
        if any(
            item["section"] == "site" and item["check_name"] == "map_identity" for item in items
        ):
            required_checks.add("map_identity")
        by_name = {
            item["check_name"]: item for item in items if item["check_name"] in required_checks
        }
        missing = sorted(required_checks - set(by_name))
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
    if doctor is None:
        raise RuntimeError("doctor preflight loop completed without producing a result")
    return (
        items,
        passed,
        {
            "doctor_overall": doctor.overall,
            "required_checks": sorted(required_checks),
            "non_passing_required": failed,
            "missing_required": missing,
            "attempts": history,
        },
    )


def _live_bridge_check(pose: Any) -> Check:
    return _check(
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


def _twin_isolation_check(
    config: AppConfig,
    options: IsaacHilOptions,
    ambient_domain: str,
) -> Check:
    if not config.twin.enabled:
        return _check(
            "twin_isolation",
            "fail" if options.require_twin else "skip",
            detail="Twin Gate is disabled; no live Twin verdict was produced.",
        )
    if str(config.twin.domain_id) != ambient_domain:
        return _check(
            "twin_isolation",
            "pass",
            detail="Twin and target ROS domains are distinct.",
            target_domain=ambient_domain,
            twin_domain=config.twin.domain_id,
        )
    return _check(
        "twin_isolation",
        "fail" if options.require_twin else "skip",
        detail=(
            "Pure-simulation target shares the configured Twin domain; "
            "this run does not claim deployment isolation or a Twin verdict."
        ),
        target_domain=ambient_domain,
        twin_domain=config.twin.domain_id,
    )


async def _run_route_goal(
    gateway: NavigationGateway,
    execution_config: AppConfig,
    goal: Location,
) -> list[Check]:
    progress = _ProgressSampler()
    gate_reports: list[Check] = []
    started = time.perf_counter()
    result = await gateway.execute(
        {"goal": goal.model_dump(mode="json")},
        on_progress=progress.record,
        on_gate_report=lambda report: gate_reports.append(report.model_dump(mode="json")),
    )
    checks = [
        _check(
            f"route:{goal.name}",
            "pass" if result.execution_status == "succeeded" else "fail",
            detail=result.route_preview,
            execution_status=result.execution_status,
            elapsed_s=round(time.perf_counter() - started, 3),
            progress_samples=progress.finish(status=result.execution_status),
            gate_reports=gate_reports,
        )
    ]
    if execution_config.twin.enabled:
        report = gate_reports[-1] if gate_reports else None
        checks.append(
            _check(
                f"twin_verdict:{goal.name}",
                "pass" if report and report["verdict"] == "pass" else "fail",
                detail=(
                    f"Twin Gate returned {report['verdict']}."
                    if report
                    else "Twin Gate did not return a structured verdict."
                ),
                report=report,
            )
        )
    return checks


async def _run_cancel_goal(
    execution_config: AppConfig,
    bridge: RosBridgeClient,
    goal: Location,
    options: IsaacHilOptions,
) -> Check:
    # Cancellation must exercise the target Nav2 goal, not merely stop an
    # in-progress rehearsal in a separate Twin domain.
    cancel_config = execution_config.model_copy(
        update={"twin": execution_config.twin.model_copy(update={"enabled": False})}
    )
    gateway = NavigationGateway(cancel_config, get_bridge=lambda: _ready_bridge(bridge))
    return await _run_cancel_and_stop(gateway, bridge, cancel_config, goal, options)


async def _run_live(
    config: AppConfig,
    locations: list[Location],
    options: IsaacHilOptions,
) -> list[Check]:
    checks: list[Check] = []
    bridge = RosBridgeClient()
    ambient_domain = os.environ.get("ROS_DOMAIN_ID", "0").strip() or "0"
    execution_config = _execution_config(config, options.target, ambient_domain)
    gateway = NavigationGateway(execution_config, get_bridge=lambda: _ready_bridge(bridge))
    try:
        await arm_watchdog(execution_config, bridge)
        await bridge.start()
        pose = await bridge.get_pose(timeout=3.0)
        checks.append(_live_bridge_check(pose))
        map_cell = (
            await bridge.map_cell(pose.x, pose.y, timeout=3.0) if pose.frame_id == "map" else None
        )
        start_pose_check = _evaluate_start_pose(
            pose,
            config,
            check_id="start_pose_recheck",
            map_cell=map_cell,
        )
        checks.append(start_pose_check)
        if start_pose_check["status"] != "pass":
            return checks

        checks.append(_twin_isolation_check(config, options, ambient_domain))
        if options.require_twin and not execution_config.twin.enabled:
            return checks
        abort_reason: str | None = None
        for goal_name in options.goals:
            if abort_reason is not None:
                checks.append(
                    _check(
                        f"route:{goal_name}",
                        "skip",
                        detail=(
                            f"Live goal withheld after an earlier motion failure: {abort_reason}"
                        ),
                    )
                )
                continue
            goal = find_location(locations, goal_name)
            goal_checks = await _run_route_goal(gateway, execution_config, goal)
            checks.extend(goal_checks)
            failed = next((check for check in goal_checks if check["status"] == "fail"), None)
            if failed is not None:
                abort_reason = f"{failed['id']}: {failed['detail']}"
        if options.cancel_goal:
            if abort_reason is not None:
                checks.append(
                    _check(
                        f"cancel_stop:{options.cancel_goal}",
                        "skip",
                        detail=(
                            "Cancel exercise withheld after an earlier motion failure: "
                            f"{abort_reason}"
                        ),
                    )
                )
            else:
                goal = find_location(locations, options.cancel_goal)
                checks.append(await _run_cancel_goal(execution_config, bridge, goal, options))
    except Exception as exc:
        # This evidence boundary must serialize unexpected failures before exit.
        checks.append(_check("live_exception", "fail", detail=f"{type(exc).__name__}: {exc}"))
    finally:
        try:
            halt_message = await halt_robot(execution_config, bridge)
        except Exception as exc:
            checks.append(
                _check(
                    "final_halt",
                    "fail",
                    detail=f"Final emergency halt was not confirmed: {type(exc).__name__}: {exc}",
                )
            )
        else:
            checks.append(_check("final_halt", "pass", detail=halt_message))
        try:
            await bridge.stop()
        except Exception as exc:
            checks.append(
                _check(
                    "bridge_shutdown",
                    "fail",
                    detail=f"ROS bridge shutdown failed: {type(exc).__name__}: {exc}",
                )
            )
        else:
            checks.append(
                _check("bridge_shutdown", "pass", detail="ROS bridge process stopped cleanly.")
            )
    return checks


async def _ready_bridge(bridge: RosBridgeClient) -> RosBridgeClient:
    if not bridge.running:
        await bridge.start()
    return bridge


async def _run_cancel_and_stop(
    gateway: NavigationGateway,
    bridge: RosBridgeClient,
    config: AppConfig,
    goal: Location,
    options: IsaacHilOptions,
) -> Check:
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


async def run_isaac_hil(options: IsaacHilOptions) -> Check:
    """Run preflight or live acceptance and always persist one JSON artifact."""
    options.validate()
    config_path = options.config_path or default_config_path()
    started_at = _utc_now()
    source_revision, source_dirty = _source_state()
    checks: list[Check] = []
    artifact: dict[str, Any] = {
        "schema_version": 1,
        "run_id": f"isaac-hil-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:6]}",
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
        route_plans_ok = False
        if doctor_ok:
            scan_quality_check = await _inspect_scan_quality()
            checks.append(scan_quality_check)
            scan_quality_ok = scan_quality_check["status"] == "pass"
            if scan_quality_ok:
                start_pose_check = await _inspect_start_pose(config)
                checks.append(start_pose_check)
                start_pose_ok = start_pose_check["status"] == "pass"
                if start_pose_ok:
                    route_plan_checks = await _inspect_route_plans(locations, options)
                    checks.extend(route_plan_checks)
                    route_plans_ok = bool(route_plan_checks) and all(
                        check["status"] == "pass" for check in route_plan_checks
                    )
        if options.execute and doctor_ok and scan_quality_ok and start_pose_ok and route_plans_ok:
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
