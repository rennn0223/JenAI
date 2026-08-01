from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from jenai.acceptance import nav_differential_runner as runner
from jenai.acceptance.nav_differential import CanonicalGoal, GroundTruthCalibration
from jenai.acceptance.nav_differential_runner import (
    DIFFERENTIAL_EXECUTION_CONFIRMATION,
    DifferentialCaptureOptions,
    DifferentialMode,
    ResetPolicy,
)
from jenai.config.models import AppConfig


def _stamp(total_ns: int) -> dict[str, int]:
    return {
        "sec": total_ns // 1_000_000_000,
        "nanosec": total_ns % 1_000_000_000,
    }


def _pose_message(*, stamp_ns: int, x: float, frame_id: str = "map") -> dict[str, Any]:
    return {
        "header": {"stamp": _stamp(stamp_ns), "frame_id": frame_id},
        "pose": {
            "position": {"x": x, "y": 2.0, "z": 0.0},
            "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        },
    }


def _amcl_message(*, stamp_ns: int, x: float = 1.0) -> dict[str, Any]:
    message = _pose_message(stamp_ns=stamp_ns, x=x)
    message["pose"] = {
        "pose": cast(dict[str, Any], message["pose"]),
        "covariance": [0.01 if index in {0, 7} else 0.0 for index in range(36)],
    }
    return message


def _odom_message(*, stamp_ns: int, x: float = 1.0) -> dict[str, Any]:
    message = _amcl_message(stamp_ns=stamp_ns, x=x)
    message["twist"] = {
        "twist": {
            "linear": {"x": 0.0, "y": 0.0, "z": 0.0},
            "angular": {"x": 0.0, "y": 0.0, "z": 0.0},
        }
    }
    return message


def _recorders(
    *,
    clock_samples: list[tuple[int, int]],
    ground_truth_samples: list[tuple[int, int, float]] | None = None,
) -> dict[str, runner._TopicRecorder]:
    recorders = {
        key: runner._TopicRecorder()
        for key in ("clock", "amcl", "odom", "action_status", "ground_truth")
    }
    recorders["clock"].samples = [
        {"host_monotonic_ns": host_ns, "message": {"clock": _stamp(clock_ns)}}
        for host_ns, clock_ns in clock_samples
    ]
    recorders["amcl"].samples = [
        {
            "host_monotonic_ns": host_ns,
            "message": _amcl_message(stamp_ns=clock_ns),
        }
        for host_ns, clock_ns in clock_samples
    ]
    recorders["odom"].samples = [
        {
            "host_monotonic_ns": host_ns,
            "message": _odom_message(stamp_ns=clock_ns),
        }
        for host_ns, clock_ns in clock_samples
    ]
    recorders["ground_truth"].samples = [
        {
            "host_monotonic_ns": host_ns,
            "message": _pose_message(stamp_ns=stamp_ns, x=x, frame_id="world"),
        }
        for host_ns, stamp_ns, x in (ground_truth_samples or [])
    ]
    return recorders


def _options(tmp_path: Path, **overrides: Any) -> DifferentialCaptureOptions:
    values: dict[str, Any] = {
        "output": tmp_path / "capture.json",
        "location": "Dock",
        "pair_id": "pair-01",
        "mode": DifferentialMode.R1_BRIDGE_NAV2,
        "simulation_epoch": "epoch-01",
        "reset_policy": ResetPolicy.NAV2_RESTART,
        "expected_source_root": tmp_path,
        "expected_git_sha": "1" * 40,
        "min_final_pose_samples": 2,
    }
    values.update(overrides)
    return DifferentialCaptureOptions(**values)


def _map_sample(x: float) -> dict[str, Any]:
    return {
        "fresh": True,
        "pose": {
            "x": x,
            "y": 2.0,
            "yaw": 0.0,
            "frame_id": "map",
            "source": "tf",
        },
    }


def test_final_window_fails_when_ros_clock_is_paused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paused_clock_ns = 10_000_000_000
    recorders = _recorders(
        clock_samples=[(100, paused_clock_ns), (150, paused_clock_ns), (200, paused_clock_ns)]
    )

    async def collect_paused_window(*args: object, **kwargs: object) -> tuple[Any, ...]:
        del args, kwargs
        return (
            100,
            200,
            paused_clock_ns,
            paused_clock_ns,
            False,
            [_map_sample(1.0), _map_sample(1.1)],
            [],
        )

    monkeypatch.setattr(runner, "_collect_final_map_window", collect_paused_window)

    result = asyncio.run(
        runner._sample_final_observation_window(
            cast(Any, object()),
            AppConfig(),
            _options(tmp_path),
            recorders,
            terminal_host_ns=90,
        )
    )

    assert result["status"] == "FAIL"
    assert "final_clock_did_not_advance_required_window" in result["failures"]


def test_final_evidence_excludes_samples_before_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recorders = _recorders(
        clock_samples=[
            (90, 9_000_000_000),
            (100, 10_000_000_000),
            (150, 11_000_000_000),
            (200, 12_000_000_000),
        ],
        ground_truth_samples=[
            (90, 9_000_000_000, 99.0),
            (150, 11_000_000_000, 1.0),
        ],
    )

    async def collect_terminal_window(*args: object, **kwargs: object) -> tuple[Any, ...]:
        del args, kwargs
        return (
            100,
            200,
            10_000_000_000,
            12_000_000_000,
            False,
            [_map_sample(1.0), _map_sample(1.2)],
            [],
        )

    monkeypatch.setattr(runner, "_collect_final_map_window", collect_terminal_window)
    result = asyncio.run(
        runner._sample_final_observation_window(
            cast(Any, object()),
            AppConfig(),
            _options(tmp_path),
            recorders,
            terminal_host_ns=95,
        )
    )
    calibration = GroundTruthCalibration(
        status="VERIFIED",
        scene_sha256="a" * 64,
        map_sha256="b" * 64,
        source="test calibration",
        world_frame_id="world",
        map_frame_id="map",
        translation_x_m=0.0,
        translation_y_m=0.0,
        rotation_yaw_rad=0.0,
        calibration_method="test",
        residual_m=0.0,
    )
    ground_truth = runner._ground_truth_samples(result["ground_truth_samples"], calibration)

    assert result["status"] == "PASS"
    assert [sample["host_monotonic_ns"] for sample in result["ground_truth_samples"]] == [150]
    assert len(ground_truth) == 1
    assert ground_truth[0]["world_pose"]["x"] == pytest.approx(1.0)
    assert [sample["pose"]["x"] for sample in result["map_pose_samples"]] == [1.0, 1.2]


def test_prepare_failure_persists_reloadable_schema_v1_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    options = _options(tmp_path)

    def fail_prepare(_: DifferentialCaptureOptions) -> Any:
        raise RuntimeError("unexpected preparation failure")

    monkeypatch.setattr(runner, "_prepare_capture", fail_prepare)

    result = asyncio.run(runner.capture_navigation_differential(options))
    reloaded = runner.load_differential_artifact(options.output)

    assert result["schema_version"] == 1
    assert result["overall"] == "failed"
    assert result["failure"] == {
        "type": "RuntimeError",
        "stage": "prepare",
        "detail": "unexpected preparation failure",
    }
    assert reloaded == result
    assert not list(tmp_path.glob(f".{options.output.name}.*.tmp"))


def test_live_preflight_runs_t0_and_t1_without_forwarding_goal(  # noqa: C901
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scene = tmp_path / "warehouse.usd"
    scene.write_text("#usda 1.0", encoding="utf-8")
    options = _options(
        tmp_path,
        live_preflight=True,
        scene_path=scene,
        live_scene_sha256="a" * 64,
    )
    config = AppConfig(deployment_mode="simulation")
    goal = CanonicalGoal.from_yaw(
        frame_id="map",
        x=1.0,
        y=2.0,
        yaw=0.0,
        simulation_epoch=options.simulation_epoch,
    )
    forwarded_goals: list[tuple[object, ...]] = []

    class Bridge:
        running = True

        async def configure_safety(self, **kwargs: object) -> None:
            del kwargs

        async def start(self) -> None:
            return None

        async def runtime_identity(self, *, pin: bool = False) -> object:
            assert pin is True

            class Identity:
                @staticmethod
                def to_payload() -> dict[str, object]:
                    return {}

            return Identity()

        async def nav_send(self, *args: object, **kwargs: object) -> None:
            forwarded_goals.append((*args, kwargs))
            raise AssertionError("live preflight must not forward a navigation goal")

    async def no_op_enrich(_bridge: object, identity: dict[str, Any]) -> None:
        identity["live_map_identity_initial"] = {"digest": "b" * 64, "frame_id": "map"}
        identity["controller_odom_topic"] = "/odom"
        identity["amcl_resample_interval"] = 3

    async def watch_topics(*args: object, **kwargs: object) -> list[int]:
        del kwargs
        recorders = cast(dict[str, runner._TopicRecorder], args[1])
        recorders["clock"].record({"clock": _stamp(1_000_000_000)})
        return []

    async def heartbeat(_bridge: object) -> None:
        return None

    async def start_state(*args: object, **kwargs: object) -> dict[str, Any]:
        del args
        assert kwargs["nomotion_max_attempts"] == 3
        return {"status": "PASS", "failures": [], "known_goal_ids": []}

    async def dispatch_state(*args: object, **kwargs: object) -> dict[str, Any]:
        del args
        assert kwargs["nomotion_max_attempts"] == 3
        return {"status": "PASS", "failures": [], "known_goal_ids": []}

    async def map_checkpoint(
        _bridge: object,
        *,
        label: str,
        expected: object,
    ) -> dict[str, Any]:
        return {
            "label": label,
            "status": "PASS",
            "observed_host_monotonic_ns": 1,
            "identity": expected,
            "failures": [],
        }

    async def cleanup(*args: object, **kwargs: object) -> dict[str, Any]:
        del args, kwargs
        return {
            "status": "PASS",
            "failures": [],
            "final_halt": {
                "status": "SKIP",
                "detail": "No motion was attempted.",
            },
            "bridge_shutdown": {"status": "PASS"},
        }

    monkeypatch.setattr(
        runner,
        "_prepare_capture",
        lambda _: (
            tmp_path / "config.toml",
            config,
            {"capability_id": "navigate", "goal": {}},
            goal,
            {
                "deployment_mode": "simulation",
                "source_root": str(tmp_path),
                "live_map_identity_initial": {"digest": "b" * 64, "frame_id": "map"},
            },
            None,
        ),
    )
    monkeypatch.setattr(runner, "RosBridgeClient", lambda **_: Bridge())
    monkeypatch.setattr(runner, "_enrich_live_identity", no_op_enrich)
    monkeypatch.setattr(runner, "_source_identity_failures", lambda _identity, **_kwargs: [])
    monkeypatch.setattr(runner, "_runtime_identity_failures", lambda _identity: [])
    monkeypatch.setattr(runner, "_watch_topics", watch_topics)
    monkeypatch.setattr(runner, "_heartbeat", heartbeat)
    monkeypatch.setattr(runner, "_collect_start_state", start_state)
    monkeypatch.setattr(runner, "_collect_dispatch_state", dispatch_state)
    monkeypatch.setattr(
        runner,
        "_capture_input_continuity",
        lambda _identity: {"status": "PASS", "failures": []},
    )
    monkeypatch.setattr(runner, "_capture_map_identity_checkpoint", map_checkpoint)
    monkeypatch.setattr(
        runner,
        "_capture_runtime_stack_checkpoint",
        lambda _identity, *, label: {"label": label, "status": "PASS", "failures": []},
    )
    monkeypatch.setattr(runner, "_safe_cleanup_live_capture", cleanup)
    monkeypatch.setattr(runner, "_record_end_generation", lambda _artifact: None)

    result = asyncio.run(runner.capture_navigation_differential(options))
    reloaded = runner.load_differential_artifact(options.output)

    assert result["overall"] == "preflight_only"
    assert result["live_preflight_requested"] is True
    assert result["execution_requested"] is False
    assert result["motion_attempted"] is False
    assert result["t0_scenario_start"]["status"] == "PASS"
    assert result["t1_pre_dispatch"]["status"] == "PASS"
    assert result["dispatch_observations"][0]["nav_send_forwarded_host_monotonic_ns"] is None
    assert result["checks"][-1] == {
        "id": "live_preflight",
        "status": "PASS",
        "detail": "All live pre-dispatch gates passed without forwarding a goal.",
    }
    assert result["cleanup"]["status"] == "PASS"
    assert forwarded_goals == []
    assert reloaded == result

    async def blocked_start(*args: object, **kwargs: object) -> dict[str, Any]:
        del args, kwargs
        return {"status": "FAIL", "failures": ["forced_start_gate_failure"]}

    monkeypatch.setattr(runner, "_collect_start_state", blocked_start)
    blocked_options = options.model_copy(update={"output": tmp_path / "blocked.json"})
    blocked = asyncio.run(runner.capture_navigation_differential(blocked_options))

    assert blocked["overall"] == "blocked"
    assert blocked["motion_attempted"] is False
    assert blocked["topic_samples"]["clock"][0]["message"] == {"clock": _stamp(1_000_000_000)}


def test_cleanup_failure_downgrades_blocked_capture_and_persists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scene = tmp_path / "warehouse.usd"
    scene.write_text("#usda 1.0", encoding="utf-8")
    options = _options(
        tmp_path,
        execute=True,
        confirmation=DIFFERENTIAL_EXECUTION_CONFIRMATION,
        scene_path=scene,
        live_scene_sha256="a" * 64,
    )
    config = AppConfig(deployment_mode="simulation")
    goal = CanonicalGoal.from_yaw(
        frame_id="map",
        x=1.0,
        y=2.0,
        yaw=0.0,
        simulation_epoch=options.simulation_epoch,
    )

    class Bridge:
        async def configure_safety(self, **kwargs: object) -> None:
            del kwargs

        async def start(self) -> None:
            return None

        async def runtime_identity(self, *, pin: bool = False) -> object:
            assert pin is True

            class Identity:
                @staticmethod
                def to_payload() -> dict[str, object]:
                    return {}

            return Identity()

    async def no_op_enrich(bridge: object, identity: dict[str, Any]) -> None:
        del bridge, identity

    async def failed_cleanup(*args: object, **kwargs: object) -> dict[str, Any]:
        del args, kwargs
        return {
            "status": "FAIL",
            "failures": [{"step": "bridge_shutdown", "detail": "forced failure"}],
            "final_halt": {"status": "SKIP"},
            "bridge_shutdown": {"status": "FAIL"},
        }

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
    monkeypatch.setattr(runner, "RosBridgeClient", lambda **_: Bridge())
    monkeypatch.setattr(runner, "_enrich_live_identity", no_op_enrich)
    monkeypatch.setattr(runner, "_source_identity_failures", lambda _identity, **_kwargs: [])
    monkeypatch.setattr(runner, "_runtime_identity_failures", lambda _: ["forced_identity_block"])
    monkeypatch.setattr(runner, "_safe_cleanup_live_capture", failed_cleanup)

    result = asyncio.run(runner.capture_navigation_differential(options))
    reloaded = runner.load_differential_artifact(options.output)

    assert result["overall"] == "cleanup_failed"
    assert result["overall_before_cleanup"] == "blocked"
    assert result["cleanup"]["status"] == "FAIL"
    assert reloaded == result


def test_r1_nav_send_failure_cancels_waiter_and_removes_listener() -> None:
    class Bridge:
        def __init__(self) -> None:
            self.listeners: list[Any] = []
            self.removed: list[Any] = []

        def on_event(self, event: str, callback: Any) -> None:
            assert event == "nav_result"
            self.listeners.append(callback)

        def off_event(self, event: str, callback: Any) -> None:
            assert event == "nav_result"
            self.listeners.remove(callback)
            self.removed.append(callback)

        async def nav_send(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            assert len(self.listeners) == 1
            raise RuntimeError("nav_send failed")

    bridge = Bridge()
    goal = CanonicalGoal.from_yaw(frame_id="map", x=1.0, y=2.0, yaw=0.0)

    with pytest.raises(RuntimeError, match="nav_send failed"):
        asyncio.run(
            runner._run_r1(
                cast(Any, bridge),
                goal,
                tag="navdiff-test",
                timeout_s=1.0,
            )
        )

    assert bridge.listeners == []
    assert len(bridge.removed) == 1
