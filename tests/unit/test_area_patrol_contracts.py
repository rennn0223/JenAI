from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from jenai.workflows.area_patrol import (
    AreaPatrolRequest,
    AreaPatrolWorkflow,
    AreaStatus,
    InspectionPoint,
    InspectionResult,
    InspectionVerdict,
    NavigationResult,
    PatrolArea,
    PatrolMissionStatus,
    StepVerdict,
)


class ContractRuntime:
    def __init__(self) -> None:
        self.workflow: AreaPatrolWorkflow | None = None
        self.navigation_verdicts: list[StepVerdict] = [StepVerdict.SUCCEEDED]
        self.raise_navigation = False
        self.raise_inspection = False
        self.raise_home = False
        self.cancel_after_first_inspection = False
        self.calls: list[tuple[str, str]] = []

    async def navigate(self, point: InspectionPoint) -> NavigationResult:
        self.calls.append(("navigate", point.location))
        if self.raise_navigation:
            raise RuntimeError("navigation transport failed")
        verdict = (
            self.navigation_verdicts.pop(0)
            if len(self.navigation_verdicts) > 1
            else self.navigation_verdicts[0]
        )
        return NavigationResult(verdict, verdict.value)

    async def inspect(self, point: InspectionPoint) -> InspectionResult:
        self.calls.append(("inspect", point.location))
        if self.raise_inspection:
            raise RuntimeError("camera transport failed")
        if self.cancel_after_first_inspection:
            if self.workflow is None:
                raise RuntimeError("test runtime is missing its workflow")
            self.workflow.cancel()
        return InspectionResult(
            InspectionVerdict.VERIFIED,
            "observation preserved",
            ("image://evidence",),
        )

    async def return_home(self, location: str) -> NavigationResult:
        self.calls.append(("return_home", location))
        if self.raise_home:
            raise RuntimeError("home transport failed")
        return NavigationResult(StepVerdict.SUCCEEDED, "home reached")


def _request(**overrides: object) -> AreaPatrolRequest:
    values: dict[str, object] = {
        "mission_id": "contract-mission",
        "target": "laboratory",
        "home_location": "Dock",
        "max_navigation_retries": 1,
        "return_home": True,
    }
    values.update(overrides)
    return AreaPatrolRequest(**values)


def _area(*locations: str, area_id: str = "required") -> PatrolArea:
    return PatrolArea(
        area_id=area_id,
        display_name="Required area",
        inspection_points=tuple(InspectionPoint(location) for location in locations),
    )


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: InspectionPoint(" "), "location"),
        (lambda: PatrolArea("", "Area", (InspectionPoint("A"),)), "identity"),
        (lambda: PatrolArea("a", "Area", ()), "inspection point"),
        (
            lambda: AreaPatrolRequest("", "target", "Dock"),
            "mission identity",
        ),
        (
            lambda: AreaPatrolRequest("mission", "", "Dock"),
            "mission identity",
        ),
        (
            lambda: AreaPatrolRequest("mission", "target", "Dock", max_navigation_retries=6),
            "between 0 and 5",
        ),
        (
            lambda: AreaPatrolRequest("mission", "target", None),
            "home_location",
        ),
    ],
)
def test_domain_models_reject_invalid_mission_inputs(factory, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


def test_plan_rejects_duplicate_area_ids_and_workflow_is_single_use() -> None:
    runtime = ContractRuntime()
    workflow = AreaPatrolWorkflow(runtime)
    first = _area("A", area_id="same")
    duplicate = _area("B", area_id="SAME")

    with pytest.raises(ValueError, match="duplicate area"):
        asyncio.run(workflow.run(_request(), (first, duplicate)))

    report = asyncio.run(workflow.run(_request(), (first,)))
    assert report.status is PatrolMissionStatus.SUCCESS
    with pytest.raises(RuntimeError, match="exactly one mission"):
        asyncio.run(workflow.run(_request(), (first,)))


def test_retry_exhaustion_is_failed_and_never_inspects() -> None:
    runtime = ContractRuntime()
    runtime.navigation_verdicts = [
        StepVerdict.RETRYABLE_FAILURE,
        StepVerdict.RETRYABLE_FAILURE,
    ]

    report = asyncio.run(
        AreaPatrolWorkflow(runtime).run(
            _request(max_navigation_retries=1),
            (_area("A"),),
        )
    )

    assert report.status is PatrolMissionStatus.FAILED
    assert report.areas[0].status is AreaStatus.FAILED
    assert report.areas[0].points[0].navigation_attempts == 2
    assert all(call[0] != "inspect" for call in runtime.calls)


def test_adapter_failures_are_typed_and_never_become_success() -> None:
    navigation_runtime = ContractRuntime()
    navigation_runtime.raise_navigation = True
    navigation_report = asyncio.run(
        AreaPatrolWorkflow(navigation_runtime).run(_request(), (_area("A"),))
    )
    assert navigation_report.status is PatrolMissionStatus.FAILED
    assert "navigation adapter error" in navigation_report.areas[0].points[0].detail

    inspection_runtime = ContractRuntime()
    inspection_runtime.raise_inspection = True
    inspection_report = asyncio.run(
        AreaPatrolWorkflow(inspection_runtime).run(_request(), (_area("A"),))
    )
    assert inspection_report.status is PatrolMissionStatus.REQUIRES_HUMAN_REVIEW
    assert "inspection adapter error" in inspection_report.areas[0].points[0].detail

    home_runtime = ContractRuntime()
    home_runtime.raise_home = True
    home_report = asyncio.run(AreaPatrolWorkflow(home_runtime).run(_request(), (_area("A"),)))
    assert home_report.status is PatrolMissionStatus.PARTIAL_SUCCESS
    assert home_report.returned_home is False
    assert home_report.home_detail == "return-home adapter error: home transport failed"


def test_return_home_is_optional_but_report_remains_explicit() -> None:
    request = replace(_request(), return_home=False, home_location=None)
    runtime = ContractRuntime()

    report = asyncio.run(AreaPatrolWorkflow(runtime).run(request, (_area("A"),)))

    assert report.status is PatrolMissionStatus.SUCCESS
    assert report.returned_home is False
    assert report.home_detail is None
    assert all(call[0] != "return_home" for call in runtime.calls)


def test_cancel_in_last_area_stops_before_remaining_point_and_return_home() -> None:
    runtime = ContractRuntime()
    workflow = AreaPatrolWorkflow(runtime)
    runtime.workflow = workflow
    runtime.cancel_after_first_inspection = True

    report = asyncio.run(workflow.run(_request(), (_area("A", "B"),)))

    assert report.status is PatrolMissionStatus.ABORTED
    assert report.returned_home is False
    assert report.coverage_ratio == 0.0
    assert report.areas[0].status is AreaStatus.FAILED
    assert runtime.calls == [("navigate", "A"), ("inspect", "A")]
