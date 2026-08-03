from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import io
import json
import math
import os
import runpy
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import jenai.acceptance.motion_safety as motion_safety
import jenai.acceptance.motion_safety_isaac as motion_safety_isaac
import jenai.acceptance.motion_safety_probe as motion_safety_probe
from jenai.acceptance.motion_safety import (
    BoundMeasurements,
    ClearanceBudget,
    ClearanceBudgetTerm,
    ClearanceLayer,
    ClearanceSourceEvidence,
    CollisionEvent,
    CollisionFilterCoverageEvidence,
    CollisionFilterPrimRule,
    CollisionObservationWindow,
    CollisionStreamEvidence,
    CollisionWindowKind,
    CostmapCell,
    CostmapLayerEvidence,
    CostmapRun,
    EvidenceStatus,
    MotionAuthorizationNonceStore,
    MotionReadinessArtifact,
    MotionRequestBinding,
    NavFootprintComponent,
    NavFootprintEvidence,
    PathEvidence,
    Point2,
    Point3,
    Polygon2,
    Pose2,
    ProbeIdentityEvidence,
    RuntimeBinding,
    SafetyTermKind,
    SpeedLatencyMeasurements,
    StoppingMeasurements,
    UsdCollisionGeometryEvidence,
    UsdCollisionPrimEvidence,
    UsdSceneCollisionEntry,
    UsdSceneCollisionEnumerationEvidence,
    collision_prim_inventory_sha256,
    costmap_rle_sha256,
    create_motion_authorization_binding,
    evaluate_motion_readiness,
    interpolate_path,
    load_and_validate_motion_readiness,
    offset_convex_polygon,
    signed_polygon_clearance,
    transform_polygon,
    validate_motion_readiness_artifact,
    write_motion_readiness_artifact,
)
from jenai.acceptance.motion_safety_capture import (
    BlockedMotionReadinessArtifact,
    IsaacMotionReadinessCollector,
    capture_no_motion_readiness,
    validate_blocked_collection_artifact,
)
from jenai.acceptance.motion_safety_cli import main as readiness_cli
from jenai.acceptance.motion_safety_isaac import (
    IsaacObservationOperation,
    IsaacRosReadOnlyEvidenceSource,
    RepositoryIsaacReadOnlyTransport,
)
from jenai.acceptance.motion_safety_probe import (
    ExportedUsdObservationBackend,
    RepositoryIsaacProbe,
)


def _polygon(*points: tuple[float, float]) -> Polygon2:
    return Polygon2(vertices=tuple(Point2(x=x, y=y) for x, y in points))


def _binding() -> RuntimeBinding:
    return RuntimeBinding(
        git_sha="a" * 40,
        scene_path="/sim/JenAI.usd",
        scene_sha256="b" * 64,
        map_sha256="c" * 64,
        nav2_params_sha256="d" * 64,
        runtime_fingerprint="e" * 64,
        simulation_epoch="epoch-1",
        runtime_boot_id="boot-1",
        product_config_sha256="f" * 64,
        planner_config_sha256="1" * 64,
        site_id="warehouse",
        collision_filter_sha256=_collision_filter().content_sha256,
        capture_ros_ns=10_000,
        capture_host_monotonic_ns=20_000,
        max_evidence_age_ns=5_000,
    )


def _probe_identity() -> ProbeIdentityEvidence:
    return ProbeIdentityEvidence.create(
        source_git_sha="a" * 40,
        entrypoint_path="scripts/isaac_motion_readiness_probe.py",
        entrypoint_sha256="2" * 64,
        source_bundle_sha256="5" * 64,
        config_path="/sim/motion-readiness.json",
        config_sha256="3" * 64,
        python_executable="/usr/bin/python3",
        python_executable_sha256="6" * 64,
        environment_sha256="4" * 64,
    )


def _motion_request(
    *, start: Pose2 | None = None, goal: Pose2 | None = None
) -> MotionRequestBinding:
    runtime = _binding()
    return MotionRequestBinding.create(
        authorization_nonce="authorization-nonce-0001",
        site_id="warehouse",
        start=start or Pose2(x=0.0, y=0.0, yaw=0.0),
        goal=goal or Pose2(x=1.0, y=0.0, yaw=0.0),
        planner_id="nav2-global-planner",
        planner_config_sha256=runtime.planner_config_sha256,
        nav2_params_sha256=runtime.nav2_params_sha256,
        product_config_sha256=runtime.product_config_sha256,
        scene_sha256=runtime.scene_sha256,
        map_sha256=runtime.map_sha256,
        runtime_fingerprint=runtime.runtime_fingerprint,
        collision_filter_sha256=runtime.collision_filter_sha256,
        simulation_epoch=runtime.simulation_epoch,
        runtime_boot_id=runtime.runtime_boot_id,
        valid_from_ros_ns=9_000,
        valid_until_ros_ns=11_000,
        valid_from_host_monotonic_ns=19_000,
        valid_until_host_monotonic_ns=21_000,
    )


def _footprint() -> Polygon2:
    return _polygon((-0.6, -0.25), (0.14, -0.25), (0.14, 0.25), (-0.6, 0.25))


def _configured_footprint() -> Polygon2:
    return _polygon((-0.57, -0.22), (0.11, -0.22), (0.11, 0.22), (-0.57, 0.22))


def _layer(
    kind: ClearanceLayer,
    cells: tuple[CostmapCell, ...] = (),
    *,
    status: EvidenceStatus = EvidenceStatus.OBSERVED,
) -> CostmapLayerEvidence:
    width = 400
    height = 400
    origin = Point2(x=-10.0, y=-10.0)
    resolution = 0.05
    indexed = sorted(
        (
            int(round((cell.y - origin.y) / resolution - 0.5)) * width
            + int(round((cell.x - origin.x) / resolution - 0.5)),
            cell.cost,
        )
        for cell in cells
    )
    runs: list[CostmapRun] = []
    cursor = 0
    for index, cost in indexed:
        if index > cursor:
            runs.append(CostmapRun(cost=0, count=index - cursor))
        runs.append(CostmapRun(cost=cost, count=1))
        cursor = index + 1
    if cursor < width * height:
        runs.append(CostmapRun(cost=0, count=width * height - cursor))
    return CostmapLayerEvidence.create(
        evidence_id=f"layer-{kind.value}",
        layer=kind,
        frame_id="map",
        source_topic=f"/synthetic/{kind.value}",
        source_message_type="nav2_msgs/msg/Costmap",
        semantic_attestation="synthetic_fixture",
        resolution_m=resolution,
        origin=origin,
        width=width,
        height=height,
        source_stamp_ns=10_000,
        received_host_monotonic_ns=20_000,
        map_sha256="c" * 64,
        runtime_fingerprint="e" * 64,
        simulation_epoch="epoch-1",
        runtime_boot_id="boot-1",
        status=status,
        cells=cells,
        raw_costs_rle=tuple(runs),
        raw_grid_sha256=costmap_rle_sha256(tuple(runs)),
    )


def _clearance_specs() -> tuple[
    tuple[SafetyTermKind, str, float, str, dict[str, tuple[float, ...]]], ...
]:
    return (
        (
            SafetyTermKind.GEOMETRY_ATTESTATION_UNCERTAINTY,
            "maximum_bound",
            0.01,
            "b" * 64,
            {"bound_m": (0.01,)},
        ),
        (
            SafetyTermKind.LOCALIZATION_UNCERTAINTY,
            "maximum_bound",
            0.02,
            "c" * 64,
            {"bound_m": (0.02,)},
        ),
        (
            SafetyTermKind.CONTROLLER_TRACKING_BOUND,
            "maximum_bound",
            0.03,
            "d" * 64,
            {"bound_m": (0.03,)},
        ),
        (
            SafetyTermKind.MAP_DISCRETIZATION_BOUND,
            "map_resolution_diagonal_bound",
            0.04,
            "c" * 64,
            {"bound_m": (0.04,)},
        ),
        (
            SafetyTermKind.LATENCY_DISTANCE,
            "speed_times_latency",
            0.01,
            "e" * 64,
            {"maximum_speed_magnitude_mps": (0.10,), "latency_s": (0.10,)},
        ),
        (
            SafetyTermKind.STOPPING_DISTANCE,
            "kinematic_stopping_distance",
            0.05,
            "d" * 64,
            {"maximum_speed_magnitude_mps": (math.sqrt(0.1),), "minimum_deceleration_mps2": (1.0,)},
        ),
        (
            SafetyTermKind.FIXED_PRODUCT_MARGIN,
            "product_policy_margin",
            0.04,
            "f" * 64,
            {"bound_m": (0.04,)},
        ),
    )


def _clearance_sources() -> tuple[ClearanceSourceEvidence, ...]:
    return tuple(
        ClearanceSourceEvidence.create(
            evidence_id=f"source-{kind.value}",
            kind=kind,
            status=EvidenceStatus.OBSERVED,
            source_assurance="runtime_observed",
            transport_security="local_process",
            source_timestamp_ns=10_000,
            config_sha256=config,
            simulation_epoch="epoch-1",
            runtime_boot_id="boot-1",
            runtime_fingerprint="e" * 64,
            measurements=measurements,
        )
        for kind, _method, _value, config, measurements in _clearance_specs()
    )


def _budget() -> ClearanceBudget:
    terms = []
    for kind, method, value, config, _measurements in _clearance_specs():
        terms.append(
            ClearanceBudgetTerm(
                kind=kind,
                value_m=value,
                source_evidence_id=f"source-{kind.value}",
                source_timestamp_ns=10_000,
                config_sha256=config,
                method=method,
            )
        )
    return ClearanceBudget.create(terms=tuple(terms))


def _usd(
    hull: Polygon2 | None = None,
    *,
    counterpart_prims: tuple[str, ...] = ("/World/Warehouse",),
    counterpart_categories: tuple[str, ...] = ("environment",),
    raw_entries: tuple[tuple[str, str], ...] | None = None,
) -> UsdCollisionGeometryEvidence:
    hull = hull or _polygon((-0.55, -0.20), (0.10, -0.20), (0.10, 0.20), (-0.55, 0.20))
    raw_entries = raw_entries or tuple(zip(counterpart_prims, counterpart_categories, strict=True))
    scene_enumeration = UsdSceneCollisionEnumerationEvidence.create(
        source="isaac_usd_stage_query",
        query_name="collision_enabled_scene_prims_v1",
        scene_sha256="b" * 64,
        source_timestamp_ns=10_000,
        reported_count=len(raw_entries),
        complete_attestation=True,
        entries=tuple(
            UsdSceneCollisionEntry(
                prim_path=prim_path,
                category=category,
                collision_enabled=True,
            )
            for prim_path, category in raw_entries
        ),
    )
    return UsdCollisionGeometryEvidence.create(
        evidence_id="usd-geometry",
        scene_path="/sim/JenAI.usd",
        scene_sha256="b" * 64,
        robot_root_prim="/World/Robot",
        base_frame="base_link",
        simulation_epoch="epoch-1",
        runtime_boot_id="boot-1",
        runtime_fingerprint="e" * 64,
        nav2_params_sha256="d" * 64,
        axis_convention="x_forward_y_left_yaw_ccw",
        meters_per_unit=1.0,
        source_timestamp_ns=10_000,
        collision_prim_inventory_complete=True,
        scene_collision_enumeration=scene_enumeration,
        collision_enabled_counterpart_prim_paths=counterpart_prims,
        collision_enabled_counterpart_categories=counterpart_categories,
        collision_prims=(
            UsdCollisionPrimEvidence(
                prim_path="/World/Robot/chassis/collision",
                shape_type="box",
                mesh_identity="chassis-box",
                local_geometry_vertices=tuple(
                    Point3(x=point.x, y=point.y, z=0.0) for point in hull.vertices
                ),
                base_from_prim_transform=tuple(float(index % 5 == 0) for index in range(16)),
                scale=(1.0, 1.0, 1.0),
                transform_convention="row_major_affine_column_vector",
                transform_translation_unit="m",
                transform_includes_scale=False,
                projected_base_hull=hull,
            ),
        ),
        projected_base_hull=hull,
        source_assurance="simulator_stage_query",
        transport_security="local_process",
    )


def _collision_filter() -> CollisionFilterCoverageEvidence:
    counterpart_prims = ("/World/Warehouse",)
    counterpart_categories = ("environment",)
    return CollisionFilterCoverageEvidence.create(
        collision_enabled_counterpart_prim_paths=counterpart_prims,
        collision_enabled_counterpart_categories=counterpart_categories,
        prim_rules=(
            CollisionFilterPrimRule(
                monitored_prim_path="/World/Robot/chassis/collision",
                counterpart_prim_paths=counterpart_prims,
                counterpart_categories=counterpart_categories,
                contact_reporting_enabled=True,
            ),
        ),
    )


def _collision(
    *, status: EvidenceStatus = EvidenceStatus.OBSERVED, stale: bool = False
) -> CollisionStreamEvidence:
    return CollisionStreamEvidence.create(
        evidence_id="collision-stream",
        topic="/twin/collision",
        message_type="jenai_msgs/msg/CollisionEvent",
        qos="reliable",
        status=status,
        source_timestamp_capable=True,
        clock_aligned=not stale,
        stream_presence_attested=status == EvidenceStatus.OBSERVED,
        phase="no_motion",
        simulation_epoch="epoch-1",
        runtime_boot_id="boot-1",
        source_assurance="simulator_attested",
        transport_security="local_process",
        scene_sha256="b" * 64,
        map_sha256="c" * 64,
        nav2_params_sha256="d" * 64,
        runtime_fingerprint="e" * 64,
        robot_root_prim="/World/Robot",
        monitored_prim_paths=("/World/Robot/chassis/collision",),
        monitored_prim_inventory_sha256=collision_prim_inventory_sha256(
            ("/World/Robot/chassis/collision",)
        ),
        collision_filter=_collision_filter(),
        windows=(
            CollisionObservationWindow(
                kind=CollisionWindowKind.PRE_DISPATCH,
                observed_from_ros_ns=9_000,
                observed_until_ros_ns=11_000,
                observed_from_host_monotonic_ns=19_000,
                observed_until_host_monotonic_ns=21_000,
                raw_messages=(
                    CollisionEvent(
                        ros_stamp_ns=10_000,
                        host_monotonic_ns=20_000,
                        simulation_epoch="epoch-1",
                        runtime_boot_id="boot-1",
                        raw_message={"collision_detected": False},
                    ),
                ),
            ),
        ),
    )


def _artifact(
    *,
    path: tuple[Pose2, ...] | None = None,
    layers: tuple[CostmapLayerEvidence, ...] | None = None,
    usd: UsdCollisionGeometryEvidence | None = None,
    collision: CollisionStreamEvidence | None = None,
    sources: tuple[ClearanceSourceEvidence, ...] | None = None,
    budget: ClearanceBudget | None = None,
    motion_request: MotionRequestBinding | None = None,
    nav_footprint: NavFootprintEvidence | None = None,
) -> MotionReadinessArtifact:
    path = path or (Pose2(x=0.0, y=0.0, yaw=0.0), Pose2(x=1.0, y=0.0, yaw=0.0))
    request = motion_request or _motion_request(start=path[0], goal=path[-1])
    layers = layers or tuple(_layer(kind) for kind in ClearanceLayer)
    raw = MotionReadinessArtifact(
        schema_version=5,
        evidence_derivation_version=5,
        runtime=_binding(),
        runtime_after=_binding(),
        probe_identity=_probe_identity(),
        motion_request=request,
        authorization=None,
        path=PathEvidence.create(
            evidence_id="planned-path",
            frame_id="map",
            source_timestamp_ns=10_000,
            received_host_monotonic_ns=20_000,
            map_sha256="c" * 64,
            runtime_fingerprint="e" * 64,
            simulation_epoch="epoch-1",
            runtime_boot_id="boot-1",
            motion_request_sha256=request.content_sha256,
            nav2_params_sha256="d" * 64,
            poses=path,
        ),
        nav_footprint=nav_footprint
        or NavFootprintEvidence.create(
            evidence_id="nav-footprint",
            frame_id="base_link",
            source="live:/local_costmap/local_costmap:get_parameters",
            source_timestamp_ns=10_000,
            received_host_monotonic_ns=20_000,
            nav2_params_sha256="d" * 64,
            runtime_fingerprint="e" * 64,
            simulation_epoch="epoch-1",
            runtime_boot_id="boot-1",
            axis_convention="x_forward_y_left_yaw_ccw",
            footprint_padding_m=0.03,
            padding_applied=True,
            configured_polygon=_configured_footprint(),
            polygon=_footprint(),
            components=(
                NavFootprintComponent(
                    source="synthetic:local-costmap",
                    frame_id="base_link",
                    configured_polygon=_configured_footprint(),
                    footprint_padding_m=0.03,
                    effective_polygon=_footprint(),
                ),
                NavFootprintComponent(
                    source="synthetic:global-costmap",
                    frame_id="base_link",
                    configured_polygon=_configured_footprint(),
                    footprint_padding_m=0.03,
                    effective_polygon=_footprint(),
                ),
            ),
        ),
        usd_geometry=usd or _usd(),
        costmap_layers=layers,
        collision_stream=collision or _collision(),
        clearance_sources=sources or _clearance_sources(),
        clearance_budget=budget or _budget(),
        result=None,
    )
    bound = raw.model_copy(update={"authorization": create_motion_authorization_binding(raw)})
    return bound.model_copy(update={"result": evaluate_motion_readiness(bound)})


def test_transform_polygon_translates_and_rotates_ninety_degrees() -> None:
    square = _polygon((0.0, 0.0), (1.0, 0.0), (1.0, 0.5), (0.0, 0.5))

    transformed = transform_polygon(square, Pose2(x=2.0, y=3.0, yaw=math.pi / 2))

    assert [(point.x, point.y) for point in transformed.vertices] == pytest.approx(
        [(2.0, 3.0), (2.0, 4.0), (1.5, 4.0), (1.5, 3.0)]
    )


def test_effective_footprint_is_rebuilt_from_configured_polygon_and_padding() -> None:
    actual = offset_convex_polygon(_configured_footprint(), 0.03)
    actual_coordinates = [value for point in actual.vertices for value in (point.x, point.y)]
    expected_coordinates = [
        value for point in _footprint().vertices for value in (point.x, point.y)
    ]
    assert actual_coordinates == pytest.approx(expected_coordinates)


def test_interpolate_path_bounds_translation_and_corner_sweep() -> None:
    samples = interpolate_path(
        (Pose2(x=0.0, y=0.0, yaw=0.0), Pose2(x=0.2, y=0.0, yaw=math.pi / 2)),
        footprint=_footprint(),
        max_step_m=0.025,
    )

    assert len(samples) > 8
    assert (
        max(
            math.hypot(right.x - left.x, right.y - left.y)
            for left, right in zip(samples, samples[1:], strict=False)
        )
        <= 0.0251
    )
    assert samples[-1].yaw == pytest.approx(math.pi / 2)


def test_interpolation_bounds_every_vertex_during_simultaneous_translation_and_rotation() -> None:
    samples = interpolate_path(
        (Pose2(x=0.0, y=0.0, yaw=0.0), Pose2(x=0.2, y=0.1, yaw=math.pi / 2)),
        footprint=_footprint(),
        max_step_m=0.025,
    )
    transformed = [transform_polygon(_footprint(), pose) for pose in samples]

    assert (
        max(
            math.hypot(right.x - left.x, right.y - left.y)
            for previous, current in zip(transformed, transformed[1:], strict=False)
            for left, right in zip(previous.vertices, current.vertices, strict=True)
        )
        <= 0.0251
    )


def test_signed_clearance_is_negative_for_polygon_overlap() -> None:
    robot = _polygon((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5))
    wall = _polygon((0.4, -1.0), (0.6, -1.0), (0.6, 1.0), (0.4, 1.0))

    assert signed_polygon_clearance(robot, wall) < 0.0


def test_wide_route_passes_all_four_gates() -> None:
    artifact = _artifact()

    assert artifact.result is not None
    assert artifact.result.decision == "PASS"
    assert artifact.result.measured_minimum_clearance_m > artifact.result.required_clearance_m


def test_centerline_safe_but_rotated_corner_hits_wall_and_blocks() -> None:
    # The robot centre stays clear; a front corner sweeps across this cell.
    obstacle = CostmapCell(x=0.225, y=0.125, cost=254)
    artifact = _artifact(
        path=(
            Pose2(x=0.0, y=0.0, yaw=0.0),
            Pose2(x=0.0, y=0.0, yaw=math.pi / 2),
        ),
        layers=tuple(
            _layer(kind, (obstacle,) if kind == ClearanceLayer.STATIC_LETHAL else ())
            for kind in ClearanceLayer
        ),
    )

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    assert artifact.result.swept_clearance.worst is not None
    assert artifact.result.swept_clearance.worst.clearance_m < 0.0


def test_simultaneous_translate_rotate_corner_crossing_blocks() -> None:
    obstacle = CostmapCell(x=-0.475, y=-0.325, cost=254)
    artifact = _artifact(
        path=(
            Pose2(x=0.0, y=0.0, yaw=0.0),
            Pose2(x=0.2, y=0.1, yaw=math.pi / 2),
        ),
        layers=tuple(
            _layer(kind, (obstacle,) if kind == ClearanceLayer.STATIC_LETHAL else ())
            for kind in ClearanceLayer
        ),
    )

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    assert artifact.result.swept_clearance.worst is not None
    assert artifact.result.swept_clearance.worst.clearance_m < 0.0


def test_duplicate_costmap_layer_kind_blocks() -> None:
    layers = tuple(_layer(kind) for kind in ClearanceLayer)
    artifact = _artifact(layers=(*layers, layers[0]))

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    assert "all four costmap layers must be present exactly once" in (
        artifact.result.swept_clearance.failures
    )


def test_path_outside_observed_grid_blocks_even_when_all_layers_are_empty() -> None:
    artifact = _artifact(path=(Pose2(x=50.0, y=50.0, yaw=0.0), Pose2(x=51.0, y=50.0, yaw=0.0)))

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    assert "grid coverage" in " ".join(artifact.result.swept_clearance.failures)


def test_route_near_grid_boundary_blocks_when_clearance_budget_extends_into_unknown() -> None:
    artifact = _artifact(path=(Pose2(x=9.84, y=0.0, yaw=0.0), Pose2(x=9.85, y=0.0, yaw=0.0)))

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    assert artifact.result.swept_clearance.worst is not None
    assert artifact.result.swept_clearance.worst.nearest_boundary == "maximum_x"
    assert artifact.result.margin_m is not None and artifact.result.margin_m < 0.0


def test_clearance_uses_conservative_segment_lower_bound() -> None:
    obstacle = CostmapCell(x=0.025, y=0.275, cost=254)
    artifact = _artifact(
        layers=tuple(
            _layer(kind, (obstacle,) if kind == ClearanceLayer.STATIC_LETHAL else ())
            for kind in ClearanceLayer
        )
    )

    assert artifact.result is not None
    clearance = artifact.result.swept_clearance
    assert clearance.sampled_minimum_clearance_m == pytest.approx(0.0, abs=0.026)
    assert clearance.interpolation_error_bound_m > 0.0
    assert clearance.conservative_minimum_clearance_m == pytest.approx(
        clearance.minimum_clearance_m
    )
    assert clearance.conservative_minimum_clearance_m <= (
        clearance.sampled_minimum_clearance_m - clearance.interpolation_error_bound_m
    )
    assert clearance.worst is not None
    assert clearance.worst.segment_index is not None
    assert clearance.worst.segment_start_pose is not None
    assert clearance.worst.segment_end_pose is not None
    assert clearance.worst.sampled_clearance_m is not None
    assert clearance.worst.interpolation_error_bound_m > 0.0


def _needle_footprint() -> NavFootprintEvidence:
    polygon = _polygon((1.001, 0.0), (0.9, -0.01), (0.9, 0.01))
    return NavFootprintEvidence.create(
        evidence_id="nav-footprint-needle",
        frame_id="base_link",
        source="live:/local_costmap/local_costmap:get_parameters",
        source_timestamp_ns=10_000,
        received_host_monotonic_ns=20_000,
        nav2_params_sha256="d" * 64,
        runtime_fingerprint="e" * 64,
        simulation_epoch="epoch-1",
        runtime_boot_id="boot-1",
        axis_convention="x_forward_y_left_yaw_ccw",
        footprint_padding_m=0.0,
        padding_applied=True,
        configured_polygon=polygon,
        polygon=polygon,
        components=(
            NavFootprintComponent(
                source="synthetic:local-costmap",
                frame_id="base_link",
                configured_polygon=polygon,
                footprint_padding_m=0.0,
                effective_polygon=polygon,
            ),
            NavFootprintComponent(
                source="synthetic:global-costmap",
                frame_id="base_link",
                configured_polygon=polygon,
                footprint_padding_m=0.0,
                effective_polygon=polygon,
            ),
        ),
    )


def _middle_sweep_artifact(*, translate_y: float = 0.0) -> MotionReadinessArtifact:
    footprint = _needle_footprint()
    start = Pose2(x=-0.00097, y=-translate_y, yaw=-0.05)
    end = Pose2(x=-0.00097, y=translate_y, yaw=0.05)
    obstacle = CostmapCell(x=1.025, y=0.025, cost=254)
    return _artifact(
        path=(start, end),
        nav_footprint=footprint,
        usd=_usd(footprint.polygon),
        layers=tuple(
            _layer(kind, (obstacle,) if kind == ClearanceLayer.STATIC_LETHAL else ())
            for kind in ClearanceLayer
        ),
    )


def test_endpoint_samples_safe_but_middle_rotation_sweep_blocks() -> None:
    artifact = _middle_sweep_artifact()
    obstacle = _polygon((1.0, 0.0), (1.05, 0.0), (1.05, 0.05), (1.0, 0.05))
    start, end = artifact.path.poses
    midpoint = Pose2(
        x=(start.x + end.x) / 2.0,
        y=(start.y + end.y) / 2.0,
        yaw=(start.yaw + end.yaw) / 2.0,
    )
    assert (
        signed_polygon_clearance(transform_polygon(artifact.nav_footprint.polygon, start), obstacle)
        > 0.0
    )
    assert (
        signed_polygon_clearance(transform_polygon(artifact.nav_footprint.polygon, end), obstacle)
        > 0.0
    )
    assert (
        signed_polygon_clearance(
            transform_polygon(artifact.nav_footprint.polygon, midpoint), obstacle
        )
        < 0.0
    )

    assert artifact.result is not None
    clearance = artifact.result.swept_clearance
    assert clearance.sampled_minimum_clearance_m is not None
    assert clearance.sampled_minimum_clearance_m > 0.0
    assert clearance.conservative_minimum_clearance_m is not None
    assert clearance.conservative_minimum_clearance_m < 0.0
    assert clearance.status == "BLOCK"
    assert clearance.worst is not None
    assert clearance.worst.nearest_cell == CostmapCell(x=1.025, y=0.025, cost=254)


def test_sampled_clearance_smaller_than_interpolation_bound_blocks() -> None:
    artifact = _middle_sweep_artifact()

    assert artifact.result is not None
    clearance = artifact.result.swept_clearance
    assert clearance.sampled_minimum_clearance_m is not None
    assert clearance.sampled_minimum_clearance_m < clearance.interpolation_error_bound_m
    assert clearance.conservative_minimum_clearance_m is not None
    assert clearance.conservative_minimum_clearance_m <= (
        clearance.sampled_minimum_clearance_m - clearance.interpolation_error_bound_m
    )
    assert clearance.status == "BLOCK"


def test_translation_and_rotation_segment_uses_conservative_sweep_bound() -> None:
    artifact = _middle_sweep_artifact(translate_y=0.004)

    assert artifact.result is not None
    clearance = artifact.result.swept_clearance
    assert clearance.sampled_minimum_clearance_m is not None
    assert clearance.sampled_minimum_clearance_m > 0.0
    assert clearance.conservative_minimum_clearance_m is not None
    assert clearance.conservative_minimum_clearance_m < 0.0
    assert clearance.worst is not None
    assert clearance.worst.segment_start_pose != clearance.worst.segment_end_pose


def test_finer_sampling_never_removes_the_explicit_error_bound() -> None:
    artifact = _middle_sweep_artifact()

    assert artifact.result is not None
    clearance = artifact.result.swept_clearance
    assert clearance.evaluated_pose_count > 2
    assert clearance.interpolation_error_bound_m > 0.0
    assert clearance.conservative_minimum_clearance_m is not None
    assert clearance.sampled_minimum_clearance_m is not None
    assert clearance.conservative_minimum_clearance_m < clearance.sampled_minimum_clearance_m


def test_inflation_is_reported_but_not_used_as_physical_clearance() -> None:
    inflation = CostmapCell(x=0.025, y=0.025, cost=81)
    layers = tuple(
        _layer(kind, (inflation,) if kind == ClearanceLayer.STATIC_INFLATION else ())
        for kind in ClearanceLayer
    )
    artifact = _artifact(layers=layers)

    assert artifact.result is not None
    assert artifact.result.decision == "PASS"
    assert (
        artifact.result.swept_clearance.per_layer_minimum_m[ClearanceLayer.STATIC_INFLATION] < 0.0
    )
    assert artifact.result.measured_minimum_clearance_m > 0.0


@pytest.mark.parametrize(
    ("layers", "collision", "usd", "expected_gate"),
    [
        (
            tuple(
                _layer(
                    kind,
                    (CostmapCell(x=0.025, y=0.025, cost=254),)
                    if kind == ClearanceLayer.LIVE_OBSTACLE
                    else (),
                )
                for kind in ClearanceLayer
            ),
            None,
            None,
            "swept_footprint_clearance",
        ),
        (
            tuple(
                _layer(kind, status=EvidenceStatus.MISSING)
                if kind == ClearanceLayer.UNKNOWN
                else _layer(kind)
                for kind in ClearanceLayer
            ),
            None,
            None,
            "swept_footprint_clearance",
        ),
        (None, _collision(status=EvidenceStatus.MISSING), None, "collision_timeline"),
        (None, _collision(stale=True), None, "collision_timeline"),
        (
            None,
            None,
            _usd(_polygon((-0.7, -0.3), (0.2, -0.3), (0.2, 0.3), (-0.7, 0.3))),
            "geometry_attestation",
        ),
    ],
)
def test_missing_or_unsafe_evidence_blocks(
    layers: tuple[CostmapLayerEvidence, ...] | None,
    collision: CollisionStreamEvidence | None,
    usd: UsdCollisionGeometryEvidence | None,
    expected_gate: str,
) -> None:
    artifact = _artifact(layers=layers, collision=collision, usd=usd)

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    assert expected_gate in artifact.result.blocking_gates


def test_missing_or_zero_clearance_budget_term_blocks() -> None:
    budget = _budget().model_copy(update={"terms": _budget().terms[:-1]})
    artifact = _artifact(budget=budget)

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    assert "minimum_safe_clearance_policy" in artifact.result.blocking_gates


def test_collision_window_without_raw_presence_observation_blocks() -> None:
    stream = _collision()
    window = stream.windows[0].model_copy(update={"raw_messages": ()})
    values = stream.model_dump(mode="python", exclude={"content_sha256", "windows"})
    artifact = _artifact(collision=CollisionStreamEvidence.create(**values, windows=(window,)))

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    assert "collision_timeline" in artifact.result.blocking_gates


def test_stale_collision_presence_sample_inside_stretched_window_blocks() -> None:
    stream = _collision()
    event = (
        stream.windows[0]
        .raw_messages[0]
        .model_copy(update={"ros_stamp_ns": 4_000, "host_monotonic_ns": 14_000})
    )
    window = stream.windows[0].model_copy(
        update={
            "observed_from_ros_ns": 4_000,
            "observed_until_ros_ns": 10_000,
            "observed_from_host_monotonic_ns": 14_000,
            "observed_until_host_monotonic_ns": 20_000,
            "raw_messages": (event,),
        }
    )
    values = stream.model_dump(mode="python", exclude={"content_sha256", "windows"})
    artifact = _artifact(collision=CollisionStreamEvidence.create(**values, windows=(window,)))

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    assert "collision_timeline" in artifact.result.blocking_gates


def test_positive_collision_observation_blocks() -> None:
    stream = _collision()
    event = (
        stream.windows[0]
        .raw_messages[0]
        .model_copy(update={"raw_message": {"collision_detected": True}})
    )
    window = stream.windows[0].model_copy(update={"raw_messages": (event,)})
    values = stream.model_dump(mode="python", exclude={"content_sha256", "windows"})
    artifact = _artifact(collision=CollisionStreamEvidence.create(**values, windows=(window,)))

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    assert artifact.result.collision_timeline.collision_observed is True


def test_usd_transform_is_rebuilt_instead_of_trusting_stored_hull() -> None:
    usd = _usd()
    prim = usd.collision_prims[0]
    transform = list(prim.base_from_prim_transform)
    transform[3] = 0.25
    modified_prim = prim.model_copy(update={"base_from_prim_transform": tuple(transform)})
    values = usd.model_dump(mode="python", exclude={"content_sha256", "collision_prims"})
    modified = UsdCollisionGeometryEvidence.create(**values, collision_prims=(modified_prim,))

    artifact = _artifact(usd=modified)

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    assert "geometry_attestation" in artifact.result.blocking_gates


def test_clearance_summary_cannot_hide_modified_source_measurements() -> None:
    sources = list(_clearance_sources())
    original = sources[0]
    values = original.model_dump(mode="python", exclude={"content_sha256"})
    values["measurements"] = {"bound_m": (0.5,)}
    sources[0] = ClearanceSourceEvidence.create(**values)

    artifact = _artifact(sources=tuple(sources))

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    assert "minimum_safe_clearance_policy" in artifact.result.blocking_gates


def test_observed_vendor_boolean_is_not_a_collision_timeline() -> None:
    weak = _collision().model_dump(mode="python", exclude={"content_sha256"})
    weak.update(
        {
            "message_type": "std_msgs/msg/Bool",
            "source_timestamp_capable": False,
            "clock_aligned": False,
        }
    )
    artifact = _artifact(collision=CollisionStreamEvidence.create(**weak))

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    assert "collision_timeline" in artifact.result.blocking_gates


def test_empty_clearance_derivation_method_blocks() -> None:
    budget = _budget()
    terms = (budget.terms[0].model_copy(update={"method": ""}), *budget.terms[1:])
    artifact = _artifact(budget=ClearanceBudget.create(terms=terms))

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    assert "minimum_safe_clearance_policy" in artifact.result.blocking_gates


def test_offline_validator_returns_invalid_for_non_finite_number(tmp_path: Path) -> None:
    payload = _artifact().model_dump(mode="json")
    payload["path"]["poses"][0]["x"] = float("nan")
    path = tmp_path / "nan.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = load_and_validate_motion_readiness(path)

    assert report.valid is False
    assert report.decision is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["path"]["poses"][0].__setitem__("yaw", 0.1),
        lambda payload: payload["nav_footprint"]["polygon"]["vertices"][0].__setitem__("x", -0.1),
        lambda payload: payload["usd_geometry"].__setitem__("scene_sha256", "9" * 64),
        lambda payload: payload["costmap_layers"][0]["cells"].append(
            {"x": 0.0, "y": 0.0, "cost": 254}
        ),
        lambda payload: payload["collision_stream"].__setitem__("status", "missing"),
        lambda payload: payload["clearance_budget"]["terms"][0].__setitem__("value_m", 0.0),
        lambda payload: payload["runtime"].__setitem__("simulation_epoch", "other-epoch"),
        lambda payload: payload["path"].__setitem__("source_timestamp_ns", 99_000),
    ],
)
def test_offline_validator_rejects_tampered_raw_evidence(
    mutation: object,
    tmp_path: Path,
) -> None:
    path = tmp_path / "artifact.json"
    payload = _artifact().model_dump(mode="json")
    cast_mutation = mutation
    assert callable(cast_mutation)
    cast_mutation(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = load_and_validate_motion_readiness(path)

    assert report.valid is False
    assert report.failures


@pytest.mark.parametrize(
    "mutation",
    [
        lambda result: result["swept_clearance"].__setitem__("sampled_minimum_clearance_m", 999.0),
        lambda result: result["swept_clearance"].__setitem__("interpolation_error_bound_m", 0.0),
        lambda result: result["swept_clearance"].__setitem__(
            "conservative_minimum_clearance_m", 999.0
        ),
        lambda result: result["swept_clearance"]["worst"].__setitem__("segment_index", 999),
        lambda result: result["swept_clearance"]["worst"].__setitem__(
            "segment_start_pose", {"x": 9.0, "y": 9.0, "yaw": 0.0}
        ),
    ],
)
def test_offline_validator_rejects_tampered_continuous_sweep_derivation(
    mutation: object,
    tmp_path: Path,
) -> None:
    payload = _middle_sweep_artifact().model_dump(mode="json")
    cast_mutation = mutation
    assert callable(cast_mutation)
    cast_mutation(payload["result"])
    path = tmp_path / "tampered-sweep.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = load_and_validate_motion_readiness(path)

    assert report.valid is False
    assert "stored result differs from re-derived result" in report.failures


def test_offline_validator_rederives_result_instead_of_trusting_summary(tmp_path: Path) -> None:
    artifact = _artifact()
    payload = artifact.model_dump(mode="json")
    payload["result"]["decision"] = "BLOCK"
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = load_and_validate_motion_readiness(path)

    assert report.valid is False
    assert "stored result differs from re-derived result" in report.failures


def test_offline_validator_rejects_probe_identity_tampering() -> None:
    artifact = _artifact()
    tampered_probe = artifact.probe_identity.model_copy(update={"entrypoint_sha256": "9" * 64})
    tampered = artifact.model_copy(update={"probe_identity": tampered_probe})

    report = validate_motion_readiness_artifact(tampered)

    assert report.valid is False
    assert "observation probe identity content digest mismatch" in report.failures


def test_offline_validator_rejects_missing_final_input_digest(tmp_path: Path) -> None:
    payload = _artifact().model_dump(mode="json")
    payload["input_sha256"] = ""
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = load_and_validate_motion_readiness(path)

    assert report.valid is False
    assert report.failures == ("final artifact input digest is missing",)


def test_public_capture_cli_writes_assemble_ready_raw_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _artifact()
    transport = _FakeIsaacTransport(expected)
    monkeypatch.setattr(
        "jenai.acceptance.motion_safety_cli.RepositoryIsaacReadOnlyTransport",
        lambda **_kwargs: transport,
    )
    output = tmp_path / "capture.json"

    assert (
        readiness_cli(
            [
                "capture",
                "--config",
                str(tmp_path / "motion-readiness.json"),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    raw = MotionReadinessArtifact.model_validate_json(output.read_text(encoding="utf-8"))
    assert raw.result is None
    assembled = _assembled_capture(raw)
    assert validate_motion_readiness_artifact(assembled).valid is True


def test_public_capture_cli_persists_typed_block_on_probe_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _artifact()

    class FailingTransport(_FakeIsaacTransport):
        async def observe(
            self,
            operation: IsaacObservationOperation,
            context: dict[str, object],
        ) -> object:
            if operation == IsaacObservationOperation.COLLISION_TIMELINE:
                raise RuntimeError("untrusted private detail")
            return await super().observe(operation, context)

    monkeypatch.setattr(
        "jenai.acceptance.motion_safety_cli.RepositoryIsaacReadOnlyTransport",
        lambda **_kwargs: FailingTransport(expected),
    )
    output = tmp_path / "blocked-capture.json"

    assert (
        readiness_cli(
            [
                "capture",
                "--config",
                str(tmp_path / "motion-readiness.json"),
                "--output",
                str(output),
            ]
        )
        == 3
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["artifact_kind"] == "motion_readiness_collection_block"
    assert payload["decision"] == "BLOCK"
    assert payload["failures"] == [
        {
            "operation": "collision_timeline",
            "reason": "source_error",
            "exception_type": "RuntimeError",
        }
    ]
    assert "private detail" not in output.read_text(encoding="utf-8")
    assert readiness_cli(["validate", "--artifact", str(output)]) == 3


def test_public_cli_returns_distinct_exit_for_valid_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact = _artifact(collision=_collision(status=EvidenceStatus.MISSING))
    path = tmp_path / "blocked.json"
    path.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")

    assert readiness_cli(["validate", "--artifact", str(path)]) == 3
    assert '"decision": "BLOCK"' in capsys.readouterr().out


def test_public_cli_validates_artifact_and_rejects_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw_path = tmp_path / "raw.json"
    raw_path.write_text(_artifact().model_dump_json(indent=2), encoding="utf-8")
    output = tmp_path / "readiness.json"

    assert readiness_cli(["assemble", "--evidence", str(raw_path), "--output", str(output)]) == 0
    assert readiness_cli(["validate", "--artifact", str(output)]) == 0
    rendered = capsys.readouterr().out
    assert '"valid": true' in rendered
    with pytest.raises(FileExistsError):
        readiness_cli(["assemble", "--evidence", str(raw_path), "--output", str(output)])


@pytest.mark.parametrize(
    ("name", "blocking_gate"),
    [
        ("candidate-route-block.json", "collision_timeline"),
        ("known-narrow-route-block.json", "swept_footprint_clearance"),
        ("collision-unavailable-block.json", "collision_timeline"),
        ("footprint-mismatch-block.json", "geometry_attestation"),
    ],
)
def test_review_fixtures_are_reconstructible_blocks(name: str, blocking_gate: str) -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "validation"
        / "evidence"
        / "motion-safety"
        / name
    )

    report = load_and_validate_motion_readiness(path)
    artifact = MotionReadinessArtifact.model_validate_json(path.read_text(encoding="utf-8"))

    assert report.valid is True
    assert report.decision == "BLOCK"
    assert artifact.result is not None
    assert blocking_gate in artifact.result.blocking_gates


def _consume_authorization(
    artifact: MotionReadinessArtifact,
    requested: MotionRequestBinding | None = None,
    *,
    runtime: RuntimeBinding | None = None,
    ros_ns: int = 10_000,
    host_ns: int = 20_000,
) -> bool:
    return MotionAuthorizationNonceStore().consume_if_authorized(
        artifact,
        requested or artifact.motion_request,
        current_runtime=runtime or artifact.runtime,
        current_ros_ns=ros_ns,
        current_host_monotonic_ns=host_ns,
    )


def test_motion_authorization_requires_exact_request_binding() -> None:
    artifact = _artifact()
    changed_goal = _motion_request(goal=Pose2(x=1.01, y=0.0, yaw=0.0))

    assert _consume_authorization(artifact) is True
    assert _consume_authorization(artifact, changed_goal) is False


def test_motion_authorization_rejects_stop_play_epoch_or_runtime_drift() -> None:
    artifact = _artifact()

    assert (
        _consume_authorization(
            artifact,
            runtime=artifact.runtime.model_copy(update={"simulation_epoch": "epoch-after-play"}),
        )
        is False
    )
    assert (
        _consume_authorization(
            artifact,
            runtime=artifact.runtime.model_copy(update={"runtime_boot_id": "boot-after-restart"}),
        )
        is False
    )
    assert (
        _consume_authorization(
            artifact,
            runtime=artifact.runtime.model_copy(update={"scene_sha256": "9" * 64}),
        )
        is False
    )
    assert (
        _consume_authorization(
            artifact,
            runtime=artifact.runtime.model_copy(update={"map_sha256": "8" * 64}),
        )
        is False
    )


def test_motion_authorization_rejects_post_capture_clock_rollback() -> None:
    artifact = _artifact()
    rolled_back = artifact.runtime_after.model_copy(
        update={
            "capture_ros_ns": artifact.runtime_after.capture_ros_ns - 1,
            "capture_host_monotonic_ns": (artifact.runtime_after.capture_host_monotonic_ns - 1),
        }
    )

    assert (
        _consume_authorization(
            artifact,
            runtime=rolled_back,
            ros_ns=rolled_back.capture_ros_ns,
            host_ns=rolled_back.capture_host_monotonic_ns,
        )
        is False
    )


def test_motion_authorization_rejects_clock_arguments_not_bound_to_runtime_snapshot() -> None:
    artifact = _artifact()

    assert _consume_authorization(artifact, ros_ns=10_001, host_ns=20_001) is False


def test_future_shifted_short_authorization_window_cannot_replay_old_evidence() -> None:
    original = _motion_request()
    values = original.model_dump(mode="python", exclude={"content_sha256"})
    values.update(
        {
            "valid_from_ros_ns": 12_000,
            "valid_until_ros_ns": 13_000,
            "valid_from_host_monotonic_ns": 22_000,
            "valid_until_host_monotonic_ns": 23_000,
        }
    )
    shifted = MotionRequestBinding.create(**values)
    artifact = _artifact(motion_request=shifted)

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    assert "does not overlap the evidence capture" in " ".join(
        artifact.result.evidence_contract_failures
    )
    assert (
        _consume_authorization(
            artifact,
            shifted,
            ros_ns=12_500,
            host_ns=22_500,
        )
        is False
    )


def test_motion_authorization_rejects_expired_or_consumed_nonce() -> None:
    artifact = _artifact()

    assert _consume_authorization(artifact, ros_ns=11_001) is False
    assert _consume_authorization(artifact, host_ns=21_001) is False


def test_motion_authorization_nonce_is_atomically_single_use() -> None:
    artifact = _artifact()
    store = MotionAuthorizationNonceStore()

    assert (
        store.consume_if_authorized(
            artifact,
            artifact.motion_request,
            current_runtime=artifact.runtime,
            current_ros_ns=10_000,
            current_host_monotonic_ns=20_000,
        )
        is True
    )
    assert (
        store.consume_if_authorized(
            artifact,
            artifact.motion_request,
            current_runtime=artifact.runtime,
            current_ros_ns=10_000,
            current_host_monotonic_ns=20_000,
        )
        is False
    )


def test_motion_authorization_concurrent_consume_has_one_winner() -> None:
    artifact = _artifact()
    store = MotionAuthorizationNonceStore()

    def consume() -> bool:
        return store.consume_if_authorized(
            artifact,
            artifact.motion_request,
            current_runtime=artifact.runtime,
            current_ros_ns=10_000,
            current_host_monotonic_ns=20_000,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(lambda _index: consume(), range(32)))

    assert results.count(True) == 1
    assert results.count(False) == 31


def test_motion_authorization_binds_exact_path_and_artifact_input() -> None:
    artifact = _artifact()
    changed_path = PathEvidence.create(
        **artifact.path.model_dump(mode="python", exclude={"content_sha256", "poses"}),
        poses=(
            artifact.motion_request.start,
            Pose2(x=0.5, y=0.1, yaw=0.0),
            artifact.motion_request.goal,
        ),
    )
    replay = artifact.model_copy(update={"path": changed_path})

    assert _consume_authorization(replay) is False


def test_replayed_safe_path_for_different_goal_blocks() -> None:
    different_request = _motion_request(goal=Pose2(x=2.0, y=0.0, yaw=0.0))
    artifact = _artifact(motion_request=different_request)

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    assert (
        "planned path goal differs from motion request"
        in artifact.result.evidence_contract_failures
    )


def test_collision_monitor_inventory_must_exactly_match_usd_geometry() -> None:
    stream = _collision()
    values = stream.model_dump(
        mode="python",
        exclude={"content_sha256", "monitored_prim_paths", "monitored_prim_inventory_sha256"},
    )
    wrong_inventory = ("/World/Robot/leg/collision",)
    artifact = _artifact(
        collision=CollisionStreamEvidence.create(
            **values,
            monitored_prim_paths=wrong_inventory,
            monitored_prim_inventory_sha256=collision_prim_inventory_sha256(wrong_inventory),
        )
    )

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    assert "monitored prim inventory differs" in " ".join(
        artifact.result.collision_timeline.failures
    )


def test_raw_scene_query_catches_summary_and_filter_omitting_dynamic_cart() -> None:
    artifact = _artifact(
        usd=_usd(
            counterpart_prims=("/World/Warehouse",),
            counterpart_categories=("environment",),
            raw_entries=(
                ("/World/DynamicCart", "dynamic"),
                ("/World/Warehouse", "environment"),
            ),
        ),
        collision=_collision(),
    )

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    geometry_failures = " ".join(artifact.result.geometry_attestation.failures)
    collision_failures = " ".join(artifact.result.collision_timeline.failures)
    assert "prim summary differs from raw scene enumeration" in geometry_failures
    assert "category summary differs from raw scene enumeration" in geometry_failures
    assert "omits enabled counterparts" in collision_failures
    assert "omits enabled categories" in collision_failures


def test_positive_collision_requires_participants_and_contact_evidence() -> None:
    stream = _collision()
    event = (
        stream.windows[0]
        .raw_messages[0]
        .model_copy(update={"raw_message": {"collision_detected": True}})
    )
    window = stream.windows[0].model_copy(update={"raw_messages": (event,)})
    values = stream.model_dump(mode="python", exclude={"content_sha256", "windows"})
    artifact = _artifact(collision=CollisionStreamEvidence.create(**values, windows=(window,)))

    assert artifact.result is not None
    assert artifact.result.decision == "BLOCK"
    assert "lacks participants or contact evidence" in " ".join(
        artifact.result.collision_timeline.failures
    )


@pytest.mark.parametrize(
    "measurements",
    [
        lambda: BoundMeasurements(bound_m=(-0.01,)),
        lambda: SpeedLatencyMeasurements(
            maximum_speed_magnitude_mps=(-1.0, 0.1),
            latency_s=(0.1,),
        ),
        lambda: SpeedLatencyMeasurements(
            maximum_speed_magnitude_mps=(0.1,),
            latency_s=(-0.1,),
        ),
        lambda: StoppingMeasurements(
            maximum_speed_magnitude_mps=(0.1,),
            minimum_deceleration_mps2=(0.0,),
        ),
    ],
)
def test_clearance_measurements_reject_signed_or_nonphysical_values(measurements: object) -> None:
    assert callable(measurements)
    with pytest.raises(ValueError):
        measurements()


def test_usd_transform_uses_declared_row_major_rigid_transform_and_separate_scale() -> None:
    local = (
        Point3(x=-1.0, y=-0.5, z=0.0),
        Point3(x=1.0, y=-0.5, z=0.0),
        Point3(x=1.0, y=0.5, z=0.0),
        Point3(x=-1.0, y=0.5, z=0.0),
    )
    projected = _polygon((2.25, 2.5), (2.25, 3.5), (1.75, 3.5), (1.75, 2.5))
    transform = (
        0.0,
        -1.0,
        0.0,
        2.0,
        1.0,
        0.0,
        0.0,
        3.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
    )
    usd = _usd()
    prim = UsdCollisionPrimEvidence(
        prim_path="/World/Robot/chassis/collision",
        shape_type="box",
        mesh_identity="scaled-rotated-box",
        local_geometry_vertices=local,
        base_from_prim_transform=transform,
        scale=(0.5, 0.5, 1.0),
        transform_convention="row_major_affine_column_vector",
        transform_translation_unit="m",
        transform_includes_scale=False,
        projected_base_hull=projected,
    )
    values = usd.model_dump(
        mode="python", exclude={"content_sha256", "collision_prims", "projected_base_hull"}
    )
    artifact = _artifact(
        usd=UsdCollisionGeometryEvidence.create(
            **values,
            collision_prims=(prim,),
            projected_base_hull=projected,
        )
    )

    assert artifact.result is not None
    assert not any(
        "cannot be rebuilt" in failure for failure in artifact.result.geometry_attestation.failures
    )
    assert artifact.result.geometry_attestation.usd_width_m == pytest.approx(0.5)
    assert artifact.result.geometry_attestation.usd_length_m == pytest.approx(1.0)


def test_usd_transform_rejects_scale_embedded_in_affine_matrix() -> None:
    with pytest.raises(ValueError, match="rigid and exclude scale"):
        UsdCollisionPrimEvidence(
            prim_path="/World/Robot/chassis/collision",
            shape_type="box",
            mesh_identity="invalid-scaled-transform",
            local_geometry_vertices=(
                Point3(x=-1.0, y=-1.0, z=0.0),
                Point3(x=1.0, y=-1.0, z=0.0),
                Point3(x=1.0, y=1.0, z=0.0),
                Point3(x=-1.0, y=1.0, z=0.0),
            ),
            base_from_prim_transform=(
                0.5,
                0.0,
                0.0,
                0.0,
                0.0,
                0.5,
                0.0,
                0.0,
                0.0,
                0.0,
                0.5,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
            ),
            scale=(1.0, 1.0, 1.0),
            transform_convention="row_major_affine_column_vector",
            transform_translation_unit="m",
            transform_includes_scale=False,
            projected_base_hull=_polygon((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)),
        )


def _repository_transport_for_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    *,
    python_executable: Path | None = None,
) -> RepositoryIsaacReadOnlyTransport:
    probe = tmp_path / "repository-probe.py"
    probe.write_text(source, encoding="utf-8")
    config = tmp_path / "motion-readiness.json"
    config.write_text(
        json.dumps(
            {
                "timeout_s": 0.05,
                "runtime": {"git_sha": "a" * 40},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(motion_safety_isaac, "_REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(motion_safety_isaac, "_PROBE_ENTRYPOINT", probe)
    monkeypatch.setattr(
        motion_safety_isaac,
        "_PROBE_PYTHON_EXECUTABLE",
        python_executable or Path(sys.executable),
    )
    monkeypatch.setattr(motion_safety_isaac, "_attest_repository_source", lambda _path: None)
    source_bundle = io.BytesIO()
    with zipfile.ZipFile(source_bundle, "w") as archive:
        archive.writestr("jenai/__init__.py", "")
    monkeypatch.setattr(
        motion_safety_isaac,
        "_reviewed_source_bundle",
        lambda _sha: source_bundle.getvalue(),
    )
    return RepositoryIsaacReadOnlyTransport(config_path=config, timeout_s=0.5)


def test_source_bundle_comes_from_reviewed_git_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def trusted_git(*args: str) -> bytes:
        calls.append(args)
        if args[:3] == ("ls-tree", "-r", "--name-only"):
            return b"src/jenai/__init__.py\nsrc/jenai/acceptance/motion_safety_probe.py\n"
        if args[0] == "show":
            return ("reviewed:" + args[1]).encode()
        raise AssertionError(args)

    monkeypatch.setattr(motion_safety_isaac, "_trusted_git", trusted_git)
    payload = motion_safety_isaac._reviewed_source_bundle("a" * 40)

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert (
            archive.read("jenai/acceptance/motion_safety_probe.py")
            == ("reviewed:" + "a" * 40 + ":src/jenai/acceptance/motion_safety_probe.py").encode()
        )
        assert json.loads(archive.read("jenai/_motion_safety_source_manifest.json")) == {
            "source_git_sha": "a" * 40
        }
    assert all(call[0] in {"ls-tree", "show"} for call in calls)


def test_real_probe_source_identity_uses_sealed_bundle_manifest(tmp_path: Path) -> None:
    bundle = tmp_path / "reviewed-source.zip"
    source_root = Path(__file__).resolve().parents[2] / "src"
    with zipfile.ZipFile(bundle, "w") as archive:
        for source in sorted((source_root / "jenai").rglob("*.py")):
            archive.writestr(source.relative_to(source_root).as_posix(), source.read_bytes())
        archive.writestr(
            "jenai/_motion_safety_source_manifest.json",
            json.dumps({"source_git_sha": "a" * 40}),
        )
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(bundle)!r}); "
        "from jenai.acceptance.motion_safety_probe import _repository_source_identity; "
        f"_repository_source_identity({'a' * 40!r}); "
        "print('manifest-ok')"
    )

    completed = subprocess.run(
        [sys.executable, "-I", "-c", code],
        check=True,
        capture_output=True,
        text=True,
        timeout=10.0,
    )

    assert completed.stdout.strip() == "manifest-ok"


def test_stage_export_preparation_is_create_once_and_source_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    entrypoint = repository / "scripts" / "isaac_motion_readiness_stage_export.py"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("# reviewed stage bootstrap\n", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text('{"runtime":{"git_sha":"' + "a" * 40 + '"}}', encoding="utf-8")
    output = tmp_path / "reviewed-source.zip"
    bundle = b"reviewed-source-closure"
    monkeypatch.setattr(motion_safety_isaac, "_REPOSITORY_ROOT", repository)
    monkeypatch.setattr(motion_safety_isaac, "_attest_repository_source", lambda _data: None)
    monkeypatch.setattr(motion_safety_isaac, "_reviewed_source_bundle", lambda _sha: bundle)

    prepared = motion_safety_isaac.prepare_reviewed_stage_export_bundle(config, output)

    assert output.read_bytes() == bundle
    assert prepared["source_bundle_sha256"] == hashlib.sha256(bundle).hexdigest()
    assert (
        prepared["stage_entrypoint_sha256"] == hashlib.sha256(entrypoint.read_bytes()).hexdigest()
    )
    with pytest.raises(FileExistsError):
        motion_safety_isaac.prepare_reviewed_stage_export_bundle(config, output)


def test_stage_export_bootstrap_holds_git_verified_bundle_descriptor(
    tmp_path: Path,
) -> None:
    namespace = runpy.run_path(
        str(
            Path(__file__).resolve().parents[2]
            / "scripts"
            / "isaac_motion_readiness_stage_export.py"
        )
    )
    head = "a" * 40
    repository_path = "src/jenai/__init__.py"
    reviewed = b"# reviewed\n"
    bundle = tmp_path / "source.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("jenai/__init__.py", reviewed)
        archive.writestr(
            "jenai/_motion_safety_source_manifest.json",
            json.dumps({"source_git_sha": head}),
        )

    def git(_repository: Path, *args: str) -> bytes:
        if args == ("rev-parse", "HEAD"):
            return (head + "\n").encode()
        if args == ("status", "--porcelain"):
            return b""
        if args[:3] == ("ls-tree", "-r", "--name-only"):
            return (repository_path + "\n").encode()
        if args == ("show", f"{head}:{repository_path}"):
            return reviewed
        raise AssertionError(args)

    namespace["_open_reviewed_bundle"].__globals__["_git"] = git
    descriptor = namespace["_open_reviewed_bundle"](bundle, tmp_path)
    os.close(descriptor)

    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("jenai/__init__.py", b"# malicious\n")
        archive.writestr(
            "jenai/_motion_safety_source_manifest.json",
            json.dumps({"source_git_sha": head}),
        )
    with pytest.raises(RuntimeError, match="content differs"):
        namespace["_open_reviewed_bundle"](bundle, tmp_path)


def test_repository_transport_reaps_forked_process_group_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _repository_transport_for_test(
        tmp_path,
        monkeypatch,
        """import json, os, sys, time
json.loads(sys.stdin.read())
pid = os.fork()
if pid == 0:
    os.close(1)
    os.close(2)
    time.sleep(60)
    os._exit(0)
print(json.dumps({\"child_pid\": pid}), flush=True)
""",
    )

    payload = asyncio.run(transport.observe(IsaacObservationOperation.RUNTIME_BINDING, {}))
    child_pid = int(payload["child_pid"])
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    else:
        pytest.fail("repository probe left a forked process alive")


def test_repository_transport_bounds_timeout_and_malformed_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    timeout_transport = _repository_transport_for_test(
        tmp_path, monkeypatch, "import time; time.sleep(60)"
    )
    with pytest.raises(TimeoutError):
        asyncio.run(timeout_transport.observe(IsaacObservationOperation.RUNTIME_BINDING, {}))

    malformed_transport = _repository_transport_for_test(tmp_path, monkeypatch, 'print("not-json")')
    with pytest.raises(ValueError, match="malformed JSON"):
        asyncio.run(malformed_transport.observe(IsaacObservationOperation.RUNTIME_BINDING, {}))


def test_repository_transport_never_executes_replaced_entrypoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "unreviewed-ran"
    transport = _repository_transport_for_test(
        tmp_path,
        monkeypatch,
        'import json, sys; json.loads(sys.stdin.read()); print("{}")',
    )
    original_spawn = asyncio.create_subprocess_exec

    async def replace_then_spawn(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        motion_safety_isaac._PROBE_ENTRYPOINT.write_text(
            f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
            encoding="utf-8",
        )
        return await original_spawn(*args, **kwargs)

    monkeypatch.setattr(motion_safety_isaac.asyncio, "create_subprocess_exec", replace_then_spawn)
    with pytest.raises(RuntimeError, match="identity changed"):
        asyncio.run(transport.observe(IsaacObservationOperation.RUNTIME_BINDING, {}))
    assert marker.exists() is False


def test_repository_transport_executes_opened_python_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python_link = tmp_path / "python"
    python_link.symlink_to(Path(sys.executable).resolve())
    transport = _repository_transport_for_test(
        tmp_path,
        monkeypatch,
        'import json, sys; json.loads(sys.stdin.read()); print("{}")',
        python_executable=python_link,
    )
    original_spawn = asyncio.create_subprocess_exec

    async def replace_then_spawn(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        python_link.unlink()
        python_link.symlink_to("/bin/false")
        return await original_spawn(*args, **kwargs)

    monkeypatch.setattr(motion_safety_isaac.asyncio, "create_subprocess_exec", replace_then_spawn)

    assert asyncio.run(transport.observe(IsaacObservationOperation.RUNTIME_BINDING, {})) == {}


def test_repository_transport_rejects_outer_budget_shorter_than_plan_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text('print("{}")', encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"runtime": {"git_sha": "a" * 40}, "timeout_s": 1.0}),
        encoding="utf-8",
    )
    monkeypatch.setattr(motion_safety_isaac, "_PROBE_ENTRYPOINT", probe)
    monkeypatch.setattr(motion_safety_isaac, "_PROBE_PYTHON_EXECUTABLE", Path(sys.executable))
    monkeypatch.setattr(motion_safety_isaac, "_attest_repository_source", lambda _data: None)
    with pytest.raises(ValueError, match="cannot cover plan and cancellation"):
        RepositoryIsaacReadOnlyTransport(config_path=config, timeout_s=6.0)


def test_repository_transport_bounds_output_and_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(motion_safety_isaac, "_MAX_PROBE_RESPONSE_BYTES", 32)
    oversized = _repository_transport_for_test(tmp_path, monkeypatch, 'print("x" * 64)')
    with pytest.raises(ValueError, match="size limit"):
        asyncio.run(oversized.observe(IsaacObservationOperation.RUNTIME_BINDING, {}))

    failed = _repository_transport_for_test(tmp_path, monkeypatch, "raise SystemExit(7)")
    with pytest.raises(RuntimeError, match="probe failed"):
        asyncio.run(failed.observe(IsaacObservationOperation.RUNTIME_BINDING, {}))


def test_repository_transport_freezes_config_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _repository_transport_for_test(
        tmp_path,
        monkeypatch,
        """import json, sys
config_path = sys.argv[sys.argv.index("--config") + 1]
with open(config_path, encoding="utf-8") as handle:
    print(json.dumps(json.load(handle)))
""",
    )
    transport.config_path.write_text('{"changed": true}', encoding="utf-8")

    payload = asyncio.run(transport.observe(IsaacObservationOperation.RUNTIME_BINDING, {}))

    assert payload == {"runtime": {"git_sha": "a" * 40}, "timeout_s": 0.05}


def test_repository_transport_ignores_hostile_pythonpath_and_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shadow = tmp_path / "shadow" / "jenai" / "acceptance"
    shadow.mkdir(parents=True)
    marker = tmp_path / "shadow-imported"
    (shadow / "motion_safety_probe.py").write_text(
        f'from pathlib import Path; Path({str(marker)!r}).write_text("ran")',
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "shadow"))
    monkeypatch.setenv("PATH", str(tmp_path / "shadow-bin"))
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "timeout_s": 0.05,
                "runtime": {"git_sha": "a" * 40},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(motion_safety_isaac, "_PROBE_PYTHON_EXECUTABLE", Path(sys.executable))
    monkeypatch.setattr(motion_safety_isaac, "_attest_repository_source", lambda _data: None)
    source_bundle = io.BytesIO()
    with zipfile.ZipFile(source_bundle, "w") as archive:
        archive.writestr("jenai/__init__.py", "")
        archive.writestr("jenai/acceptance/__init__.py", "")
        archive.writestr(
            "jenai/acceptance/motion_safety_probe.py",
            "def main(argv=None):\n    raise SystemExit(7)\n",
        )
    monkeypatch.setattr(
        motion_safety_isaac,
        "_reviewed_source_bundle",
        lambda _sha: source_bundle.getvalue(),
    )
    transport = RepositoryIsaacReadOnlyTransport(config_path=config, timeout_s=1.0)

    with pytest.raises(RuntimeError, match="probe failed"):
        asyncio.run(transport.observe(IsaacObservationOperation.RUNTIME_BINDING, {}))

    assert marker.exists() is False
    assert "PYTHONPATH" not in transport._effective_environment()
    assert "PATH" not in transport._effective_environment()


def test_usd_collision_traversal_includes_instance_proxies_and_enabled_state() -> None:
    proxy = SimpleNamespace(name="proxy")
    environment = SimpleNamespace(name="environment")
    disabled = SimpleNamespace(name="disabled")
    predicate = object()

    class Usd:
        @staticmethod
        def TraverseInstanceProxies() -> object:
            return predicate

    class CollisionApi:
        def __init__(self, prim: object) -> None:
            self.prim = prim

        def GetCollisionEnabledAttr(self) -> object:
            return SimpleNamespace(Get=lambda: self.prim is not disabled)

    class Physics:
        CollisionAPI = CollisionApi

    for prim in (proxy, environment, disabled):
        prim.HasAPI = lambda _api: True

    class Stage:
        def Traverse(self, received: object) -> tuple[object, ...]:
            assert received is predicate
            return (proxy, environment, disabled)

    observed = motion_safety_probe.LiveUsdObservationBackend._collision_prims(Stage(), Usd, Physics)

    assert observed == (proxy, environment)


def test_capsule_collision_extent_includes_hemisphere_radius_for_every_axis() -> None:
    class Attribute:
        def __init__(self, value: object) -> None:
            self.value = value

        def Get(self) -> object:
            return self.value

    class Capsule:
        def __init__(self, axis: str) -> None:
            self.axis = axis

        def GetTypeName(self) -> str:
            return "Capsule"

        def GetAttribute(self, name: str) -> Attribute:
            return Attribute({"radius": 0.3, "height": 1.0, "axis": self.axis}[name])

    for axis, coordinate in (("X", "x"), ("Y", "y"), ("Z", "z")):
        shape, vertices = motion_safety_probe.LiveUsdObservationBackend._local_collision_vertices(
            Capsule(axis)
        )
        assert shape == "Capsule"
        assert max(abs(getattr(vertex, coordinate)) for vertex in vertices) == pytest.approx(0.8)


def test_rotated_costmap_origin_fails_closed() -> None:
    identity = SimpleNamespace(
        position=SimpleNamespace(x=1.0, y=2.0),
        orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
    )
    assert motion_safety_probe.RepositoryIsaacProbe._costmap_origin(
        SimpleNamespace(origin=identity)
    ) == Point2(x=1.0, y=2.0)

    rotated = SimpleNamespace(
        position=SimpleNamespace(x=1.0, y=2.0),
        orientation=SimpleNamespace(x=0.0, y=0.0, z=math.sin(math.pi / 4), w=math.cos(math.pi / 4)),
    )
    with pytest.raises(RuntimeError, match="rotated costmap origins"):
        motion_safety_probe.RepositoryIsaacProbe._costmap_origin(SimpleNamespace(origin=rotated))
    for quaternion, message in (
        ((0.0, 0.0, 0.0, 0.0), "not normalized"),
        ((0.0, 0.0, 0.0, 2.0), "not normalized"),
        ((math.sin(0.1), 0.0, 0.0, math.cos(0.1)), "rotated"),
        ((0.0, math.sin(0.1), 0.0, math.cos(0.1)), "rotated"),
    ):
        x, y, z, w = quaternion
        origin = SimpleNamespace(
            position=SimpleNamespace(x=0.0, y=0.0),
            orientation=SimpleNamespace(x=x, y=y, z=z, w=w),
        )
        with pytest.raises(RuntimeError, match=message):
            motion_safety_probe.RepositoryIsaacProbe._costmap_origin(SimpleNamespace(origin=origin))


def test_usd_mesh_approximation_is_bound_or_fails_closed() -> None:
    class Attribute:
        def __init__(self, value: object) -> None:
            self.value = value

        def Get(self) -> object:
            return self.value

    class Mesh:
        def __init__(self, approximation: str) -> None:
            self.approximation = approximation

        def GetTypeName(self) -> str:
            return "Mesh"

        def GetAttribute(self, name: str) -> Attribute:
            if name == "physics:approximation":
                return Attribute(self.approximation)
            return Attribute(((0, 0, 0), (1, 0, 0), (0, 1, 0)))

    shape, _vertices = motion_safety_probe.LiveUsdObservationBackend._local_collision_vertices(
        Mesh("convexHull")
    )
    assert shape == "Mesh:convexHull"
    with pytest.raises(RuntimeError, match="unsupported collision mesh approximation"):
        motion_safety_probe.LiveUsdObservationBackend._local_collision_vertices(
            Mesh("boundingCube")
        )


def test_plan_goal_cancellation_requires_acknowledgement() -> None:
    class Future:
        def __init__(self, result: object) -> None:
            self._result = result

        def result(self) -> object:
            return self._result

    goal_id = SimpleNamespace(uuid=bytes(range(16)))

    class Handle:
        def __init__(self, result: object) -> None:
            self.result = result
            self.goal_id = goal_id

        def cancel_goal_async(self) -> Future:
            return Future(self.result)

    spins: list[float] = []
    rclpy = SimpleNamespace(
        spin_until_future_complete=lambda _node, _future, timeout_sec: spins.append(timeout_sec)
    )
    acknowledged = SimpleNamespace(goals_canceling=[SimpleNamespace(goal_id=goal_id)])
    terminal = Future(SimpleNamespace(status=5))
    motion_safety_probe.LiveRosObservationBackend._cancel_plan_goal(
        rclpy, object(), Handle(acknowledged), terminal, 1.5, 5
    )
    assert spins == [1.5, 1.5]
    with pytest.raises(RuntimeError, match="not acknowledged"):
        motion_safety_probe.LiveRosObservationBackend._cancel_plan_goal(
            rclpy, object(), Handle(None), terminal, 1.5, 5
        )


def test_artifact_persistence_completes_short_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_write = os.write

    def short_write(descriptor: int, content: object) -> int:
        return real_write(descriptor, bytes(content)[:7])

    monkeypatch.setattr(motion_safety.os, "write", short_write)
    output = tmp_path / "artifact.json"
    write_motion_readiness_artifact(_artifact(), output)

    report = load_and_validate_motion_readiness(output)
    assert report.valid is True


def test_repository_probe_builds_reconstructible_block_from_raw_fake_runtime(  # noqa: C901
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = _artifact()
    config = {
        "timeout_s": 0.5,
        "runtime": expected.runtime.model_dump(mode="json"),
        "motion_request": {
            "authorization_nonce": expected.motion_request.authorization_nonce,
            "start": expected.motion_request.start.model_dump(mode="json"),
            "goal": expected.motion_request.goal.model_dump(mode="json"),
            "planner_id": expected.motion_request.planner_id,
        },
        "map_frame": "map",
        "nav_footprint": {
            "local_node": "/local_costmap/local_costmap",
            "global_node": "/global_costmap/global_costmap",
            "footprint_parameter": "footprint",
            "padding_parameter": "footprint_padding",
        },
        "usd": {
            "scene_path": expected.runtime.scene_path,
            "robot_root_prim": "/World/Robot",
            "base_frame": "base_link",
            "stage_export_path": str(tmp_path / "stage-evidence.json"),
            "stage_export_sha256": "9" * 64,
        },
        "costmap_topics": {
            ClearanceLayer.STATIC_LETHAL.value: "/costmap/static-lethal",
            ClearanceLayer.STATIC_INFLATION.value: "/costmap/static-inflation",
            ClearanceLayer.LIVE_OBSTACLE.value: "/costmap/live-obstacle",
            ClearanceLayer.UNKNOWN.value: "/costmap/unknown",
        },
        "collision_stream": {
            "topic": "/twin/collision",
            "message_type": "jenai_msgs/msg/CollisionEvent",
            "qos": "reliable",
            "robot_root_prim": "/World/Robot",
            "monitored_prim_paths": ["/World/Robot/chassis/collision"],
            "collision_filter": expected.collision_stream.collision_filter.model_dump(mode="json"),
        },
        "clearance_budget": expected.clearance_budget.model_dump(mode="json"),
        "clearance_sources": [
            source.model_dump(mode="json") for source in expected.clearance_sources
        ],
    }
    config_path = tmp_path / "motion-readiness.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    stamp = SimpleNamespace(sec=0, nanosec=10_000)
    header = SimpleNamespace(frame_id="map", stamp=stamp)
    footprint_message = SimpleNamespace(
        header=SimpleNamespace(frame_id="base_link", stamp=stamp),
        polygon=SimpleNamespace(
            points=[SimpleNamespace(x=point.x, y=point.y) for point in _footprint().vertices]
        ),
    )
    layer_costs = {
        "/costmap/static-lethal": 254,
        "/costmap/static-inflation": 81,
        "/costmap/live-obstacle": 254,
        "/costmap/unknown": 255,
    }

    class RawRos:
        def one_message(
            self, topic: str, _type_name: str, _qos: str = "reliable"
        ) -> tuple[object, int]:
            if topic == "/clock":
                return SimpleNamespace(clock=stamp), 20_000
            if topic == "/local_costmap/published_footprint":
                return footprint_message, 20_000
            cost = layer_costs[topic]
            metadata = SimpleNamespace(
                size_x=1,
                size_y=1,
                resolution=0.05,
                origin=SimpleNamespace(
                    position=SimpleNamespace(x=0.0, y=0.0),
                    orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
                ),
            )
            return SimpleNamespace(header=header, metadata=metadata, data=[cost]), 20_000

        def parameters(self, remote_node: str, names: tuple[str, ...]) -> tuple[object, ...]:
            assert remote_node in {
                "/local_costmap/local_costmap",
                "/global_costmap/global_costmap",
            }
            assert names == ("footprint", "footprint_padding", "robot_base_frame")
            polygon = [[point.x, point.y] for point in _configured_footprint().vertices]
            return (json.dumps(polygon), 0.03, "base_link")

        def compute_path(
            self, start: Pose2, goal: Pose2, frame_id: str
        ) -> tuple[list[Pose2], int, int]:
            assert (start, goal, frame_id) == (
                expected.motion_request.start,
                expected.motion_request.goal,
                "map",
            )
            return list(expected.path.poses), 10_000, 20_000

        def topic_type(self, topic: str) -> None:
            assert topic == "/twin/collision"
            return None

    class RawUsd:
        def collision_geometry(
            self, _config: dict[str, object], runtime: RuntimeBinding
        ) -> UsdCollisionGeometryEvidence:
            assert runtime.runtime_fingerprint == expected.runtime.runtime_fingerprint
            return expected.usd_geometry

    monkeypatch.setattr(motion_safety_probe, "_repository_source_identity", lambda _sha: None)
    monkeypatch.setattr(motion_safety_probe, "_sha256_file", lambda _path: "b" * 64)
    probe = RepositoryIsaacProbe(
        config_path,
        ros_backend=RawRos(),
        usd_backend=RawUsd(),
    )

    class InProcessTransport:
        def probe_identity(self, source_git_sha: str) -> ProbeIdentityEvidence:
            assert source_git_sha == "a" * 40
            return expected.probe_identity

        async def observe(
            self,
            operation: IsaacObservationOperation,
            context: dict[str, object],
        ) -> object:
            result = probe.execute(operation.value, context)
            if isinstance(result, tuple):
                return [item.model_dump(mode="json") for item in result]
            return result.model_dump(mode="json")

    outcome = asyncio.run(
        IsaacMotionReadinessCollector(
            IsaacRosReadOnlyEvidenceSource(InProcessTransport())
        ).collect()
    )
    assert outcome.status == "captured", outcome
    assert outcome.artifact is not None
    artifact = _assembled_capture(outcome.artifact)
    report = validate_motion_readiness_artifact(artifact)
    assert report.valid is True
    assert report.decision == "BLOCK"
    assert artifact.collision_stream.status == EvidenceStatus.MISSING
    assert artifact.runtime.observation_limitations
    assert all(layer.semantic_attestation == "unavailable" for layer in artifact.costmap_layers)
    assert all(source.status == EvidenceStatus.UNAVAILABLE for source in artifact.clearance_sources)


def test_nav_footprint_config_rejects_duplicate_local_and_global_nodes() -> None:
    with pytest.raises(ValueError, match="must be distinct"):
        motion_safety_probe.NavFootprintProbeConfig(
            local_node="/local_costmap/local_costmap",
            global_node="local_costmap/local_costmap",
        )


def test_exported_usd_backend_binds_create_once_stage_evidence(tmp_path: Path) -> None:
    expected = _artifact()
    export = tmp_path / "stage-evidence.json"
    export.write_text(expected.usd_geometry.model_dump_json(), encoding="utf-8")
    digest = hashlib.sha256(export.read_bytes()).hexdigest()
    backend = ExportedUsdObservationBackend()

    usd_config = motion_safety_probe.UsdProbeConfig(
        scene_path=expected.runtime.scene_path,
        robot_root_prim=expected.usd_geometry.robot_root_prim,
        base_frame=expected.usd_geometry.base_frame,
        stage_export_path=str(export),
        stage_export_sha256=digest,
    )
    observed = backend.collision_geometry(usd_config, expected.runtime)

    assert observed == expected.usd_geometry
    with pytest.raises(RuntimeError, match="digest is not bound"):
        backend.collision_geometry(
            usd_config.model_copy(update={"stage_export_sha256": None}),
            expected.runtime,
        )
    stale_runtime = expected.runtime.model_copy(
        update={"capture_ros_ns": expected.runtime.capture_ros_ns + 10_000}
    )
    with pytest.raises(RuntimeError, match="stale or binds another runtime"):
        backend.collision_geometry(usd_config, stale_runtime)


class _FakeIsaacTransport:
    def __init__(
        self,
        expected: MotionReadinessArtifact,
        *,
        collision: CollisionStreamEvidence | None = None,
        sources: tuple[ClearanceSourceEvidence, ...] | None = None,
        runtime_after: RuntimeBinding | None = None,
    ) -> None:
        self.expected = expected
        self.collision = collision or expected.collision_stream
        self.sources = sources or expected.clearance_sources
        self.runtime_after = runtime_after or expected.runtime_after
        self.runtime_reads = 0
        self.operations: list[IsaacObservationOperation] = []

    def probe_identity(self, source_git_sha: str) -> ProbeIdentityEvidence:
        assert source_git_sha == "a" * 40
        return self.expected.probe_identity

    async def observe(
        self,
        operation: IsaacObservationOperation,
        context: dict[str, object],
    ) -> object:
        self.operations.append(operation)
        if operation == IsaacObservationOperation.RUNTIME_BINDING:
            self.runtime_reads += 1
            runtime = self.expected.runtime if self.runtime_reads == 1 else self.runtime_after
            return runtime.model_dump(mode="json")
        footprint_operation = IsaacObservationOperation.EFFECTIVE_NAV_FOOTPRINT
        values: dict[IsaacObservationOperation, object] = {
            IsaacObservationOperation.MOTION_REQUEST: self.expected.motion_request.model_dump(
                mode="json"
            ),
            IsaacObservationOperation.PLANNED_PATH: self.expected.path.model_dump(mode="json"),
            footprint_operation: self.expected.nav_footprint.model_dump(mode="json"),
            IsaacObservationOperation.USD_COLLISION_GEOMETRY: self.expected.usd_geometry.model_dump(
                mode="json"
            ),
            IsaacObservationOperation.COSTMAP_LAYERS: [
                layer.model_dump(mode="json") for layer in self.expected.costmap_layers
            ],
            IsaacObservationOperation.COLLISION_TIMELINE: self.collision.model_dump(mode="json"),
            IsaacObservationOperation.CLEARANCE_BUDGET: self.expected.clearance_budget.model_dump(
                mode="json"
            ),
            IsaacObservationOperation.CLEARANCE_SOURCES: [
                source.model_dump(mode="json") for source in self.sources
            ],
        }
        return values[operation]


def test_concrete_isaac_ros_source_capture_assemble_validate() -> None:
    expected = _artifact()
    transport = _FakeIsaacTransport(expected)
    outcome = asyncio.run(
        IsaacMotionReadinessCollector(IsaacRosReadOnlyEvidenceSource(transport)).collect()
    )
    assert outcome.artifact is not None
    assembled = _assembled_capture(outcome.artifact)

    report = validate_motion_readiness_artifact(assembled)
    assert report.valid is True
    assert report.decision == expected.result.decision
    assert set(transport.operations) == set(IsaacObservationOperation)
    assert transport.operations.count(IsaacObservationOperation.RUNTIME_BINDING) == 2


def test_concrete_source_missing_collision_and_unavailable_tracking_are_valid_block() -> None:
    expected = _artifact()
    unavailable_sources = tuple(
        ClearanceSourceEvidence.create(
            **source.model_dump(mode="python", exclude={"content_sha256", "status"}),
            status=EvidenceStatus.UNAVAILABLE,
        )
        if source.kind == SafetyTermKind.CONTROLLER_TRACKING_BOUND
        else source
        for source in expected.clearance_sources
    )
    transport = _FakeIsaacTransport(
        expected,
        collision=_collision(status=EvidenceStatus.MISSING),
        sources=unavailable_sources,
    )
    outcome = asyncio.run(
        IsaacMotionReadinessCollector(IsaacRosReadOnlyEvidenceSource(transport)).collect()
    )
    assert outcome.artifact is not None
    assembled = _assembled_capture(outcome.artifact)

    report = validate_motion_readiness_artifact(assembled)
    assert report.valid is True
    assert report.decision == "BLOCK"
    assert assembled.result is not None
    assert {"collision_timeline", "minimum_safe_clearance_policy"}.issubset(
        assembled.result.blocking_gates
    )


def test_concrete_source_runtime_drift_is_captured_not_hidden() -> None:
    expected = _artifact()
    runtime_after = expected.runtime_after.model_copy(
        update={"runtime_boot_id": "boot-concrete-drift"}
    )
    transport = _FakeIsaacTransport(expected, runtime_after=runtime_after)
    outcome = asyncio.run(
        IsaacMotionReadinessCollector(IsaacRosReadOnlyEvidenceSource(transport)).collect()
    )
    assert outcome.artifact is not None
    assembled = _assembled_capture(outcome.artifact)

    report = validate_motion_readiness_artifact(assembled)
    assert report.valid is False
    assert report.decision == "BLOCK"
    assert "runtime generation changed during collection: runtime_boot_id" in report.failures


def test_concrete_transport_operation_vocabulary_is_observation_only() -> None:
    assert {operation.value for operation in IsaacObservationOperation} == {
        "runtime_binding",
        "motion_request",
        "planned_path",
        "effective_nav_footprint",
        "usd_collision_geometry",
        "costmap_layers",
        "collision_timeline",
        "clearance_budget",
        "clearance_sources",
    }


class _FakeIsaacReadOnlyRuntime:
    def __init__(
        self,
        expected: MotionReadinessArtifact,
        *,
        collision: CollisionStreamEvidence | None = None,
        sources: tuple[ClearanceSourceEvidence, ...] | None = None,
        runtime_after: RuntimeBinding | None = None,
    ) -> None:
        self.expected = expected
        self.collision = collision or expected.collision_stream
        self.sources = sources or expected.clearance_sources
        self.runtime_after = runtime_after or expected.runtime
        self.runtime_reads = 0

    async def runtime_binding(self) -> RuntimeBinding:
        self.runtime_reads += 1
        return self.expected.runtime if self.runtime_reads == 1 else self.runtime_after

    async def probe_identity(self, runtime: RuntimeBinding) -> ProbeIdentityEvidence:
        return self.expected.probe_identity

    async def motion_request(self, runtime: RuntimeBinding) -> MotionRequestBinding:
        return self.expected.motion_request

    async def planned_path(
        self, runtime: RuntimeBinding, request: MotionRequestBinding
    ) -> PathEvidence:
        return self.expected.path

    async def effective_nav_footprint(self, runtime: RuntimeBinding) -> NavFootprintEvidence:
        return self.expected.nav_footprint

    async def usd_collision_geometry(self, runtime: RuntimeBinding) -> UsdCollisionGeometryEvidence:
        return self.expected.usd_geometry

    async def costmap_layers(self, runtime: RuntimeBinding) -> tuple[CostmapLayerEvidence, ...]:
        return self.expected.costmap_layers

    async def collision_timeline(self, runtime: RuntimeBinding) -> CollisionStreamEvidence:
        return self.collision

    async def clearance_budget(self, runtime: RuntimeBinding) -> ClearanceBudget:
        return self.expected.clearance_budget

    async def clearance_sources(
        self, runtime: RuntimeBinding
    ) -> tuple[ClearanceSourceEvidence, ...]:
        return self.sources


def _assembled_capture(raw: MotionReadinessArtifact) -> MotionReadinessArtifact:
    return raw.model_copy(update={"result": evaluate_motion_readiness(raw)})


def test_concrete_isaac_collector_produces_offline_valid_artifact() -> None:
    expected = _artifact()
    outcome = asyncio.run(
        IsaacMotionReadinessCollector(_FakeIsaacReadOnlyRuntime(expected)).collect()
    )
    assert outcome.artifact is not None
    raw = outcome.artifact
    assembled = _assembled_capture(raw)

    report = validate_motion_readiness_artifact(assembled)
    assert report.valid is True
    assert report.decision == expected.result.decision
    assert raw.collection_failures == ()


def test_collector_missing_collision_stream_produces_valid_block() -> None:
    expected = _artifact()
    source = _FakeIsaacReadOnlyRuntime(
        expected,
        collision=_collision(status=EvidenceStatus.MISSING),
    )
    outcome = asyncio.run(IsaacMotionReadinessCollector(source).collect())
    assert outcome.artifact is not None
    assembled = _assembled_capture(outcome.artifact)

    report = validate_motion_readiness_artifact(assembled)
    assert report.valid is True
    assert report.decision == "BLOCK"
    assert assembled.result is not None
    assert "collision_timeline" in assembled.result.blocking_gates


def test_runtime_observation_limitation_produces_valid_block() -> None:
    expected = _artifact()
    runtime = expected.runtime.model_copy(
        update={"observation_limitations": ("map_identity_not_live_observed",)}
    )
    values = expected.model_dump(mode="json")
    values.update(
        {
            "runtime": runtime.model_dump(mode="json"),
            "runtime_after": runtime.model_dump(mode="json"),
            "authorization": None,
            "result": None,
            "input_sha256": "",
        }
    )
    raw = MotionReadinessArtifact.model_validate(values)
    raw = raw.model_copy(update={"authorization": create_motion_authorization_binding(raw)})
    assembled = _assembled_capture(raw)

    report = validate_motion_readiness_artifact(assembled)
    assert report.valid is True
    assert report.decision == "BLOCK"
    assert assembled.result is not None
    assert "evidence_contract" in assembled.result.blocking_gates


def test_unattested_or_duplicate_costmap_sources_produce_valid_block() -> None:
    expected = _artifact()
    first_topic = expected.costmap_layers[0].source_topic
    layers = tuple(
        CostmapLayerEvidence.create(
            **(
                layer.model_dump(mode="json", exclude={"content_sha256"})
                | {
                    "source_topic": first_topic,
                    "semantic_attestation": "unavailable",
                }
            )
        )
        for layer in expected.costmap_layers
    )
    values = expected.model_dump(mode="json")
    values.update(
        {
            "costmap_layers": [layer.model_dump(mode="json") for layer in layers],
            "authorization": None,
            "result": None,
            "input_sha256": "",
        }
    )
    raw = MotionReadinessArtifact.model_validate(values)
    raw = raw.model_copy(update={"authorization": create_motion_authorization_binding(raw)})
    assembled = _assembled_capture(raw)

    report = validate_motion_readiness_artifact(assembled)
    assert report.valid is True
    assert report.decision == "BLOCK"
    assert assembled.result is not None
    assert "swept_footprint_clearance" in assembled.result.blocking_gates
    assert "costmap layer sources must be distinct" in assembled.result.swept_clearance.failures
    assert any(
        "layer semantics are not attested" in failure
        for failure in assembled.result.swept_clearance.failures
    )


def test_collector_unavailable_tracking_bound_produces_valid_block() -> None:
    expected = _artifact()
    sources = tuple(
        ClearanceSourceEvidence.create(
            evidence_id=source.evidence_id,
            kind=source.kind,
            status=EvidenceStatus.UNAVAILABLE,
            source_assurance=source.source_assurance,
            transport_security=source.transport_security,
            source_timestamp_ns=source.source_timestamp_ns,
            config_sha256=source.config_sha256,
            simulation_epoch=source.simulation_epoch,
            runtime_boot_id=source.runtime_boot_id,
            runtime_fingerprint=source.runtime_fingerprint,
            measurements=source.measurements,
        )
        if source.kind == SafetyTermKind.CONTROLLER_TRACKING_BOUND
        else source
        for source in expected.clearance_sources
    )
    outcome = asyncio.run(
        IsaacMotionReadinessCollector(
            _FakeIsaacReadOnlyRuntime(expected, sources=sources)
        ).collect()
    )
    assert outcome.artifact is not None
    assembled = _assembled_capture(outcome.artifact)

    report = validate_motion_readiness_artifact(assembled)
    assert report.valid is True
    assert report.decision == "BLOCK"
    assert assembled.result is not None
    assert "minimum_safe_clearance_policy" in assembled.result.blocking_gates


def test_collector_runtime_drift_is_preserved_and_blocks() -> None:
    expected = _artifact()
    runtime_after = expected.runtime.model_copy(update={"runtime_boot_id": "boot-2"})
    outcome = asyncio.run(
        IsaacMotionReadinessCollector(
            _FakeIsaacReadOnlyRuntime(expected, runtime_after=runtime_after)
        ).collect()
    )
    assert outcome.artifact is not None
    raw = outcome.artifact
    assembled = _assembled_capture(raw)

    report = validate_motion_readiness_artifact(assembled)
    assert report.valid is False
    assert report.decision == "BLOCK"
    assert raw.runtime_after.runtime_boot_id == "boot-2"
    assert "runtime generation changed during collection: runtime_boot_id" in report.failures


def test_collector_blocks_ros_clock_regression_even_when_epoch_string_is_stale() -> None:
    expected = _artifact()
    runtime_after = expected.runtime.model_copy(
        update={
            "capture_ros_ns": 1,
            "capture_host_monotonic_ns": expected.runtime.capture_host_monotonic_ns + 1_000,
        }
    )
    outcome = asyncio.run(
        IsaacMotionReadinessCollector(
            _FakeIsaacReadOnlyRuntime(expected, runtime_after=runtime_after)
        ).collect()
    )
    assert outcome.artifact is not None
    assembled = _assembled_capture(outcome.artifact)

    report = validate_motion_readiness_artifact(assembled)
    assert report.valid is False
    assert report.decision == "BLOCK"
    assert "runtime ROS clock regressed without an epoch transition" in report.failures


def test_collector_blocks_evidence_age_contract_drift() -> None:
    expected = _artifact()
    runtime_after = expected.runtime_after.model_copy(
        update={"max_evidence_age_ns": expected.runtime.max_evidence_age_ns + 1}
    )
    outcome = asyncio.run(
        IsaacMotionReadinessCollector(
            _FakeIsaacReadOnlyRuntime(expected, runtime_after=runtime_after)
        ).collect()
    )
    assert outcome.artifact is not None
    assembled = _assembled_capture(outcome.artifact)

    report = validate_motion_readiness_artifact(assembled)
    assert report.valid is False
    assert report.decision == "BLOCK"
    assert "runtime generation changed during collection: max_evidence_age_ns" in report.failures


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), 183.0, 0.0, -1.0])
def test_collector_rejects_unbounded_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        IsaacMotionReadinessCollector(_FakeIsaacReadOnlyRuntime(_artifact()), timeout)


def test_collector_timeout_returns_typed_block_outcome() -> None:
    expected = _artifact()

    class HangingSource(_FakeIsaacReadOnlyRuntime):
        async def planned_path(
            self, runtime: RuntimeBinding, request: MotionRequestBinding
        ) -> PathEvidence:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    outcome = asyncio.run(
        IsaacMotionReadinessCollector(HangingSource(expected), operation_timeout_s=0.01).collect()
    )

    assert outcome.status == "blocked"
    assert isinstance(outcome.artifact, BlockedMotionReadinessArtifact)
    assert validate_blocked_collection_artifact(outcome.artifact).valid is True
    assert [(failure.operation, failure.reason) for failure in outcome.failures] == [
        ("planned_path", "timeout")
    ]


def test_collector_source_exception_returns_typed_block_outcome() -> None:
    expected = _artifact()

    class FailingSource(_FakeIsaacReadOnlyRuntime):
        async def costmap_layers(self, runtime: RuntimeBinding) -> tuple[CostmapLayerEvidence, ...]:
            raise RuntimeError("private diagnostic must not be persisted")

    outcome = asyncio.run(IsaacMotionReadinessCollector(FailingSource(expected)).collect())

    assert outcome.status == "blocked"
    assert isinstance(outcome.artifact, BlockedMotionReadinessArtifact)
    assert validate_blocked_collection_artifact(outcome.artifact).valid is True
    assert [
        (failure.operation, failure.reason, failure.exception_type) for failure in outcome.failures
    ] == [("costmap_layers", "source_error", "RuntimeError")]
    assert "private diagnostic" not in outcome.model_dump_json()


def test_collector_post_runtime_timeout_returns_typed_block_outcome() -> None:
    expected = _artifact()

    class PostRuntimeTimeoutSource(_FakeIsaacReadOnlyRuntime):
        async def runtime_binding(self) -> RuntimeBinding:
            self.runtime_reads += 1
            if self.runtime_reads == 1:
                return self.expected.runtime
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    outcome = asyncio.run(
        IsaacMotionReadinessCollector(
            PostRuntimeTimeoutSource(expected), operation_timeout_s=0.01
        ).collect()
    )

    assert outcome.status == "blocked"
    assert isinstance(outcome.artifact, BlockedMotionReadinessArtifact)
    assert validate_blocked_collection_artifact(outcome.artifact).valid is True
    assert [(failure.operation, failure.reason) for failure in outcome.failures] == [
        ("runtime_binding_after", "timeout")
    ]
    completed = dict(outcome.artifact.completed_operation_sha256)
    assert set(completed) == {
        "clearance_budget",
        "clearance_sources",
        "collision_timeline",
        "costmap_layers",
        "effective_nav_footprint",
        "motion_request",
        "planned_path",
        "probe_identity",
        "runtime_binding_before",
        "usd_collision_geometry",
    }


def test_concrete_collector_port_has_no_motion_operation() -> None:
    public_methods = {name for name in dir(_FakeIsaacReadOnlyRuntime) if not name.startswith("_")}
    assert public_methods == {
        "clearance_budget",
        "clearance_sources",
        "collision_timeline",
        "costmap_layers",
        "effective_nav_footprint",
        "motion_request",
        "planned_path",
        "probe_identity",
        "runtime_binding",
        "usd_collision_geometry",
    }


def test_capture_port_exposes_observations_only_and_builds_one_artifact() -> None:
    expected = _artifact()

    outcome = asyncio.run(capture_no_motion_readiness(_FakeIsaacReadOnlyRuntime(expected)))

    assert outcome.status == "captured"
    assert outcome.artifact is not None
    assert outcome.artifact.result is None
    assert outcome.artifact.model_dump(exclude={"result"}) == expected.model_dump(
        exclude={"result"}
    )
