from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from jenai.acceptance import nav_differential_runner as runner
from jenai.acceptance.nav_differential import CanonicalGoal, GroundTruthCalibration
from jenai.acceptance.nav_differential_runner import (
    DifferentialCaptureOptions,
    DifferentialMode,
    ResetPolicy,
)
from jenai.bridge import BridgeError
from jenai.config.models import AppConfig

ArtifactFactory = Callable[..., dict[str, object]]


def _options(tmp_path: Path, *, output: Path | None = None) -> DifferentialCaptureOptions:
    return DifferentialCaptureOptions(
        output=output or tmp_path / "capture.json",
        location="Dock",
        pair_id="pair-final-safety",
        mode=DifferentialMode.R1_BRIDGE_NAV2,
        simulation_epoch="epoch-final-safety",
        reset_policy=ResetPolicy.NAV2_RESTART,
        preflight_sample_s=0.001,
    )


def _unavailable_calibration() -> GroundTruthCalibration:
    return GroundTruthCalibration(
        status="GROUND_TRUTH_UNAVAILABLE",
        scene_sha256="e" * 64,
        map_sha256="b" * 64,
        source="regression test without calibrated ground truth",
    )


def _runtime_snapshot(identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_name_counts": copy.deepcopy(identity["node_name_counts"]),
        "navigate_to_pose_action_count": identity["navigate_to_pose_action_count"],
        "navigate_to_pose_server_providers": copy.deepcopy(
            identity["navigate_to_pose_server_providers"]
        ),
        "controller_odom_topic": identity["controller_odom_topic"],
        "nav2_tmux_session": identity["nav2_tmux_session"],
        "nav2_process_generation": copy.deepcopy(identity["nav2_process_generation"]),
        "controller_lifecycle": identity["controller_lifecycle"],
        "planner_lifecycle": identity["planner_lifecycle"],
        "bt_navigator_lifecycle": identity["bt_navigator_lifecycle"],
        "runtime_parameter_sha256": copy.deepcopy(identity["runtime_parameter_sha256"]),
    }


def _drifted_runtime_snapshot(identity: dict[str, Any], drift: str) -> dict[str, Any]:
    snapshot = _runtime_snapshot(identity)
    if drift == "provider":
        providers = cast(list[dict[str, str]], snapshot["navigate_to_pose_server_providers"])
        providers.append(
            {
                "node": "/orphan_navigator",
                "action_type": "nav2_msgs/action/NavigateToPose",
            }
        )
    elif drift == "node":
        cast(dict[str, int], snapshot["node_name_counts"])["/bt_navigator"] = 2
    elif drift == "lifecycle":
        snapshot["controller_lifecycle"] = "inactive [2]"
    elif drift == "parameter":
        cast(dict[str, str], snapshot["runtime_parameter_sha256"])["/controller_server"] = "9" * 64
    else:  # pragma: no cover - the parametrization is the exhaustive contract
        raise AssertionError(f"Unsupported drift case: {drift}")
    return snapshot


class _ResultBridge:
    def __init__(self) -> None:
        self.motion_calls = 0
        self._handlers: list[Callable[[dict[str, Any]], None]] = []

    def on_event(self, event: str, callback: Callable[[dict[str, Any]], None]) -> None:
        assert event == "nav_result"
        self._handlers.append(callback)

    def off_event(self, event: str, callback: Callable[[dict[str, Any]], None]) -> None:
        assert event == "nav_result"
        self._handlers.remove(callback)

    async def request_nomotion_update(self) -> bool:
        return True

    async def nav_send(
        self,
        x: float,
        y: float,
        yaw: float,
        *,
        frame_id: str,
        tag: str,
    ) -> None:
        del x, y, yaw, frame_id
        self.motion_calls += 1
        for callback in tuple(self._handlers):
            callback({"event": "nav_result", "tag": tag, "status": "succeeded"})


def _topic_recorders() -> dict[str, runner._TopicRecorder]:
    return {
        key: runner._TopicRecorder()
        for key in ("clock", "amcl", "odom", "action_status", "ground_truth")
    }


@pytest.mark.parametrize("drift", ["provider", "node", "lifecycle", "parameter"])
def test_runtime_stack_drift_after_initial_gate_blocks_nav_send(
    drift: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    differential_artifact_factory: ArtifactFactory,
) -> None:
    artifact = cast(
        dict[str, Any],
        differential_artifact_factory(
            mode="R1_bridge_nav2",
            epoch="epoch-final-safety",
        ),
    )
    identity = cast(dict[str, Any], artifact["runtime_identity"])
    t1_state = cast(dict[str, Any], artifact["t1_goal_dispatch"])["state_before_forward"]
    assert isinstance(t1_state, dict)
    bridge = _ResultBridge()
    options = _options(tmp_path)
    goal = CanonicalGoal.model_validate(artifact["canonical_goal"])

    async def collect_dispatch_state(*args: object, **kwargs: object) -> dict[str, Any]:
        del args, kwargs
        return copy.deepcopy(t1_state)

    async def map_checkpoint(*args: object, **kwargs: object) -> dict[str, Any]:
        del args, kwargs
        return copy.deepcopy(cast(dict[str, Any], t1_state["map_identity_checkpoint"]))

    async def skip_final_evidence(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr(runner, "_collect_dispatch_state", collect_dispatch_state)
    monkeypatch.setattr(
        runner,
        "_capture_input_continuity",
        lambda _identity: copy.deepcopy(t1_state["input_continuity"]),
    )
    monkeypatch.setattr(runner, "_capture_map_identity_checkpoint", map_checkpoint)
    monkeypatch.setattr(
        runner,
        "_nav2_runtime_identity",
        lambda *args, **kwargs: _drifted_runtime_snapshot(identity, drift),
    )
    monkeypatch.setattr(runner, "_record_final_live_evidence", skip_final_evidence)

    try:
        asyncio.run(
            runner._record_live_evidence(
                artifact,
                options,
                cast(Any, bridge),
                AppConfig(deployment_mode="simulation"),
                tmp_path / "config.toml",
                goal,
                {"capability_id": "navigate", "goal": {}},
                _unavailable_calibration(),
                _topic_recorders(),
                runner._PoseObservationRecorder(),
            )
        )
    except BridgeError:
        pass

    assert bridge.motion_calls == 0


def _recorders_from_artifact(artifact: dict[str, Any]) -> dict[str, runner._TopicRecorder]:
    streams = cast(dict[str, list[dict[str, Any]]], artifact["topic_samples"])
    recorders = _topic_recorders()
    for key, recorder in recorders.items():
        recorder.samples = copy.deepcopy(streams.get(key, []))
    return recorders


def test_post_terminal_runtime_parameter_drift_makes_artifact_ineligible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    differential_artifact_factory: ArtifactFactory,
) -> None:
    artifact = cast(
        dict[str, Any],
        differential_artifact_factory(
            mode="R1_bridge_nav2",
            epoch="epoch-final-safety",
        ),
    )
    identity = cast(dict[str, Any], artifact["runtime_identity"])
    existing_window = copy.deepcopy(cast(dict[str, Any], artifact["final_observation_window"]))
    existing_map_checkpoint = copy.deepcopy(
        cast(dict[str, Any], artifact["post_final_window_map_identity_checkpoint"])
    )

    async def final_window(*args: object, **kwargs: object) -> dict[str, Any]:
        del args, kwargs
        return copy.deepcopy(existing_window)

    async def map_checkpoint(*args: object, **kwargs: object) -> dict[str, Any]:
        del args, kwargs
        return copy.deepcopy(existing_map_checkpoint)

    monkeypatch.setattr(runner, "_sample_final_observation_window", final_window)
    monkeypatch.setattr(runner, "_capture_map_identity_checkpoint", map_checkpoint)
    monkeypatch.setattr(
        runner,
        "_nav2_runtime_identity",
        lambda *args, **kwargs: _drifted_runtime_snapshot(identity, "parameter"),
    )

    asyncio.run(
        runner._record_final_live_evidence(
            artifact,
            _options(tmp_path),
            cast(Any, object()),
            AppConfig(deployment_mode="simulation"),
            _unavailable_calibration(),
            _recorders_from_artifact(artifact),
            runner._PoseObservationRecorder(),
            cast(dict[str, Any], artifact["nav2_terminal"]),
            None,
            cast(dict[str, Any], artifact["t1_goal_dispatch"]),
            identity["live_map_identity_initial"],
        )
    )

    detail = runner._comparison_eligibility_failure(artifact, "r1")

    assert detail is not None
    assert "runtime" in detail or "parameter" in detail


def _goal_status(
    *,
    byte: int,
    status: int,
    stamp_ns: int,
) -> dict[str, object]:
    return {
        "goal_info": {
            "goal_id": {"uuid": [byte] * 16},
            "stamp": {
                "sec": stamp_ns // 1_000_000_000,
                "nanosec": stamp_ns % 1_000_000_000,
            },
        },
        "status": status,
    }


def _install_t1_terminal_foreign_goal(artifact: dict[str, Any]) -> None:
    foreign_uuid = "02" * 16
    timeline = cast(dict[str, Any], artifact["t1_goal_dispatch"])
    t1_states = [
        cast(dict[str, Any], timeline["state_before_forward"]),
        cast(dict[str, Any], cast(list[dict[str, Any]], timeline["dispatch_observations"])[0])[
            "state_before_forward"
        ],
    ]
    for state in t1_states:
        state["known_goal_ids"] = [foreign_uuid]
        state["active_goal_ids"] = []

    foreign = _goal_status(byte=2, status=4, stamp_ns=3_500_000_000)
    for stream_name in ("topic_samples_at_dispatch_end", "topic_samples"):
        streams = cast(dict[str, list[dict[str, Any]]], artifact[stream_name])
        samples = streams["action_status"]
        before_dispatch = next(sample for sample in samples if sample["host_monotonic_ns"] == 135)
        after_dispatch = next(sample for sample in samples if sample["host_monotonic_ns"] == 150)
        cast(dict[str, Any], before_dispatch["message"])["status_list"] = [copy.deepcopy(foreign)]
        actual = cast(
            list[dict[str, Any]], cast(dict[str, Any], after_dispatch["message"])["status_list"]
        )[0]
        cast(dict[str, Any], after_dispatch["message"])["status_list"] = [
            copy.deepcopy(foreign),
            actual,
        ]


def test_goal_uuid_derivation_excludes_ids_known_at_t1_not_only_t0(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    artifacts = [
        cast(dict[str, Any], differential_artifact_factory(mode=mode))
        for mode in ("R1_bridge_nav2", "R2_jenai_no_retry")
    ]
    for artifact in artifacts:
        _install_t1_terminal_foreign_goal(artifact)

    report = runner.compare_differential_artifacts(*artifacts)

    assert report["included"] is True, report


def _install_waiting_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    entered_motion: asyncio.Event,
    release_motion: asyncio.Event,
) -> None:
    config = AppConfig(deployment_mode="simulation")
    goal = CanonicalGoal.from_yaw(
        frame_id="map",
        x=1.0,
        y=2.0,
        yaw=0.0,
        simulation_epoch="epoch-final-safety",
    )

    monkeypatch.setattr(
        runner,
        "_prepare_capture",
        lambda _options: (
            tmp_path / "config.toml",
            config,
            {"capability_id": "navigate", "goal": {}},
            goal,
            {"deployment_mode": "simulation"},
            None,
        ),
    )
    monkeypatch.setattr(runner, "_complete_without_live_bridge", lambda *args: False)

    async def wait_during_motion(
        artifact: dict[str, Any],
        _options: DifferentialCaptureOptions,
        **kwargs: Any,
    ) -> None:
        resources = kwargs["resources"]
        resources.stage = "motion_dispatch"
        resources.motion_attempted = True
        entered_motion.set()
        await release_motion.wait()
        artifact["overall"] = "captured"

    monkeypatch.setattr(runner, "_capture_live_path", wait_during_motion)


def test_official_comparison_writer_cannot_claim_reserved_capture_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    differential_artifact_factory: ArtifactFactory,
) -> None:
    r1_path = tmp_path / "r1.json"
    r2_path = tmp_path / "r2.json"
    output = tmp_path / "shared-output.json"
    runner._write_capture_artifact(
        r1_path,
        cast(dict[str, Any], differential_artifact_factory(mode="R1_bridge_nav2")),
    )
    runner._write_capture_artifact(
        r2_path,
        cast(dict[str, Any], differential_artifact_factory(mode="R2_jenai_no_retry")),
    )

    async def scenario() -> tuple[Exception | None, object]:
        options = _options(tmp_path, output=output)
        entered_motion = asyncio.Event()
        release_motion = asyncio.Event()
        _install_waiting_capture(
            monkeypatch,
            tmp_path,
            entered_motion=entered_motion,
            release_motion=release_motion,
        )
        capture = asyncio.create_task(runner.capture_navigation_differential(options))
        await asyncio.wait_for(entered_motion.wait(), timeout=1.0)
        writer_error: Exception | None = None
        try:
            runner.load_and_compare(r1_path, r2_path, output)
        except Exception as exc:
            writer_error = exc
        finally:
            release_motion.set()
        capture_result = await asyncio.gather(capture, return_exceptions=True)
        return writer_error, capture_result[0]

    writer_error, capture_result = asyncio.run(scenario())

    assert isinstance(writer_error, FileExistsError)
    assert isinstance(capture_result, dict)
    assert runner.load_differential_artifact(output)["overall"] == "captured"


def test_motion_persistence_crash_leaves_recovery_marker_at_actual_output_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    options = _options(tmp_path)

    async def scenario() -> object:
        entered_motion = asyncio.Event()
        release_motion = asyncio.Event()
        _install_waiting_capture(
            monkeypatch,
            tmp_path,
            entered_motion=entered_motion,
            release_motion=release_motion,
        )

        def fail_persistence(path: Path, artifact: dict[str, Any]) -> dict[str, Any]:
            del path, artifact
            raise OSError("simulated persistence failure")

        monkeypatch.setattr(runner, "_write_capture_artifact", fail_persistence)
        capture = asyncio.create_task(runner.capture_navigation_differential(options))
        await asyncio.wait_for(entered_motion.wait(), timeout=1.0)
        release_motion.set()
        return (await asyncio.gather(capture, return_exceptions=True))[0]

    result = asyncio.run(scenario())

    assert isinstance(result, OSError)
    assert options.output.is_file()
    assert options.output.stat().st_size > 0
    assert (
        json.loads(options.output.read_text(encoding="utf-8"))["output_name"] == options.output.name
    )
