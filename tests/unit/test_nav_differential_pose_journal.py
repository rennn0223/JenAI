from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

import pytest

from jenai.acceptance.nav_differential import PairClassification
from jenai.acceptance.nav_differential_runner import compare_differential_artifacts


def _pair(factory: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        cast(dict[str, Any], factory(mode="R1_bridge_nav2")),
        cast(dict[str, Any], factory(mode="R2_jenai_no_retry")),
    )


def _assert_insufficient(left: dict[str, Any], right: dict[str, Any]) -> None:
    report = compare_differential_artifacts(left, right)
    assert report["included"] is False
    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


def test_v3_pose_journal_fixture_is_eligible(differential_artifact_factory: Any) -> None:
    left, right = _pair(differential_artifact_factory)

    report = compare_differential_artifacts(left, right)

    assert report["included"] is True


def test_t0_summary_cannot_replace_raw_pose_observation(
    differential_artifact_factory: Any,
) -> None:
    left, right = _pair(differential_artifact_factory)
    for artifact in (left, right):
        cast(dict[str, Any], artifact["t0_scenario_start"])["map_to_base"] = {
            "x": 9.0,
            "y": 0.0,
            "yaw": 0.0,
        }

    _assert_insufficient(left, right)


def test_t1_public_and_nested_summaries_cannot_replace_raw_pose_observation(
    differential_artifact_factory: Any,
) -> None:
    left, right = _pair(differential_artifact_factory)
    for artifact in (left, right):
        timeline = cast(dict[str, Any], artifact["t1_goal_dispatch"])
        states = [
            cast(dict[str, Any], timeline["state_before_forward"]),
            cast(
                dict[str, Any],
                cast(list[dict[str, Any]], timeline["dispatch_observations"])[0][
                    "state_before_forward"
                ],
            ),
        ]
        for state in states:
            state["map_to_base"] = {"x": 9.0, "y": 0.0, "yaw": 0.0}

    _assert_insufficient(left, right)


def test_all_final_pose_projections_cannot_replace_raw_pose_observations(
    differential_artifact_factory: Any,
) -> None:
    left, right = _pair(differential_artifact_factory)
    for artifact in (left, right):
        window = cast(dict[str, Any], artifact["final_observation_window"])
        for key in ("map_pose_attempts", "map_pose_samples"):
            for sample in cast(list[dict[str, Any]], window[key]):
                cast(dict[str, Any], sample["pose"])["x"] = 9.0
        for sample in cast(list[dict[str, Any]], artifact["final_map_pose_samples"]):
            cast(dict[str, Any], sample["pose"])["x"] = 9.0
        cast(dict[str, Any], artifact["final_map_pose_median"])["x"] = 9.0

    _assert_insufficient(left, right)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda journal: journal.__setitem__(1, deepcopy(journal[0])),
        lambda journal: journal[0].__setitem__("sequence", 99),
        lambda journal: journal[0].__setitem__("purpose", "final_window"),
        lambda journal: journal[0].__setitem__("frame_id", "odom"),
        lambda journal: cast(dict[str, Any], journal[0]["result"]).__setitem__(
            "stamp_ns", 1_900_000_000
        ),
    ],
)
def test_malformed_pose_journal_is_ineligible(
    differential_artifact_factory: Any,
    mutate: Any,
) -> None:
    left, right = _pair(differential_artifact_factory)
    mutate(cast(list[dict[str, Any]], right["pose_observations"]))

    _assert_insufficient(left, right)


def test_v1_or_missing_pose_journal_is_ineligible(
    differential_artifact_factory: Any,
) -> None:
    left, right = _pair(differential_artifact_factory)
    right["evidence_derivation_version"] = 1
    right.pop("pose_observations")

    _assert_insufficient(left, right)
