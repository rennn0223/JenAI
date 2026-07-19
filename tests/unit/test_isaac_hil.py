from __future__ import annotations

import asyncio
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenai.acceptance import isaac_hil
from jenai.acceptance.isaac_hil import (
    EXECUTION_CONFIRMATION,
    IsaacHilOptions,
    _doctor_checks,
    _evaluate_start_pose,
    _execution_config,
    _inspect_scan_quality,
    _ProgressSampler,
    _run_cancel_and_stop,
    _source_state,
    run_isaac_hil,
)
from jenai.config.models import AppConfig, ForbiddenZone, TwinProfile
from jenai.schemas import Location, Pose2D
from jenai.tools.nav_live import NavigationCancelled


def _options(tmp_path: Path, **overrides) -> IsaacHilOptions:
    values = {
        "output": tmp_path / "acceptance.json",
        "goals": ("corner",),
        "cancel_after_s": 0.001,
        "settle_s": 0.001,
    }
    values.update(overrides)
    return IsaacHilOptions(**values)


def test_source_state_records_revision_and_dirty_tree(monkeypatch) -> None:
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        stdout = "abc123\n" if args[1] == "rev-parse" else " M tracked.py\n"
        return SimpleNamespace(returncode=0, stdout=stdout)

    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.delenv("JENAI_SOURCE_REVISION", raising=False)
    monkeypatch.setattr(isaac_hil.subprocess, "run", fake_run)

    revision, dirty = _source_state()

    assert revision == "abc123"
    assert dirty is True
    assert [call[1] for call in calls] == ["rev-parse", "status"]


def test_live_execution_requires_exact_confirmation(tmp_path: Path) -> None:
    options = _options(tmp_path, execute=True, confirmation="yes")

    with pytest.raises(ValueError, match=EXECUTION_CONFIRMATION):
        options.validate()


def test_require_twin_needs_live_execution(tmp_path: Path) -> None:
    options = _options(tmp_path, require_twin=True)

    with pytest.raises(ValueError, match="only with --execute"):
        options.validate()


def test_artifact_is_append_only_by_default(tmp_path: Path) -> None:
    output = tmp_path / "acceptance.json"
    output.write_text("existing", encoding="utf-8")

    with pytest.raises(FileExistsError):
        _options(tmp_path, output=output).validate()


def test_same_domain_pure_sim_bypasses_only_twin_rehearsal(tmp_path: Path) -> None:
    config = AppConfig(twin=TwinProfile(enabled=True, domain_id=0))

    execution = _execution_config(config, "isaac-sim", "0")

    assert execution.twin.enabled is False
    assert config.twin.enabled is True


def test_isolated_twin_remains_enabled_for_structured_verdict() -> None:
    config = AppConfig(twin=TwinProfile(enabled=True, domain_id=42))

    execution = _execution_config(config, "isaac-sim", "0")

    assert execution.twin.enabled is True


def _pose(x: float, y: float, *, frame_id: str = "map"):
    return SimpleNamespace(x=x, y=y, yaw=0.0, frame_id=frame_id, source="/amcl_pose")


def test_start_pose_fails_inside_configured_forbidden_zone() -> None:
    config = AppConfig(
        twin=TwinProfile(
            forbidden_zones=[
                ForbiddenZone(name="wall", x_min=-9.0, y_min=-13.0, x_max=-4.5, y_max=-9.0)
            ]
        )
    )

    result = _evaluate_start_pose(_pose(-7.16, -9.48), config)

    assert result["status"] == "fail"
    assert result["evidence"]["forbidden_zone"]["name"] == "wall"


def test_start_pose_passes_outside_zones_with_finite_map_pose() -> None:
    config = AppConfig(
        twin=TwinProfile(
            forbidden_zones=[ForbiddenZone(name="wall", x_min=-9, y_min=-13, x_max=-4.5, y_max=-9)]
        )
    )

    result = _evaluate_start_pose(_pose(0.0, 0.0), config)

    assert result["status"] == "pass"
    assert result["evidence"]["configured_forbidden_zones"] == ["wall"]


def test_start_pose_cannot_compare_map_zones_to_odom_pose() -> None:
    config = AppConfig(
        twin=TwinProfile(
            forbidden_zones=[ForbiddenZone(name="wall", x_min=-9, y_min=-13, x_max=-4.5, y_max=-9)]
        )
    )

    result = _evaluate_start_pose(_pose(0.0, 0.0, frame_id="odom"), config)

    assert result["status"] == "fail"
    assert "not map-localized" in result["detail"]


def test_start_pose_rejects_non_finite_coordinates() -> None:
    result = _evaluate_start_pose(_pose(float("nan"), 0.0), AppConfig())

    assert result["status"] == "fail"
    assert "non-finite" in result["detail"]


def test_doctor_fails_closed_when_required_item_is_missing(monkeypatch) -> None:
    item = SimpleNamespace(
        check_name="ros2_cli",
        status="pass",
        model_dump=lambda **_kwargs: {
            "section": "ros2",
            "check_name": "ros2_cli",
            "status": "pass",
            "message": "ok",
            "fix_suggestion": None,
        },
    )
    monkeypatch.setattr(
        isaac_hil,
        "run_doctor",
        lambda _path: SimpleNamespace(items=[item], overall="pass"),
    )

    _items, passed, evidence = _doctor_checks(Path("config.toml"), attempts=1)

    assert passed is False
    assert set(evidence["missing_required"]) == {
        "map",
        "localization",
        "laser",
        "nav2",
        "cmd_vel",
    }


@pytest.mark.parametrize(
    ("nav_cancel_acknowledged", "expected_status", "expected_progress_status"),
    [
        (True, "pass", "canceled"),
        (False, "fail", "cancel_unacknowledged"),
    ],
)
def test_cancel_and_stop_requires_nav2_acknowledgement_even_with_zero_drift(
    tmp_path: Path,
    nav_cancel_acknowledged: bool,
    expected_status: str,
    expected_progress_status: str,
) -> None:
    class FakeGateway:
        async def execute(self, _action, *, on_progress):
            on_progress(SimpleNamespace(distance_remaining=3.0, recoveries=0, elapsed=0.1))
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise NavigationCancelled(nav_cancel_acknowledged=nav_cancel_acknowledged) from None

    class FakeBridge:
        async def get_pose(self, timeout=3.0):
            return SimpleNamespace(x=1.0, y=2.0, yaw=0.0, frame_id="map", source="/amcl_pose")

        async def halt(self, **_kwargs):
            return True

    goal = Location(name="corner", pose=Pose2D(x=4.0, y=5.0, yaw=0.0))
    result = asyncio.run(
        _run_cancel_and_stop(
            FakeGateway(),
            FakeBridge(),
            AppConfig(),
            goal,
            _options(tmp_path),
        )
    )

    assert result["status"] == expected_status
    assert result["evidence"]["task_cancelled"] is True
    assert result["evidence"]["nav_cancel_acknowledged"] is nav_cancel_acknowledged
    assert result["evidence"]["drift_m"] == 0.0
    assert result["evidence"]["progress_samples"]
    assert result["evidence"]["progress_samples"][-1]["status"] == expected_progress_status


class _ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _progress(distance: float, *, recoveries: int = 0, elapsed: float = 0.0, status=None):
    return SimpleNamespace(
        distance_remaining=distance,
        recoveries=recoveries,
        elapsed=elapsed,
        status=status,
    )


def test_progress_sampler_thins_by_wall_time_and_keeps_important_final_values() -> None:
    clock = _ManualClock()
    sampler = _ProgressSampler(clock=clock)
    sampler.record(_progress(10.0))

    clock.now = 0.1
    sampler.record(_progress(9.9, elapsed=0.1))
    clock.now = 0.2
    sampler.record(_progress(8.5, elapsed=0.2))
    clock.now = 0.3
    sampler.record(_progress(8.4, recoveries=1, elapsed=0.3))
    clock.now = 0.4
    sampler.record(_progress(8.3, recoveries=1, elapsed=0.4, status="recovering"))
    clock.now = 0.5
    sampler.record(_progress(8.2, recoveries=1, elapsed=0.5, status="recovering"))

    samples = sampler.finish(status="aborted")

    assert [sample["elapsed"] for sample in samples[:-1]] == [0.0, 0.2, 0.3, 0.4]
    assert samples[-1]["distance_remaining"] == 8.2
    assert samples[-1]["status"] == "aborted"


def test_progress_sampler_hard_limit_retains_first_and_final_samples() -> None:
    clock = _ManualClock()
    sampler = _ProgressSampler(interval_s=0.5, max_samples=8, clock=clock)

    for index in range(40):
        clock.now = index * 0.5
        sampler.record(_progress(40.0 - index * 0.1, elapsed=clock.now))

    samples = sampler.finish(status="succeeded")

    assert len(samples) == 8
    assert samples[0]["elapsed"] == 0.0
    assert samples[-1]["elapsed"] == 19.5
    assert samples[-1]["status"] == "succeeded"


def test_preflight_overall_fails_when_start_pose_gate_fails(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("test", encoding="utf-8")
    config = AppConfig(locations_path="locations.toml")
    location = Location(name="corner", pose=Pose2D(x=4.0, y=5.0, yaw=0.0))

    monkeypatch.setattr(isaac_hil, "load_config", lambda _path: config)
    monkeypatch.setattr(isaac_hil, "load_locations", lambda _path: [location])
    monkeypatch.setattr(
        isaac_hil,
        "_doctor_checks",
        lambda _path: ([], True, {"required_checks": [], "attempts": []}),
    )

    async def blocked_start(_config):
        return {
            "id": "start_pose",
            "status": "fail",
            "detail": "inside forbidden zone",
            "evidence": {},
        }

    async def passing_scan():
        return {
            "id": "scan_quality",
            "status": "pass",
            "detail": "ok",
            "evidence": {},
        }

    monkeypatch.setattr(isaac_hil, "_inspect_scan_quality", passing_scan)
    monkeypatch.setattr(isaac_hil, "_inspect_start_pose", blocked_start)

    artifact = asyncio.run(run_isaac_hil(_options(tmp_path, config_path=config_path)))

    assert artifact["overall"] == "fail"
    assert [check["id"] for check in artifact["checks"]] == [
        "preflight",
        "scan_quality",
        "start_pose",
    ]


def test_setup_failure_is_preserved_in_artifact(tmp_path: Path) -> None:
    output = tmp_path / "failed.json"
    artifact = asyncio.run(
        run_isaac_hil(_options(tmp_path, output=output, config_path=tmp_path / "missing.toml"))
    )

    assert artifact["overall"] == "fail"
    assert artifact["checks"][0]["id"] == "setup"
    assert output.is_file()


def test_doctor_retries_transient_graph_discovery_and_preserves_attempts(
    monkeypatch,
) -> None:
    def item(name: str, status: str):
        payload = {
            "section": "nav",
            "check_name": name,
            "status": status,
            "message": name,
            "fix_suggestion": None,
        }
        return SimpleNamespace(
            check_name=name,
            status=status,
            model_dump=lambda **_kwargs: payload,
        )

    names = sorted(isaac_hil.REQUIRED_NAV_CHECKS)
    results = [
        SimpleNamespace(
            items=[item(name, "warn" if name == "map" else "pass") for name in names],
            overall="warn",
        ),
        SimpleNamespace(
            items=[item(name, "pass") for name in names],
            overall="pass",
        ),
    ]
    monkeypatch.setattr(isaac_hil, "run_doctor", lambda _path: results.pop(0))

    _items, passed, evidence = _doctor_checks(Path("config.toml"), attempts=2, retry_delay_s=0)

    assert passed is True
    assert len(evidence["attempts"]) == 2
    assert evidence["attempts"][0]["non_passing_required"][0]["check_name"] == "map"
    assert evidence["non_passing_required"] == []


class _FakeScanBridge:
    def __init__(self, messages=(), watch_error: Exception | None = None) -> None:
        self.messages = messages
        self.watch_error = watch_error

    async def start(self) -> None:
        return None

    async def watch(self, _topic, _msg_type, handler, *, throttle):
        assert throttle == 0.0
        if self.watch_error is not None:
            raise self.watch_error
        for message in self.messages:
            handler(message)
        return 7

    async def unwatch(self, watch_id: int) -> None:
        assert watch_id == 7

    async def stop(self) -> None:
        return None


def _scan_message(index: int, ranges=None, *, stamp_ns: int | None = None, **overrides):
    bins = 362
    angle_min = -math.pi / 2
    angle_max = math.pi / 2
    timestamp = index * 100_000_000 if stamp_ns is None else stamp_ns
    message = {
        "header": {
            "stamp": {
                "sec": timestamp // 1_000_000_000,
                "nanosec": timestamp % 1_000_000_000,
            },
            "frame_id": "front_3d_lidar",
        },
        "angle_min": angle_min,
        "angle_max": angle_max,
        "angle_increment": (angle_max - angle_min) / (bins - 1),
        "scan_time": 0.1,
        "range_min": 0.05,
        "range_max": 100.0,
        "ranges": ranges if ranges is not None else [1.0] * 254 + [float("inf")] * 108,
    }
    message.update(overrides)
    return message


def test_scan_quality_accepts_live_shaped_forward_scans_and_records_only_summary(
    monkeypatch,
) -> None:
    messages = [_scan_message(index) for index in range(10)]
    monkeypatch.setattr(isaac_hil, "RosBridgeClient", lambda: _FakeScanBridge(messages))

    result = asyncio.run(_inspect_scan_quality(timeout_s=0.01))

    assert result["status"] == "pass"
    evidence = result["evidence"]
    assert evidence["samples_received"] == 10
    assert evidence["finite_bin_coverage"] == pytest.approx(254 / 362, abs=1e-6)
    assert evidence["range_bins"]["total"] == 3620
    assert evidence["metadata"]["frame_ids"] == ["front_3d_lidar"]
    assert evidence["metadata"]["angle_span_rad"]["minimum"] == pytest.approx(math.pi)
    assert "ranges" not in evidence
    assert "full-scan" not in result["detail"]


def test_scan_quality_rejects_partial_wedges_and_all_inf_scans(monkeypatch) -> None:
    blank_ranges = [float("inf")] * 362
    wedge_ranges = [1.0] * 72 + [float("inf")] * 290
    messages = [
        _scan_message(index, blank_ranges if index < 3 else wedge_ranges) for index in range(10)
    ]
    monkeypatch.setattr(isaac_hil, "RosBridgeClient", lambda: _FakeScanBridge(messages))

    result = asyncio.run(_inspect_scan_quality(timeout_s=0.01))

    assert result["status"] == "fail"
    assert result["evidence"]["all_inf_sample_ratio"] == 0.3
    assert result["evidence"]["finite_bin_coverage"] == pytest.approx(504 / 3620, abs=1e-6)


def test_scan_quality_rejects_one_bin_partial_arcs(monkeypatch) -> None:
    messages = [
        _scan_message(
            index,
            [1.0],
            angle_min=-0.1,
            angle_max=0.1,
            angle_increment=0.2,
        )
        for index in range(10)
    ]
    monkeypatch.setattr(isaac_hil, "RosBridgeClient", lambda: _FakeScanBridge(messages))

    result = asyncio.run(_inspect_scan_quality(timeout_s=0.01))

    assert result["status"] == "fail"
    assert result["evidence"]["undersized_samples"] == 10
    assert result["evidence"]["metadata"]["angle_span_rad"]["invalid_samples"] == 10


def test_scan_quality_rejects_missing_metadata(monkeypatch) -> None:
    messages = [{"ranges": [1.0] * 362} for _ in range(10)]
    monkeypatch.setattr(isaac_hil, "RosBridgeClient", lambda: _FakeScanBridge(messages))

    result = asyncio.run(_inspect_scan_quality(timeout_s=0.01))

    assert result["status"] == "fail"
    metadata = result["evidence"]["metadata"]
    assert metadata["missing_frame_samples"] == 10
    assert metadata["missing_stamp_samples"] == 10
    assert metadata["angle_increment_rad"]["invalid_samples"] == 10


def test_scan_quality_rejects_stale_timestamps_and_changing_frames(monkeypatch) -> None:
    messages = [_scan_message(index, stamp_ns=0) for index in range(10)]
    messages[-1]["header"]["frame_id"] = "other_lidar"
    monkeypatch.setattr(isaac_hil, "RosBridgeClient", lambda: _FakeScanBridge(messages))

    result = asyncio.run(_inspect_scan_quality(timeout_s=0.01))

    assert result["status"] == "fail"
    metadata = result["evidence"]["metadata"]
    assert metadata["nonadvancing_stamp_pairs"] == 9
    assert metadata["frame_ids"] == ["front_3d_lidar", "other_lidar"]


def test_scan_quality_rejects_malformed_geometry_bounds_and_scan_time(monkeypatch) -> None:
    messages = [
        _scan_message(
            index,
            angle_increment=-0.01 if index < 5 else 0.001,
            range_min=10.0,
            range_max=1.0,
            scan_time=0.0,
        )
        for index in range(10)
    ]
    monkeypatch.setattr(isaac_hil, "RosBridgeClient", lambda: _FakeScanBridge(messages))

    result = asyncio.run(_inspect_scan_quality(timeout_s=0.01))

    assert result["status"] == "fail"
    metadata = result["evidence"]["metadata"]
    assert metadata["angle_increment_rad"]["invalid_samples"] == 5
    assert metadata["geometry_error_rad"]["invalid_samples"] == 10
    assert metadata["invalid_range_bound_samples"] == 10
    assert metadata["scan_time_s"]["invalid_samples"] == 10


def test_scan_quality_rejects_nan_negative_inf_and_out_of_range_values(monkeypatch) -> None:
    invalid_ranges = [
        -0.1,
        100.1,
        float("nan"),
        float("-inf"),
    ] + [1.0] * 358
    messages = [_scan_message(index, invalid_ranges if index == 0 else None) for index in range(10)]
    monkeypatch.setattr(isaac_hil, "RosBridgeClient", lambda: _FakeScanBridge(messages))

    result = asyncio.run(_inspect_scan_quality(timeout_s=0.01))

    assert result["status"] == "fail"
    assert result["evidence"]["nan_bins"] == 1
    assert result["evidence"]["negative_inf_bins"] == 1
    assert result["evidence"]["out_of_range_bins"] == 2


def test_scan_quality_fails_closed_on_timeout(monkeypatch) -> None:
    monkeypatch.setattr(isaac_hil, "RosBridgeClient", lambda: _FakeScanBridge())

    result = asyncio.run(_inspect_scan_quality(sample_count=2, timeout_s=0.001))

    assert result["status"] == "fail"
    assert result["evidence"]["topic_available"] is True
    assert result["evidence"]["samples_received"] == 0
    assert "Timed out" in result["evidence"]["error"]


def test_scan_quality_fails_closed_when_topic_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        isaac_hil,
        "RosBridgeClient",
        lambda: _FakeScanBridge(watch_error=RuntimeError("no /scan")),
    )

    result = asyncio.run(_inspect_scan_quality(sample_count=2, timeout_s=0.001))

    assert result["status"] == "fail"
    assert result["evidence"]["topic_available"] is False
    assert "no /scan" in result["evidence"]["error"]


def test_scan_failure_withholds_live_goals(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("test", encoding="utf-8")
    config = AppConfig(locations_path="locations.toml")
    location = Location(name="corner", pose=Pose2D(x=4.0, y=5.0, yaw=0.0))

    monkeypatch.setattr(isaac_hil, "load_config", lambda _path: config)
    monkeypatch.setattr(isaac_hil, "load_locations", lambda _path: [location])
    monkeypatch.setattr(
        isaac_hil,
        "_doctor_checks",
        lambda _path: ([], True, {"required_checks": [], "attempts": []}),
    )

    async def failed_scan():
        return {
            "id": "scan_quality",
            "status": "fail",
            "detail": "partial scan",
            "evidence": {"samples_received": 10},
        }

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("navigation or pose inspection ran after a failed scan gate")

    monkeypatch.setattr(isaac_hil, "_inspect_scan_quality", failed_scan)
    monkeypatch.setattr(isaac_hil, "_inspect_start_pose", must_not_run)
    monkeypatch.setattr(isaac_hil, "_run_live", must_not_run)

    artifact = asyncio.run(
        run_isaac_hil(
            _options(
                tmp_path,
                config_path=config_path,
                execute=True,
                confirmation=EXECUTION_CONFIRMATION,
            )
        )
    )

    assert artifact["overall"] == "fail"
    assert [check["id"] for check in artifact["checks"]] == [
        "preflight",
        "scan_quality",
        "live_execution",
    ]
