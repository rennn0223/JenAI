"""Read-only capture seam for the Motion Safety Gate.

Concrete Isaac/ROS collectors implement this port after code review.  The port
contains observation methods only, so a capture implementation cannot acquire a
motion operation through this interface.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from jenai.acceptance.motion_safety import (
    ClearanceBudget,
    ClearanceSourceEvidence,
    CollisionStreamEvidence,
    CostmapLayerEvidence,
    MotionReadinessArtifact,
    MotionRequestBinding,
    NavFootprintEvidence,
    PathEvidence,
    RuntimeBinding,
    UsdCollisionGeometryEvidence,
    create_motion_authorization_binding,
)


class MotionReadinessEvidenceSource(Protocol):
    async def runtime_binding(self) -> RuntimeBinding: ...

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


async def capture_no_motion_readiness(
    source: MotionReadinessEvidenceSource,
) -> MotionReadinessArtifact:
    """Capture immutable read-only inputs; offline assembly owns derivation."""

    runtime = await source.runtime_binding()
    request = await source.motion_request(runtime)
    async with asyncio.TaskGroup() as group:
        path_task = group.create_task(source.planned_path(runtime, request))
        footprint_task = group.create_task(source.effective_nav_footprint(runtime))
        usd_task = group.create_task(source.usd_collision_geometry(runtime))
        layers_task = group.create_task(source.costmap_layers(runtime))
        collision_task = group.create_task(source.collision_timeline(runtime))
        budget_task = group.create_task(source.clearance_budget(runtime))
        sources_task = group.create_task(source.clearance_sources(runtime))
    raw = MotionReadinessArtifact(
        schema_version=4,
        evidence_derivation_version=4,
        runtime=runtime,
        motion_request=request,
        authorization=None,
        path=path_task.result(),
        nav_footprint=footprint_task.result(),
        usd_geometry=usd_task.result(),
        costmap_layers=layers_task.result(),
        collision_stream=collision_task.result(),
        clearance_sources=sources_task.result(),
        clearance_budget=budget_task.result(),
        result=None,
    )
    return raw.model_copy(update={"authorization": create_motion_authorization_binding(raw)})
