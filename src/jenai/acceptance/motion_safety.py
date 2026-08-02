"""Observation-only motion-readiness domain and offline evidence validator.

This module is deliberately pure Python.  It does not import ROS, Isaac, the
Navigation Gateway, or a UI.  Capture adapters may create the typed evidence,
but only this module decides whether that evidence is sufficient to request a
motion authorization.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from collections.abc import Mapping, Set
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import to_jsonable_python

_DIGEST_LENGTH = 64
_EPSILON = 1e-9
_NO_OBSTACLE_CLEARANCE_M = 1_000_000.0


def _canonical_digest(value: object) -> str:
    value = to_jsonable_python(value)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def costmap_rle_sha256(runs: tuple[CostmapRun, ...]) -> str:
    """Return the canonical digest used to bind a preserved raw costmap grid."""

    return _canonical_digest(runs)


def collision_prim_inventory_sha256(paths: tuple[str, ...]) -> str:
    """Return the digest of one canonical sorted monitored-prim inventory."""

    return _canonical_digest(tuple(sorted(paths)))


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class Point2(FrozenModel):
    x: float
    y: float


class Point3(FrozenModel):
    x: float
    y: float
    z: float


class Pose2(FrozenModel):
    x: float
    y: float
    yaw: float


class Polygon2(FrozenModel):
    vertices: tuple[Point2, ...]

    @model_validator(mode="after")
    def validate_polygon(self) -> Self:
        if len(self.vertices) < 3:
            raise ValueError("a polygon requires at least three vertices")
        if abs(_polygon_area(self)) <= _EPSILON:
            raise ValueError("polygon area must be non-zero")
        if not _is_convex(self):
            raise ValueError("motion-safety polygons must be convex")
        return self


class EvidenceStatus(StrEnum):
    OBSERVED = "observed"
    MISSING = "missing"
    STALE = "stale"
    CLOCK_MISMATCH = "clock_mismatch"
    UNAVAILABLE = "unavailable"


class ClearanceLayer(StrEnum):
    STATIC_LETHAL = "static_lethal"
    STATIC_INFLATION = "static_inscribed_or_inflation"
    LIVE_OBSTACLE = "live_obstacle"
    UNKNOWN = "unknown"


class SafetyTermKind(StrEnum):
    GEOMETRY_ATTESTATION_UNCERTAINTY = "geometry_attestation_uncertainty"
    LOCALIZATION_UNCERTAINTY = "localization_uncertainty"
    CONTROLLER_TRACKING_BOUND = "controller_tracking_bound"
    MAP_DISCRETIZATION_BOUND = "map_discretization_bound"
    LATENCY_DISTANCE = "latency_distance"
    STOPPING_DISTANCE = "stopping_distance"
    FIXED_PRODUCT_MARGIN = "fixed_product_margin"


class CollisionWindowKind(StrEnum):
    PRE_DISPATCH = "pre_dispatch"
    MOTION = "motion"
    TERMINAL_RELATIVE = "terminal_relative"
    POST_STOP = "post_stop"


class RuntimeBinding(FrozenModel):
    git_sha: str = Field(min_length=40, max_length=64)
    scene_path: str
    scene_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    map_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    nav2_params_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    runtime_fingerprint: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    simulation_epoch: str
    runtime_boot_id: str
    product_config_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    planner_config_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    site_id: str = Field(min_length=1)
    collision_filter_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    capture_ros_ns: int
    capture_host_monotonic_ns: int
    max_evidence_age_ns: int = Field(gt=0)


class MotionRequestBinding(FrozenModel):
    """Canonical, time-bounded request identity for one runtime generation."""

    authorization_nonce: str = Field(min_length=16)
    site_id: str = Field(min_length=1)
    start: Pose2
    goal: Pose2
    planner_id: str = Field(min_length=1)
    planner_config_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    nav2_params_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    product_config_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    scene_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    map_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    runtime_fingerprint: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    collision_filter_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    simulation_epoch: str = Field(min_length=1)
    runtime_boot_id: str = Field(min_length=1)
    valid_from_ros_ns: int
    valid_until_ros_ns: int
    valid_from_host_monotonic_ns: int
    valid_until_host_monotonic_ns: int
    content_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)

    @classmethod
    def create(cls, **values: Any) -> Self:
        return cls(**values, content_sha256=_canonical_digest(values))

    def digest_is_valid(self) -> bool:
        return self.content_sha256 == _canonical_digest(
            self.model_dump(mode="json", exclude={"content_sha256"})
        )

    @model_validator(mode="after")
    def validate_validity_window(self) -> Self:
        if self.valid_until_ros_ns < self.valid_from_ros_ns:
            raise ValueError("motion request ROS validity window is reversed")
        if self.valid_until_host_monotonic_ns < self.valid_from_host_monotonic_ns:
            raise ValueError("motion request host validity window is reversed")
        return self


class MotionAuthorizationBinding(FrozenModel):
    """Admission token bound to one request, one path, and one immutable artifact."""

    authorization_nonce: str = Field(min_length=16)
    motion_request_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    path_evidence_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    artifact_input_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    content_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)

    @classmethod
    def create(cls, **values: Any) -> Self:
        return cls(**values, content_sha256=_canonical_digest(values))

    def digest_is_valid(self) -> bool:
        return self.content_sha256 == _canonical_digest(
            self.model_dump(mode="json", exclude={"content_sha256"})
        )


class PathEvidence(FrozenModel):
    evidence_id: str
    frame_id: str
    source_timestamp_ns: int
    received_host_monotonic_ns: int
    map_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    runtime_fingerprint: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    simulation_epoch: str
    runtime_boot_id: str
    motion_request_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    nav2_params_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    poses: tuple[Pose2, ...]
    content_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)

    @classmethod
    def create(cls, **values: Any) -> Self:
        digest = _canonical_digest(values)
        return cls(**values, content_sha256=digest)

    def digest_is_valid(self) -> bool:
        payload = self.model_dump(mode="json", exclude={"content_sha256"})
        return self.content_sha256 == _canonical_digest(payload)

    @model_validator(mode="after")
    def validate_path(self) -> Self:
        if len(self.poses) < 1:
            raise ValueError("planned path must contain at least one pose")
        return self


class NavFootprintEvidence(FrozenModel):
    evidence_id: str
    frame_id: str
    source: str
    source_timestamp_ns: int
    received_host_monotonic_ns: int
    nav2_params_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    runtime_fingerprint: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    simulation_epoch: str
    runtime_boot_id: str
    axis_convention: str
    footprint_padding_m: float = Field(ge=0.0)
    padding_applied: bool
    configured_polygon: Polygon2
    polygon: Polygon2
    content_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)

    @classmethod
    def create(cls, **values: Any) -> Self:
        return cls(**values, content_sha256=_canonical_digest(values))

    def digest_is_valid(self) -> bool:
        return self.content_sha256 == _canonical_digest(
            self.model_dump(mode="json", exclude={"content_sha256"})
        )


class CostmapCell(FrozenModel):
    """One cell centre from a preserved raw costmap layer."""

    x: float
    y: float
    cost: int = Field(ge=0, le=255)


class CostmapRun(FrozenModel):
    cost: int = Field(ge=0, le=255)
    count: int = Field(gt=0)


class CostmapLayerEvidence(FrozenModel):
    evidence_id: str
    layer: ClearanceLayer
    frame_id: str
    resolution_m: float = Field(gt=0.0)
    origin: Point2
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    source_stamp_ns: int | None
    received_host_monotonic_ns: int
    map_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    runtime_fingerprint: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    simulation_epoch: str
    runtime_boot_id: str
    status: EvidenceStatus
    cells: tuple[CostmapCell, ...]
    raw_costs_rle: tuple[CostmapRun, ...]
    raw_grid_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    content_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)

    @classmethod
    def create(cls, **values: Any) -> Self:
        return cls(**values, content_sha256=_canonical_digest(values))

    def digest_is_valid(self) -> bool:
        return self.content_sha256 == _canonical_digest(
            self.model_dump(mode="json", exclude={"content_sha256"})
        )

    @model_validator(mode="after")
    def validate_cells(self) -> Self:
        _validate_preserved_costmap_cells(self)
        _validate_costmap_rle(self)
        return self


def _validate_preserved_costmap_cells(layer: CostmapLayerEvidence) -> None:
    maximum_x = layer.origin.x + layer.width * layer.resolution_m
    maximum_y = layer.origin.y + layer.height * layer.resolution_m
    coordinates: set[tuple[float, float]] = set()
    for cell in layer.cells:
        if not (layer.origin.x <= cell.x <= maximum_x and layer.origin.y <= cell.y <= maximum_y):
            raise ValueError("costmap cell lies outside declared grid")
        coordinate = (cell.x, cell.y)
        if coordinate in coordinates:
            raise ValueError("costmap layer contains duplicate cells")
        coordinates.add(coordinate)
        if not _cost_belongs_to_layer(cell.cost, layer.layer):
            raise ValueError("preserved cell cost does not belong to the declared layer")


def _validate_costmap_rle(layer: CostmapLayerEvidence) -> None:
    if sum(run.count for run in layer.raw_costs_rle) != layer.width * layer.height:
        raise ValueError("costmap RLE does not cover the declared grid")
    if layer.raw_grid_sha256 != costmap_rle_sha256(layer.raw_costs_rle):
        raise ValueError("costmap raw-grid digest mismatch")
    reconstructed: set[tuple[float, float, int]] = set()
    flat_index = 0
    for run in layer.raw_costs_rle:
        if _cost_belongs_to_layer(run.cost, layer.layer):
            for offset in range(run.count):
                index = flat_index + offset
                column = index % layer.width
                row = index // layer.width
                reconstructed.add(
                    (
                        round(layer.origin.x + (column + 0.5) * layer.resolution_m, 12),
                        round(layer.origin.y + (row + 0.5) * layer.resolution_m, 12),
                        run.cost,
                    )
                )
        flat_index += run.count
    preserved = {(round(cell.x, 12), round(cell.y, 12), cell.cost) for cell in layer.cells}
    if preserved != reconstructed:
        raise ValueError("preserved hazard cells do not match the raw costmap RLE")


class UsdCollisionPrimEvidence(FrozenModel):
    prim_path: str
    shape_type: str
    mesh_identity: str
    local_geometry_vertices: tuple[Point3, ...]
    base_from_prim_transform: tuple[float, ...]
    scale: tuple[float, float, float]
    transform_convention: Literal["row_major_affine_column_vector"]
    transform_translation_unit: Literal["m"]
    transform_includes_scale: Literal[False]
    projected_base_hull: Polygon2

    @model_validator(mode="after")
    def validate_transform(self) -> Self:
        if len(self.local_geometry_vertices) < 3:
            raise ValueError("USD collision primitive requires raw local geometry vertices")
        if len(self.base_from_prim_transform) != 16:
            raise ValueError("USD composed transform must contain 16 values")
        if not all(math.isfinite(value) for value in self.base_from_prim_transform):
            raise ValueError("USD composed transform must be finite")
        if any(scale <= 0.0 for scale in self.scale):
            raise ValueError("USD collision scale must be positive")
        matrix = self.base_from_prim_transform
        if not all(
            math.isclose(matrix[index], expected, abs_tol=1e-9)
            for index, expected in zip((12, 13, 14, 15), (0.0, 0.0, 0.0, 1.0), strict=True)
        ):
            raise ValueError("USD transform must be an affine row-major column-vector matrix")
        rotation = (
            (matrix[0], matrix[1], matrix[2]),
            (matrix[4], matrix[5], matrix[6]),
            (matrix[8], matrix[9], matrix[10]),
        )
        for row in rotation:
            if not math.isclose(sum(value * value for value in row), 1.0, abs_tol=1e-9):
                raise ValueError("USD transform rotation must be rigid and exclude scale")
        if any(
            not math.isclose(sum(left[i] * right[i] for i in range(3)), 0.0, abs_tol=1e-9)
            for left, right in (
                (rotation[0], rotation[1]),
                (rotation[0], rotation[2]),
                (rotation[1], rotation[2]),
            )
        ):
            raise ValueError("USD transform rotation must be orthogonal")
        determinant = (
            rotation[0][0] * (rotation[1][1] * rotation[2][2] - rotation[1][2] * rotation[2][1])
            - rotation[0][1] * (rotation[1][0] * rotation[2][2] - rotation[1][2] * rotation[2][0])
            + rotation[0][2] * (rotation[1][0] * rotation[2][1] - rotation[1][1] * rotation[2][0])
        )
        if not math.isclose(determinant, 1.0, abs_tol=1e-9):
            raise ValueError("USD transform rotation must be right-handed")
        return self


class UsdSceneCollisionEntry(FrozenModel):
    prim_path: str = Field(min_length=1)
    category: str = Field(min_length=1)
    collision_enabled: Literal[True]


class UsdSceneCollisionEnumerationEvidence(FrozenModel):
    """Raw independent Stage query of every non-robot collision-enabled prim."""

    source: Literal["isaac_usd_stage_query"]
    query_name: Literal["collision_enabled_scene_prims_v1"]
    query_identity_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    scene_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    source_timestamp_ns: int
    reported_count: int = Field(ge=0)
    complete_attestation: bool
    entries: tuple[UsdSceneCollisionEntry, ...]
    content_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)

    @classmethod
    def create(cls, **values: Any) -> Self:
        identity_values = {
            "source": values["source"],
            "query_name": values["query_name"],
            "scene_sha256": values["scene_sha256"],
        }
        return cls(
            **values,
            query_identity_sha256=_canonical_digest(identity_values),
            content_sha256=_canonical_digest(
                {**values, "query_identity_sha256": _canonical_digest(identity_values)}
            ),
        )

    def digest_is_valid(self) -> bool:
        return self.content_sha256 == _canonical_digest(
            self.model_dump(mode="json", exclude={"content_sha256"})
        )

    @model_validator(mode="after")
    def validate_raw_enumeration(self) -> Self:
        expected_identity = _canonical_digest(
            {
                "source": self.source,
                "query_name": self.query_name,
                "scene_sha256": self.scene_sha256,
            }
        )
        if self.query_identity_sha256 != expected_identity:
            raise ValueError("USD scene collision query identity mismatch")
        canonical = tuple(sorted(self.entries, key=lambda entry: (entry.prim_path, entry.category)))
        if not canonical or canonical != self.entries:
            raise ValueError("USD scene collision enumeration must be non-empty and sorted")
        identities = {(entry.prim_path, entry.category) for entry in self.entries}
        if len(identities) != len(self.entries):
            raise ValueError("USD scene collision enumeration contains duplicate entries")
        if self.reported_count != len(self.entries):
            raise ValueError("USD scene collision enumeration count mismatch")
        return self

    def counterpart_prim_paths(self) -> tuple[str, ...]:
        return tuple(sorted({entry.prim_path for entry in self.entries}))

    def counterpart_categories(self) -> tuple[str, ...]:
        return tuple(sorted({entry.category for entry in self.entries}))


class UsdCollisionGeometryEvidence(FrozenModel):
    evidence_id: str
    scene_path: str
    scene_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    robot_root_prim: str
    base_frame: str
    simulation_epoch: str
    runtime_boot_id: str
    runtime_fingerprint: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    nav2_params_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    axis_convention: str
    meters_per_unit: float = Field(gt=0.0)
    source_timestamp_ns: int
    collision_prim_inventory_complete: bool
    scene_collision_enumeration: UsdSceneCollisionEnumerationEvidence
    collision_enabled_counterpart_prim_paths: tuple[str, ...]
    collision_enabled_counterpart_categories: tuple[str, ...]
    collision_prims: tuple[UsdCollisionPrimEvidence, ...]
    projected_base_hull: Polygon2
    source_assurance: str
    transport_security: str
    content_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)

    @classmethod
    def create(cls, **values: Any) -> Self:
        return cls(**values, content_sha256=_canonical_digest(values))

    def digest_is_valid(self) -> bool:
        return self.content_sha256 == _canonical_digest(
            self.model_dump(mode="json", exclude={"content_sha256"})
        )

    @model_validator(mode="after")
    def validate_prims(self) -> Self:
        for values, label in (
            (self.collision_enabled_counterpart_prim_paths, "counterpart prim inventory"),
            (self.collision_enabled_counterpart_categories, "counterpart category inventory"),
        ):
            if not values or values != tuple(sorted(set(values))):
                raise ValueError(f"USD collision {label} must be non-empty, unique, sorted")
        if not self.collision_prims:
            raise ValueError("at least one collision-enabled USD prim is required")
        for collision_prim in self.collision_prims:
            for point in collision_prim.projected_base_hull.vertices:
                boundary_distance = min(
                    _point_segment_distance(point, start, end)
                    for start, end in _segments(self.projected_base_hull)
                )
                if not _point_in_polygon(point, self.projected_base_hull) and (
                    boundary_distance > _EPSILON
                ):
                    raise ValueError("combined USD hull does not contain every collision prim")
        return self


class CollisionEvent(FrozenModel):
    ros_stamp_ns: int
    host_monotonic_ns: int
    simulation_epoch: str
    runtime_boot_id: str
    goal_uuid: str | None = None
    command_tag: str | None = None
    prim_a: str | None = None
    prim_b: str | None = None
    contact_point: tuple[float, float, float] | None = None
    contact_normal: tuple[float, float, float] | None = None
    penetration_m: float | None = None
    impulse_ns: float | None = None
    raw_message: dict[str, Any]


class CollisionObservationWindow(FrozenModel):
    kind: CollisionWindowKind
    observed_from_ros_ns: int | None
    observed_until_ros_ns: int | None
    observed_from_host_monotonic_ns: int
    observed_until_host_monotonic_ns: int
    raw_messages: tuple[CollisionEvent, ...]

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.observed_until_host_monotonic_ns < self.observed_from_host_monotonic_ns:
            raise ValueError("collision observation host window is reversed")
        if self.observed_from_ros_ns is not None and self.observed_until_ros_ns is not None:
            if self.observed_until_ros_ns < self.observed_from_ros_ns:
                raise ValueError("collision observation ROS window is reversed")
        return self


class CollisionFilterPrimRule(FrozenModel):
    """Raw effective filter rule for one monitored collision-enabled robot prim."""

    monitored_prim_path: str = Field(min_length=1)
    counterpart_prim_paths: tuple[str, ...]
    counterpart_categories: tuple[str, ...]
    contact_reporting_enabled: bool

    @model_validator(mode="after")
    def validate_canonical_rule(self) -> Self:
        for values, label in (
            (self.counterpart_prim_paths, "counterpart prim paths"),
            (self.counterpart_categories, "counterpart categories"),
        ):
            if not values or values != tuple(sorted(set(values))):
                raise ValueError(f"collision filter {label} must be non-empty, unique, sorted")
        return self


class CollisionFilterCoverageEvidence(FrozenModel):
    """Preserved raw filter inputs from which coverage is derived offline."""

    collision_enabled_counterpart_prim_paths: tuple[str, ...]
    collision_enabled_counterpart_categories: tuple[str, ...]
    prim_rules: tuple[CollisionFilterPrimRule, ...]
    content_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)

    @classmethod
    def create(cls, **values: Any) -> Self:
        return cls(**values, content_sha256=_canonical_digest(values))

    def digest_is_valid(self) -> bool:
        return self.content_sha256 == _canonical_digest(
            self.model_dump(mode="json", exclude={"content_sha256"})
        )

    @model_validator(mode="after")
    def validate_canonical_inventory(self) -> Self:
        for values, label in (
            (self.collision_enabled_counterpart_prim_paths, "counterpart prim inventory"),
            (self.collision_enabled_counterpart_categories, "counterpart category inventory"),
        ):
            if not values or values != tuple(sorted(set(values))):
                raise ValueError(f"collision filter {label} must be non-empty, unique, sorted")
        rule_paths = tuple(rule.monitored_prim_path for rule in self.prim_rules)
        if not rule_paths or rule_paths != tuple(sorted(set(rule_paths))):
            raise ValueError("collision filter rules must be non-empty, unique, sorted by prim")
        return self


class CollisionStreamEvidence(FrozenModel):
    evidence_id: str
    topic: str
    message_type: str
    qos: str
    status: EvidenceStatus
    source_timestamp_capable: bool
    clock_aligned: bool
    stream_presence_attested: bool
    phase: str
    simulation_epoch: str
    runtime_boot_id: str
    source_assurance: str
    transport_security: str
    scene_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    map_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    nav2_params_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    runtime_fingerprint: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    robot_root_prim: str = Field(min_length=1)
    monitored_prim_paths: tuple[str, ...]
    monitored_prim_inventory_sha256: str = Field(
        min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH
    )
    collision_filter: CollisionFilterCoverageEvidence
    windows: tuple[CollisionObservationWindow, ...]
    content_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)

    @classmethod
    def create(cls, **values: Any) -> Self:
        return cls(**values, content_sha256=_canonical_digest(values))

    def digest_is_valid(self) -> bool:
        return self.content_sha256 == _canonical_digest(
            self.model_dump(mode="json", exclude={"content_sha256"})
        )

    @model_validator(mode="after")
    def validate_monitored_inventory(self) -> Self:
        canonical = tuple(sorted(self.monitored_prim_paths))
        if (
            not canonical
            or canonical != self.monitored_prim_paths
            or len(set(canonical)) != len(canonical)
        ):
            raise ValueError("collision monitored prim inventory must be non-empty, unique, sorted")
        if self.monitored_prim_inventory_sha256 != collision_prim_inventory_sha256(canonical):
            raise ValueError("collision monitored prim inventory digest mismatch")
        return self


class ClearanceBudgetTerm(FrozenModel):
    kind: SafetyTermKind
    value_m: float | None
    source_evidence_id: str | None
    source_timestamp_ns: int | None
    config_sha256: str | None
    method: str
    unit: str = "m"


NonNegativeFloat = Annotated[float, Field(ge=0.0)]
PositiveFloat = Annotated[float, Field(gt=0.0)]


class BoundMeasurements(FrozenModel):
    bound_m: tuple[NonNegativeFloat, ...] = Field(min_length=1)


class SpeedLatencyMeasurements(FrozenModel):
    maximum_speed_magnitude_mps: tuple[NonNegativeFloat, ...] = Field(min_length=1)
    latency_s: tuple[NonNegativeFloat, ...] = Field(min_length=1)


class StoppingMeasurements(FrozenModel):
    maximum_speed_magnitude_mps: tuple[NonNegativeFloat, ...] = Field(min_length=1)
    minimum_deceleration_mps2: tuple[PositiveFloat, ...] = Field(min_length=1)


ClearanceMeasurements = BoundMeasurements | SpeedLatencyMeasurements | StoppingMeasurements


class ClearanceSourceEvidence(FrozenModel):
    evidence_id: str
    kind: SafetyTermKind
    status: EvidenceStatus
    source_assurance: str
    transport_security: str
    source_timestamp_ns: int
    config_sha256: str
    simulation_epoch: str
    runtime_boot_id: str
    runtime_fingerprint: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)
    measurements: ClearanceMeasurements
    content_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)

    @classmethod
    def create(cls, **values: Any) -> Self:
        return cls(**values, content_sha256=_canonical_digest(values))

    def digest_is_valid(self) -> bool:
        return self.content_sha256 == _canonical_digest(
            self.model_dump(mode="json", exclude={"content_sha256"})
        )


class ClearanceBudget(FrozenModel):
    terms: tuple[ClearanceBudgetTerm, ...]
    content_sha256: str = Field(min_length=_DIGEST_LENGTH, max_length=_DIGEST_LENGTH)

    @classmethod
    def create(cls, **values: Any) -> Self:
        return cls(**values, content_sha256=_canonical_digest(values))

    def digest_is_valid(self) -> bool:
        return self.content_sha256 == _canonical_digest(
            self.model_dump(mode="json", exclude={"content_sha256"})
        )


class ClearanceWitness(FrozenModel):
    layer: ClearanceLayer
    path_pose_index: int
    interpolated_pose_index: int
    path_distance_m: float
    pose: Pose2
    nearest_cell: CostmapCell | None
    nearest_boundary: str | None = None
    clearance_m: float
    source_stamp_ns: int | None


class SweptClearanceResult(FrozenModel):
    status: str
    minimum_clearance_m: float | None
    worst: ClearanceWitness | None
    per_layer_minimum_m: Mapping[ClearanceLayer, float | None]
    interpolation_step_m: float
    evaluated_pose_count: int
    failures: tuple[str, ...]


class GeometryAttestationResult(FrozenModel):
    status: str
    maximum_inward_error_m: float | None
    maximum_outward_error_m: float | None
    centroid_offset_m: float | None
    usd_width_m: float | None
    usd_length_m: float | None
    nav_width_m: float | None
    nav_length_m: float | None
    failures: tuple[str, ...]


class CollisionTimelineResult(FrozenModel):
    status: str
    collision_observed: bool
    event_count: int
    failures: tuple[str, ...]


class ClearancePolicyResult(FrozenModel):
    status: str
    required_clearance_m: float | None
    dominant_term: SafetyTermKind | None
    failures: tuple[str, ...]


class MotionReadinessResult(FrozenModel):
    decision: str
    blocking_gates: tuple[str, ...]
    measured_minimum_clearance_m: float | None
    required_clearance_m: float | None
    margin_m: float | None
    swept_clearance: SweptClearanceResult
    geometry_attestation: GeometryAttestationResult
    collision_timeline: CollisionTimelineResult
    clearance_policy: ClearancePolicyResult
    evidence_contract_failures: tuple[str, ...]


class MotionReadinessArtifact(FrozenModel):
    schema_version: int
    evidence_derivation_version: int
    runtime: RuntimeBinding
    motion_request: MotionRequestBinding
    authorization: MotionAuthorizationBinding | None
    path: PathEvidence
    nav_footprint: NavFootprintEvidence
    usd_geometry: UsdCollisionGeometryEvidence
    costmap_layers: tuple[CostmapLayerEvidence, ...]
    collision_stream: CollisionStreamEvidence
    clearance_sources: tuple[ClearanceSourceEvidence, ...]
    clearance_budget: ClearanceBudget
    result: MotionReadinessResult | None
    input_sha256: str = ""

    @model_validator(mode="after")
    def initialize_input_digest(self) -> Self:
        if not self.input_sha256:
            object.__setattr__(self, "input_sha256", self.expected_input_sha256())
        return self

    def expected_input_sha256(self) -> str:
        return _canonical_digest(
            self.model_dump(mode="json", exclude={"authorization", "result", "input_sha256"})
        )


class OfflineValidationReport(FrozenModel):
    valid: bool
    failures: tuple[str, ...]
    decision: str | None


def _polygon_area(polygon: Polygon2) -> float:
    total = 0.0
    points = polygon.vertices
    for left, right in zip(points, points[1:] + points[:1], strict=True):
        total += left.x * right.y - right.x * left.y
    return total / 2.0


def _cost_belongs_to_layer(cost: int, layer: ClearanceLayer) -> bool:
    if layer in {ClearanceLayer.STATIC_LETHAL, ClearanceLayer.LIVE_OBSTACLE}:
        return cost == 254
    if layer == ClearanceLayer.STATIC_INFLATION:
        return 1 <= cost <= 253
    return cost == 255


def _is_convex(polygon: Polygon2) -> bool:
    sign = 0
    points = polygon.vertices
    count = len(points)
    for index in range(count):
        first = points[index]
        second = points[(index + 1) % count]
        third = points[(index + 2) % count]
        cross = (second.x - first.x) * (third.y - second.y) - (second.y - first.y) * (
            third.x - second.x
        )
        if abs(cross) <= _EPSILON:
            continue
        current_sign = 1 if cross > 0.0 else -1
        if sign and current_sign != sign:
            return False
        sign = current_sign
    return bool(sign)


def _convex_hull(points: tuple[Point2, ...]) -> Polygon2:
    unique = sorted({(point.x, point.y) for point in points})
    if len(unique) < 3:
        raise ValueError("projected USD geometry requires at least three distinct points")

    def cross(
        origin: tuple[float, float], left: tuple[float, float], right: tuple[float, float]
    ) -> float:
        return (left[0] - origin[0]) * (right[1] - origin[1]) - (left[1] - origin[1]) * (
            right[0] - origin[0]
        )

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0.0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0.0:
            upper.pop()
        upper.append(point)
    return Polygon2(vertices=tuple(Point2(x=x, y=y) for x, y in lower[:-1] + upper[:-1]))


def _project_usd_prim(prim: UsdCollisionPrimEvidence, meters_per_unit: float) -> Polygon2:
    matrix = prim.base_from_prim_transform
    projected: list[Point2] = []
    for vertex in prim.local_geometry_vertices:
        x = vertex.x * prim.scale[0] * meters_per_unit
        y = vertex.y * prim.scale[1] * meters_per_unit
        z = vertex.z * prim.scale[2] * meters_per_unit
        projected.append(
            Point2(
                x=matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
                y=matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
            )
        )
    return _convex_hull(tuple(projected))


def _centroid(polygon: Polygon2) -> Point2:
    factor = 0.0
    x = 0.0
    y = 0.0
    points = polygon.vertices
    for left, right in zip(points, points[1:] + points[:1], strict=True):
        cross = left.x * right.y - right.x * left.y
        factor += cross
        x += (left.x + right.x) * cross
        y += (left.y + right.y) * cross
    return Point2(x=x / (3.0 * factor), y=y / (3.0 * factor))


def transform_polygon(polygon: Polygon2, pose: Pose2) -> Polygon2:
    cosine = math.cos(pose.yaw)
    sine = math.sin(pose.yaw)
    return Polygon2(
        vertices=tuple(
            Point2(
                x=pose.x + point.x * cosine - point.y * sine,
                y=pose.y + point.x * sine + point.y * cosine,
            )
            for point in polygon.vertices
        )
    )


def _line_intersection(
    first_start: Point2,
    first_end: Point2,
    second_start: Point2,
    second_end: Point2,
) -> Point2:
    first_dx = first_end.x - first_start.x
    first_dy = first_end.y - first_start.y
    second_dx = second_end.x - second_start.x
    second_dy = second_end.y - second_start.y
    denominator = first_dx * second_dy - first_dy * second_dx
    if abs(denominator) <= _EPSILON:
        raise ValueError("adjacent footprint edges cannot be parallel")
    offset_x = second_start.x - first_start.x
    offset_y = second_start.y - first_start.y
    position = (offset_x * second_dy - offset_y * second_dx) / denominator
    return Point2(
        x=first_start.x + position * first_dx,
        y=first_start.y + position * first_dy,
    )


def offset_convex_polygon(polygon: Polygon2, distance_m: float) -> Polygon2:
    """Offset a convex footprint using the intersection of shifted edge lines."""

    if distance_m < 0.0:
        raise ValueError("footprint offset must be non-negative")
    if distance_m == 0.0:
        return polygon
    orientation = 1.0 if _polygon_area(polygon) > 0.0 else -1.0
    shifted_edges: list[tuple[Point2, Point2]] = []
    for start, end in _segments(polygon):
        dx = end.x - start.x
        dy = end.y - start.y
        length = math.hypot(dx, dy)
        normal_x = orientation * dy / length
        normal_y = orientation * -dx / length
        shifted_edges.append(
            (
                Point2(x=start.x + normal_x * distance_m, y=start.y + normal_y * distance_m),
                Point2(x=end.x + normal_x * distance_m, y=end.y + normal_y * distance_m),
            )
        )
    vertices = tuple(
        _line_intersection(*shifted_edges[index - 1], *shifted_edges[index])
        for index in range(len(shifted_edges))
    )
    return Polygon2(vertices=vertices)


def _normalize_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def interpolate_path(
    poses: tuple[Pose2, ...],
    *,
    footprint: Polygon2,
    max_step_m: float,
) -> tuple[Pose2, ...]:
    if not poses:
        return ()
    if max_step_m <= 0.0:
        raise ValueError("max_step_m must be positive")
    radius = max(math.hypot(point.x, point.y) for point in footprint.vertices)
    output = [poses[0]]
    for start, end in zip(poses, poses[1:], strict=False):
        distance = math.hypot(end.x - start.x, end.y - start.y)
        delta_yaw = _normalize_angle(end.yaw - start.yaw)
        swept_distance = distance + abs(delta_yaw) * radius
        steps = max(1, math.ceil(swept_distance / max_step_m))
        for index in range(1, steps + 1):
            ratio = index / steps
            output.append(
                Pose2(
                    x=start.x + (end.x - start.x) * ratio,
                    y=start.y + (end.y - start.y) * ratio,
                    yaw=_normalize_angle(start.yaw + delta_yaw * ratio),
                )
            )
    return tuple(output)


def _segments(polygon: Polygon2) -> tuple[tuple[Point2, Point2], ...]:
    points = polygon.vertices
    return tuple(zip(points, points[1:] + points[:1], strict=True))


def _point_in_polygon(point: Point2, polygon: Polygon2) -> bool:
    inside = False
    previous = polygon.vertices[-1]
    for current in polygon.vertices:
        crosses = (current.y > point.y) != (previous.y > point.y)
        if crosses:
            intersection_x = (previous.x - current.x) * (point.y - current.y) / (
                previous.y - current.y
            ) + current.x
            if point.x < intersection_x:
                inside = not inside
        previous = current
    return inside


def _point_segment_distance(point: Point2, start: Point2, end: Point2) -> float:
    dx = end.x - start.x
    dy = end.y - start.y
    length_squared = dx * dx + dy * dy
    if length_squared <= _EPSILON:
        return math.hypot(point.x - start.x, point.y - start.y)
    position = ((point.x - start.x) * dx + (point.y - start.y) * dy) / length_squared
    position = min(1.0, max(0.0, position))
    return math.hypot(point.x - (start.x + position * dx), point.y - (start.y + position * dy))


def _axes(polygon: Polygon2) -> tuple[Point2, ...]:
    axes: list[Point2] = []
    for start, end in _segments(polygon):
        dx = end.x - start.x
        dy = end.y - start.y
        length = math.hypot(dx, dy)
        if length > _EPSILON:
            axes.append(Point2(x=-dy / length, y=dx / length))
    return tuple(axes)


def _projection(polygon: Polygon2, axis: Point2) -> tuple[float, float]:
    values = tuple(point.x * axis.x + point.y * axis.y for point in polygon.vertices)
    return min(values), max(values)


def _overlap_depth(left: Polygon2, right: Polygon2) -> float | None:
    minimum = math.inf
    for axis in _axes(left) + _axes(right):
        left_min, left_max = _projection(left, axis)
        right_min, right_max = _projection(right, axis)
        overlap = min(left_max, right_max) - max(left_min, right_min)
        if overlap < -_EPSILON:
            return None
        minimum = min(minimum, max(0.0, overlap))
    return minimum


def signed_polygon_clearance(left: Polygon2, right: Polygon2) -> float:
    overlap = _overlap_depth(left, right)
    if overlap is not None:
        return -max(overlap, _EPSILON)
    distances = [
        _point_segment_distance(point, start, end)
        for point in left.vertices
        for start, end in _segments(right)
    ]
    distances.extend(
        _point_segment_distance(point, start, end)
        for point in right.vertices
        for start, end in _segments(left)
    )
    return min(distances)


def _cell_polygon(cell: CostmapCell, resolution_m: float) -> Polygon2:
    half = resolution_m / 2.0
    return Polygon2(
        vertices=(
            Point2(x=cell.x - half, y=cell.y - half),
            Point2(x=cell.x + half, y=cell.y - half),
            Point2(x=cell.x + half, y=cell.y + half),
            Point2(x=cell.x - half, y=cell.y + half),
        )
    )


def _path_distances(poses: tuple[Pose2, ...]) -> tuple[float, ...]:
    distances = [0.0]
    for left, right in zip(poses, poses[1:], strict=False):
        distances.append(distances[-1] + math.hypot(right.x - left.x, right.y - left.y))
    return tuple(distances)


def _validate_clearance_layers(artifact: MotionReadinessArtifact) -> list[str]:
    failures: list[str] = []
    layers_by_kind = {layer.layer: layer for layer in artifact.costmap_layers}
    if len(artifact.costmap_layers) != len(ClearanceLayer) or set(layers_by_kind) != set(
        ClearanceLayer
    ):
        failures.append("all four costmap layers must be present exactly once")
    for kind in ClearanceLayer:
        layer = layers_by_kind.get(kind)
        if layer is None or layer.status != EvidenceStatus.OBSERVED:
            failures.append(f"{kind.value} layer evidence is not observed")
        elif layer.frame_id != artifact.path.frame_id:
            failures.append(f"{kind.value} layer frame does not match planned path")
    return failures


def _blocked_clearance_result(
    failures: list[str], *, step: float = 0.0, evaluated: int = 0
) -> SweptClearanceResult:
    return SweptClearanceResult(
        status="BLOCK",
        minimum_clearance_m=None,
        worst=None,
        per_layer_minimum_m={kind: None for kind in ClearanceLayer},
        interpolation_step_m=step,
        evaluated_pose_count=evaluated,
        failures=tuple(dict.fromkeys(failures)),
    )


def _grid_coverage_failures(
    artifact: MotionReadinessArtifact, interpolated: tuple[Pose2, ...]
) -> list[str]:
    failures: list[str] = []
    for pose in interpolated:
        footprint = transform_polygon(artifact.nav_footprint.polygon, pose)
        for layer in artifact.costmap_layers:
            maximum_x = layer.origin.x + layer.width * layer.resolution_m
            maximum_y = layer.origin.y + layer.height * layer.resolution_m
            outside = any(
                point.x < layer.origin.x
                or point.x > maximum_x
                or point.y < layer.origin.y
                or point.y > maximum_y
                for point in footprint.vertices
            )
            if outside:
                failures.append(f"swept footprint leaves {layer.layer.value} grid coverage")
    return failures


def _boundary_clearance(footprint: Polygon2, layer: CostmapLayerEvidence) -> tuple[float, str]:
    maximum_x = layer.origin.x + layer.width * layer.resolution_m
    maximum_y = layer.origin.y + layer.height * layer.resolution_m
    candidates = (
        (min(point.x - layer.origin.x for point in footprint.vertices), "minimum_x"),
        (min(maximum_x - point.x for point in footprint.vertices), "maximum_x"),
        (min(point.y - layer.origin.y for point in footprint.vertices), "minimum_y"),
        (min(maximum_y - point.y for point in footprint.vertices), "maximum_y"),
    )
    return min(candidates, key=lambda item: item[0])


def _evaluate_clearance_witnesses(
    artifact: MotionReadinessArtifact, interpolated: tuple[Pose2, ...]
) -> tuple[dict[ClearanceLayer, float], ClearanceWitness | None, float]:
    original_distances = _path_distances(artifact.path.poses)
    per_layer = {kind: _NO_OBSTACLE_CLEARANCE_M for kind in ClearanceLayer}
    worst: ClearanceWitness | None = None
    minimum = _NO_OBSTACLE_CLEARANCE_M
    cumulative = 0.0
    previous = interpolated[0]
    for interpolated_index, pose in enumerate(interpolated):
        if interpolated_index:
            cumulative += math.hypot(pose.x - previous.x, pose.y - previous.y)
        previous = pose
        footprint = transform_polygon(artifact.nav_footprint.polygon, pose)
        path_index = min(
            range(len(original_distances)),
            key=lambda index: abs(original_distances[index] - cumulative),
        )
        for layer in artifact.costmap_layers:
            boundary_clearance, boundary = _boundary_clearance(footprint, layer)
            per_layer[layer.layer] = min(per_layer[layer.layer], boundary_clearance)
            if boundary_clearance < minimum:
                minimum = boundary_clearance
                worst = ClearanceWitness(
                    layer=layer.layer,
                    path_pose_index=path_index,
                    interpolated_pose_index=interpolated_index,
                    path_distance_m=cumulative,
                    pose=pose,
                    nearest_cell=None,
                    nearest_boundary=boundary,
                    clearance_m=boundary_clearance,
                    source_stamp_ns=layer.source_stamp_ns,
                )
            for cell in layer.cells:
                clearance = signed_polygon_clearance(
                    footprint, _cell_polygon(cell, layer.resolution_m)
                )
                per_layer[layer.layer] = min(per_layer[layer.layer], clearance)
                if layer.layer != ClearanceLayer.STATIC_INFLATION and clearance < minimum:
                    minimum = clearance
                    worst = ClearanceWitness(
                        layer=layer.layer,
                        path_pose_index=path_index,
                        interpolated_pose_index=interpolated_index,
                        path_distance_m=cumulative,
                        pose=pose,
                        nearest_cell=cell,
                        nearest_boundary=None,
                        clearance_m=clearance,
                        source_stamp_ns=layer.source_stamp_ns,
                    )
    return per_layer, worst, minimum


def _swept_clearance(artifact: MotionReadinessArtifact) -> SweptClearanceResult:
    failures = _validate_clearance_layers(artifact)
    if failures:
        return _blocked_clearance_result(failures)
    resolution = min(layer.resolution_m for layer in artifact.costmap_layers)
    step = resolution / 2.0
    interpolated = interpolate_path(
        artifact.path.poses,
        footprint=artifact.nav_footprint.polygon,
        max_step_m=step,
    )
    coverage_failures = _grid_coverage_failures(artifact, interpolated)
    if coverage_failures:
        return _blocked_clearance_result(coverage_failures, step=step, evaluated=len(interpolated))
    per_layer, worst, minimum = _evaluate_clearance_witnesses(artifact, interpolated)
    intersected = minimum < 0.0
    return SweptClearanceResult(
        status="BLOCK" if intersected else "PASS",
        minimum_clearance_m=minimum,
        worst=worst,
        per_layer_minimum_m=per_layer,
        interpolation_step_m=step,
        evaluated_pose_count=len(interpolated),
        failures=("swept footprint intersects physical hazard",) if intersected else (),
    )


def _bounds(polygon: Polygon2) -> tuple[float, float]:
    xs = [point.x for point in polygon.vertices]
    ys = [point.y for point in polygon.vertices]
    return max(xs) - min(xs), max(ys) - min(ys)


def _polygons_close(left: Polygon2, right: Polygon2) -> bool:
    if len(left.vertices) != len(right.vertices):
        return False
    unmatched = list(right.vertices)
    for point in left.vertices:
        match = next(
            (
                candidate
                for candidate in unmatched
                if math.hypot(point.x - candidate.x, point.y - candidate.y) <= 1e-6
            ),
            None,
        )
        if match is None:
            return False
        unmatched.remove(match)
    return not unmatched


def _geometry_contract_failures(artifact: MotionReadinessArtifact) -> list[str]:
    usd = artifact.usd_geometry
    checks = (
        (
            usd.scene_path != artifact.runtime.scene_path
            or usd.scene_sha256 != artifact.runtime.scene_sha256,
            "USD geometry does not bind to the active scene",
        ),
        (
            usd.simulation_epoch != artifact.runtime.simulation_epoch,
            "USD geometry simulation epoch differs from runtime",
        ),
        (
            usd.source_assurance != "simulator_stage_query",
            "USD collision geometry source assurance is insufficient",
        ),
        (
            usd.transport_security not in {"local_process", "authenticated"},
            "USD collision geometry transport is not identified",
        ),
        (
            usd.base_frame != artifact.nav_footprint.frame_id,
            "USD collision geometry base frame differs from live Nav2 footprint",
        ),
        (
            not usd.collision_prim_inventory_complete,
            "USD collision primitive inventory is incomplete",
        ),
        (
            not usd.scene_collision_enumeration.complete_attestation,
            "USD scene collision enumeration is not complete",
        ),
        (
            not usd.scene_collision_enumeration.digest_is_valid(),
            "USD scene collision enumeration digest is invalid",
        ),
        (
            usd.scene_collision_enumeration.scene_sha256 != usd.scene_sha256,
            "USD scene collision enumeration binds a different scene",
        ),
        (
            abs(
                usd.scene_collision_enumeration.source_timestamp_ns
                - artifact.runtime.capture_ros_ns
            )
            > artifact.runtime.max_evidence_age_ns,
            "USD scene collision enumeration is stale",
        ),
        (
            usd.collision_enabled_counterpart_prim_paths
            != usd.scene_collision_enumeration.counterpart_prim_paths(),
            "USD collision counterpart prim summary differs from raw scene enumeration",
        ),
        (
            usd.collision_enabled_counterpart_categories
            != usd.scene_collision_enumeration.counterpart_categories(),
            "USD collision counterpart category summary differs from raw scene enumeration",
        ),
        (
            len({prim.prim_path for prim in usd.collision_prims}) != len(usd.collision_prims)
            or any(
                not prim.prim_path.startswith(f"{usd.robot_root_prim.rstrip('/')}/")
                for prim in usd.collision_prims
            ),
            "USD collision primitive inventory is duplicated or outside the robot root",
        ),
        (
            usd.axis_convention != artifact.nav_footprint.axis_convention,
            "USD and Nav2 footprint axis/yaw conventions differ",
        ),
        (
            not artifact.nav_footprint.padding_applied,
            "live Nav2 footprint padding was not applied to the effective polygon",
        ),
    )
    return [message for failed, message in checks if failed]


def _geometry_rebuild_failures(artifact: MotionReadinessArtifact) -> list[str]:
    usd = artifact.usd_geometry
    failures: list[str] = []
    derived_prim_hulls = tuple(
        _project_usd_prim(prim, usd.meters_per_unit) for prim in usd.collision_prims
    )
    failures.extend(
        f"USD projected hull cannot be rebuilt for {prim.prim_path}"
        for prim, derived in zip(usd.collision_prims, derived_prim_hulls, strict=True)
        if not _polygons_close(prim.projected_base_hull, derived)
    )
    derived_combined = _convex_hull(
        tuple(point for hull in derived_prim_hulls for point in hull.vertices)
    )
    if not _polygons_close(usd.projected_base_hull, derived_combined):
        failures.append("combined USD collision hull cannot be rebuilt from raw prim geometry")
    expected_effective = offset_convex_polygon(
        artifact.nav_footprint.configured_polygon,
        artifact.nav_footprint.footprint_padding_m,
    )
    if not _polygons_close(artifact.nav_footprint.polygon, expected_effective):
        failures.append("effective Nav2 footprint cannot be rebuilt from polygon plus padding")
    return failures


def _geometry_attestation(artifact: MotionReadinessArtifact) -> GeometryAttestationResult:
    usd = artifact.usd_geometry
    nav = artifact.nav_footprint.polygon
    failures = _geometry_contract_failures(artifact) + _geometry_rebuild_failures(artifact)
    outside_distances = [
        min(_point_segment_distance(point, start, end) for start, end in _segments(nav))
        for point in usd.projected_base_hull.vertices
        if not _point_in_polygon(point, nav)
    ]
    maximum_inward = max(outside_distances, default=0.0)
    if maximum_inward > _EPSILON:
        failures.append("live Nav2 footprint is smaller than the USD collision hull")
    outward_distances = [
        min(
            _point_segment_distance(point, start, end)
            for start, end in _segments(usd.projected_base_hull)
        )
        for point in nav.vertices
    ]
    usd_centroid = _centroid(usd.projected_base_hull)
    nav_centroid = _centroid(nav)
    usd_width, usd_length = _bounds(usd.projected_base_hull)
    nav_width, nav_length = _bounds(nav)
    return GeometryAttestationResult(
        status="PASS" if not failures else "BLOCK",
        maximum_inward_error_m=maximum_inward,
        maximum_outward_error_m=max(outward_distances),
        centroid_offset_m=math.hypot(
            usd_centroid.x - nav_centroid.x, usd_centroid.y - nav_centroid.y
        ),
        usd_width_m=usd_width,
        usd_length_m=usd_length,
        nav_width_m=nav_width,
        nav_length_m=nav_length,
        failures=tuple(failures),
    )


def _collision_stream_failures(artifact: MotionReadinessArtifact) -> list[str]:
    stream = artifact.collision_stream
    checks = (
        (stream.status != EvidenceStatus.OBSERVED, "collision stream was not observed and fresh"),
        (not stream.stream_presence_attested, "collision stream presence is not attested"),
        (not stream.source_timestamp_capable, "collision source cannot timestamp events"),
        (not stream.clock_aligned, "collision source is not aligned to simulation clock"),
        (
            stream.simulation_epoch != artifact.runtime.simulation_epoch,
            "collision stream simulation epoch differs from runtime",
        ),
        (
            stream.runtime_boot_id != artifact.runtime.runtime_boot_id,
            "collision stream boot identity differs from runtime",
        ),
        (
            stream.source_assurance not in {"runtime_observed", "simulator_attested"},
            "collision stream source assurance is insufficient",
        ),
        (
            stream.transport_security not in {"local_process", "authenticated"},
            "collision stream transport is unidentified",
        ),
        (
            stream.scene_sha256 != artifact.runtime.scene_sha256,
            "collision stream scene identity differs from runtime",
        ),
        (
            stream.map_sha256 != artifact.runtime.map_sha256,
            "collision stream map identity differs from runtime",
        ),
        (
            stream.nav2_params_sha256 != artifact.runtime.nav2_params_sha256,
            "collision stream Nav2 parameters identity differs from runtime",
        ),
        (
            stream.runtime_fingerprint != artifact.runtime.runtime_fingerprint,
            "collision stream runtime identity differs from runtime",
        ),
        (
            stream.robot_root_prim != artifact.usd_geometry.robot_root_prim,
            "collision stream robot root differs from USD geometry",
        ),
        (
            stream.monitored_prim_paths
            != tuple(sorted(prim.prim_path for prim in artifact.usd_geometry.collision_prims)),
            "collision stream monitored prim inventory differs from USD geometry",
        ),
        (
            stream.monitored_prim_inventory_sha256
            != collision_prim_inventory_sha256(stream.monitored_prim_paths),
            "collision stream monitored prim inventory digest is invalid",
        ),
        (
            not stream.collision_filter.digest_is_valid(),
            "collision filter digest is invalid",
        ),
        (
            stream.collision_filter.content_sha256 != artifact.runtime.collision_filter_sha256,
            "collision filter identity differs from runtime",
        ),
    )
    failures = [message for failed, message in checks if failed]
    collision_filter = stream.collision_filter
    rules = {rule.monitored_prim_path: rule for rule in collision_filter.prim_rules}
    if set(rules) != set(stream.monitored_prim_paths):
        failures.append("collision filter rules do not cover every monitored USD prim")
    scene_enumeration = artifact.usd_geometry.scene_collision_enumeration
    expected_prims = scene_enumeration.counterpart_prim_paths()
    expected_categories = scene_enumeration.counterpart_categories()
    if collision_filter.collision_enabled_counterpart_prim_paths != expected_prims:
        failures.append("collision filter counterpart inventory differs from USD scene inventory")
    if collision_filter.collision_enabled_counterpart_categories != expected_categories:
        failures.append("collision filter category inventory differs from USD scene inventory")
    for prim_path in stream.monitored_prim_paths:
        rule = rules.get(prim_path)
        if rule is None:
            continue
        if rule.counterpart_prim_paths != expected_prims:
            failures.append(f"collision filter rule for {prim_path} omits enabled counterparts")
        if rule.counterpart_categories != expected_categories:
            failures.append(f"collision filter rule for {prim_path} omits enabled categories")
        if not rule.contact_reporting_enabled:
            failures.append(f"collision filter rule for {prim_path} disables contact reporting")
    kinds = [window.kind for window in stream.windows]
    required = (
        {CollisionWindowKind.PRE_DISPATCH}
        if stream.phase == "no_motion"
        else set(CollisionWindowKind)
    )
    if not required.issubset(kinds) or len(kinds) != len(set(kinds)):
        failures.append("collision timeline windows are missing or duplicated")
    return failures


def _raw_collision_detected(event: CollisionEvent) -> bool | None:
    value = event.raw_message.get("collision_detected")
    return value if type(value) is bool else None


def _collision_window_failures(
    artifact: MotionReadinessArtifact, window: CollisionObservationWindow
) -> list[str]:
    runtime = artifact.runtime
    from_ros_ns = window.observed_from_ros_ns
    until_ros_ns = window.observed_until_ros_ns
    ros_missing = from_ros_ns is None or until_ros_ns is None
    ros_stale_or_long = False
    if from_ros_ns is not None and until_ros_ns is not None:
        ros_stale_or_long = (
            abs(from_ros_ns - runtime.capture_ros_ns) > runtime.max_evidence_age_ns
            or abs(until_ros_ns - runtime.capture_ros_ns) > runtime.max_evidence_age_ns
            or until_ros_ns - from_ros_ns > runtime.max_evidence_age_ns
        )
    host_stale_or_long = (
        abs(window.observed_from_host_monotonic_ns - runtime.capture_host_monotonic_ns)
        > runtime.max_evidence_age_ns
        or abs(window.observed_until_host_monotonic_ns - runtime.capture_host_monotonic_ns)
        > runtime.max_evidence_age_ns
        or (
            window.observed_until_host_monotonic_ns - window.observed_from_host_monotonic_ns
            > runtime.max_evidence_age_ns
        )
    )
    checks = (
        (not window.raw_messages, "collision observation window lacks a raw presence sample"),
        (ros_missing, "collision stream lacks a ROS-time observation window"),
        (ros_stale_or_long, "collision ROS observation window is stale or unbounded"),
        (host_stale_or_long, "collision host observation window is stale or unbounded"),
    )
    return [message for failed, message in checks if failed]


def _single_collision_event_failures(
    artifact: MotionReadinessArtifact,
    stream: CollisionStreamEvidence,
    window: CollisionObservationWindow,
    event: CollisionEvent,
) -> list[str]:
    runtime = artifact.runtime
    ros_outside = (
        window.observed_from_ros_ns is not None
        and window.observed_until_ros_ns is not None
        and not (window.observed_from_ros_ns <= event.ros_stamp_ns <= window.observed_until_ros_ns)
    )
    checks = (
        (
            event.simulation_epoch != stream.simulation_epoch,
            "collision event simulation epoch drifted",
        ),
        (
            event.runtime_boot_id != stream.runtime_boot_id,
            "collision event runtime boot identity drifted",
        ),
        (
            not (
                window.observed_from_host_monotonic_ns
                <= event.host_monotonic_ns
                <= window.observed_until_host_monotonic_ns
            ),
            "collision event lies outside the observed host timeline",
        ),
        (ros_outside, "collision event lies outside the observed ROS timeline"),
        (
            abs(event.ros_stamp_ns - runtime.capture_ros_ns) > runtime.max_evidence_age_ns
            or abs(event.host_monotonic_ns - runtime.capture_host_monotonic_ns)
            > runtime.max_evidence_age_ns,
            "collision presence sample is stale",
        ),
        (
            _raw_collision_detected(event) is None,
            "collision raw payload lacks an exact Boolean collision_detected field",
        ),
        (
            _raw_collision_detected(event) is True
            and (
                not event.prim_a
                or not event.prim_b
                or event.contact_point is None
                or event.contact_normal is None
                or event.penetration_m is None
                or event.impulse_ns is None
            ),
            "positive collision event lacks participants or contact evidence",
        ),
        (
            _raw_collision_detected(event) is True
            and event.prim_a not in stream.monitored_prim_paths
            and event.prim_b not in stream.monitored_prim_paths,
            "positive collision event is unrelated to the monitored robot geometry",
        ),
    )
    return [message for failed, message in checks if failed]


def _collision_event_failures(artifact: MotionReadinessArtifact) -> list[str]:
    stream = artifact.collision_stream
    failures: list[str] = []
    for window in stream.windows:
        failures.extend(_collision_window_failures(artifact, window))
        for event in window.raw_messages:
            failures.extend(_single_collision_event_failures(artifact, stream, window, event))
    return failures


def _collision_timeline(artifact: MotionReadinessArtifact) -> CollisionTimelineResult:
    stream = artifact.collision_stream
    failures = _collision_stream_failures(artifact) + _collision_event_failures(artifact)
    events = tuple(event for window in stream.windows for event in window.raw_messages)
    collision_observed = any(_raw_collision_detected(event) is True for event in events)
    if collision_observed:
        failures.append("collision event observed")
    return CollisionTimelineResult(
        status="PASS" if not failures else "BLOCK",
        collision_observed=collision_observed,
        event_count=len(events),
        failures=tuple(dict.fromkeys(failures)),
    )


def _term_expected_config(artifact: MotionReadinessArtifact, kind: SafetyTermKind) -> str:
    if kind == SafetyTermKind.GEOMETRY_ATTESTATION_UNCERTAINTY:
        return artifact.runtime.scene_sha256
    if kind in {SafetyTermKind.LOCALIZATION_UNCERTAINTY, SafetyTermKind.MAP_DISCRETIZATION_BOUND}:
        return artifact.runtime.map_sha256
    if kind in {SafetyTermKind.CONTROLLER_TRACKING_BOUND, SafetyTermKind.STOPPING_DISTANCE}:
        return artifact.runtime.nav2_params_sha256
    if kind == SafetyTermKind.LATENCY_DISTANCE:
        return artifact.runtime.runtime_fingerprint
    return artifact.runtime.product_config_sha256


def _term_calculated_value(
    artifact: MotionReadinessArtifact, term: ClearanceBudgetTerm
) -> float | None:
    source = next(
        (
            item
            for item in artifact.clearance_sources
            if item.evidence_id == term.source_evidence_id
        ),
        None,
    )
    if source is None or source.kind != term.kind:
        return None
    measurements = source.measurements
    if term.kind == SafetyTermKind.LATENCY_DISTANCE:
        if not isinstance(measurements, SpeedLatencyMeasurements):
            return None
        return max(measurements.maximum_speed_magnitude_mps) * max(measurements.latency_s)
    if term.kind == SafetyTermKind.STOPPING_DISTANCE:
        if not isinstance(measurements, StoppingMeasurements):
            return None
        return max(measurements.maximum_speed_magnitude_mps) ** 2 / (
            2.0 * min(measurements.minimum_deceleration_mps2)
        )
    if not isinstance(measurements, BoundMeasurements):
        return None
    bound = max(measurements.bound_m)
    if term.kind == SafetyTermKind.MAP_DISCRETIZATION_BOUND:
        minimum = max(layer.resolution_m for layer in artifact.costmap_layers) / math.sqrt(2.0)
        if bound is None or bound < minimum:
            return None
    return bound


_SAFETY_DERIVATION_METHODS: Mapping[SafetyTermKind, str] = {
    SafetyTermKind.GEOMETRY_ATTESTATION_UNCERTAINTY: "maximum_bound",
    SafetyTermKind.LOCALIZATION_UNCERTAINTY: "maximum_bound",
    SafetyTermKind.CONTROLLER_TRACKING_BOUND: "maximum_bound",
    SafetyTermKind.MAP_DISCRETIZATION_BOUND: "map_resolution_diagonal_bound",
    SafetyTermKind.LATENCY_DISTANCE: "speed_times_latency",
    SafetyTermKind.STOPPING_DISTANCE: "kinematic_stopping_distance",
    SafetyTermKind.FIXED_PRODUCT_MARGIN: "product_policy_margin",
}


def _clearance_term_failure(
    artifact: MotionReadinessArtifact,
    kind: SafetyTermKind,
    term: ClearanceBudgetTerm | None,
    sources_by_id: Mapping[str, ClearanceSourceEvidence],
) -> tuple[str | None, float | None]:
    if term is None:
        return f"missing clearance budget term: {kind.value}", None
    if term.value_m is None or term.value_m <= 0.0:
        return f"clearance budget term is unavailable or non-positive: {kind.value}", None
    if not term.source_evidence_id or term.source_timestamp_ns is None or not term.config_sha256:
        return f"clearance budget term lacks source binding: {kind.value}", None
    source = sources_by_id.get(term.source_evidence_id)
    checks = (
        (
            source is None or source.kind != kind,
            "clearance source evidence is missing or wrong kind",
        ),
        (
            source is not None
            and (source.status != EvidenceStatus.OBSERVED or not source.digest_is_valid()),
            "clearance source evidence is unavailable or modified",
        ),
        (
            source is not None
            and source.source_assurance not in {"runtime_observed", "simulator_attested"},
            "clearance source assurance is insufficient",
        ),
        (
            source is not None
            and source.transport_security not in {"local_process", "authenticated"},
            "clearance source transport is unidentified",
        ),
        (
            source is not None and source.source_timestamp_ns != term.source_timestamp_ns,
            "clearance term timestamp differs from source",
        ),
        (
            source is not None and source.config_sha256 != term.config_sha256,
            "clearance term config differs from source",
        ),
        (term.unit != "m", "clearance budget term has unsupported unit"),
        (
            term.method != _SAFETY_DERIVATION_METHODS[kind],
            "clearance budget term has unsupported derivation method",
        ),
        (
            term.config_sha256 != _term_expected_config(artifact, kind),
            "clearance budget term has wrong config binding",
        ),
        (
            abs(term.source_timestamp_ns - artifact.runtime.capture_ros_ns)
            > artifact.runtime.max_evidence_age_ns,
            "clearance budget term is stale",
        ),
    )
    failure = next((message for failed, message in checks if failed), None)
    if failure is not None:
        return f"{failure}: {kind.value}", None
    calculated = _term_calculated_value(artifact, term)
    if calculated is None or not math.isclose(term.value_m, calculated, abs_tol=1e-9):
        return f"clearance budget term does not match its inputs: {kind.value}", None
    return None, term.value_m


def _clearance_policy(artifact: MotionReadinessArtifact) -> ClearancePolicyResult:
    terms_by_kind = {term.kind: term for term in artifact.clearance_budget.terms}
    sources_by_id = {source.evidence_id: source for source in artifact.clearance_sources}
    failures: list[str] = []
    if len(terms_by_kind) != len(artifact.clearance_budget.terms):
        failures.append("clearance budget contains duplicate terms")
    if len(sources_by_id) != len(artifact.clearance_sources):
        failures.append("clearance source evidence IDs are duplicated")
    values: dict[SafetyTermKind, float] = {}
    for kind in SafetyTermKind:
        failure, value = _clearance_term_failure(
            artifact, kind, terms_by_kind.get(kind), sources_by_id
        )
        if failure is not None:
            failures.append(failure)
        elif value is not None:
            values[kind] = value
    dominant = max(values, key=lambda kind: values[kind]) if values else None
    return ClearancePolicyResult(
        status="PASS" if not failures else "BLOCK",
        required_clearance_m=sum(values.values()) if not failures else None,
        dominant_term=dominant,
        failures=tuple(failures),
    )


def evaluate_motion_readiness(artifact: MotionReadinessArtifact) -> MotionReadinessResult:
    swept = _swept_clearance(artifact)
    geometry = _geometry_attestation(artifact)
    collision = _collision_timeline(artifact)
    policy = _clearance_policy(artifact)
    evidence_failures = tuple(
        _evidence_digest_failures(artifact) + _evidence_binding_failures(artifact)
    )
    blocking: list[str] = []
    if swept.status != "PASS":
        blocking.append("swept_footprint_clearance")
    if geometry.status != "PASS":
        blocking.append("geometry_attestation")
    if collision.status != "PASS":
        blocking.append("collision_timeline")
    if policy.status != "PASS":
        blocking.append("minimum_safe_clearance_policy")
    if evidence_failures:
        blocking.append("evidence_contract")
    measured = swept.minimum_clearance_m
    required = policy.required_clearance_m
    margin = measured - required if measured is not None and required is not None else None
    if margin is not None and margin < 0.0 and "swept_footprint_clearance" not in blocking:
        blocking.append("swept_footprint_clearance")
    return MotionReadinessResult(
        decision="PASS" if not blocking else "BLOCK",
        blocking_gates=tuple(blocking),
        measured_minimum_clearance_m=measured,
        required_clearance_m=required,
        margin_m=margin,
        swept_clearance=swept,
        geometry_attestation=geometry,
        collision_timeline=collision,
        clearance_policy=policy,
        evidence_contract_failures=evidence_failures,
    )


def _evidence_digest_failures(artifact: MotionReadinessArtifact) -> list[str]:
    failures: list[str] = []
    evidence: tuple[tuple[str, object], ...] = (
        ("motion request", artifact.motion_request),
        ("planned path", artifact.path),
        ("Nav2 footprint", artifact.nav_footprint),
        ("USD collision geometry", artifact.usd_geometry),
        ("collision stream", artifact.collision_stream),
    )
    for label, item in evidence:
        if not item.digest_is_valid():  # type: ignore[attr-defined]
            failures.append(f"{label} content digest mismatch")
    if artifact.authorization is None:
        failures.append("motion authorization binding is missing")
    elif not artifact.authorization.digest_is_valid():
        failures.append("motion authorization content digest mismatch")
    if not artifact.clearance_budget.digest_is_valid():
        failures.append("clearance budget content digest mismatch")
    failures.extend(
        f"{source.kind.value} clearance source content digest mismatch"
        for source in artifact.clearance_sources
        if not source.digest_is_valid()
    )
    failures.extend(
        f"{layer.layer.value} costmap content digest mismatch"
        for layer in artifact.costmap_layers
        if not layer.digest_is_valid()
    )
    return failures


RuntimeGenerationEvidence = (
    PathEvidence
    | NavFootprintEvidence
    | UsdCollisionGeometryEvidence
    | CostmapLayerEvidence
    | ClearanceSourceEvidence
)


def _runtime_generation_failures(
    item: RuntimeGenerationEvidence, label: str, runtime: RuntimeBinding
) -> list[str]:
    checks = (
        (item.runtime_fingerprint != runtime.runtime_fingerprint, "runtime identity"),
        (item.simulation_epoch != runtime.simulation_epoch, "simulation epoch"),
        (item.runtime_boot_id != runtime.runtime_boot_id, "boot identity"),
    )
    return [f"{label} {dimension} differs from runtime" for failed, dimension in checks if failed]


def _authorization_binding_failures(artifact: MotionReadinessArtifact) -> list[str]:
    runtime = artifact.runtime
    request = artifact.motion_request
    failures: list[str] = []
    ros_endpoints = (request.valid_from_ros_ns, request.valid_until_ros_ns)
    host_endpoints = (
        request.valid_from_host_monotonic_ns,
        request.valid_until_host_monotonic_ns,
    )
    if any(
        abs(value - runtime.capture_ros_ns) > runtime.max_evidence_age_ns for value in ros_endpoints
    ):
        failures.append("motion request ROS validity lies outside the captured evidence age")
    if any(
        abs(value - runtime.capture_host_monotonic_ns) > runtime.max_evidence_age_ns
        for value in host_endpoints
    ):
        failures.append("motion request host validity lies outside the captured evidence age")
    if not request.valid_from_ros_ns <= runtime.capture_ros_ns <= request.valid_until_ros_ns:
        failures.append("motion request ROS validity does not overlap the evidence capture")
    if not (
        request.valid_from_host_monotonic_ns
        <= runtime.capture_host_monotonic_ns
        <= request.valid_until_host_monotonic_ns
    ):
        failures.append("motion request host validity does not overlap the evidence capture")
    if request.valid_until_ros_ns - request.valid_from_ros_ns > runtime.max_evidence_age_ns:
        failures.append("motion request ROS validity exceeds the bounded evidence age")
    if (
        request.valid_until_host_monotonic_ns - request.valid_from_host_monotonic_ns
        > runtime.max_evidence_age_ns
    ):
        failures.append("motion request host validity exceeds the bounded evidence age")
    authorization = artifact.authorization
    if authorization is None:
        return failures
    bindings = (
        (authorization.authorization_nonce, request.authorization_nonce, "authorization nonce"),
        (
            authorization.motion_request_sha256,
            request.content_sha256,
            "authorization motion request identity",
        ),
        (
            authorization.path_evidence_sha256,
            artifact.path.content_sha256,
            "authorization planned path identity",
        ),
        (
            authorization.artifact_input_sha256,
            artifact.expected_input_sha256(),
            "authorization artifact input identity",
        ),
    )
    failures.extend(
        f"{label} differs from artifact"
        for observed, expected, label in bindings
        if observed != expected
    )
    return failures


def _identity_binding_failures(artifact: MotionReadinessArtifact) -> list[str]:
    runtime = artifact.runtime
    failures: list[str] = []
    direct: tuple[tuple[RuntimeGenerationEvidence, str], ...] = (
        (artifact.path, "planned path"),
        (artifact.nav_footprint, "Nav2 footprint"),
        (artifact.usd_geometry, "USD geometry"),
        *((layer, f"{layer.layer.value} costmap") for layer in artifact.costmap_layers),
        *(
            (source, f"{source.kind.value} clearance source")
            for source in artifact.clearance_sources
        ),
    )
    for item, label in direct:
        failures.extend(_runtime_generation_failures(item, label, runtime))
    bindings = (
        (
            artifact.motion_request.nav2_params_sha256,
            runtime.nav2_params_sha256,
            "motion request Nav2 parameter identity",
        ),
        (
            artifact.motion_request.product_config_sha256,
            runtime.product_config_sha256,
            "motion request product config identity",
        ),
        (
            artifact.motion_request.planner_config_sha256,
            runtime.planner_config_sha256,
            "motion request planner config identity",
        ),
        (artifact.motion_request.site_id, runtime.site_id, "motion request Site identity"),
        (
            artifact.motion_request.scene_sha256,
            runtime.scene_sha256,
            "motion request scene identity",
        ),
        (
            artifact.motion_request.map_sha256,
            runtime.map_sha256,
            "motion request map identity",
        ),
        (
            artifact.motion_request.runtime_fingerprint,
            runtime.runtime_fingerprint,
            "motion request runtime identity",
        ),
        (
            artifact.motion_request.collision_filter_sha256,
            runtime.collision_filter_sha256,
            "motion request collision filter identity",
        ),
        (
            artifact.motion_request.simulation_epoch,
            runtime.simulation_epoch,
            "motion request simulation epoch",
        ),
        (
            artifact.motion_request.runtime_boot_id,
            runtime.runtime_boot_id,
            "motion request boot identity",
        ),
        (
            artifact.path.motion_request_sha256,
            artifact.motion_request.content_sha256,
            "planned path motion request identity",
        ),
        (
            artifact.path.nav2_params_sha256,
            runtime.nav2_params_sha256,
            "planned path Nav2 parameter identity",
        ),
        (artifact.path.map_sha256, runtime.map_sha256, "planned path map identity"),
        (
            artifact.nav_footprint.nav2_params_sha256,
            runtime.nav2_params_sha256,
            "Nav2 footprint parameter identity",
        ),
        (
            artifact.usd_geometry.nav2_params_sha256,
            runtime.nav2_params_sha256,
            "USD geometry Nav2 parameter identity",
        ),
    )
    failures.extend(
        f"{label} differs from runtime"
        for observed, expected, label in bindings
        if observed != expected
    )
    failures.extend(_authorization_binding_failures(artifact))
    if artifact.path.poses[0] != artifact.motion_request.start:
        failures.append("planned path start differs from motion request")
    if artifact.path.poses[-1] != artifact.motion_request.goal:
        failures.append("planned path goal differs from motion request")
    failures.extend(
        f"{layer.layer.value} costmap map identity differs from runtime"
        for layer in artifact.costmap_layers
        if layer.map_sha256 != runtime.map_sha256
    )
    return failures


def _freshness_binding_failures(artifact: MotionReadinessArtifact) -> list[str]:
    runtime = artifact.runtime
    failures: list[str] = []
    ros_times: list[tuple[str, int | None]] = [
        ("planned path", artifact.path.source_timestamp_ns),
        ("Nav2 footprint", artifact.nav_footprint.source_timestamp_ns),
        ("USD geometry", artifact.usd_geometry.source_timestamp_ns),
    ]
    ros_times.extend(
        (f"{layer.layer.value} costmap", layer.source_stamp_ns) for layer in artifact.costmap_layers
    )
    for label, timestamp in ros_times:
        if (
            timestamp is None
            or abs(timestamp - runtime.capture_ros_ns) > runtime.max_evidence_age_ns
        ):
            failures.append(f"{label} source timestamp is missing or outside the capture window")
    host_times = [
        ("planned path", artifact.path.received_host_monotonic_ns),
        ("Nav2 footprint", artifact.nav_footprint.received_host_monotonic_ns),
    ]
    host_times.extend(
        (f"{layer.layer.value} costmap", layer.received_host_monotonic_ns)
        for layer in artifact.costmap_layers
    )
    for label, timestamp in host_times:
        if abs(timestamp - runtime.capture_host_monotonic_ns) > runtime.max_evidence_age_ns:
            failures.append(f"{label} host timestamp is outside the capture window")
    return failures


def _evidence_binding_failures(artifact: MotionReadinessArtifact) -> list[str]:
    return _identity_binding_failures(artifact) + _freshness_binding_failures(artifact)


def validate_motion_readiness_artifact(
    artifact: MotionReadinessArtifact,
) -> OfflineValidationReport:
    failures = _evidence_digest_failures(artifact) + _evidence_binding_failures(artifact)
    if artifact.input_sha256 != artifact.expected_input_sha256():
        failures.append("artifact input digest mismatch")
    if artifact.schema_version != 4 or artifact.evidence_derivation_version != 4:
        failures.append("unsupported motion-readiness evidence version")
    if artifact.path.frame_id != "map":
        failures.append("planned path must use map frame")
    if artifact.nav_footprint.frame_id != "base_link":
        failures.append("Nav2 footprint must use base_link frame")
    rederived = evaluate_motion_readiness(artifact)
    if artifact.result is None or artifact.result != rederived:
        failures.append("stored result differs from re-derived result")
    return OfflineValidationReport(
        valid=not failures,
        failures=tuple(failures),
        decision=rederived.decision,
    )


def create_motion_authorization_binding(
    artifact: MotionReadinessArtifact,
) -> MotionAuthorizationBinding:
    """Create the immutable admission token after all captured input is fixed."""

    return MotionAuthorizationBinding.create(
        authorization_nonce=artifact.motion_request.authorization_nonce,
        motion_request_sha256=artifact.motion_request.content_sha256,
        path_evidence_sha256=artifact.path.content_sha256,
        artifact_input_sha256=artifact.expected_input_sha256(),
    )


def _same_authorization_runtime(left: RuntimeBinding, right: RuntimeBinding) -> bool:
    fields = (
        "git_sha",
        "scene_path",
        "scene_sha256",
        "map_sha256",
        "nav2_params_sha256",
        "runtime_fingerprint",
        "simulation_epoch",
        "runtime_boot_id",
        "product_config_sha256",
        "planner_config_sha256",
        "site_id",
        "collision_filter_sha256",
    )
    return all(getattr(left, field) == getattr(right, field) for field in fields)


def _motion_authorization_matches(
    artifact: MotionReadinessArtifact,
    requested: MotionRequestBinding,
    *,
    current_runtime: RuntimeBinding,
    current_ros_ns: int,
    current_host_monotonic_ns: int,
    consumed_authorization_nonces: Set[str],
) -> bool:
    """Fail closed unless one unconsumed admission is exact, current, and fresh."""

    report = validate_motion_readiness_artifact(artifact)
    request = artifact.motion_request
    authorization = artifact.authorization
    return (
        report.valid
        and report.decision == "PASS"
        and authorization is not None
        and authorization.digest_is_valid()
        and requested.digest_is_valid()
        and requested == request
        and authorization.authorization_nonce == request.authorization_nonce
        and authorization.motion_request_sha256 == request.content_sha256
        and authorization.path_evidence_sha256 == artifact.path.content_sha256
        and authorization.artifact_input_sha256 == artifact.input_sha256
        and artifact.path.motion_request_sha256 == request.content_sha256
        and _same_authorization_runtime(artifact.runtime, current_runtime)
        and abs(current_ros_ns - artifact.runtime.capture_ros_ns)
        <= artifact.runtime.max_evidence_age_ns
        and abs(current_host_monotonic_ns - artifact.runtime.capture_host_monotonic_ns)
        <= artifact.runtime.max_evidence_age_ns
        and request.valid_from_ros_ns <= current_ros_ns <= request.valid_until_ros_ns
        and request.valid_from_host_monotonic_ns
        <= current_host_monotonic_ns
        <= request.valid_until_host_monotonic_ns
        and request.authorization_nonce not in consumed_authorization_nonces
    )


class MotionAuthorizationNonceStore:
    """Process-local atomic single-use admission store.

    A durable runtime must implement the same compare-and-consume contract in
    its authoritative transaction/event store before dispatch.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._consumed: set[str] = set()

    def consume_if_authorized(
        self,
        artifact: MotionReadinessArtifact,
        requested: MotionRequestBinding,
        *,
        current_runtime: RuntimeBinding,
        current_ros_ns: int,
        current_host_monotonic_ns: int,
    ) -> bool:
        with self._lock:
            if not _motion_authorization_matches(
                artifact,
                requested,
                current_runtime=current_runtime,
                current_ros_ns=current_ros_ns,
                current_host_monotonic_ns=current_host_monotonic_ns,
                consumed_authorization_nonces=self._consumed,
            ):
                return False
            self._consumed.add(requested.authorization_nonce)
            return True

    def consumed_nonces(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._consumed)


def load_and_validate_motion_readiness(path: Path) -> OfflineValidationReport:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not payload.get("input_sha256"):
            return OfflineValidationReport(
                valid=False,
                failures=("final artifact input digest is missing",),
                decision=None,
            )
        artifact = MotionReadinessArtifact.model_validate(payload)
        return validate_motion_readiness_artifact(artifact)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return OfflineValidationReport(valid=False, failures=(str(exc),), decision=None)


def assemble_motion_readiness_artifact(source: Path) -> MotionReadinessArtifact:
    """Load captured typed Evidence and derive the immutable readiness result."""

    payload = json.loads(source.read_text(encoding="utf-8"))
    captured = MotionReadinessArtifact.model_validate(payload)
    raw = captured.model_copy(update={"result": None})
    return raw.model_copy(update={"result": evaluate_motion_readiness(raw)})


def write_motion_readiness_artifact(
    artifact: MotionReadinessArtifact,
    output: Path,
) -> None:
    """Persist once; a readiness artifact must never overwrite prior Evidence."""

    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        content = artifact.model_dump_json(indent=2).encode()
        os.write(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
