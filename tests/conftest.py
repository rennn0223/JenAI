from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from jenai.tools.registry import TOOL_RISK_REGISTRY


def _canonical_fixture_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _differential_middleware_identity() -> dict[str, object]:
    descriptor: dict[str, object] = {
        "schema_version": 1,
        "pid": 4242,
        "launch_nonce": "a" * 32,
        "boot_id": "12345678-1234-5678-1234-567812345678",
        "process_start_ticks": 12345,
        "rmw_implementation_requested": None,
        "rmw_implementation_effective": "rmw_fastrtps_cpp",
        "python_executable": "/usr/bin/python3.12",
        "python_version": "3.12.3",
        "ros_domain_id": 7,
        "dds_config_mode": "middleware_default",
        "dds_bindings": {},
        "dds_config_sha256": _canonical_fixture_sha256({}),
        "ros_environment_bindings": {},
        "ros_environment_sha256": _canonical_fixture_sha256({}),
    }
    return {**descriptor, "descriptor_sha256": _canonical_fixture_sha256(descriptor)}


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
    map_identity = {
        "algorithm": "sha256-occupancy-grid-v1",
        "digest": "b" * 64,
        "frame_id": "map",
        "source": "/map",
        "geometry": {
            "width": 100,
            "height": 100,
            "resolution": 0.05,
            "origin_x": -1.0,
            "origin_y": -1.0,
            "origin_yaw": 0.0,
        },
    }
    generation = {
        "boot_id": "12345678-1234-5678-1234-567812345678",
        "session": "nav2",
        "session_id": "$1",
        "session_created": 1000,
        "pane_id": "%1",
        "pane_pid": 101,
        "pane_start_ticks": 1001,
        "processes": [
            {
                "pid": 101,
                "ppid": 1,
                "start_ticks": 1001,
                "cmdline_sha256": "5" * 64,
            },
            {
                "pid": 102,
                "ppid": 101,
                "start_ticks": 1002,
                "cmdline_sha256": "6" * 64,
            },
        ],
    }
    identity: dict[str, object] = {
        "git_sha": git_sha,
        "git_dirty": False,
        "source_root": source_root,
        "expected_source_root": source_root,
        "expected_git_sha": git_sha,
        "expected_git_dirty": False,
        "reviewed_git_sha": git_sha,
        "jenai_import_path": f"{source_root}/src/jenai/__init__.py",
        "bridge_script_path": f"{source_root}/src/jenai/bridge/ros_bridge.py",
        "bridge_script_sha256": "f" * 64,
        "python_executable": "/usr/bin/python3.12",
        "python_version": "3.12.3",
        "ros_middleware": _differential_middleware_identity(),
        "deployment_mode": "simulation",
        "config_sha256": "a" * 64,
        "config_path": f"{source_root}/config.toml",
        "site_id": runtime,
        "site_map_sha256": "b" * 64,
        "site_locations_sha256": "c" * 64,
        "locations_sha256": "c" * 64,
        "locations_path": f"{source_root}/locations.toml",
        "nav_params_sha256": "d" * 64,
        "scene_sha256": "e" * 64,
        "live_scene_sha256": "e" * 64,
        "live_map_sha256": "b" * 64,
        "site_map_frame": "map",
        "robot_base_frame": "base_link",
        "live_map_frame": "map",
        "live_map_identity_initial": map_identity,
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
        "navigate_to_pose_server_providers": [
            {
                "node": "/bt_navigator",
                "action_type": "nav2_msgs/action/NavigateToPose",
            }
        ],
        "controller_odom_topic": "/chassis/odom",
        "nav2_tmux_session": "nav2",
        "nav2_process_generation": generation,
        "nav2_process_generation_end": deepcopy(generation),
        "amcl_resample_interval": 3,
        "runtime_parameter_sha256": {
            "/amcl": "1" * 64,
            "/controller_server": "2" * 64,
            "/planner_server": "3" * 64,
            "/bt_navigator": "4" * 64,
        },
    }
    _apply_runtime_fingerprint(identity)
    return identity


def _differential_state(
    epoch: str,
    *,
    cutoff_host_ns: int,
    clock_start_host_ns: int,
    evaluated_host_ns: int,
    clock_start_ns: int,
    clock_end_ns: int,
    source_stamp_ns: int,
    baseline_source_stamp_ns: int,
    map_pose_observation_id: str,
) -> dict[str, object]:
    source_host_ns = evaluated_host_ns - 5
    return {
        "status": "PASS",
        "failures": [],
        "simulation_epoch": epoch,
        "cutoff_host_monotonic_ns": cutoff_host_ns,
        "evaluated_host_monotonic_ns": evaluated_host_ns,
        "map_pose_observation_id": map_pose_observation_id,
        "map_to_base": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        "amcl_pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        "odom_pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        "amcl_covariance_xy": 0.01,
        "linear_velocity_mps": 0.0,
        "angular_velocity_rps": 0.0,
        "stationary": True,
        "active_goal_ids": [],
        "known_goal_ids": [],
        "clock_evidence": [
            {"host_monotonic_ns": clock_start_host_ns, "clock_ns": clock_start_ns},
            {"host_monotonic_ns": evaluated_host_ns, "clock_ns": clock_end_ns},
        ],
        "clock_samples_ns": [clock_start_ns, clock_end_ns],
        "clock_advancing": True,
        "clock_backwards": False,
        "ground_truth_required": False,
        "ground_truth_source": None,
        "ground_truth_source_frame_id": None,
        "ground_truth_world_pose": None,
        "ground_truth_map_pose": None,
        "amcl_source": {
            "host_monotonic_ns": source_host_ns,
            "host_age_ns": 5,
            "source_stamp_ns": source_stamp_ns,
            "sample_clock_ns": clock_start_ns,
            "capture_clock_ns": clock_end_ns,
            "source_age_ns": clock_end_ns - source_stamp_ns,
            "fresh": True,
        },
        "odom_source": {
            "host_monotonic_ns": source_host_ns,
            "host_age_ns": 5,
            "source_stamp_ns": source_stamp_ns,
            "sample_clock_ns": clock_start_ns,
            "capture_clock_ns": clock_end_ns,
            "source_age_ns": clock_end_ns - source_stamp_ns,
            "fresh": True,
        },
        "action_status_source": {
            "host_monotonic_ns": source_host_ns,
            "host_age_ns": 5,
            "fresh": True,
            "schema_valid": True,
        },
        "amcl_nomotion_update_acknowledged": True,
        "amcl_nomotion_request_host_monotonic_ns": source_host_ns,
        "amcl_nomotion_baseline_source_stamp_ns": baseline_source_stamp_ns,
        "amcl_nomotion_attempts": [
            {
                "sequence": 1,
                "request_host_monotonic_ns": source_host_ns,
                "completed_host_monotonic_ns": evaluated_host_ns,
                "baseline_source_stamp_ns": baseline_source_stamp_ns,
                "acknowledged": True,
                "newer_amcl_observed": True,
            }
        ],
    }


def _stamp(nanoseconds: int) -> dict[str, int]:
    return {"sec": nanoseconds // 1_000_000_000, "nanosec": nanoseconds % 1_000_000_000}


def _clock_sample(host_ns: int, clock_ns: int) -> dict[str, object]:
    return {"host_monotonic_ns": host_ns, "message": {"clock": _stamp(clock_ns)}}


def _pose_message(
    stamp_ns: int,
    *,
    x: float,
    y: float,
    odometry: bool,
) -> dict[str, object]:
    pose = {
        "position": {"x": x, "y": y, "z": 0.0},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    }
    message: dict[str, object] = {
        "header": {"stamp": _stamp(stamp_ns), "frame_id": "map"},
        "pose": {"pose": pose},
    }
    if odometry:
        message["twist"] = {
            "twist": {
                "linear": {"x": 0.0, "y": 0.0, "z": 0.0},
                "angular": {"x": 0.0, "y": 0.0, "z": 0.0},
            }
        }
    else:
        covariance = [0.0] * 36
        covariance[0] = 0.01
        covariance[7] = 0.01
        cast_pose = message["pose"]
        assert isinstance(cast_pose, dict)
        cast_pose["covariance"] = covariance
    return message


def _localization_sample(
    host_ns: int,
    stamp_ns: int,
    *,
    x: float,
    y: float,
    odometry: bool,
) -> dict[str, object]:
    return {
        "host_monotonic_ns": host_ns,
        "message": _pose_message(stamp_ns, x=x, y=y, odometry=odometry),
    }


def _action_status_sample(
    host_ns: int,
    *,
    goal_status: int | None = None,
    goal_stamp_ns: int | None = None,
) -> dict[str, object]:
    statuses: list[dict[str, object]] = []
    if goal_status is not None and goal_stamp_ns is not None:
        statuses.append(
            {
                "goal_info": {
                    "goal_id": {"uuid": [1] * 16},
                    "stamp": _stamp(goal_stamp_ns),
                },
                "status": goal_status,
            }
        )
    return {"host_monotonic_ns": host_ns, "message": {"status_list": statuses}}


def _raw_topic_samples(final_x: float) -> tuple[dict[str, object], dict[str, object]]:
    dispatch = {
        "clock": [
            _clock_sample(20, 1_000_000_000),
            _clock_sample(40, 2_000_000_000),
            _clock_sample(120, 3_000_000_000),
            _clock_sample(140, 4_000_000_000),
            _clock_sample(150, 4_500_000_000),
        ],
        "amcl": [
            _localization_sample(25, 500_000_000, x=0.0, y=0.0, odometry=False),
            _localization_sample(35, 1_000_000_000, x=0.0, y=0.0, odometry=False),
            _localization_sample(135, 3_000_000_000, x=0.0, y=0.0, odometry=False),
        ],
        "odom": [
            _localization_sample(35, 1_000_000_000, x=0.0, y=0.0, odometry=True),
            _localization_sample(135, 3_000_000_000, x=0.0, y=0.0, odometry=True),
        ],
        "action_status": [
            _action_status_sample(35),
            _action_status_sample(135),
            _action_status_sample(150, goal_status=2, goal_stamp_ns=4_000_000_000),
        ],
    }
    complete = deepcopy(dispatch)
    complete["clock"].extend(
        [
            _clock_sample(170, 10_000_000_000),
            _clock_sample(190, 11_000_000_000),
            _clock_sample(230, 12_000_000_000),
        ]
    )
    for key, odometry in (("amcl", False), ("odom", True)):
        complete[key].extend(
            [
                _localization_sample(170, 10_000_000_000, x=final_x, y=2.0, odometry=odometry),
                _localization_sample(190, 11_000_000_000, x=final_x, y=2.0, odometry=odometry),
                _localization_sample(230, 12_000_000_000, x=final_x, y=2.0, odometry=odometry),
            ]
        )
    return dispatch, complete


def _differential_stream_stamps() -> tuple[int, int, int]:
    return (10_000_000_000, 11_000_000_000, 12_000_000_000)


def _pose_lookup_observation(
    *,
    observation_id: str,
    sequence: int,
    purpose: str,
    host_ns: int,
    clock_ns: int,
    x: float,
    y: float,
    attempt_tag: str | None = None,
) -> dict[str, object]:
    return {
        "observation_id": observation_id,
        "sequence": sequence,
        "purpose": purpose,
        "attempt_tag": attempt_tag,
        "request_host_monotonic_ns": host_ns,
        "completed_host_monotonic_ns": host_ns,
        "request_clock_ns": clock_ns - 2,
        "completed_clock_ns": clock_ns,
        "fresh_requested": True,
        "frame_id": "map",
        "base_frame": "base_link",
        "timeout_s": 3.0 if purpose != "final_window" else 0.5,
        "status": "SUCCESS",
        "result": {
            "x": x,
            "y": y,
            "yaw": 0.0,
            "frame_id": "map",
            "base_frame": "base_link",
            "source": "tf2",
            "initial_stamp_ns": clock_ns - 2,
            "stamp_ns": clock_ns - 1,
            "fresh_after_request": True,
        },
        "raw_result": None,
        "error_type": None,
        "error_detail": None,
    }


def _differential_amcl_samples(final_x: float) -> list[dict[str, object]]:
    return [
        {
            "host_monotonic_ns": host_ns,
            "host_age_ns": 0,
            "source_stamp_ns": stamp,
            "sample_clock_ns": stamp,
            "capture_clock_ns": stamp,
            "source_age_ns": 0,
            "fresh": True,
            "message": _pose_message(stamp, x=final_x, y=2.0, odometry=False),
            "pose": {"x": final_x, "y": 2.0, "yaw": 0.0},
            "covariance_xy": 0.01,
        }
        for host_ns, stamp in zip((170, 190, 230), _differential_stream_stamps(), strict=True)
    ]


def _differential_odom_samples(final_x: float) -> list[dict[str, object]]:
    return [
        {
            "host_monotonic_ns": host_ns,
            "host_age_ns": 0,
            "source_stamp_ns": stamp,
            "sample_clock_ns": stamp,
            "capture_clock_ns": stamp,
            "source_age_ns": 0,
            "fresh": True,
            "message": _pose_message(stamp, x=final_x, y=2.0, odometry=True),
            "pose": {"x": final_x, "y": 2.0, "yaw": 0.0},
            "linear_velocity_mps": 0.0,
            "angular_velocity_rps": 0.0,
        }
        for host_ns, stamp in zip((170, 190, 230), _differential_stream_stamps(), strict=True)
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
        target_record = {
            "capability_id": "navigate",
            "frame_id": "map",
            "locations_sha256": "c" * 64,
            "pose": {"x": goal_x, "y": 2.0, "yaw": 0.0},
            "resolved_id": "loc-dock",
            "resolved_name": "Dock",
        }
        target_stable_binding = {
            "canonical_goal_sha256": _canonical_fixture_sha256(goal),
            "canonical_record_sha256": _canonical_fixture_sha256(target_record),
            "capability_id": "navigate",
            "locations_sha256": "c" * 64,
        }
        target_binding = {
            "requested_query": "Dock",
            "resolved_name": "Dock",
            "resolved_id": "loc-dock",
            "frame_id": "map",
            "pose": {"x": goal_x, "y": 2.0, "yaw": 0.0},
            "capability_id": "navigate",
            "locations_sha256": "c" * 64,
            "canonical_record_sha256": target_stable_binding["canonical_record_sha256"],
            "canonical_goal_sha256": target_stable_binding["canonical_goal_sha256"],
            "binding_sha256": _canonical_fixture_sha256(target_stable_binding),
        }
        map_clock_stamps = [10_000_000_000 + (index * 2_000_000_000) // 9 for index in range(10)]
        t0_state = _differential_state(
            epoch,
            cutoff_host_ns=10,
            clock_start_host_ns=20,
            evaluated_host_ns=40,
            clock_start_ns=1_000_000_000,
            clock_end_ns=2_000_000_000,
            source_stamp_ns=1_000_000_000,
            baseline_source_stamp_ns=500_000_000,
            map_pose_observation_id="pose-t0",
        )
        t1_state = _differential_state(
            epoch,
            cutoff_host_ns=110,
            clock_start_host_ns=120,
            evaluated_host_ns=140,
            clock_start_ns=3_000_000_000,
            clock_end_ns=4_000_000_000,
            source_stamp_ns=3_000_000_000,
            baseline_source_stamp_ns=1_000_000_000,
            map_pose_observation_id="pose-t1",
        )
        runtime_identity = _differential_runtime_identity(runtime)
        expected_hashes = {
            field: runtime_identity[field]
            for field in ("config_sha256", "locations_sha256", "bridge_script_sha256")
        }
        input_continuity = {
            "status": "PASS",
            "observed_host_monotonic_ns": 137,
            "observed_hashes": expected_hashes,
            "observed_git_sha": runtime_identity["git_sha"],
            "observed_git_dirty": False,
            "failures": [],
        }
        map_identity = deepcopy(runtime_identity["live_map_identity_initial"])
        runtime_stack = {
            field: deepcopy(runtime_identity[field])
            for field in (
                "node_name_counts",
                "navigate_to_pose_action_count",
                "navigate_to_pose_server_providers",
                "controller_odom_topic",
                "amcl_resample_interval",
                "nav2_tmux_session",
                "nav2_process_generation",
                "controller_lifecycle",
                "planner_lifecycle",
                "bt_navigator_lifecycle",
                "runtime_parameter_sha256",
            )
        }
        t1_state["input_continuity"] = input_continuity
        t1_state["map_identity_checkpoint"] = {
            "label": "pre_dispatch",
            "status": "PASS",
            "observed_host_monotonic_ns": 138,
            "identity": deepcopy(map_identity),
            "failures": [],
        }
        t1_state["runtime_stack_checkpoint"] = {
            "label": "pre_dispatch",
            "status": "PASS",
            "observed_host_monotonic_ns": 139,
            "expected": deepcopy(runtime_stack),
            "observed": deepcopy(runtime_stack),
            "failures": [],
        }
        raw_dispatch, raw_complete = _raw_topic_samples(final_x)
        amcl_samples = _differential_amcl_samples(final_x)
        odom_samples = _differential_odom_samples(final_x)
        map_pose_attempts = [
            {
                "pose_observation_id": f"pose-final-{index}",
                "requested_host_monotonic_ns": 170 + (index * 60) // 9,
                "observed_host_monotonic_ns": 170 + (index * 60) // 9,
                "capture_clock_ns": stamp,
                "fresh": True,
                "pose": {
                    "x": final_x,
                    "y": 2.0,
                    "yaw": 0.0,
                    "frame_id": "map",
                    "source": "tf2",
                },
            }
            for index, stamp in enumerate(map_clock_stamps)
        ]
        endpoint_pose_observations = (
            [
                _pose_lookup_observation(
                    observation_id="pose-r2-verdict",
                    sequence=2,
                    purpose="r2_completion_verdict",
                    host_ns=162,
                    clock_ns=4_800_000_000,
                    x=final_x,
                    y=2.0,
                    attempt_tag="attempt-1",
                )
            ]
            if mode == "R2_jenai_no_retry"
            else []
        )
        final_sequence_offset = 3 if endpoint_pose_observations else 2
        pose_observations = [
            _pose_lookup_observation(
                observation_id="pose-t0",
                sequence=0,
                purpose="t0_start",
                host_ns=35,
                clock_ns=1_800_000_000,
                x=0.0,
                y=0.0,
            ),
            _pose_lookup_observation(
                observation_id="pose-t1",
                sequence=1,
                purpose="t1_pre_dispatch",
                host_ns=135,
                clock_ns=3_800_000_000,
                x=0.0,
                y=0.0,
            ),
            *endpoint_pose_observations,
            *[
                _pose_lookup_observation(
                    observation_id=f"pose-final-{index}",
                    sequence=index + final_sequence_offset,
                    purpose="final_window",
                    host_ns=170 + (index * 60) // 9,
                    clock_ns=stamp,
                    x=final_x,
                    y=2.0,
                )
                for index, stamp in enumerate(map_clock_stamps)
            ],
        ]
        artifact: dict[str, object] = {
            "schema_version": 1,
            "evidence_derivation_version": 5,
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
                "min_final_state_samples": 3,
                "min_final_ground_truth_samples": 3,
                "final_wall_timeout_s": 15.0,
                "max_start_speed_mps": 0.02,
                "max_start_yaw_rate_rps": 0.03,
                "max_covariance_xy": 0.1,
                "max_pair_start_position_delta_m": 0.05,
                "max_pair_start_yaw_delta_rad": 0.05,
            },
            "started_at": "2026-07-31T00:00:00+00:00",
            "finished_at": "2026-07-31T00:01:00+00:00",
            "runtime_identity": runtime_identity,
            "pose_observations": pose_observations,
            "topic_stream_contract": {
                "clock": {
                    "topic": "/clock",
                    "message_type": "rosgraph_msgs/msg/Clock",
                    "qos_profile": "sensor_data",
                },
                "amcl": {
                    "topic": "/amcl_pose",
                    "message_type": "geometry_msgs/msg/PoseWithCovarianceStamped",
                    "qos_profile": "transient_local",
                },
                "odom": {
                    "topic": "/chassis/odom",
                    "message_type": "nav_msgs/msg/Odometry",
                    "qos_profile": "sensor_data",
                },
                "action_status": {
                    "topic": "/navigate_to_pose/_action/status",
                    "message_type": "action_msgs/msg/GoalStatusArray",
                    "qos_profile": "transient_local",
                },
            },
            "checks": [],
            "overall": "captured",
            "t0_scenario_start": t0_state,
            "canonical_goal": goal,
            "target_binding": target_binding,
            "t1_goal_dispatch": {
                "status": "PASS",
                "failures": [],
                "dispatch_count": 1,
                "actual_goal": goal,
                "state_before_forward": deepcopy(t1_state),
                "request_host_monotonic_ns": 100,
                "return_host_monotonic_ns": 165,
                "dispatch_observations": [
                    {
                        "tag": "attempt-1",
                        "nav_send_invoked_host_monotonic_ns": 110,
                        "nav_send_forwarded_host_monotonic_ns": 140,
                        "forward_completed_host_monotonic_ns": 145,
                        "actual_goal": goal,
                        "state_before_forward": deepcopy(t1_state),
                    }
                ],
                "nav_send_forwarded_host_monotonic_ns": 140,
                "accepted_goal_observations": [
                    {
                        "goal_uuid": "01" * 16,
                        "status": 2,
                        "goal_stamp_ns": 4_000_000_000,
                        "capture_clock_ns": 4_500_000_000,
                        "goal_stamp_age_ns": 500_000_000,
                        "goal_stamp_fresh": True,
                        "observed_host_monotonic_ns": 150,
                    }
                ],
                "accepted_goal_uuid": "01" * 16,
                "goal_uuid_evidence": "INFERRED_UNIQUE_ACTION_STATUS",
            },
            "nav2_terminal": {
                "status": "succeeded",
                "tag": "attempt-1",
                "observed_host_monotonic_ns": 160,
            },
            "final_observation_window": {
                "status": "PASS",
                "failures": [],
                "terminal_host_monotonic_ns": 160,
                "start_host_monotonic_ns": 170,
                "end_host_monotonic_ns": 230,
                "start_clock_ns": 10_000_000_000,
                "end_clock_ns": 12_000_000_000,
                "required_duration_ns": 2_000_000_000,
                "coverage_slack_ns": 400_000_000,
                "clock_backwards": False,
                "clock_samples": [
                    {"host_monotonic_ns": 170, "clock_ns": 10_000_000_000},
                    {"host_monotonic_ns": 190, "clock_ns": 11_000_000_000},
                    {"host_monotonic_ns": 230, "clock_ns": 12_000_000_000},
                ],
                "map_pose_samples": [deepcopy(sample) for sample in map_pose_attempts],
                "map_pose_attempts": deepcopy(map_pose_attempts),
                "amcl_samples": deepcopy(amcl_samples),
                "valid_amcl_samples": deepcopy(amcl_samples),
                "odom_samples": deepcopy(odom_samples),
                "valid_odom_samples": deepcopy(odom_samples),
                "ground_truth_samples": [],
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
            "terminal_map_identity_checkpoint": {
                "label": "terminal",
                "status": "PASS",
                "observed_host_monotonic_ns": 165,
                "identity": deepcopy(map_identity),
                "failures": [],
            },
            "post_final_window_map_identity_checkpoint": {
                "label": "post_final_window",
                "status": "PASS",
                "observed_host_monotonic_ns": 235,
                "identity": deepcopy(map_identity),
                "failures": [],
            },
            "post_final_window_runtime_stack_checkpoint": {
                "label": "post_final_window",
                "status": "PASS",
                "observed_host_monotonic_ns": 236,
                "expected": deepcopy(runtime_stack),
                "observed": deepcopy(runtime_stack),
                "failures": [],
            },
            "post_cleanup_input_continuity": {
                **input_continuity,
                "observed_host_monotonic_ns": 250,
            },
            "final_map_pose_median": {"x": final_x, "y": 2.0, "yaw": 0.0},
            "final_map_pose_samples": deepcopy(map_pose_attempts),
            "ground_truth_samples": [],
            "final_ground_truth_map_median": None,
            "topic_samples_at_dispatch_end": raw_dispatch,
            "topic_samples": raw_complete,
            "execution_status": status,
        }
        if mode == "R2_jenai_no_retry":
            artifact["jenai_result"] = {
                "execution_status": status,
                "effective_experimental_config": {"nav_endpoint_retry_limit": 0},
                "endpoint_pose_observation_ids": ["pose-r2-verdict"],
                "navigation_attempts": [{"tag": "attempt-1"}],
                "observed_nav_results": [
                    {
                        "status": "succeeded",
                        "tag": "attempt-1",
                        "observed_host_monotonic_ns": 160,
                    }
                ],
            }
        return artifact

    return build
