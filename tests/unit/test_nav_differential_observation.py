from __future__ import annotations

from jenai.acceptance.nav_differential import PairClassification
from jenai.acceptance.nav_differential_runner import (
    _new_goal_ids,
    _pose_from_message,
    _TopicRecorder,
    compare_differential_artifacts,
)


def _goal_status(uuid: list[int], status: int) -> dict[str, object]:
    return {
        "goal_info": {
            "goal_id": {"uuid": uuid},
            "stamp": {"sec": 12, "nanosec": 34},
        },
        "status": status,
    }


def test_amcl_pose_with_covariance_is_parsed_from_nested_pose() -> None:
    message = {
        "pose": {
            "pose": {
                "position": {"x": 1.25, "y": -2.5, "z": 0.0},
                "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            },
            "covariance": [0.0] * 36,
        }
    }

    pose = _pose_from_message(message)

    assert pose is not None
    assert pose.x == 1.25
    assert pose.y == -2.5
    assert pose.yaw == 0.0


def test_new_goal_observer_ignores_uuid_already_known_at_t0() -> None:
    recorder = _TopicRecorder()
    recorder.samples = [
        {
            "host_monotonic_ns": 110,
            "message": {
                "status_list": [
                    _goal_status([1] * 16, 4),
                    _goal_status([2] * 16, 2),
                ]
            },
        }
    ]
    clock = _TopicRecorder()
    clock.samples = [
        {
            "host_monotonic_ns": 109,
            "message": {"clock": {"sec": 12, "nanosec": 34}},
        }
    ]

    observations = _new_goal_ids(
        recorder,
        clock,
        before={"01" * 16},
        dispatched_at_ns=100,
        max_age_s=1.0,
    )

    assert len(observations) == 1
    assert observations[0]["goal_uuid"] == "02" * 16
    assert observations[0]["goal_stamp_fresh"] is True


def _artifact(mode: str, *, reset_policy: str, pair_id: str = "pair-01") -> dict[str, object]:
    goal = {
        "frame_id": "map",
        "x": 1.0,
        "y": 2.0,
        "yaw": 0.0,
        "qx": 0.0,
        "qy": 0.0,
        "qz": 0.0,
        "qw": 1.0,
        "clock_domain": "ros",
        "simulation_epoch": "epoch",
    }
    return {
        "schema_version": 1,
        "run_id": f"run-{mode}",
        "pair_id": pair_id,
        "mode": mode,
        "reset_policy": reset_policy,
        "execution_requested": True,
        "measurement_contract": {
            "preflight_sample_s": 1.0,
            "final_sample_s": 2.0,
            "final_window_start_delay_s": 0.0,
            "sample_interval_s": 0.2,
            "max_topic_age_s": 1.0,
            "max_calibration_residual_m": 0.02,
            "min_final_pose_samples": 10,
            "final_wall_timeout_s": 15.0,
            "max_start_speed_mps": 0.02,
            "max_start_yaw_rate_rps": 0.03,
            "max_covariance_xy": 0.1,
        },
        "started_at": "2026-07-31T00:00:00+00:00",
        "finished_at": "2026-07-31T00:01:00+00:00",
        "runtime_identity": {"fingerprint": "runtime"},
        "checks": [],
        "overall": "captured",
        "t0_scenario_start": {
            "status": "PASS",
            "simulation_epoch": "epoch",
            "map_to_base": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            "amcl_covariance_xy": 0.0,
            "stationary": True,
            "active_goal_ids": [],
        },
        "canonical_goal": goal,
        "t1_goal_dispatch": {"status": "PASS", "actual_goal": goal},
        "final_observation_window": {"status": "PASS"},
        "cleanup": {"status": "PASS"},
        "final_map_pose_median": {"x": 1.0, "y": 2.0, "yaw": 0.0},
        "execution_status": "succeeded",
    }


def test_pair_comparison_rejects_mixed_reset_policy(
    differential_artifact_factory,
) -> None:
    report = compare_differential_artifacts(
        differential_artifact_factory(mode="R1_bridge_nav2", reset_policy="nav2_restart"),
        differential_artifact_factory(mode="R2_jenai_no_retry", reset_policy="isaac_replay"),
    )

    assert report["included"] is False
    assert report["classifications"] == [PairClassification.PAIRING_GATE_FAILED]
    assert "reset_policy" in report["pairing_gate"]["failures"]


def test_pair_comparison_requires_one_r1_and_one_r2(
    differential_artifact_factory,
) -> None:
    report = compare_differential_artifacts(
        differential_artifact_factory(mode="R1_bridge_nav2"),
        differential_artifact_factory(mode="R1_bridge_nav2"),
    )

    assert report["included"] is False
    assert "differential_modes" in report["pairing_gate"]["failures"]
