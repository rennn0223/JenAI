from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from jenai.acceptance import nav_differential_runner as runner
from jenai.acceptance.nav_differential import CanonicalGoal
from jenai.acceptance.nav_differential_runner import (
    DIFFERENTIAL_EXECUTION_CONFIRMATION,
    DifferentialCaptureOptions,
    DifferentialMode,
    ResetPolicy,
)
from jenai.config.models import AppConfig


def _options(tmp_path: Path) -> DifferentialCaptureOptions:
    scene = tmp_path / "warehouse.usd"
    scene.write_text("#usda 1.0", encoding="utf-8")
    return DifferentialCaptureOptions(
        output=tmp_path / "capture.json",
        location="Dock",
        pair_id="pair-cancellation",
        mode=DifferentialMode.R1_BRIDGE_NAV2,
        simulation_epoch="epoch-cancellation",
        reset_policy=ResetPolicy.NAV2_RESTART,
        execute=True,
        confirmation=DIFFERENTIAL_EXECUTION_CONFIRMATION,
        scene_path=scene,
        live_scene_sha256="a" * 64,
        expected_source_root=tmp_path,
        expected_git_sha="1" * 40,
    )


def _install_persistence_spy(
    monkeypatch: pytest.MonkeyPatch,
    *,
    events: list[str],
) -> None:
    def persist(
        path: Path,
        artifact: dict[str, Any],
        *,
        overwrite: bool,
    ) -> dict[str, Any]:
        del overwrite
        assert events[-1] == "cleanup_finished"
        events.append("artifact_persisted")
        payload = copy.deepcopy(artifact)
        path.write_text(
            json.dumps(payload, default=str, sort_keys=True),
            encoding="utf-8",
        )
        return payload

    monkeypatch.setattr(runner, "_write_capture_artifact", persist)


def _install_cancellable_capture_body(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    body_started: asyncio.Event,
) -> None:
    config = AppConfig(deployment_mode="simulation")
    goal = CanonicalGoal.from_yaw(
        frame_id="map",
        x=1.0,
        y=2.0,
        yaw=0.0,
        simulation_epoch="epoch-cancellation",
    )

    class Bridge:
        async def configure_safety(self, **kwargs: object) -> None:
            del kwargs

        async def start(self) -> None:
            return None

    async def block_in_capture_body(
        bridge: object,
        identity: dict[str, Any],
    ) -> None:
        del bridge, identity
        body_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        runner,
        "_prepare_capture",
        lambda _: (
            tmp_path / "config.toml",
            config,
            {"goal": {}},
            goal,
            {"deployment_mode": "simulation"},
            None,
        ),
    )
    monkeypatch.setattr(runner, "_complete_without_live_bridge", lambda *args: False)
    monkeypatch.setattr(runner, "RosBridgeClient", lambda **_: Bridge())
    monkeypatch.setattr(runner, "_enrich_live_identity", block_in_capture_body)


def test_capture_cancellation_finishes_cleanup_and_persists_before_reraise(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        options = _options(tmp_path)
        body_started = asyncio.Event()
        events: list[str] = []
        _install_cancellable_capture_body(
            monkeypatch,
            tmp_path,
            body_started=body_started,
        )
        _install_persistence_spy(monkeypatch, events=events)

        async def cleanup(*args: object, **kwargs: object) -> dict[str, Any]:
            del args, kwargs
            events.append("cleanup_started")
            await asyncio.sleep(0)
            events.append("cleanup_finished")
            return {
                "status": "PASS",
                "failures": [],
                "final_halt": {"status": "SKIP"},
                "bridge_shutdown": {"status": "PASS"},
            }

        monkeypatch.setattr(runner, "_safe_cleanup_live_capture", cleanup)

        task = asyncio.create_task(runner.capture_navigation_differential(options))
        await asyncio.wait_for(body_started.wait(), timeout=1.0)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1.0)

        assert events == [
            "cleanup_started",
            "cleanup_finished",
            "artifact_persisted",
        ]
        persisted = json.loads(options.output.read_text(encoding="utf-8"))
        assert persisted["failure"]["type"] == "CancelledError"
        assert persisted["cleanup"]["status"] == "PASS"

    asyncio.run(scenario())


def test_repeated_cancellation_cannot_interrupt_the_same_cleanup_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        options = _options(tmp_path)
        body_started = asyncio.Event()
        cleanup_started = asyncio.Event()
        cleanup_release = asyncio.Event()
        cleanup_finished = asyncio.Event()
        events: list[str] = []
        cleanup_task_ids: list[int] = []
        _install_cancellable_capture_body(
            monkeypatch,
            tmp_path,
            body_started=body_started,
        )
        _install_persistence_spy(monkeypatch, events=events)

        async def cleanup(*args: object, **kwargs: object) -> dict[str, Any]:
            del args, kwargs
            current = asyncio.current_task()
            assert current is not None
            cleanup_task_ids.append(id(current))
            events.append("cleanup_started")
            cleanup_started.set()
            await cleanup_release.wait()
            cleanup_finished.set()
            events.append("cleanup_finished")
            return {
                "status": "PASS",
                "failures": [],
                "final_halt": {"status": "SKIP"},
                "bridge_shutdown": {"status": "PASS"},
            }

        monkeypatch.setattr(runner, "_safe_cleanup_live_capture", cleanup)

        task = asyncio.create_task(runner.capture_navigation_differential(options))
        await asyncio.wait_for(body_started.wait(), timeout=1.0)
        task.cancel()
        await asyncio.wait_for(cleanup_started.wait(), timeout=1.0)

        task.cancel()
        await asyncio.sleep(0)
        cleanup_release.set()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1.0)

        assert len(cleanup_task_ids) == 1
        assert cleanup_finished.is_set()
        assert events == [
            "cleanup_started",
            "cleanup_finished",
            "artifact_persisted",
        ]
        assert options.output.is_file()

    asyncio.run(scenario())


def test_cleanup_internal_cancelled_error_is_recorded_and_persisted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        options = _options(tmp_path)
        events: list[str] = []
        _install_persistence_spy(monkeypatch, events=events)

        async def cleanup(*args: object, **kwargs: object) -> dict[str, Any]:
            del args, kwargs
            events.append("cleanup_started")
            events.append("cleanup_finished")
            raise asyncio.CancelledError("cleanup task cancelled internally")

        monkeypatch.setattr(runner, "_safe_cleanup_live_capture", cleanup)
        artifact = runner._base_artifact(options)
        artifact["overall"] = "captured"

        result = await runner._finalize_capture(
            artifact,
            options,
            bridge=object(),  # type: ignore[arg-type]
            config=AppConfig(deployment_mode="simulation"),
            watch_ids=[],
            heartbeat=None,
            motion_attempted=True,
        )

        assert result["overall"] == "cleanup_failed"
        assert result["cleanup"]["status"] == "FAIL"
        assert result["cleanup"]["failures"] == [
            {
                "step": "cleanup_orchestrator",
                "type": "CancelledError",
                "detail": "cleanup task cancelled internally",
            }
        ]
        assert events == [
            "cleanup_started",
            "cleanup_finished",
            "artifact_persisted",
        ]
        assert options.output.is_file()

    asyncio.run(scenario())


def test_finalization_persists_only_after_cleanup_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        options = _options(tmp_path)
        events: list[str] = []
        _install_persistence_spy(monkeypatch, events=events)

        async def cleanup(*args: object, **kwargs: object) -> dict[str, Any]:
            del args, kwargs
            events.append("cleanup_started")
            await asyncio.sleep(0)
            events.append("cleanup_finished")
            return {
                "status": "PASS",
                "failures": [],
                "final_halt": {"status": "SKIP"},
                "bridge_shutdown": {"status": "PASS"},
            }

        monkeypatch.setattr(runner, "_safe_cleanup_live_capture", cleanup)
        artifact = runner._base_artifact(options)
        artifact["overall"] = "captured"

        result = await runner._finalize_capture(
            artifact,
            options,
            bridge=object(),  # type: ignore[arg-type]
            config=AppConfig(deployment_mode="simulation"),
            watch_ids=[],
            heartbeat=None,
            motion_attempted=False,
        )

        assert events == [
            "cleanup_started",
            "cleanup_finished",
            "artifact_persisted",
        ]
        assert result["cleanup"]["status"] == "PASS"

    asyncio.run(scenario())
