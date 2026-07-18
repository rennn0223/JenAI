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
    _execution_config,
    _run_cancel_and_stop,
    run_isaac_hil,
)
from jenai.config.models import AppConfig, TwinProfile
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
