from __future__ import annotations

import asyncio
import concurrent.futures
import json
import math
from pathlib import Path

import pytest

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
    NavFootprintEvidence,
    PathEvidence,
    Point2,
    Point3,
    Polygon2,
    Pose2,
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
)
from jenai.acceptance.motion_safety_capture import capture_no_motion_readiness
from jenai.acceptance.motion_safety_cli import main as readiness_cli


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
) -> MotionReadinessArtifact:
    path = path or (Pose2(x=0.0, y=0.0, yaw=0.0), Pose2(x=1.0, y=0.0, yaw=0.0))
    request = motion_request or _motion_request(start=path[0], goal=path[-1])
    layers = layers or tuple(_layer(kind) for kind in ClearanceLayer)
    raw = MotionReadinessArtifact(
        schema_version=4,
        evidence_derivation_version=4,
        runtime=_binding(),
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
        nav_footprint=NavFootprintEvidence.create(
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


def test_offline_validator_rederives_result_instead_of_trusting_summary(tmp_path: Path) -> None:
    artifact = _artifact()
    payload = artifact.model_dump(mode="json")
    payload["result"]["decision"] = "BLOCK"
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = load_and_validate_motion_readiness(path)

    assert report.valid is False
    assert "stored result differs from re-derived result" in report.failures


def test_offline_validator_rejects_missing_final_input_digest(tmp_path: Path) -> None:
    payload = _artifact().model_dump(mode="json")
    payload["input_sha256"] = ""
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = load_and_validate_motion_readiness(path)

    assert report.valid is False
    assert report.failures == ("final artifact input digest is missing",)


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


def test_capture_port_exposes_observations_only_and_builds_one_artifact() -> None:
    expected = _artifact()

    class Source:
        async def runtime_binding(self) -> RuntimeBinding:
            return expected.runtime

        async def motion_request(self, runtime: RuntimeBinding) -> MotionRequestBinding:
            assert runtime == expected.runtime
            return expected.motion_request

        async def planned_path(
            self, runtime: RuntimeBinding, request: MotionRequestBinding
        ) -> PathEvidence:
            assert runtime == expected.runtime
            assert request == expected.motion_request
            return expected.path

        async def effective_nav_footprint(self, runtime: RuntimeBinding) -> NavFootprintEvidence:
            assert runtime == expected.runtime
            return expected.nav_footprint

        async def usd_collision_geometry(
            self, runtime: RuntimeBinding
        ) -> UsdCollisionGeometryEvidence:
            assert runtime == expected.runtime
            return expected.usd_geometry

        async def costmap_layers(self, runtime: RuntimeBinding) -> tuple[CostmapLayerEvidence, ...]:
            assert runtime == expected.runtime
            return expected.costmap_layers

        async def collision_timeline(self, runtime: RuntimeBinding) -> CollisionStreamEvidence:
            assert runtime == expected.runtime
            return expected.collision_stream

        async def clearance_budget(self, runtime: RuntimeBinding) -> ClearanceBudget:
            assert runtime == expected.runtime
            return expected.clearance_budget

        async def clearance_sources(
            self, runtime: RuntimeBinding
        ) -> tuple[ClearanceSourceEvidence, ...]:
            assert runtime == expected.runtime
            return expected.clearance_sources

    captured = asyncio.run(capture_no_motion_readiness(Source()))

    assert captured.result is None
    assert captured.model_dump(exclude={"result"}) == expected.model_dump(exclude={"result"})
