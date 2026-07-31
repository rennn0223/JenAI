from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from jenai.acceptance import nav_differential_runner as runner
from jenai.acceptance.nav_differential import CanonicalGoal
from jenai.bridge import PoseInfo

ArtifactFactory = Callable[..., dict[str, object]]


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_separate_bridge_instances_with_same_effective_runtime_remain_pairable(
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
    right_identity = cast(dict[str, Any], right["runtime_identity"])
    middleware = cast(dict[str, Any], right_identity["ros_middleware"])
    descriptor = {
        **{key: value for key, value in middleware.items() if key != "descriptor_sha256"},
        "pid": 8484,
        "launch_nonce": "b" * 32,
        "process_start_ticks": 848400,
    }
    right_identity["ros_middleware"] = {
        **descriptor,
        "descriptor_sha256": _canonical_sha256(descriptor),
    }
    runner._apply_runtime_fingerprint(right_identity)

    report = runner.compare_differential_artifacts(left, right)

    assert report["included"] is True


def test_goal_uuid_first_seen_after_terminal_is_not_eligible(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    artifacts = [
        cast(dict[str, Any], differential_artifact_factory(mode=mode))
        for mode in ("R1_bridge_nav2", "R2_jenai_no_retry")
    ]
    for artifact in artifacts:
        timeline = cast(dict[str, Any], artifact["t1_goal_dispatch"])
        accepted = cast(list[dict[str, Any]], timeline["accepted_goal_observations"])[0]
        accepted["observed_host_monotonic_ns"] = 161
        for stream_name in ("topic_samples_at_dispatch_end", "topic_samples"):
            streams = cast(dict[str, Any], artifact[stream_name])
            status_samples = cast(list[dict[str, Any]], streams["action_status"])
            status_samples[-1]["host_monotonic_ns"] = 161

    report = runner.compare_differential_artifacts(*artifacts)

    assert report["included"] is False


def test_runtime_gate_rejects_multiple_navigate_to_pose_server_providers(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    artifact = cast(
        dict[str, Any],
        differential_artifact_factory(mode="R1_bridge_nav2"),
    )
    identity = cast(dict[str, Any], artifact["runtime_identity"])
    identity["navigate_to_pose_server_providers"] = [
        {"node": "/bt_navigator", "action_type": "nav2_msgs/action/NavigateToPose"},
        {"node": "/orphan_navigator", "action_type": "nav2_msgs/action/NavigateToPose"},
    ]
    runner._apply_runtime_fingerprint(identity)

    failures = runner._runtime_identity_failures(identity)

    assert "navigate_to_pose_server_uniqueness" in failures


def test_r2_proxy_journals_the_exact_fresh_pose_used_for_endpoint_verdict() -> None:
    class Delegate:
        async def nav_send(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def get_pose(self, *args: object, **kwargs: object) -> PoseInfo:
            del args, kwargs
            return PoseInfo(
                x=1.04,
                y=2.0,
                yaw=0.12,
                frame_id="map",
                source="/tf(map->base_link)",
                base_frame="base_link",
                initial_stamp_ns=10,
                stamp_ns=11,
                fresh_after_request=True,
            )

    async def scenario() -> list[dict[str, Any]]:
        recorder = runner._PoseObservationRecorder()
        clock = runner._TopicRecorder()
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
        return recorder.snapshot()

    observations = asyncio.run(scenario())

    endpoint = observations[-1]
    assert endpoint["purpose"] == "r2_completion_verdict"
    assert endpoint["attempt_tag"] == "attempt-1"
    assert endpoint["result"] == {
        "x": 1.04,
        "y": 2.0,
        "yaw": 0.12,
        "frame_id": "map",
        "base_frame": "base_link",
        "source": "/tf(map->base_link)",
        "initial_stamp_ns": 10,
        "stamp_ns": 11,
        "fresh_after_request": True,
    }


def test_r2_artifact_without_exact_endpoint_verdict_pose_is_ineligible(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    artifact = cast(
        dict[str, Any],
        differential_artifact_factory(mode="R2_jenai_no_retry"),
    )
    result = cast(dict[str, Any], artifact["jenai_result"])
    result["endpoint_pose_observation_ids"] = []

    detail = runner._comparison_eligibility_failure(artifact, "r2")

    assert detail is not None
    assert "r2_endpoint_pose_count" in detail


def test_r2_artifact_preserves_verdict_pose_separately_from_later_window(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    artifact = cast(
        dict[str, Any],
        differential_artifact_factory(mode="R2_jenai_no_retry"),
    )
    result = cast(dict[str, Any], artifact["jenai_result"])
    endpoint_id = cast(list[str], result["endpoint_pose_observation_ids"])[0]
    observations = cast(list[dict[str, Any]], artifact["pose_observations"])
    endpoint = next(item for item in observations if item["observation_id"] == endpoint_id)
    endpoint_result = cast(dict[str, Any], endpoint["result"])
    endpoint_result["x"] = 1.049

    validated = runner._validated_artifact(artifact)

    assert validated is not None
    validated_observations = cast(list[dict[str, Any]], validated["pose_observations"])
    validated_endpoint = next(
        item for item in validated_observations if item["observation_id"] == endpoint_id
    )
    assert cast(dict[str, Any], validated_endpoint["result"])["x"] == 1.049
    assert cast(dict[str, Any], validated["final_map_pose_median"])["x"] == 1.0


def test_input_continuity_detects_config_drift(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    config_path = tmp_path / "config.toml"
    locations_path = tmp_path / "locations.toml"
    bridge_path = tmp_path / "ros_bridge.py"
    config_path.write_text("version = 'one'\n", encoding="utf-8")
    locations_path.write_text("locations = []\n", encoding="utf-8")
    bridge_path.write_text("# reviewed bridge\n", encoding="utf-8")
    git_sha = "1" * 40
    identity = {
        "source_root": str(source_root),
        "git_sha": git_sha,
        "git_dirty": False,
        "config_path": str(config_path),
        "config_sha256": runner._sha256(config_path),
        "locations_path": str(locations_path),
        "locations_sha256": runner._sha256(locations_path),
        "bridge_script_path": str(bridge_path),
        "bridge_script_sha256": runner._sha256(bridge_path),
    }

    def command_output(command: list[str], **_: object) -> str:
        return git_sha if command[-1] == "HEAD" else ""

    monkeypatch.setattr(runner, "_command_output", command_output)
    assert runner._capture_input_continuity(identity)["status"] == "PASS"

    config_path.write_text("version = 'two'\n", encoding="utf-8")
    evidence = runner._capture_input_continuity(identity)

    assert evidence["status"] == "FAIL"
    assert "config_changed_since_prepare" in evidence["failures"]


def test_post_final_window_map_switch_makes_artifact_ineligible(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    artifact = cast(
        dict[str, Any],
        differential_artifact_factory(mode="R1_bridge_nav2"),
    )
    checkpoint = cast(dict[str, Any], artifact["post_final_window_map_identity_checkpoint"])
    observed = cast(dict[str, Any], checkpoint["identity"])
    checkpoint["identity"] = {**observed, "digest": "9" * 64}

    detail = runner._comparison_eligibility_failure(artifact, "r1")

    assert detail is not None
    assert "post_final_window_map_identity_changed" in detail
