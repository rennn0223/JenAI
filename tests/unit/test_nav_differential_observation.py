from __future__ import annotations

from jenai.acceptance.nav_differential import PairClassification
from jenai.acceptance.nav_differential_runner import (
    _new_goal_ids,
    _pose_from_message,
    _TopicRecorder,
    compare_differential_artifacts,
)


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
                    {
                        "goal_info": {"goal_id": {"uuid": [1] * 16}},
                        "status": 4,
                    },
                    {
                        "goal_info": {"goal_id": {"uuid": [2] * 16}},
                        "status": 2,
                    },
                ]
            },
        }
    ]

    observations = _new_goal_ids(
        recorder,
        before={"01" * 16},
        dispatched_at_ns=100,
    )

    assert observations == [
        {
            "goal_uuid": "02" * 16,
            "observed_host_monotonic_ns": 110,
        }
    ]


def _artifact(mode: str, *, reset_policy: str, pair_id: str = "pair-01") -> dict[str, object]:
    return {
        "pair_id": pair_id,
        "mode": mode,
        "reset_policy": reset_policy,
        "runtime_identity": {"fingerprint": "runtime"},
        "t0_scenario_start": {
            "simulation_epoch": "epoch",
            "map_to_base": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            "amcl_covariance_xy": 0.0,
            "stationary": True,
            "active_goal_ids": [],
        },
        "canonical_goal": {
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
        },
        "final_map_pose_median": {"x": 1.0, "y": 2.0, "yaw": 0.0},
        "execution_status": "succeeded",
    }


def test_pair_comparison_rejects_mixed_reset_policy() -> None:
    report = compare_differential_artifacts(
        _artifact("R1_bridge_nav2", reset_policy="nav2_restart"),
        _artifact("R2_jenai_no_retry", reset_policy="isaac_replay"),
    )

    assert report["included"] is False
    assert report["classifications"] == [PairClassification.PAIRING_GATE_FAILED]
    assert "reset_policy" in report["pairing_gate"]["failures"]


def test_pair_comparison_requires_one_r1_and_one_r2() -> None:
    report = compare_differential_artifacts(
        _artifact("R1_bridge_nav2", reset_policy="nav2_restart"),
        _artifact("R1_bridge_nav2", reset_policy="nav2_restart"),
    )

    assert report["included"] is False
    assert "differential_modes" in report["pairing_gate"]["failures"]
