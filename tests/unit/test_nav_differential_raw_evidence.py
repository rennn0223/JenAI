from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from jenai.acceptance.nav_differential import PairClassification
from jenai.acceptance.nav_differential_runner import (
    _snapshot_topic_samples,
    _TopicRecorder,
    compare_differential_artifacts,
)

ArtifactFactory = Callable[..., dict[str, object]]


def test_dispatch_topic_snapshot_is_immutable_after_later_samples() -> None:
    recorder = _TopicRecorder()
    recorder.samples.append(
        {"host_monotonic_ns": 10, "message": {"clock": {"sec": 1, "nanosec": 0}}}
    )
    recorders: dict[str, _TopicRecorder] = {"clock": recorder}

    snapshot = _snapshot_topic_samples(recorders)
    recorder.samples[0]["message"]["clock"]["sec"] = 99
    recorder.samples.append(
        {"host_monotonic_ns": 20, "message": {"clock": {"sec": 2, "nanosec": 0}}}
    )

    assert snapshot == {
        "clock": [{"host_monotonic_ns": 10, "message": {"clock": {"sec": 1, "nanosec": 0}}}]
    }


def test_topic_recorder_owns_message_snapshot_at_callback_time() -> None:
    recorder = _TopicRecorder()
    message = {"clock": {"sec": 1, "nanosec": 0}}

    recorder.record(message)
    cast(dict[str, Any], message["clock"])["sec"] = 99

    assert recorder.samples[0]["message"] == {"clock": {"sec": 1, "nanosec": 0}}


def test_comparison_rejects_derived_amcl_pose_not_backed_by_raw_samples(
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
    window = cast(dict[str, Any], left["final_observation_window"])
    samples = cast(list[dict[str, Any]], window["valid_amcl_samples"])
    samples[1]["pose"] = {"x": 1.25, "y": 2.0, "yaw": 0.0}

    report = compare_differential_artifacts(left, right)

    assert report["included"] is False
    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


def _artifact_pair(
    factory: ArtifactFactory,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        cast(dict[str, Any], factory(mode="R1_bridge_nav2")),
        cast(dict[str, Any], factory(mode="R2_jenai_no_retry")),
    )


def _assert_ineligible(left: dict[str, Any], right: dict[str, Any]) -> None:
    report = compare_differential_artifacts(left, right)
    assert report["included"] is False
    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


def _set_source_lead(artifact: dict[str, Any], lead_ns: int) -> None:
    source_by_host = {35: 2_000_000_000 + lead_ns, 135: 4_000_000_000 + lead_ns}
    for snapshot_name in ("topic_samples", "topic_samples_at_dispatch_end"):
        streams = cast(dict[str, list[dict[str, Any]]], artifact[snapshot_name])
        for stream_name in ("amcl", "odom"):
            for sample in streams[stream_name]:
                host_ns = int(sample["host_monotonic_ns"])
                if host_ns not in source_by_host:
                    continue
                stamp_ns = source_by_host[host_ns]
                message = cast(dict[str, Any], sample["message"])
                header = cast(dict[str, Any], message["header"])
                header["stamp"] = {
                    "sec": stamp_ns // 1_000_000_000,
                    "nanosec": stamp_ns % 1_000_000_000,
                }
    timeline = cast(dict[str, Any], artifact["t1_goal_dispatch"])
    states = [
        cast(dict[str, Any], artifact["t0_scenario_start"]),
        cast(dict[str, Any], timeline["state_before_forward"]),
        cast(
            dict[str, Any],
            cast(list[dict[str, Any]], timeline["dispatch_observations"])[0][
                "state_before_forward"
            ],
        ),
    ]
    for state in states[1:]:
        state["amcl_nomotion_baseline_source_stamp_ns"] = source_by_host[35]
        attempts = cast(list[dict[str, Any]], state["amcl_nomotion_attempts"])
        attempts[-1]["baseline_source_stamp_ns"] = source_by_host[35]
    for state in states:
        capture_clock_ns = int(cast(dict[str, Any], state["amcl_source"])["capture_clock_ns"])
        for source_name in ("amcl_source", "odom_source"):
            source = cast(dict[str, Any], state[source_name])
            source["source_stamp_ns"] = capture_clock_ns + lead_ns
            source["source_age_ns"] = -lead_ns


def _mark_idle_status_windows(artifact: dict[str, Any]) -> None:
    for snapshot_name in ("topic_samples", "topic_samples_at_dispatch_end"):
        streams = cast(dict[str, list[dict[str, Any]]], artifact[snapshot_name])
        streams["action_status"] = [
            sample for sample in streams["action_status"] if int(sample["host_monotonic_ns"]) > 140
        ]
    timeline = cast(dict[str, Any], artifact["t1_goal_dispatch"])
    states = [
        cast(dict[str, Any], artifact["t0_scenario_start"]),
        cast(dict[str, Any], timeline["state_before_forward"]),
        cast(
            dict[str, Any],
            cast(list[dict[str, Any]], timeline["dispatch_observations"])[0][
                "state_before_forward"
            ],
        ),
    ]
    for state in states:
        state["action_status_source"] = {
            "fresh": True,
            "observation": "no_status_observed",
            "cutoff_host_monotonic_ns": state["cutoff_host_monotonic_ns"],
            "evaluated_host_monotonic_ns": state["evaluated_host_monotonic_ns"],
        }


def test_complete_raw_backed_pair_remains_comparison_eligible(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)

    report = compare_differential_artifacts(left, right)

    assert report["included"] is True


def test_comparison_accepts_bounded_future_source_lead(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    _set_source_lead(left, 100_000_000)
    _set_source_lead(right, 100_000_000)

    report = compare_differential_artifacts(left, right)

    assert report["included"] is True


def test_comparison_rejects_future_source_lead_beyond_sample_interval(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    _set_source_lead(left, 300_000_000)

    _assert_ineligible(left, right)


def test_comparison_rejects_missing_raw_evidence(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    left.pop("topic_samples")

    _assert_ineligible(left, right)


def test_comparison_rejects_unknown_evidence_derivation_version(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    left["evidence_derivation_version"] = None

    _assert_ineligible(left, right)


def test_comparison_rejects_missing_nomotion_acknowledgement(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    state = cast(dict[str, Any], left["t0_scenario_start"])
    state.pop("amcl_nomotion_update_acknowledged")

    _assert_ineligible(left, right)


def test_comparison_rejects_missing_nomotion_request_cutoff(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    state = cast(dict[str, Any], left["t0_scenario_start"])
    state.pop("amcl_nomotion_request_host_monotonic_ns")

    _assert_ineligible(left, right)


def test_comparison_rejects_tampered_nomotion_attempt_journal(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    state = cast(dict[str, Any], left["t0_scenario_start"])
    attempts = cast(list[dict[str, Any]], state["amcl_nomotion_attempts"])
    attempts[0]["newer_amcl_observed"] = False

    _assert_ineligible(left, right)


def test_comparison_rejects_tampered_nomotion_baseline_stamp(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    state = cast(dict[str, Any], left["t0_scenario_start"])
    state["amcl_nomotion_baseline_source_stamp_ns"] = 9_000_000_000

    _assert_ineligible(left, right)


def test_comparison_rejects_tampered_action_status_qos_contract(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    streams = cast(dict[str, dict[str, Any]], left["topic_stream_contract"])
    streams["action_status"]["qos_profile"] = "sensor_data"

    _assert_ineligible(left, right)


def test_comparison_accepts_idle_status_absence_only_for_observed_windows(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    for artifact in (left, right):
        cast(dict[str, Any], artifact["measurement_contract"])["preflight_sample_s"] = 1e-8
        _mark_idle_status_windows(artifact)

    report = compare_differential_artifacts(left, right)

    assert report["included"] is True


def test_comparison_rejects_idle_marker_when_status_was_observed_in_window(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    state = cast(dict[str, Any], left["t0_scenario_start"])
    state["action_status_source"] = {
        "fresh": True,
        "observation": "no_status_observed",
        "cutoff_host_monotonic_ns": state["cutoff_host_monotonic_ns"],
        "evaluated_host_monotonic_ns": state["evaluated_host_monotonic_ns"],
    }

    _assert_ineligible(left, right)


def test_comparison_rejects_idle_marker_when_status_was_observed_before_cutoff(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    _mark_idle_status_windows(left)
    streams = cast(dict[str, list[dict[str, Any]]], left["topic_samples_at_dispatch_end"])
    stale = {
        "host_monotonic_ns": 5,
        "message": {"status_list": []},
    }
    streams["action_status"].insert(0, stale)
    full = cast(dict[str, list[dict[str, Any]]], left["topic_samples"])
    full["action_status"].insert(0, stale.copy())

    _assert_ineligible(left, right)


def test_comparison_rejects_idle_marker_without_full_preflight_duration(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    _mark_idle_status_windows(left)

    _assert_ineligible(left, right)


def test_comparison_rejects_dispatch_snapshot_that_is_not_a_prefix(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    dispatch = cast(dict[str, list[dict[str, Any]]], left["topic_samples_at_dispatch_end"])
    message = cast(dict[str, Any], dispatch["amcl"][0]["message"])
    pose = cast(dict[str, Any], cast(dict[str, Any], message["pose"])["pose"])
    cast(dict[str, Any], pose["position"])["x"] = 9.0

    _assert_ineligible(left, right)


def test_comparison_rejects_raw_sample_captured_after_dispatch_return(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    dispatch = cast(dict[str, list[dict[str, Any]]], left["topic_samples_at_dispatch_end"])
    full = cast(dict[str, list[dict[str, Any]]], left["topic_samples"])
    dispatch["action_status"].append(full["action_status"][-1].copy())
    dispatch["action_status"][-1]["host_monotonic_ns"] = 166
    full["action_status"].append(dispatch["action_status"][-1].copy())

    _assert_ineligible(left, right)


def test_comparison_rejects_t0_pose_summary_not_derived_from_raw(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    t0 = cast(dict[str, Any], left["t0_scenario_start"])
    t0["amcl_pose"] = {"x": 0.25, "y": 0.0, "yaw": 0.0}

    _assert_ineligible(left, right)


def test_comparison_rejects_derived_odom_velocity_not_backed_by_raw(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    window = cast(dict[str, Any], left["final_observation_window"])
    samples = cast(list[dict[str, Any]], window["valid_odom_samples"])
    samples[1]["linear_velocity_mps"] = 0.01

    _assert_ineligible(left, right)


def test_comparison_rejects_uuid_summary_not_derived_from_raw_status(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    timeline = cast(dict[str, Any], left["t1_goal_dispatch"])
    forged_uuid = "02" * 16
    timeline["accepted_goal_uuid"] = forged_uuid
    observations = cast(list[dict[str, Any]], timeline["accepted_goal_observations"])
    observations[0]["goal_uuid"] = forged_uuid

    _assert_ineligible(left, right)


def test_comparison_rejects_raw_final_amcl_change_with_stale_derived_summary(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    raw = cast(dict[str, list[dict[str, Any]]], left["topic_samples"])
    message = cast(dict[str, Any], raw["amcl"][-1]["message"])
    pose = cast(dict[str, Any], cast(dict[str, Any], message["pose"])["pose"])
    cast(dict[str, Any], pose["position"])["x"] = 1.5

    _assert_ineligible(left, right)


def test_comparison_rejects_nonmonotonic_raw_clock(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    for field in ("topic_samples_at_dispatch_end", "topic_samples"):
        raw = cast(dict[str, list[dict[str, Any]]], left[field])
        clock = cast(dict[str, Any], raw["clock"][3]["message"])["clock"]
        assert isinstance(clock, dict)
        clock["sec"] = 0

    _assert_ineligible(left, right)


def test_comparison_rejects_map_sample_filtered_out_of_attempt_timeline(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    window = cast(dict[str, Any], left["final_observation_window"])
    attempts = cast(list[dict[str, Any]], window["map_pose_attempts"])
    attempts[3]["fresh"] = False
    attempts[3]["error"] = "stale transform"

    _assert_ineligible(left, right)


def test_comparison_rejects_top_level_final_pose_alias_drift(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    aliases = cast(list[dict[str, Any]], left["final_map_pose_samples"])
    aliases.pop()

    _assert_ineligible(left, right)


def test_comparison_rejects_terminal_tag_not_bound_to_dispatch(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    terminal = cast(dict[str, Any], left["nav2_terminal"])
    terminal["tag"] = "different-command"

    _assert_ineligible(left, right)


def test_comparison_rejects_r2_result_not_bound_to_single_attempt(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left, right = _artifact_pair(differential_artifact_factory)
    result = cast(dict[str, Any], right["jenai_result"])
    events = cast(list[dict[str, Any]], result["observed_nav_results"])
    events[0]["tag"] = "different-command"

    _assert_ineligible(left, right)
