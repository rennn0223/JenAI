from __future__ import annotations

import pytest

from jenai.schemas import TaskOutcome
from jenai.tools.area_patrol_agent_tools import _report_outcome
from jenai.workflows.area_patrol import PatrolMissionStatus


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (PatrolMissionStatus.SUCCESS, TaskOutcome.SUCCEEDED),
        (PatrolMissionStatus.ABORTED, TaskOutcome.CANCELLED),
        (PatrolMissionStatus.FAILED, TaskOutcome.FAILED),
        (PatrolMissionStatus.PARTIAL_SUCCESS, TaskOutcome.PARTIAL),
        (PatrolMissionStatus.REQUIRES_HUMAN_REVIEW, TaskOutcome.PARTIAL),
    ],
)
def test_patrol_status_maps_to_honest_agent_outcome(
    status: PatrolMissionStatus,
    outcome: TaskOutcome,
) -> None:
    assert _report_outcome(status) is outcome
