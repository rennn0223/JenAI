"""Semantic area-coverage patrol independent of ROS, Nav2, and LLM providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class AreaStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    REQUIRES_REVIEW = "requires_review"


class PatrolMissionStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    ABORTED = "aborted"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"


class StepVerdict(StrEnum):
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILURE = "retryable_failure"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InspectionVerdict(StrEnum):
    VERIFIED = "verified"
    REQUIRES_REVIEW = "requires_review"


@dataclass(frozen=True, slots=True)
class InspectionPoint:
    location: str
    required: bool = True

    def __post_init__(self) -> None:
        if not self.location.strip():
            raise ValueError("inspection point location must not be blank")


@dataclass(frozen=True, slots=True)
class PatrolArea:
    area_id: str
    display_name: str
    inspection_points: tuple[InspectionPoint, ...]
    required: bool = True

    def __post_init__(self) -> None:
        if not self.area_id.strip() or not self.display_name.strip():
            raise ValueError("area identity must not be blank")
        if not self.inspection_points:
            raise ValueError("patrol area must contain an inspection point")
        normalized = [point.location.strip().casefold() for point in self.inspection_points]
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"duplicate inspection point in area '{self.area_id}'")


@dataclass(frozen=True, slots=True)
class AreaPatrolRequest:
    mission_id: str
    target: str
    home_location: str | None
    max_navigation_retries: int = 1
    return_home: bool = True

    def __post_init__(self) -> None:
        if not self.mission_id.strip() or not self.target.strip():
            raise ValueError("mission identity and target must not be blank")
        if not 0 <= self.max_navigation_retries <= 5:
            raise ValueError("max_navigation_retries must be between 0 and 5")
        if self.return_home and not (self.home_location or "").strip():
            raise ValueError("return_home requires a home_location")


@dataclass(frozen=True, slots=True)
class NavigationResult:
    verdict: StepVerdict
    detail: str


@dataclass(frozen=True, slots=True)
class InspectionResult:
    verdict: InspectionVerdict
    detail: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PointResult:
    location: str
    required: bool
    status: AreaStatus
    detail: str
    navigation_attempts: int
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AreaResult:
    area_id: str
    required: bool
    status: AreaStatus
    points: tuple[PointResult, ...]

    @property
    def observation_covered(self) -> bool:
        """Whether every required point was reached and produced evidence.

        Coverage is independent from semantic clearance. A point can be
        observed while its evidence still requires human review; arriving
        without evidence is not coverage.
        """

        if self.status not in {AreaStatus.COMPLETED, AreaStatus.REQUIRES_REVIEW}:
            return False
        required_points = tuple(point for point in self.points if point.required)
        return bool(required_points) and all(point.evidence for point in required_points)


@dataclass(frozen=True, slots=True)
class CoverageEvent:
    sequence: int
    event_type: str
    area_id: str | None
    status: str
    detail: str


@dataclass(frozen=True, slots=True)
class AreaPatrolReport:
    mission_id: str
    target: str
    status: PatrolMissionStatus
    coverage_ratio: float
    areas: tuple[AreaResult, ...]
    returned_home: bool
    home_detail: str | None
    events: tuple[CoverageEvent, ...]

    @property
    def unresolved_required_areas(self) -> tuple[str, ...]:
        return tuple(
            area.area_id for area in self.areas if area.required and not area.observation_covered
        )

    @property
    def review_required_areas(self) -> tuple[str, ...]:
        """Covered required areas whose evidence is not semantically cleared."""

        return tuple(
            area.area_id
            for area in self.areas
            if area.required and area.status is AreaStatus.REQUIRES_REVIEW
        )


class AreaPatrolRuntime(Protocol):
    """Robot-facing seam implemented by production and in-memory adapters."""

    async def navigate(self, point: InspectionPoint) -> NavigationResult: ...

    async def inspect(self, point: InspectionPoint) -> InspectionResult: ...

    async def return_home(self, location: str) -> NavigationResult: ...


@dataclass(slots=True)
class _EventLog:
    items: list[CoverageEvent] = field(default_factory=list)

    def add(
        self,
        event_type: str,
        *,
        status: str,
        detail: str,
        area_id: str | None = None,
    ) -> None:
        self.items.append(
            CoverageEvent(
                sequence=len(self.items) + 1,
                event_type=event_type,
                area_id=area_id,
                status=status,
                detail=detail,
            )
        )


class AreaPatrolWorkflow:
    """Run one complete semantic coverage mission through a small runtime seam."""

    def __init__(self, runtime: AreaPatrolRuntime) -> None:
        self._runtime = runtime
        self._cancel_requested = False
        self._started = False

    def cancel(self) -> None:
        """Request cancellation; repeated calls are intentionally harmless."""

        self._cancel_requested = True

    async def run(
        self,
        request: AreaPatrolRequest,
        areas: tuple[PatrolArea, ...],
    ) -> AreaPatrolReport:
        self._validate_plan(areas)
        if self._started:
            raise RuntimeError("an AreaPatrolWorkflow instance runs exactly one mission")
        self._started = True
        events = _EventLog()
        events.add(
            "mission_started",
            status="running",
            detail=f"coverage target '{request.target}' started",
        )
        if self._cancel_requested:
            return self._aborted_report(request, areas, events)

        results: list[AreaResult] = []
        for area in areas:
            if self._cancel_requested:
                return self._aborted_report(request, areas, events, tuple(results))
            results.append(await self._run_area(request, area, events))
            if self._cancel_requested:
                return self._aborted_report(request, areas, events, tuple(results))

        returned_home, home_detail = await self._return_home(request, events)
        status = self._mission_status(request, tuple(results), returned_home)
        coverage_ratio = self._coverage_ratio(tuple(results))
        events.add(
            "mission_finished",
            status=status.value,
            detail=f"required-area coverage={coverage_ratio:.3f}",
        )
        return AreaPatrolReport(
            mission_id=request.mission_id,
            target=request.target,
            status=status,
            coverage_ratio=coverage_ratio,
            areas=tuple(results),
            returned_home=returned_home,
            home_detail=home_detail,
            events=tuple(events.items),
        )

    @staticmethod
    def _validate_plan(areas: tuple[PatrolArea, ...]) -> None:
        if not any(area.required for area in areas):
            raise ValueError("area patrol requires at least one required area")
        ids = [area.area_id.strip().casefold() for area in areas]
        if len(ids) != len(set(ids)):
            raise ValueError("area patrol contains a duplicate area id")

    async def _run_area(
        self,
        request: AreaPatrolRequest,
        area: PatrolArea,
        events: _EventLog,
    ) -> AreaResult:
        events.add(
            "area_started",
            area_id=area.area_id,
            status=AreaStatus.ACTIVE.value,
            detail=area.display_name,
        )
        points: list[PointResult] = []
        for point in area.inspection_points:
            if self._cancel_requested:
                break
            result = await self._run_point(request, point)
            points.append(result)
            events.add(
                "inspection_point_finished",
                area_id=area.area_id,
                status=result.status.value,
                detail=f"{point.location}: {result.detail}",
            )
        status = self._area_status(area, tuple(points))
        events.add(
            "area_finished",
            area_id=area.area_id,
            status=status.value,
            detail=area.display_name,
        )
        return AreaResult(area.area_id, area.required, status, tuple(points))

    async def _run_point(
        self,
        request: AreaPatrolRequest,
        point: InspectionPoint,
    ) -> PointResult:
        navigation = NavigationResult(StepVerdict.FAILED, "navigation was not attempted")
        attempts = 0
        for attempts in range(1, request.max_navigation_retries + 2):
            navigation = await self._safe_navigate(point)
            if navigation.verdict is StepVerdict.SUCCEEDED:
                break
            if navigation.verdict is not StepVerdict.RETRYABLE_FAILURE:
                return self._navigation_failure(point, navigation, attempts)
        else:
            return self._navigation_failure(point, navigation, attempts)

        if navigation.verdict is not StepVerdict.SUCCEEDED:
            return self._navigation_failure(point, navigation, attempts)
        if self._cancel_requested:
            return PointResult(
                location=point.location,
                status=AreaStatus.FAILED,
                detail="cancelled before inspection",
                navigation_attempts=attempts,
                required=point.required,
            )
        inspection = await self._safe_inspect(point)
        status = (
            AreaStatus.COMPLETED
            if inspection.verdict is InspectionVerdict.VERIFIED
            else AreaStatus.REQUIRES_REVIEW
        )
        return PointResult(
            location=point.location,
            status=status,
            detail=inspection.detail,
            navigation_attempts=attempts,
            required=point.required,
            evidence=inspection.evidence,
        )

    async def _safe_navigate(self, point: InspectionPoint) -> NavigationResult:
        try:
            return await self._runtime.navigate(point)
        except Exception as exc:  # noqa: BLE001 - the runtime seam classifies all adapter failures
            return NavigationResult(StepVerdict.FAILED, f"navigation adapter error: {exc}")

    async def _safe_inspect(self, point: InspectionPoint) -> InspectionResult:
        try:
            return await self._runtime.inspect(point)
        except Exception as exc:  # noqa: BLE001 - missing evidence must remain explicit
            return InspectionResult(
                InspectionVerdict.REQUIRES_REVIEW,
                f"inspection adapter error: {exc}",
            )

    @staticmethod
    def _navigation_failure(
        point: InspectionPoint,
        result: NavigationResult,
        attempts: int,
    ) -> PointResult:
        status = (
            AreaStatus.BLOCKED
            if result.verdict in {StepVerdict.BLOCKED, StepVerdict.UNAVAILABLE}
            else AreaStatus.FAILED
        )
        return PointResult(point.location, point.required, status, result.detail, attempts)

    @staticmethod
    def _area_status(
        area: PatrolArea,
        points: tuple[PointResult, ...],
    ) -> AreaStatus:
        missing_points = area.inspection_points[len(points) :]
        if any(point.required for point in missing_points):
            return AreaStatus.FAILED
        required = [
            result
            for definition, result in zip(
                area.inspection_points,
                points,
                strict=False,
            )
            if definition.required
        ]
        if required and all(item.status is AreaStatus.COMPLETED for item in required):
            return AreaStatus.COMPLETED
        if any(item.status is AreaStatus.REQUIRES_REVIEW for item in required):
            return AreaStatus.REQUIRES_REVIEW
        if any(item.status is AreaStatus.BLOCKED for item in required):
            return AreaStatus.BLOCKED
        return AreaStatus.FAILED

    async def _return_home(
        self,
        request: AreaPatrolRequest,
        events: _EventLog,
    ) -> tuple[bool, str | None]:
        if not request.return_home:
            return False, None
        home_location = request.home_location
        if home_location is None:
            raise RuntimeError("return-home request lost its validated home location")
        try:
            result = await self._runtime.return_home(home_location)
        except Exception as exc:  # noqa: BLE001 - classify external adapter failures
            result = NavigationResult(StepVerdict.FAILED, f"return-home adapter error: {exc}")
        returned = result.verdict is StepVerdict.SUCCEEDED
        events.add(
            "return_home_finished",
            status="succeeded" if returned else "failed",
            detail=result.detail,
        )
        return returned, result.detail

    @staticmethod
    def _coverage_ratio(areas: tuple[AreaResult, ...]) -> float:
        required = [area for area in areas if area.required]
        covered = sum(area.observation_covered for area in required)
        return covered / len(required)

    @staticmethod
    def _mission_status(
        request: AreaPatrolRequest,
        areas: tuple[AreaResult, ...],
        returned_home: bool,
    ) -> PatrolMissionStatus:
        required = [area for area in areas if area.required]
        if any(area.status is AreaStatus.REQUIRES_REVIEW for area in required):
            return PatrolMissionStatus.REQUIRES_HUMAN_REVIEW
        completed = sum(area.status is AreaStatus.COMPLETED for area in required)
        home_contract_met = not request.return_home or returned_home
        if completed == len(required) and home_contract_met:
            return PatrolMissionStatus.SUCCESS
        if completed > 0:
            return PatrolMissionStatus.PARTIAL_SUCCESS
        return PatrolMissionStatus.FAILED

    @staticmethod
    def _aborted_report(
        request: AreaPatrolRequest,
        plan: tuple[PatrolArea, ...],
        events: _EventLog,
        completed: tuple[AreaResult, ...] = (),
    ) -> AreaPatrolReport:
        completed_ids = {area.area_id for area in completed}
        remaining = tuple(
            AreaResult(area.area_id, area.required, AreaStatus.PENDING, ())
            for area in plan
            if area.area_id not in completed_ids
        )
        areas = completed + remaining
        events.add(
            "mission_finished",
            status=PatrolMissionStatus.ABORTED.value,
            detail="cancellation requested",
        )
        return AreaPatrolReport(
            mission_id=request.mission_id,
            target=request.target,
            status=PatrolMissionStatus.ABORTED,
            coverage_ratio=AreaPatrolWorkflow._coverage_ratio(areas),
            areas=areas,
            returned_home=False,
            home_detail=None,
            events=tuple(events.items),
        )
