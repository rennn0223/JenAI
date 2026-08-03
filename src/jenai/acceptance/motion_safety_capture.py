"""Bounded read-only Isaac Motion Safety Gate collection."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, Self, TypeVar, cast

from pydantic import BaseModel, ConfigDict, model_validator

from jenai.acceptance.motion_safety import (
    ClearanceBudget,
    ClearanceSourceEvidence,
    CollisionStreamEvidence,
    CostmapLayerEvidence,
    MotionReadinessArtifact,
    MotionRequestBinding,
    NavFootprintEvidence,
    OfflineValidationReport,
    PathEvidence,
    ProbeIdentityEvidence,
    RuntimeBinding,
    UsdCollisionGeometryEvidence,
    _bounded_json_object,
    create_motion_authorization_binding,
)


class MotionReadinessEvidenceSource(Protocol):
    """Platform-specific observation port; it intentionally exposes no effectful operation."""

    async def runtime_binding(self) -> RuntimeBinding: ...

    async def probe_identity(self, runtime: RuntimeBinding) -> ProbeIdentityEvidence: ...

    async def motion_request(self, runtime: RuntimeBinding) -> MotionRequestBinding: ...

    async def planned_path(
        self, runtime: RuntimeBinding, request: MotionRequestBinding
    ) -> PathEvidence: ...

    async def effective_nav_footprint(self, runtime: RuntimeBinding) -> NavFootprintEvidence: ...

    async def usd_collision_geometry(
        self, runtime: RuntimeBinding
    ) -> UsdCollisionGeometryEvidence: ...

    async def costmap_layers(self, runtime: RuntimeBinding) -> tuple[CostmapLayerEvidence, ...]: ...

    async def collision_timeline(self, runtime: RuntimeBinding) -> CollisionStreamEvidence: ...

    async def clearance_budget(self, runtime: RuntimeBinding) -> ClearanceBudget: ...

    async def clearance_sources(
        self, runtime: RuntimeBinding
    ) -> tuple[ClearanceSourceEvidence, ...]: ...


class MotionReadinessCollectionFailure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: str
    reason: Literal["timeout", "source_error"]
    exception_type: str


class BlockedMotionReadinessArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    artifact_kind: Literal["motion_readiness_collection_block"] = (
        "motion_readiness_collection_block"
    )
    decision: Literal["BLOCK"] = "BLOCK"
    failures: tuple[MotionReadinessCollectionFailure, ...]
    completed_operation_sha256: tuple[tuple[str, str], ...]
    content_sha256: str

    @classmethod
    def create(cls, results: tuple[_OperationResult, ...]) -> Self:
        failures = tuple(result.failure for result in results if result.failure is not None)
        completed = tuple(
            sorted(
                (
                    result.operation,
                    hashlib.sha256(
                        json.dumps(
                            _jsonable(result.value),
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest(),
                )
                for result in results
                if result.failure is None
            )
        )
        values = {
            "schema_version": 1,
            "artifact_kind": "motion_readiness_collection_block",
            "decision": "BLOCK",
            "failures": [failure.model_dump(mode="json") for failure in failures],
            "completed_operation_sha256": completed,
        }
        content_sha256 = hashlib.sha256(
            json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return cls(
            failures=failures,
            completed_operation_sha256=completed,
            content_sha256=content_sha256,
        )

    def digest_is_valid(self) -> bool:
        values = self.model_dump(mode="json", exclude={"content_sha256"})
        return (
            self.content_sha256
            == hashlib.sha256(
                json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )


def load_blocked_collection_artifact(path: Path) -> BlockedMotionReadinessArtifact:
    payload = _bounded_json_object(path)
    return BlockedMotionReadinessArtifact.model_validate(payload)


def validate_blocked_collection_artifact(
    artifact: BlockedMotionReadinessArtifact,
) -> OfflineValidationReport:
    failures = []
    if not artifact.failures:
        failures.append("blocked collection artifact has no failure")
    if not artifact.digest_is_valid():
        failures.append("blocked collection artifact digest mismatch")
    return OfflineValidationReport(
        valid=not failures,
        failures=tuple(failures),
        decision="BLOCK",
    )


def load_and_validate_blocked_collection(path: Path) -> OfflineValidationReport:
    try:
        return validate_blocked_collection_artifact(load_blocked_collection_artifact(path))
    except (OSError, ValueError) as exc:
        return OfflineValidationReport(valid=False, failures=(str(exc),), decision="BLOCK")


class MotionReadinessCollectionOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["captured", "blocked"]
    artifact: MotionReadinessArtifact | BlockedMotionReadinessArtifact
    failures: tuple[MotionReadinessCollectionFailure, ...]

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.status == "captured" and (
            not isinstance(self.artifact, MotionReadinessArtifact) or self.failures
        ):
            raise ValueError("captured collection outcome must contain a full artifact")
        if self.status == "blocked" and (
            not isinstance(self.artifact, BlockedMotionReadinessArtifact)
            or not self.failures
            or self.artifact.failures != self.failures
        ):
            raise ValueError("blocked collection outcome must contain a BLOCK artifact")
        return self


_T = TypeVar("_T")
_MAX_OPERATION_TIMEOUT_S = 182.0


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class _OperationResult:
    operation: str
    value: object | None = None
    failure: MotionReadinessCollectionFailure | None = None


@dataclass(frozen=True)
class IsaacMotionReadinessCollector:
    """Collect immutable Isaac/ROS Evidence through bounded observation calls."""

    source: MotionReadinessEvidenceSource
    operation_timeout_s: float = 5.0

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.operation_timeout_s)
            or self.operation_timeout_s <= 0.0
            or self.operation_timeout_s > _MAX_OPERATION_TIMEOUT_S
        ):
            raise ValueError(
                "collector operation timeout must be finite and within (0, 182] seconds"
            )

    async def _attempt(self, name: str, operation: Awaitable[_T]) -> _OperationResult:
        try:
            value = await asyncio.wait_for(operation, timeout=self.operation_timeout_s)
        except TimeoutError:
            return _OperationResult(
                operation=name,
                failure=MotionReadinessCollectionFailure(
                    operation=name,
                    reason="timeout",
                    exception_type="TimeoutError",
                ),
            )
        except Exception as exc:
            return _OperationResult(
                operation=name,
                failure=MotionReadinessCollectionFailure(
                    operation=name,
                    reason="source_error",
                    exception_type=type(exc).__name__,
                ),
            )
        return _OperationResult(operation=name, value=value)

    @staticmethod
    def _blocked(*results: _OperationResult) -> MotionReadinessCollectionOutcome | None:
        failures = tuple(result.failure for result in results if result.failure is not None)
        if not failures:
            return None
        artifact = BlockedMotionReadinessArtifact.create(results)
        return MotionReadinessCollectionOutcome(
            status="blocked",
            artifact=artifact,
            failures=failures,
        )

    async def collect(self) -> MotionReadinessCollectionOutcome:
        completed: list[_OperationResult] = []
        runtime_result = await self._attempt(
            "runtime_binding_before", self.source.runtime_binding()
        )
        completed.append(runtime_result)
        blocked = self._blocked(*completed)
        if blocked is not None:
            return blocked
        runtime = cast(RuntimeBinding, runtime_result.value)

        request_result = await self._attempt("motion_request", self.source.motion_request(runtime))
        completed.append(request_result)
        blocked = self._blocked(*completed)
        if blocked is not None:
            return blocked
        request = cast(MotionRequestBinding, request_result.value)

        async with asyncio.TaskGroup() as group:
            probe_task = group.create_task(
                self._attempt("probe_identity", self.source.probe_identity(runtime))
            )
            path_task = group.create_task(
                self._attempt("planned_path", self.source.planned_path(runtime, request))
            )
            footprint_task = group.create_task(
                self._attempt(
                    "effective_nav_footprint",
                    self.source.effective_nav_footprint(runtime),
                )
            )
            usd_task = group.create_task(
                self._attempt(
                    "usd_collision_geometry",
                    self.source.usd_collision_geometry(runtime),
                )
            )
            layers_task = group.create_task(
                self._attempt("costmap_layers", self.source.costmap_layers(runtime))
            )
            collision_task = group.create_task(
                self._attempt(
                    "collision_timeline",
                    self.source.collision_timeline(runtime),
                )
            )
            budget_task = group.create_task(
                self._attempt("clearance_budget", self.source.clearance_budget(runtime))
            )
            sources_task = group.create_task(
                self._attempt("clearance_sources", self.source.clearance_sources(runtime))
            )
        observation_results = (
            probe_task.result(),
            path_task.result(),
            footprint_task.result(),
            usd_task.result(),
            layers_task.result(),
            collision_task.result(),
            budget_task.result(),
            sources_task.result(),
        )
        completed.extend(observation_results)
        blocked = self._blocked(*completed)
        if blocked is not None:
            return blocked

        runtime_after_result = await self._attempt(
            "runtime_binding_after", self.source.runtime_binding()
        )
        completed.append(runtime_after_result)
        blocked = self._blocked(*completed)
        if blocked is not None:
            return blocked
        runtime_after = cast(RuntimeBinding, runtime_after_result.value)

        raw = MotionReadinessArtifact(
            schema_version=6,
            evidence_derivation_version=6,
            runtime=runtime,
            runtime_after=runtime_after,
            probe_identity=cast(ProbeIdentityEvidence, probe_task.result().value),
            motion_request=request,
            authorization=None,
            path=cast(PathEvidence, path_task.result().value),
            nav_footprint=cast(NavFootprintEvidence, footprint_task.result().value),
            usd_geometry=cast(UsdCollisionGeometryEvidence, usd_task.result().value),
            costmap_layers=cast(
                tuple[CostmapLayerEvidence, ...],
                layers_task.result().value,
            ),
            collision_stream=cast(
                CollisionStreamEvidence,
                collision_task.result().value,
            ),
            clearance_sources=cast(
                tuple[ClearanceSourceEvidence, ...],
                sources_task.result().value,
            ),
            clearance_budget=cast(ClearanceBudget, budget_task.result().value),
            collection_failures=(),
            result=None,
        )
        artifact = raw.model_copy(
            update={"authorization": create_motion_authorization_binding(raw)}
        )
        return MotionReadinessCollectionOutcome(
            status="captured",
            artifact=artifact,
            failures=(),
        )


async def capture_no_motion_readiness(
    source: MotionReadinessEvidenceSource,
    *,
    operation_timeout_s: float = 5.0,
) -> MotionReadinessCollectionOutcome:
    """Capture one bounded no-motion Isaac readiness Evidence set."""

    return await IsaacMotionReadinessCollector(
        source=source,
        operation_timeout_s=operation_timeout_s,
    ).collect()
