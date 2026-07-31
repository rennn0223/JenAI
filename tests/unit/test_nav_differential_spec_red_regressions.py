from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from copy import deepcopy
from typing import Any, cast

import pytest

from jenai.acceptance import nav_differential_runner as runner
from jenai.acceptance.nav_differential import CanonicalGoal, PairClassification
from jenai.bridge import PoseInfo

ArtifactFactory = Callable[..., dict[str, object]]


def _failure_tokens(report: dict[str, Any]) -> set[str]:
    detail = report.get("detail")
    if not isinstance(detail, str) or ": " not in detail:
        return set()
    return set(detail.split(": ", 1)[1].split(", "))


def _install_distinct_map_checkpoints(artifact: dict[str, Any]) -> None:
    identity = cast(dict[str, Any], artifact["runtime_identity"])
    map_identity = deepcopy(identity["live_map_identity_initial"])
    legacy = artifact.pop("post_terminal_map_identity_checkpoint", None)
    legacy_checkpoint = legacy if isinstance(legacy, dict) else {}
    artifact["terminal_map_identity_checkpoint"] = {
        "label": "terminal",
        "status": "PASS",
        "observed_host_monotonic_ns": 165,
        "identity": deepcopy(map_identity),
        "failures": [],
    }
    artifact["post_final_window_map_identity_checkpoint"] = {
        "label": "post_final_window",
        "status": "PASS",
        "observed_host_monotonic_ns": legacy_checkpoint.get(
            "observed_host_monotonic_ns",
            235,
        ),
        "identity": deepcopy(map_identity),
        "failures": [],
    }


def _pair_with_distinct_map_checkpoints(
    differential_artifact_factory: ArtifactFactory,
) -> tuple[dict[str, Any], dict[str, Any]]:
    left = cast(
        dict[str, Any],
        differential_artifact_factory(mode="R1_bridge_nav2"),
    )
    right = cast(
        dict[str, Any],
        differential_artifact_factory(mode="R2_jenai_no_retry"),
    )
    _install_distinct_map_checkpoints(left)
    _install_distinct_map_checkpoints(right)
    return left, right


def _r2_endpoint_observation(artifact: dict[str, Any]) -> dict[str, Any]:
    result = cast(dict[str, Any], artifact["jenai_result"])
    endpoint_id = cast(list[str], result["endpoint_pose_observation_ids"])[0]
    observations = cast(list[dict[str, Any]], artifact["pose_observations"])
    return next(item for item in observations if item["observation_id"] == endpoint_id)


def test_distinct_terminal_and_post_final_map_checkpoints_are_eligible(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _pair_with_distinct_map_checkpoints(differential_artifact_factory)

    report = runner.compare_differential_artifacts(left, right)

    assert report["included"] is True
    assert report["classifications"] != [PairClassification.INSUFFICIENT_EVIDENCE]


@pytest.mark.parametrize(
    ("checkpoint_key", "expected_failure"),
    [
        ("terminal_map_identity_checkpoint", "terminal_map_identity_missing"),
        (
            "post_final_window_map_identity_checkpoint",
            "post_final_window_map_identity_missing",
        ),
    ],
)
def test_each_required_map_checkpoint_is_validated_offline(
    checkpoint_key: str,
    expected_failure: str,
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _pair_with_distinct_map_checkpoints(differential_artifact_factory)
    left.pop(checkpoint_key)

    report = runner.compare_differential_artifacts(left, right)

    assert report["included"] is False
    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]
    assert expected_failure in _failure_tokens(report)


@pytest.mark.parametrize(
    ("checkpoint_key", "expected_failure"),
    [
        ("terminal_map_identity_checkpoint", "terminal_map_identity_changed"),
        (
            "post_final_window_map_identity_checkpoint",
            "post_final_window_map_identity_changed",
        ),
    ],
)
def test_map_drift_at_either_checkpoint_is_ineligible(
    checkpoint_key: str,
    expected_failure: str,
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _pair_with_distinct_map_checkpoints(differential_artifact_factory)
    checkpoint = cast(dict[str, Any], left[checkpoint_key])
    observed = cast(dict[str, Any], checkpoint["identity"])
    checkpoint["identity"] = {**observed, "digest": "9" * 64}

    report = runner.compare_differential_artifacts(left, right)

    assert report["included"] is False
    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]
    assert expected_failure in _failure_tokens(report)


def test_r2_proxy_records_request_and_completion_ros_clock() -> None:
    clock = runner._TopicRecorder()

    class Delegate:
        async def nav_send(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def get_pose(self, *args: object, **kwargs: object) -> PoseInfo:
            del args, kwargs
            clock.record({"clock": {"sec": 5, "nanosec": 0}})
            return PoseInfo(
                x=1.04,
                y=2.0,
                yaw=0.12,
                frame_id="map",
                source="/tf(map->base_link)",
                base_frame="base_link",
                initial_stamp_ns=4_000_000_000,
                stamp_ns=4_500_000_000,
                fresh_after_request=True,
            )

    async def scenario() -> dict[str, Any]:
        recorder = runner._PoseObservationRecorder()
        clock.record({"clock": {"sec": 4, "nanosec": 0}})
        proxy = runner._ObservedNavBridge(
            cast(Any, Delegate()),
            simulation_epoch="epoch-01",
            expected_goal=CanonicalGoal.from_yaw(
                frame_id="map",
                x=1.0,
                y=2.0,
                yaw=0.0,
                simulation_epoch="epoch-01",
            ),
            on_nav_send=lambda *_: asyncio.sleep(0, result={"status": "PASS"}),
            pose_observations=recorder,
            clock=clock,
        )
        await proxy.nav_send(1.0, 2.0, 0.0, frame_id="map", tag="attempt-1")
        await proxy.get_pose(
            timeout=4.0,
            fresh=True,
            frame_id="map",
            base_frame="base_link",
        )
        return recorder.snapshot()[-1]

    endpoint = asyncio.run(scenario())

    assert endpoint["purpose"] == "r2_completion_verdict"
    assert endpoint["request_clock_ns"] == 4_000_000_000
    assert endpoint["completed_clock_ns"] == 5_000_000_000


@pytest.mark.parametrize("missing_field", ["request_clock_ns", "completed_clock_ns"])
def test_r2_endpoint_pose_without_ros_clock_is_ineligible(
    missing_field: str,
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = cast(
        dict[str, Any],
        differential_artifact_factory(mode="R1_bridge_nav2"),
    )
    right = cast(
        dict[str, Any],
        differential_artifact_factory(mode="R2_jenai_no_retry"),
    )
    _r2_endpoint_observation(right)[missing_field] = None

    report = runner.compare_differential_artifacts(left, right)

    assert report["included"] is False
    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]
    assert "r2_endpoint_pose_clock_missing" in _failure_tokens(report)


def test_r2_endpoint_pose_with_backwards_ros_clock_is_ineligible(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = cast(
        dict[str, Any],
        differential_artifact_factory(mode="R1_bridge_nav2"),
    )
    right = cast(
        dict[str, Any],
        differential_artifact_factory(mode="R2_jenai_no_retry"),
    )
    endpoint = _r2_endpoint_observation(right)
    endpoint["request_clock_ns"] = 4_900_000_000
    endpoint["completed_clock_ns"] = 4_800_000_000

    report = runner.compare_differential_artifacts(left, right)

    assert report["included"] is False
    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]
    assert "pose_observation_clock_moved_backwards" in _failure_tokens(report)


def test_r2_endpoint_pose_with_future_tf_is_ineligible(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = cast(
        dict[str, Any],
        differential_artifact_factory(mode="R1_bridge_nav2"),
    )
    right = cast(
        dict[str, Any],
        differential_artifact_factory(mode="R2_jenai_no_retry"),
    )
    endpoint = _r2_endpoint_observation(right)
    result = cast(dict[str, Any], endpoint["result"])
    result["stamp_ns"] = int(endpoint["completed_clock_ns"]) + 1

    report = runner.compare_differential_artifacts(left, right)

    assert report["included"] is False
    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]
    assert "pose_observation_transform_from_future" in _failure_tokens(report)


def test_final_window_scheduler_uses_common_terminal_relative_offset() -> None:
    async def arm(return_delay_s: float) -> float:
        terminal_ns = time.monotonic_ns()
        await asyncio.sleep(return_delay_s)
        started_ns = await runner._wait_for_terminal_relative_window_start(
            terminal_ns,
            delay_s=0.05,
        )
        return (started_ns - terminal_ns) / 1_000_000_000.0

    async def scenario() -> tuple[float, float]:
        return tuple(await asyncio.gather(arm(0.0), arm(0.03)))  # type: ignore[return-value]

    immediate_offset, delayed_offset = asyncio.run(scenario())

    assert immediate_offset >= 0.05
    assert delayed_offset >= 0.05
    assert abs(immediate_offset - delayed_offset) < 0.02


def test_r1_navigation_timeout_starts_after_actual_dispatch() -> None:
    class Bridge:
        def __init__(self) -> None:
            self.handlers: list[Callable[[dict[str, Any]], None]] = []

        def on_event(self, event: str, callback: Callable[[dict[str, Any]], None]) -> None:
            assert event == "nav_result"
            self.handlers.append(callback)

        def off_event(self, event: str, callback: Callable[[dict[str, Any]], None]) -> None:
            assert event == "nav_result"
            self.handlers.remove(callback)

        async def nav_send(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            await asyncio.sleep(0.05)
            for callback in tuple(self.handlers):
                callback(
                    {
                        "event": "nav_result",
                        "tag": "navdiff-timeout-parity",
                        "status": "succeeded",
                    }
                )

    goal = CanonicalGoal.from_yaw(frame_id="map", x=1.0, y=2.0, yaw=0.0)
    terminal, _ = asyncio.run(
        runner._run_r1(
            cast(Any, Bridge()),
            goal,
            tag="navdiff-timeout-parity",
            timeout_s=0.01,
        )
    )

    assert terminal["status"] == "succeeded"
