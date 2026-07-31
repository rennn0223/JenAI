from __future__ import annotations

import pytest

from jenai.tools.registry import TOOL_RISK_REGISTRY


@pytest.fixture(autouse=True)
def _restore_tool_risk_registry():
    """`TOOL_RISK_REGISTRY` is populated once at import time by the real tool
    modules and is otherwise treated as read-only shared state. Snapshot and
    restore it around every test so a test that mutates it (even via a
    `finally` block) can never leak state into unrelated tests.
    """
    snapshot = dict(TOOL_RISK_REGISTRY)
    yield
    TOOL_RISK_REGISTRY.clear()
    TOOL_RISK_REGISTRY.update(snapshot)


def _differential_runtime_identity(runtime: str) -> dict[str, object]:
    from jenai.acceptance.nav_differential_runner import _apply_runtime_fingerprint

    git_sha = "1" * 40
    source_root = "/tmp/JenAI-reviewed"
    identity: dict[str, object] = {
        "git_sha": git_sha,
        "git_dirty": False,
        "source_root": source_root,
        "expected_source_root": source_root,
        "expected_git_sha": git_sha,
        "expected_git_dirty": False,
        "reviewed_git_sha": git_sha,
        "jenai_import_path": f"{source_root}/src/jenai/__init__.py",
        "deployment_mode": "simulation",
        "config_sha256": "a" * 64,
        "site_id": runtime,
        "site_map_sha256": "b" * 64,
        "site_locations_sha256": "c" * 64,
        "locations_sha256": "c" * 64,
        "nav_params_sha256": "d" * 64,
        "scene_sha256": "e" * 64,
        "live_scene_sha256": "e" * 64,
        "live_map_sha256": "b" * 64,
        "site_map_frame": "map",
        "live_map_frame": "map",
        "bridge_domain_id": "7",
        "controller_lifecycle": "active [3]",
        "planner_lifecycle": "active [3]",
        "bt_navigator_lifecycle": "active [3]",
        "node_name_counts": {
            "/amcl": 1,
            "/controller_server": 1,
            "/planner_server": 1,
            "/bt_navigator": 1,
        },
        "navigate_to_pose_action_count": 1,
        "runtime_parameter_sha256": {
            "/amcl": "1" * 64,
            "/controller_server": "2" * 64,
            "/planner_server": "3" * 64,
            "/bt_navigator": "4" * 64,
        },
    }
    _apply_runtime_fingerprint(identity)
    return identity


def _differential_state(epoch: str) -> dict[str, object]:
    return {
        "status": "PASS",
        "failures": [],
        "simulation_epoch": epoch,
        "cutoff_host_monotonic_ns": 80,
        "evaluated_host_monotonic_ns": 100,
        "map_to_base": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        "amcl_pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        "odom_pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        "amcl_covariance_xy": 0.01,
        "linear_velocity_mps": 0.0,
        "angular_velocity_rps": 0.0,
        "stationary": True,
        "active_goal_ids": [],
        "clock_evidence": [
            {"host_monotonic_ns": 90, "clock_ns": 10_000_000_000},
            {"host_monotonic_ns": 100, "clock_ns": 11_000_000_000},
        ],
        "clock_samples_ns": [10_000_000_000, 11_000_000_000],
        "clock_advancing": True,
        "clock_backwards": False,
        "amcl_source": {
            "host_monotonic_ns": 95,
            "host_age_ns": 5,
            "source_stamp_ns": 10_500_000_000,
            "sample_clock_ns": 10_500_000_000,
            "capture_clock_ns": 11_000_000_000,
            "source_age_ns": 500_000_000,
            "fresh": True,
        },
        "odom_source": {
            "host_monotonic_ns": 95,
            "host_age_ns": 5,
            "source_stamp_ns": 10_500_000_000,
            "sample_clock_ns": 10_500_000_000,
            "capture_clock_ns": 11_000_000_000,
            "source_age_ns": 500_000_000,
            "fresh": True,
        },
        "action_status_source": {
            "host_monotonic_ns": 95,
            "host_age_ns": 5,
            "fresh": True,
            "schema_valid": True,
        },
    }


def _differential_stream_stamps() -> tuple[int, int, int]:
    return (10_000_000_000, 11_000_000_000, 12_000_000_000)


def _differential_amcl_samples() -> list[dict[str, object]]:
    return [
        {
            "source_stamp_ns": stamp,
            "fresh": True,
            "pose": {"x": 1.0, "y": 2.0, "yaw": 0.0},
            "covariance_xy": 0.01,
        }
        for stamp in _differential_stream_stamps()
    ]


def _differential_odom_samples() -> list[dict[str, object]]:
    return [
        {
            "source_stamp_ns": stamp,
            "fresh": True,
            "pose": {"x": 1.0, "y": 2.0, "yaw": 0.0},
            "linear_velocity_mps": 0.0,
            "angular_velocity_rps": 0.0,
        }
        for stamp in _differential_stream_stamps()
    ]


@pytest.fixture
def differential_artifact_factory():
    def build(
        *,
        mode: str,
        runtime: str = "runtime",
        epoch: str = "epoch",
        status: str = "succeeded",
        final_x: float = 1.0,
        goal_x: float = 1.0,
        reset_policy: str = "nav2_restart",
        pair_id: str = "pair-01",
    ) -> dict[str, object]:
        goal = {
            "frame_id": "map",
            "x": goal_x,
            "y": 2.0,
            "yaw": 0.0,
            "qx": 0.0,
            "qy": 0.0,
            "qz": 0.0,
            "qw": 1.0,
            "stamp_ns": None,
            "clock_domain": "ros",
            "simulation_epoch": epoch,
            "stamp_fresh": None,
        }
        map_clock_stamps = [10_000_000_000 + (index * 2_000_000_000) // 9 for index in range(10)]
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
                "sample_interval_s": 0.2,
                "max_topic_age_s": 1.0,
                "max_calibration_residual_m": 0.02,
                "min_final_pose_samples": 10,
                "min_final_state_samples": 3,
                "min_final_ground_truth_samples": 3,
                "final_wall_timeout_s": 15.0,
                "max_start_speed_mps": 0.02,
                "max_start_yaw_rate_rps": 0.03,
                "max_covariance_xy": 0.1,
            },
            "started_at": "2026-07-31T00:00:00+00:00",
            "finished_at": "2026-07-31T00:01:00+00:00",
            "runtime_identity": _differential_runtime_identity(runtime),
            "checks": [],
            "overall": "captured",
            "t0_scenario_start": _differential_state(epoch),
            "canonical_goal": goal,
            "t1_goal_dispatch": {
                "status": "PASS",
                "failures": [],
                "dispatch_count": 1,
                "actual_goal": goal,
                "state_before_forward": _differential_state(epoch),
                "request_host_monotonic_ns": 100,
                "return_host_monotonic_ns": 160,
                "dispatch_observations": [
                    {
                        "nav_send_invoked_host_monotonic_ns": 110,
                        "nav_send_forwarded_host_monotonic_ns": 120,
                        "forward_completed_host_monotonic_ns": 130,
                        "actual_goal": goal,
                        "state_before_forward": _differential_state(epoch),
                    }
                ],
                "nav_send_forwarded_host_monotonic_ns": 120,
                "accepted_goal_observations": [
                    {
                        "goal_uuid": "01" * 16,
                        "goal_stamp_fresh": True,
                        "observed_host_monotonic_ns": 140,
                    }
                ],
                "accepted_goal_uuid": "01" * 16,
                "goal_uuid_evidence": "INFERRED_UNIQUE_ACTION_STATUS",
            },
            "nav2_terminal": {"status": "succeeded", "observed_host_monotonic_ns": 150},
            "final_observation_window": {
                "status": "PASS",
                "failures": [],
                "terminal_host_monotonic_ns": 150,
                "start_host_monotonic_ns": 170,
                "end_host_monotonic_ns": 200,
                "start_clock_ns": 10_000_000_000,
                "end_clock_ns": 12_000_000_000,
                "required_duration_ns": 2_000_000_000,
                "coverage_slack_ns": 400_000_000,
                "clock_backwards": False,
                "clock_samples": [
                    {"host_monotonic_ns": 170, "clock_ns": 10_000_000_000},
                    {"host_monotonic_ns": 200, "clock_ns": 12_000_000_000},
                ],
                "map_pose_samples": [
                    {
                        "fresh": True,
                        "capture_clock_ns": stamp,
                        "pose": {"x": final_x, "y": 2.0, "yaw": 0.0},
                    }
                    for stamp in map_clock_stamps
                ],
                "valid_amcl_samples": _differential_amcl_samples(),
                "valid_odom_samples": _differential_odom_samples(),
                "verified_ground_truth_samples": [],
                "ground_truth_required": False,
                "stationary": True,
            },
            "cleanup": {
                "status": "PASS",
                "failures": [],
                "final_halt": {
                    "status": "PASS",
                    "zero_velocity_command_published": True,
                    "navigation_cancel_requested": True,
                    "navigation_cancel_acknowledged": True,
                },
                "unwatch": {"status": "PASS", "failures": []},
                "bridge_shutdown": {"status": "PASS"},
            },
            "final_map_pose_median": {"x": final_x, "y": 2.0, "yaw": 0.0},
            "final_ground_truth_map_median": None,
            "execution_status": status,
        }

    return build
