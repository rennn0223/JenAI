from __future__ import annotations

import asyncio
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
    _run_cancel_and_stop,
    _source_state,
    run_isaac_hil,
)
from jenai.config.models import AppConfig, ForbiddenZone, TwinProfile
from jenai.schemas import Location, Pose2D


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


def test_cancel_and_stop_records_drift_and_propagates_cancellation(tmp_path: Path) -> None:
    class FakeGateway:
        async def execute(self, _action, *, on_progress):
            on_progress(SimpleNamespace(distance_remaining=3.0, recoveries=0, elapsed=0.1))
            await asyncio.Event().wait()

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

    assert result["status"] == "pass"
    assert result["evidence"]["task_cancelled"] is True
    assert result["evidence"]["drift_m"] == 0.0
    assert result["evidence"]["progress_samples"]


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


def test_scan_quality_accepts_full_scans_and_records_only_summary(
    monkeypatch,
) -> None:
    messages = [{"ranges": [1.0] * 7 + [float("inf")] * 3} for _ in range(10)]
    monkeypatch.setattr(isaac_hil, "RosBridgeClient", lambda: _FakeScanBridge(messages))

    result = asyncio.run(_inspect_scan_quality(timeout_s=0.01))

    assert result["status"] == "pass"
    evidence = result["evidence"]
    assert evidence["samples_received"] == 10
    assert evidence["finite_bin_coverage"] == 0.7
    assert evidence["range_bins"]["total"] == 100
    assert "ranges" not in evidence


def test_scan_quality_rejects_partial_wedges_and_all_inf_scans(monkeypatch) -> None:
    blank = {"ranges": [float("inf")] * 10}
    wedge = {"ranges": [1.0, 1.0] + [float("inf")] * 8}
    monkeypatch.setattr(
        isaac_hil,
        "RosBridgeClient",
        lambda: _FakeScanBridge([blank] * 3 + [wedge] * 7),
    )

    result = asyncio.run(_inspect_scan_quality(timeout_s=0.01))

    assert result["status"] == "fail"
    assert result["evidence"]["all_inf_sample_ratio"] == 0.3
    assert result["evidence"]["finite_bin_coverage"] == 0.14


def test_scan_quality_rejects_nan_and_negative_infinity(monkeypatch) -> None:
    invalid = {"ranges": [1.0, float("nan"), float("-inf"), float("inf")]}
    good = {"ranges": [1.0, 2.0, 3.0, float("inf")]}
    monkeypatch.setattr(
        isaac_hil,
        "RosBridgeClient",
        lambda: _FakeScanBridge([invalid] + [good] * 9),
    )

    result = asyncio.run(_inspect_scan_quality(timeout_s=0.01))

    assert result["status"] == "fail"
    assert result["evidence"]["nan_bins"] == 1
    assert result["evidence"]["negative_inf_bins"] == 1


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
