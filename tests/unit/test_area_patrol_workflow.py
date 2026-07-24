from __future__ import annotations

import asyncio
from collections import defaultdict

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


class FakeRuntime:
    def __init__(self) -> None:
        self.navigation: dict[str, list[StepVerdict]] = defaultdict(lambda: [StepVerdict.SUCCEEDED])
        self.inspections: dict[str, InspectionResult] = {}
        self.home = NavigationResult(StepVerdict.SUCCEEDED, "home reached")
        self.calls: list[tuple[str, str]] = []

    async def navigate(self, point: InspectionPoint) -> NavigationResult:
        self.calls.append(("navigate", point.location))
        verdicts = self.navigation[point.location]
        verdict = verdicts.pop(0) if len(verdicts) > 1 else verdicts[0]
        return NavigationResult(verdict, f"{point.location}: {verdict.value}")

    async def inspect(self, point: InspectionPoint) -> InspectionResult:
        self.calls.append(("inspect", point.location))
        return self.inspections.get(
            point.location,
            InspectionResult(
                verdict=InspectionVerdict.VERIFIED,
                detail="observation recorded",
                evidence=("image://frame",),
            ),
        )

    async def return_home(self, location: str) -> NavigationResult:
        self.calls.append(("return_home", location))
        return self.home


def _area(
    area_id: str,
    *locations: str,
    required: bool = True,
) -> PatrolArea:
    return PatrolArea(
        area_id=area_id,
        display_name=area_id.replace("_", " ").title(),
        inspection_points=tuple(InspectionPoint(location=item) for item in locations),
        required=required,
    )


def _request(**overrides: object) -> AreaPatrolRequest:
    values: dict[str, object] = {
        "mission_id": "mission-1",
        "target": "laboratory",
        "home_location": "Home",
        "max_navigation_retries": 1,
    }
    values.update(overrides)
    return AreaPatrolRequest(**values)


def test_full_required_coverage_returns_home_and_succeeds() -> None:
    runtime = FakeRuntime()
    workflow = AreaPatrolWorkflow(runtime)

    report = asyncio.run(
        workflow.run(
            _request(),
            (_area("entrance", "Entrance"), _area("equipment", "Equipment")),
        )
    )

    assert report.status is PatrolMissionStatus.SUCCESS
    assert report.coverage_ratio == 1.0
    assert report.returned_home is True
    assert [area.status for area in report.areas] == [
        AreaStatus.COMPLETED,
        AreaStatus.COMPLETED,
    ]
    assert runtime.calls == [
        ("navigate", "Entrance"),
        ("inspect", "Entrance"),
        ("navigate", "Equipment"),
        ("inspect", "Equipment"),
        ("return_home", "Home"),
    ]
    assert [event.sequence for event in report.events] == list(range(1, len(report.events) + 1))


def test_optional_area_failure_does_not_reduce_required_coverage() -> None:
    runtime = FakeRuntime()
    runtime.navigation["Storage"] = [StepVerdict.BLOCKED]

    report = asyncio.run(
        AreaPatrolWorkflow(runtime).run(
            _request(),
            (
                _area("entrance", "Entrance"),
                _area("storage", "Storage", required=False),
            ),
        )
    )

    assert report.status is PatrolMissionStatus.SUCCESS
    assert report.coverage_ratio == 1.0
    assert report.areas[1].status is AreaStatus.BLOCKED


def test_mandatory_area_blocked_is_partial_and_still_returns_home() -> None:
    runtime = FakeRuntime()
    runtime.navigation["Equipment"] = [StepVerdict.BLOCKED]

    report = asyncio.run(
        AreaPatrolWorkflow(runtime).run(
            _request(),
            (_area("entrance", "Entrance"), _area("equipment", "Equipment")),
        )
    )

    assert report.status is PatrolMissionStatus.PARTIAL_SUCCESS
    assert report.coverage_ratio == 0.5
    assert report.returned_home is True
    assert report.unresolved_required_areas == ("equipment",)


def test_retryable_navigation_is_bounded_then_succeeds() -> None:
    runtime = FakeRuntime()
    runtime.navigation["Equipment"] = [
        StepVerdict.RETRYABLE_FAILURE,
        StepVerdict.SUCCEEDED,
    ]

    report = asyncio.run(
        AreaPatrolWorkflow(runtime).run(
            _request(max_navigation_retries=1),
            (_area("equipment", "Equipment"),),
        )
    )

    assert report.status is PatrolMissionStatus.SUCCESS
    assert runtime.calls.count(("navigate", "Equipment")) == 2


def test_missing_required_observation_requires_human_review() -> None:
    runtime = FakeRuntime()
    runtime.inspections["Equipment"] = InspectionResult(
        InspectionVerdict.REQUIRES_REVIEW,
        "camera evidence unavailable",
    )

    report = asyncio.run(
        AreaPatrolWorkflow(runtime).run(
            _request(),
            (_area("equipment", "Equipment"),),
        )
    )

    assert report.status is PatrolMissionStatus.REQUIRES_HUMAN_REVIEW
    assert report.coverage_ratio == 0.0
    assert report.areas[0].status is AreaStatus.REQUIRES_REVIEW
    assert report.unresolved_required_areas == ("equipment",)
    assert report.review_required_areas == ("equipment",)
    assert report.returned_home is True


def test_reviewed_evidence_counts_as_covered_but_not_cleared() -> None:
    runtime = FakeRuntime()
    runtime.inspections["Equipment"] = InspectionResult(
        InspectionVerdict.REQUIRES_REVIEW,
        "possible spill requires confirmation",
        evidence=("image://possible-spill",),
    )

    report = asyncio.run(
        AreaPatrolWorkflow(runtime).run(
            _request(),
            (_area("equipment", "Equipment"),),
        )
    )

    assert report.status is PatrolMissionStatus.REQUIRES_HUMAN_REVIEW
    assert report.coverage_ratio == 1.0
    assert report.unresolved_required_areas == ()
    assert report.review_required_areas == ("equipment",)
    assert report.areas[0].observation_covered is True


def test_cancel_is_idempotent_and_prevents_robot_calls() -> None:
    runtime = FakeRuntime()
    workflow = AreaPatrolWorkflow(runtime)
    workflow.cancel()
    workflow.cancel()

    report = asyncio.run(workflow.run(_request(), (_area("equipment", "Equipment"),)))

    assert report.status is PatrolMissionStatus.ABORTED
    assert report.returned_home is False
    assert runtime.calls == []


def test_return_home_failure_prevents_success_claim() -> None:
    runtime = FakeRuntime()
    runtime.home = NavigationResult(StepVerdict.FAILED, "home goal failed")

    report = asyncio.run(
        AreaPatrolWorkflow(runtime).run(
            _request(),
            (_area("equipment", "Equipment"),),
        )
    )

    assert report.status is PatrolMissionStatus.PARTIAL_SUCCESS
    assert report.coverage_ratio == 1.0
    assert report.returned_home is False


def test_area_definitions_reject_duplicates_and_missing_required_areas() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        PatrolArea(
            area_id="equipment",
            display_name="Equipment",
            inspection_points=(
                InspectionPoint(location="A"),
                InspectionPoint(location="A"),
            ),
        )

    with pytest.raises(ValueError, match="required area"):
        asyncio.run(
            AreaPatrolWorkflow(FakeRuntime()).run(
                _request(),
                (_area("optional", "A", required=False),),
            )
        )


def test_cancel_during_navigation_stops_before_inspection_or_return_home() -> None:
    workflow: AreaPatrolWorkflow

    class CancellingRuntime(FakeRuntime):
        async def navigate(self, point: InspectionPoint) -> NavigationResult:
            self.calls.append(("navigate", point.location))
            workflow.cancel()
            return NavigationResult(
                StepVerdict.SUCCEEDED,
                "goal reached as cancellation arrived",
            )

    runtime = CancellingRuntime()
    workflow = AreaPatrolWorkflow(runtime)

    report = asyncio.run(
        workflow.run(
            _request(),
            (
                _area("entrance", "Entrance"),
                _area("equipment", "Equipment"),
            ),
        )
    )

    assert report.status is PatrolMissionStatus.ABORTED
    assert report.returned_home is False
    assert runtime.calls == [("navigate", "Entrance")]
    assert all(call[0] not in {"inspect", "return_home"} for call in runtime.calls)


def test_async_task_cancellation_returns_aborted_report_with_partial_coverage() -> None:
    async def scenario():
        second_navigation_started = asyncio.Event()

        class BlockingRuntime(FakeRuntime):
            async def navigate(self, point: InspectionPoint) -> NavigationResult:
                if point.location == "Equipment":
                    self.calls.append(("navigate", point.location))
                    second_navigation_started.set()
                    await asyncio.Event().wait()
                return await super().navigate(point)

        runtime = BlockingRuntime()
        workflow = AreaPatrolWorkflow(runtime)
        task = asyncio.create_task(
            workflow.run(
                _request(),
                (
                    _area("entrance", "Entrance"),
                    _area("equipment", "Equipment"),
                ),
            )
        )
        await second_navigation_started.wait()
        task.cancel()
        return await task, runtime

    report, runtime = asyncio.run(scenario())

    assert report.status is PatrolMissionStatus.ABORTED
    assert report.coverage_ratio == 0.5
    assert report.returned_home is False
    assert report.areas[0].status is AreaStatus.COMPLETED
    assert report.areas[0].observation_covered is True
    assert report.areas[1].status is AreaStatus.FAILED
    assert runtime.calls == [
        ("navigate", "Entrance"),
        ("inspect", "Entrance"),
        ("navigate", "Equipment"),
    ]
    assert any(
        event.event_type == "inspection_point_interrupted"
        and event.status == StepVerdict.CANCELLED.value
        for event in report.events
    )
