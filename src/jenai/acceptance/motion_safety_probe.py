"""Repository-owned read-only ROS/Isaac companion for Motion Safety capture."""

from __future__ import annotations

import argparse
import hashlib
import importlib.resources
import json
import math
import os
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from jenai.acceptance.motion_safety import (
    ClearanceBudget,
    ClearanceLayer,
    ClearanceSourceEvidence,
    CollisionEvent,
    CollisionFilterCoverageEvidence,
    CollisionObservationWindow,
    CollisionStreamEvidence,
    CollisionWindowKind,
    CostmapCell,
    CostmapLayerEvidence,
    CostmapRun,
    EvidenceStatus,
    MotionRequestBinding,
    NavFootprintComponent,
    NavFootprintEvidence,
    PathEvidence,
    PlanActionEvidence,
    Point2,
    Point3,
    Polygon2,
    Pose2,
    RuntimeBinding,
    UsdCollisionGeometryEvidence,
    UsdCollisionPrimEvidence,
    UsdSceneCollisionEntry,
    UsdSceneCollisionEnumerationEvidence,
    collision_prim_inventory_sha256,
    costmap_rle_sha256,
    normalize_ros_frame_id,
    offset_convex_polygon,
)

_TRUSTED_GIT = Path("/usr/bin/git")
_MAX_LOCAL_JSON_BYTES = 64 * 1024 * 1024


class _FrozenProbeModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MotionRequestProbeConfig(_FrozenProbeModel):
    authorization_nonce: str
    start: Pose2
    goal: Pose2
    planner_id: str


class NavFootprintProbeConfig(_FrozenProbeModel):
    local_node: str
    global_node: str
    footprint_parameter: str = "footprint"
    padding_parameter: str = "footprint_padding"

    @model_validator(mode="after")
    def validate_distinct_nodes(self) -> Self:
        local = "/" + self.local_node.strip("/")
        global_ = "/" + self.global_node.strip("/")
        if local == global_:
            raise ValueError("local and global costmap nodes must be distinct")
        object.__setattr__(self, "local_node", local)
        object.__setattr__(self, "global_node", global_)
        return self

    @property
    def nodes(self) -> tuple[str, str]:
        return (self.local_node, self.global_node)


class UsdProbeConfig(_FrozenProbeModel):
    scene_path: str
    robot_root_prim: str
    base_frame: str = "base_link"
    stage_export_path: str
    stage_export_sha256: str | None = None


class CollisionStreamProbeConfig(_FrozenProbeModel):
    topic: str
    message_type: str
    qos: Literal["sensor_data", "transient_local", "reliable"]
    robot_root_prim: str
    monitored_prim_paths: tuple[str, ...]
    collision_filter: CollisionFilterCoverageEvidence


class RepositoryProbeConfig(_FrozenProbeModel):
    timeout_s: float = 5.0
    runtime: RuntimeBinding
    motion_request: MotionRequestProbeConfig
    map_frame: str = "map"
    nav_footprint: NavFootprintProbeConfig
    usd: UsdProbeConfig
    costmap_topics: dict[ClearanceLayer, str]
    collision_stream: CollisionStreamProbeConfig
    clearance_budget: ClearanceBudget
    clearance_sources: tuple[ClearanceSourceEvidence, ...]

    @model_validator(mode="after")
    def validate_complete_sources(self) -> Self:
        if set(self.costmap_topics) != set(ClearanceLayer):
            raise ValueError("all four costmap topics must be configured exactly once")
        if not self.clearance_sources:
            raise ValueError("clearance source inventory must not be empty")
        return self


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded_bytes(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_LOCAL_JSON_BYTES:
            raise ValueError("local Evidence JSON exceeds its size limit")
        content = os.read(descriptor, _MAX_LOCAL_JSON_BYTES + 1)
        if len(content) > _MAX_LOCAL_JSON_BYTES or os.read(descriptor, 1):
            raise ValueError("local Evidence JSON exceeds its size limit")
        return content
    finally:
        os.close(descriptor)


def _bounded_json(path: Path) -> object:
    return json.loads(_bounded_bytes(path))


def _stamp_ns(stamp: Any) -> int:
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


def _yaw(orientation: Any) -> float:
    x = float(orientation.x)
    y = float(orientation.y)
    z = float(orientation.z)
    w = float(orientation.w)
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _vector3(value: object) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    return (float(value[0]), float(value[1]), float(value[2]))


def _repository_source_identity(expected_git_sha: str) -> None:
    try:
        manifest_text = (
            importlib.resources.files("jenai")
            .joinpath("_motion_safety_source_manifest.json")
            .read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        manifest_text = ""
    if manifest_text:
        payload = json.loads(manifest_text)
        if payload != {"source_git_sha": expected_git_sha}:
            raise RuntimeError("sealed probe source manifest differs from reviewed Git revision")
        return
    root = Path(__file__).resolve().parents[3]
    head = subprocess.run(
        [str(_TRUSTED_GIT), "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=5.0,
        env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/nonexistent")},
    ).stdout.strip()
    dirty = subprocess.run(
        [str(_TRUSTED_GIT), "status", "--porcelain"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=5.0,
        env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/nonexistent")},
    ).stdout
    if head != expected_git_sha or dirty:
        raise RuntimeError("repository probe source is not the reviewed clean Git revision")


def _polygon(points: Sequence[Mapping[str, Any]]) -> Polygon2:
    return Polygon2(
        vertices=tuple(Point2(x=float(point["x"]), y=float(point["y"])) for point in points)
    )


def _convex_hull(points: list[Point2]) -> Polygon2:
    unique = sorted({(point.x, point.y) for point in points})
    if len(unique) < 3:
        raise ValueError("collision projection does not contain three unique points")

    def cross(
        origin: tuple[float, float],
        left: tuple[float, float],
        right: tuple[float, float],
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
    return Polygon2(vertices=tuple(Point2(x=x, y=y) for x, y in (*lower[:-1], *upper[:-1])))


def _rle(values: list[int]) -> tuple[CostmapRun, ...]:
    if not values:
        raise ValueError("costmap data is empty")
    runs: list[CostmapRun] = []
    current = values[0]
    count = 1
    for value in values[1:]:
        if value == current:
            count += 1
        else:
            runs.append(CostmapRun(cost=current, count=count))
            current = value
            count = 1
    runs.append(CostmapRun(cost=current, count=count))
    return tuple(runs)


class LiveRosObservationBackend:
    """One-shot rclpy observations. It creates no publisher or robot-control client."""

    def __init__(self, timeout_s: float) -> None:
        self.timeout_s = timeout_s

    def _node(self, name: str) -> tuple[Any, Any, Any]:
        import rclpy
        from rclpy.context import Context
        from rclpy.node import Node

        context = Context()
        rclpy.init(context=context)
        return rclpy, context, Node(name, context=context)

    def one_message(
        self,
        topic: str,
        type_name: str,
        qos: Literal["sensor_data", "transient_local", "reliable"] = "reliable",
    ) -> tuple[Any, int]:
        from rclpy.qos import (
            DurabilityPolicy,
            HistoryPolicy,
            QoSProfile,
            ReliabilityPolicy,
            qos_profile_sensor_data,
        )
        from rosidl_runtime_py.utilities import get_message

        rclpy, context, node = self._node("jenai_motion_safety_observer")
        message_type = get_message(type_name)
        received: list[tuple[object, int]] = []
        profile = (
            qos_profile_sensor_data
            if qos == "sensor_data"
            else QoSProfile(
                history=HistoryPolicy.KEEP_LAST,
                depth=10,
                reliability=ReliabilityPolicy.RELIABLE,
                durability=(
                    DurabilityPolicy.TRANSIENT_LOCAL
                    if qos == "transient_local"
                    else DurabilityPolicy.VOLATILE
                ),
            )
        )
        subscription = node.create_subscription(
            message_type,
            topic,
            lambda message: received.append((message, time.monotonic_ns())),
            profile,
        )
        deadline = time.monotonic() + self.timeout_s
        try:
            while not received and time.monotonic() < deadline:
                rclpy.spin_once(node, timeout_sec=min(0.1, deadline - time.monotonic()))
            if not received:
                raise TimeoutError(f"no bounded observation from {topic}")
            return received[-1]
        finally:
            node.destroy_subscription(subscription)
            node.destroy_node()
            rclpy.shutdown(context=context)

    def topic_type(self, topic: str) -> str | None:
        rclpy, context, node = self._node("jenai_motion_safety_graph_observer")
        deadline = time.monotonic() + self.timeout_s
        try:
            while time.monotonic() < deadline:
                names = dict(node.get_topic_names_and_types())
                types = names.get(topic, [])
                if len(types) == 1:
                    return str(types[0])
                rclpy.spin_once(node, timeout_sec=0.1)
            return None
        finally:
            node.destroy_node()
            rclpy.shutdown(context=context)

    def parameters(self, remote_node: str, names: tuple[str, ...]) -> tuple[object, ...]:
        import rclpy
        from rclpy.context import Context
        from rclpy.node import Node
        from rclpy.parameter_client import AsyncParameterClient

        context = Context()
        rclpy.init(context=context)
        node = Node("jenai_motion_safety_parameter_observer", context=context)
        client = AsyncParameterClient(node, remote_node)
        try:
            if not client.wait_for_service(timeout_sec=self.timeout_s):
                raise TimeoutError(f"parameter service is unavailable: {remote_node}")
            future = client.get_parameters(list(names))
            rclpy.spin_until_future_complete(node, future, timeout_sec=self.timeout_s)
            values = future.result()
            if values is None or len(values) != len(names):
                raise TimeoutError(f"parameter query timed out: {remote_node}")
            return tuple(value.value for value in values)
        finally:
            node.destroy_node()
            rclpy.shutdown(context=context)

    @staticmethod
    def _cancel_plan_goal(
        rclpy: Any,
        node: Any,
        handle: Any,
        result_future: Any,
        timeout_s: float,
        canceled_status: int,
    ) -> None:
        cancellation = handle.cancel_goal_async()
        rclpy.spin_until_future_complete(node, cancellation, timeout_sec=timeout_s)
        response = cancellation.result()
        target_uuid = bytes(handle.goal_id.uuid)
        accepted = response is not None and any(
            bytes(item.goal_id.uuid) == target_uuid for item in response.goals_canceling
        )
        if not accepted:
            raise RuntimeError("ComputePathToPose cancellation was not acknowledged")
        rclpy.spin_until_future_complete(node, result_future, timeout_sec=timeout_s)
        terminal = result_future.result()
        if terminal is None or int(terminal.status) != canceled_status:
            raise RuntimeError("ComputePathToPose cancellation did not reach terminal state")

    @staticmethod
    def _decode_plan_result(
        handle: Any,
        result: Any,
        frame_id: str,
        planner_id: str,
        none_error_code: int,
    ) -> tuple[list[Pose2], PlanActionEvidence, int]:
        requested_frame = normalize_ros_frame_id(frame_id)
        error_code = int(result.error_code)
        if error_code != none_error_code:
            raise RuntimeError(
                f"ComputePathToPose succeeded with non-NONE error code: {error_code}"
            )
        path = result.path
        returned_frame = str(path.header.frame_id)
        try:
            if normalize_ros_frame_id(returned_frame) != requested_frame:
                raise RuntimeError("ComputePathToPose returned a different frame")
        except ValueError as exc:
            raise RuntimeError("ComputePathToPose returned an invalid frame") from exc
        pose_frames = tuple(str(item.header.frame_id) for item in path.poses)
        try:
            if any(
                normalize_ros_frame_id(pose_frame) != requested_frame for pose_frame in pose_frames
            ):
                raise RuntimeError("ComputePathToPose pose frame differs from Path header")
        except ValueError as exc:
            raise RuntimeError("ComputePathToPose pose frame differs from Path header") from exc
        poses = [
            Pose2(
                x=float(item.pose.position.x),
                y=float(item.pose.position.y),
                yaw=_yaw(item.pose.orientation),
            )
            for item in path.poses
        ]
        if not poses:
            raise RuntimeError("ComputePathToPose returned an empty path")
        action = PlanActionEvidence(
            planner_id=planner_id,
            requested_frame_id=frame_id,
            returned_path_frame_id=returned_frame,
            pose_frame_ids=pose_frames,
            terminal_status="SUCCEEDED",
            error_code=0,
            goal_uuid=bytes(handle.goal_id.uuid).hex(),
        )
        return poses, action, _stamp_ns(path.header.stamp)

    def compute_path(
        self,
        start: Pose2,
        goal: Pose2,
        frame_id: str,
        planner_id: str,
    ) -> tuple[list[Pose2], PlanActionEvidence, int, int]:
        import rclpy
        from action_msgs.msg import GoalStatus
        from nav2_msgs.action import ComputePathToPose
        from rclpy.action import ActionClient
        from rclpy.context import Context
        from rclpy.node import Node

        normalize_ros_frame_id(frame_id)
        if not planner_id:
            raise ValueError("ComputePathToPose planner identity is empty")
        context = Context()
        rclpy.init(context=context)
        node = Node("jenai_motion_safety_planner", context=context)
        client = ActionClient(node, ComputePathToPose, "/compute_path_to_pose")
        try:
            if not client.wait_for_server(timeout_sec=self.timeout_s):
                raise TimeoutError("ComputePathToPose is unavailable")
            request = ComputePathToPose.Goal()
            request.use_start = True
            request.planner_id = planner_id
            request.start.header.frame_id = frame_id
            request.start.pose.position.x = start.x
            request.start.pose.position.y = start.y
            request.start.pose.orientation.z = math.sin(start.yaw / 2.0)
            request.start.pose.orientation.w = math.cos(start.yaw / 2.0)
            request.goal.header.frame_id = frame_id
            request.goal.pose.position.x = goal.x
            request.goal.pose.position.y = goal.y
            request.goal.pose.orientation.z = math.sin(goal.yaw / 2.0)
            request.goal.pose.orientation.w = math.cos(goal.yaw / 2.0)
            accepted = client.send_goal_async(request)
            rclpy.spin_until_future_complete(node, accepted, timeout_sec=self.timeout_s)
            handle = accepted.result()
            if handle is None or not handle.accepted:
                raise RuntimeError("ComputePathToPose rejected the read-only request")
            result_future = handle.get_result_async()
            rclpy.spin_until_future_complete(node, result_future, timeout_sec=self.timeout_s)
            wrapped = result_future.result()
            if wrapped is None:
                self._cancel_plan_goal(
                    rclpy,
                    node,
                    handle,
                    result_future,
                    self.timeout_s,
                    GoalStatus.STATUS_CANCELED,
                )
                raise TimeoutError("ComputePathToPose result timed out after cancellation")
            if int(wrapped.status) != GoalStatus.STATUS_SUCCEEDED:
                raise RuntimeError("ComputePathToPose did not succeed")
            poses, action, stamp_ns = self._decode_plan_result(
                handle,
                wrapped.result,
                frame_id,
                planner_id,
                int(ComputePathToPose.Result.NONE),
            )
            return poses, action, stamp_ns, time.monotonic_ns()
        finally:
            node.destroy_node()
            rclpy.shutdown(context=context)


class LiveUsdObservationBackend:
    """Query the active composed Isaac Stage and conservatively project collision bounds."""

    def active_stage(self, expected_path: Path) -> Any:
        import omni.usd

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            raise RuntimeError("Isaac has no active live Stage")
        identifier = Path(stage.GetRootLayer().realPath).resolve()
        if identifier != expected_path.resolve():
            raise RuntimeError("active Isaac Stage differs from the reviewed scene")
        return stage

    @staticmethod
    def _local_collision_vertices(prim: Any) -> tuple[str, tuple[Point3, ...]]:
        type_name = str(prim.GetTypeName())
        extents: tuple[tuple[float, float], ...]
        if type_name == "Mesh":
            approximation = str(prim.GetAttribute("physics:approximation").Get() or "none")
            supported = {"none", "convexHull", "convexDecomposition"}
            if approximation not in supported:
                raise RuntimeError(f"unsupported collision mesh approximation: {approximation}")
            points = prim.GetAttribute("points").Get()
            if not points:
                raise RuntimeError("collision mesh has no authored local points")
            return f"Mesh:{approximation}", tuple(
                Point3(x=float(point[0]), y=float(point[1]), z=float(point[2])) for point in points
            )
        if type_name == "Cube":
            half = float(prim.GetAttribute("size").Get()) / 2.0
            extents = ((-half, half), (-half, half), (-half, half))
        elif type_name == "Sphere":
            radius = float(prim.GetAttribute("radius").Get())
            extents = ((-radius, radius),) * 3
        elif type_name in {"Capsule", "Cylinder", "Cone"}:
            radius = float(prim.GetAttribute("radius").Get())
            half = float(prim.GetAttribute("height").Get()) / 2.0
            axis = str(prim.GetAttribute("axis").Get() or "Z").upper()
            axial_extent = half + radius if type_name == "Capsule" else half
            extents_by_axis = {"X": radius, "Y": radius, "Z": radius}
            extents_by_axis[axis] = axial_extent
            extents = tuple(
                (-extents_by_axis[name], extents_by_axis[name]) for name in ("X", "Y", "Z")
            )
        else:
            raise RuntimeError(f"unsupported collision geometry type: {type_name}")
        return type_name, tuple(
            Point3(x=x, y=y, z=z) for x in extents[0] for y in extents[1] for z in extents[2]
        )

    @staticmethod
    def _collision_prims(stage: Any, usd: Any, usd_physics: Any) -> tuple[Any, ...]:
        predicate = usd.TraverseInstanceProxies()
        observed = []
        for prim in stage.Traverse(predicate):
            if not prim.HasAPI(usd_physics.CollisionAPI):
                continue
            enabled = usd_physics.CollisionAPI(prim).GetCollisionEnabledAttr().Get()
            if enabled is False:
                continue
            observed.append(prim)
        return tuple(observed)

    @staticmethod
    def _base_transform_contract(
        root: Any,
        prim: Any,
        cache: Any,
        meters_per_unit: float,
    ) -> tuple[tuple[float, ...], tuple[float, float, float]]:
        world_from_prim = cache.GetLocalToWorldTransform(prim)
        base_from_world = cache.GetLocalToWorldTransform(root).GetInverse()

        def transform(x: float, y: float, z: float) -> tuple[float, float, float]:
            point = base_from_world.Transform(world_from_prim.Transform((x, y, z)))
            return (float(point[0]), float(point[1]), float(point[2]))

        origin = transform(0.0, 0.0, 0.0)
        columns = []
        for unit in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)):
            endpoint = transform(*unit)
            columns.append(tuple(endpoint[index] - origin[index] for index in range(3)))
        scale = tuple(math.sqrt(sum(value * value for value in column)) for column in columns)
        if any(value <= 0.0 for value in scale):
            raise RuntimeError("collision transform contains a non-positive scale")
        normalized = tuple(
            tuple(
                column[index] / scale[column_index] for column_index, column in enumerate(columns)
            )
            for index in range(3)
        )
        for left, right in (
            (normalized[0], normalized[1]),
            (normalized[0], normalized[2]),
            (normalized[1], normalized[2]),
        ):
            if not math.isclose(
                sum(a * b for a, b in zip(left, right, strict=True)), 0.0, abs_tol=1e-7
            ):
                raise RuntimeError("collision transform contains shear")
        determinant = (
            normalized[0][0]
            * (normalized[1][1] * normalized[2][2] - normalized[1][2] * normalized[2][1])
            - normalized[0][1]
            * (normalized[1][0] * normalized[2][2] - normalized[1][2] * normalized[2][0])
            + normalized[0][2]
            * (normalized[1][0] * normalized[2][1] - normalized[1][1] * normalized[2][0])
        )
        if not math.isclose(determinant, 1.0, abs_tol=1e-7):
            raise RuntimeError("collision transform is reflected or non-rigid")
        matrix = (
            normalized[0][0],
            normalized[0][1],
            normalized[0][2],
            origin[0] * meters_per_unit,
            normalized[1][0],
            normalized[1][1],
            normalized[1][2],
            origin[1] * meters_per_unit,
            normalized[2][0],
            normalized[2][1],
            normalized[2][2],
            origin[2] * meters_per_unit,
            0.0,
            0.0,
            0.0,
            1.0,
        )
        return matrix, (float(scale[0]), float(scale[1]), float(scale[2]))

    @staticmethod
    def _project_collision_vertices(
        vertices: tuple[Point3, ...],
        matrix: tuple[float, ...],
        scale: tuple[float, float, float],
        meters_per_unit: float,
    ) -> Polygon2:
        points = []
        for vertex in vertices:
            x = vertex.x * scale[0] * meters_per_unit
            y = vertex.y * scale[1] * meters_per_unit
            z = vertex.z * scale[2] * meters_per_unit
            points.append(
                Point2(
                    x=matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
                    y=matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
                )
            )
        return _convex_hull(points)

    def collision_geometry(
        self,
        config: UsdProbeConfig,
        runtime: RuntimeBinding,
    ) -> UsdCollisionGeometryEvidence:
        from pxr import Usd, UsdGeom, UsdPhysics

        scene_path = Path(config.scene_path)
        stage = self.active_stage(scene_path)
        root_path = config.robot_root_prim
        root = stage.GetPrimAtPath(root_path)
        if not root.IsValid():
            raise RuntimeError("configured robot root prim is missing")
        cache = UsdGeom.XformCache()
        meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage))
        robot_prims: list[UsdCollisionPrimEvidence] = []
        robot_points: list[Point2] = []
        counterparts: list[UsdSceneCollisionEntry] = []
        for prim in self._collision_prims(stage, Usd, UsdPhysics):
            path = str(prim.GetPath())
            if path == root_path or path.startswith(root_path + "/"):
                if path == root_path:
                    raise RuntimeError("robot root cannot itself be the collision geometry")
                shape_type, local_vertices = self._local_collision_vertices(prim)
                matrix, scale = self._base_transform_contract(root, prim, cache, meters_per_unit)
                projected = self._project_collision_vertices(
                    local_vertices, matrix, scale, meters_per_unit
                )
                robot_points.extend(projected.vertices)
                identity_payload = {
                    "prim_path": path,
                    "shape_type": shape_type,
                    "local_vertices": [vertex.model_dump(mode="json") for vertex in local_vertices],
                }
                robot_prims.append(
                    UsdCollisionPrimEvidence(
                        prim_path=path,
                        shape_type=shape_type,
                        mesh_identity=hashlib.sha256(
                            json.dumps(
                                identity_payload,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode()
                        ).hexdigest(),
                        local_geometry_vertices=local_vertices,
                        base_from_prim_transform=matrix,
                        scale=scale,
                        transform_convention="row_major_affine_column_vector",
                        transform_translation_unit="m",
                        transform_includes_scale=False,
                        projected_base_hull=projected,
                    )
                )
            else:
                counterparts.append(
                    UsdSceneCollisionEntry(
                        prim_path=path,
                        category="environment",
                        collision_enabled=True,
                    )
                )
        if not robot_prims or not counterparts:
            raise RuntimeError("live Stage collision inventory is incomplete")
        counterparts_tuple = tuple(
            sorted(counterparts, key=lambda entry: (entry.prim_path, entry.category))
        )
        counterpart_paths = tuple(sorted({entry.prim_path for entry in counterparts_tuple}))
        counterpart_categories = tuple(sorted({entry.category for entry in counterparts_tuple}))
        enumeration = UsdSceneCollisionEnumerationEvidence.create(
            source="isaac_usd_stage_query",
            query_name="collision_enabled_scene_prims_v1",
            scene_sha256=runtime.scene_sha256,
            source_timestamp_ns=runtime.capture_ros_ns,
            reported_count=len(counterparts_tuple),
            complete_attestation=True,
            entries=counterparts_tuple,
        )
        return UsdCollisionGeometryEvidence.create(
            evidence_id="live-usd-collision-geometry",
            scene_path=str(scene_path),
            scene_sha256=runtime.scene_sha256,
            robot_root_prim=root_path,
            base_frame=config.base_frame,
            simulation_epoch=runtime.simulation_epoch,
            runtime_boot_id=runtime.runtime_boot_id,
            runtime_fingerprint=runtime.runtime_fingerprint,
            nav2_params_sha256=runtime.nav2_params_sha256,
            axis_convention="x_forward_y_left_yaw_ccw",
            meters_per_unit=meters_per_unit,
            source_timestamp_ns=runtime.capture_ros_ns,
            collision_prim_inventory_complete=True,
            scene_collision_enumeration=enumeration,
            collision_enabled_counterpart_prim_paths=counterpart_paths,
            collision_enabled_counterpart_categories=counterpart_categories,
            collision_prims=tuple(sorted(robot_prims, key=lambda item: item.prim_path)),
            projected_base_hull=_convex_hull(robot_points),
            source_assurance="simulator_stage_query",
            transport_security="local_process",
        )


class ExportedUsdObservationBackend:
    """Load the create-once Stage Evidence exported inside the live Isaac process."""

    def collision_geometry(
        self,
        config: UsdProbeConfig,
        runtime: RuntimeBinding,
    ) -> UsdCollisionGeometryEvidence:
        export_path = Path(config.stage_export_path)
        if not export_path.is_absolute():
            raise ValueError("Isaac Stage export path must be absolute")
        if config.stage_export_sha256 is None:
            raise RuntimeError("Isaac Stage export digest is not bound")
        content = _bounded_bytes(export_path)
        if hashlib.sha256(content).hexdigest() != config.stage_export_sha256:
            raise RuntimeError("Isaac Stage export digest differs from reviewed config")
        evidence = UsdCollisionGeometryEvidence.model_validate(json.loads(content))
        if (
            evidence.scene_path != runtime.scene_path
            or evidence.scene_sha256 != runtime.scene_sha256
            or evidence.simulation_epoch != runtime.simulation_epoch
            or evidence.runtime_boot_id != runtime.runtime_boot_id
            or evidence.runtime_fingerprint != runtime.runtime_fingerprint
            or evidence.nav2_params_sha256 != runtime.nav2_params_sha256
            or abs(evidence.source_timestamp_ns - runtime.capture_ros_ns)
            > runtime.max_evidence_age_ns
        ):
            raise RuntimeError("Isaac Stage export is stale or binds another runtime")
        return evidence


class RepositoryIsaacProbe:
    def __init__(
        self,
        config_path: Path,
        *,
        ros_backend: Any | None = None,
        usd_backend: Any | None = None,
    ) -> None:
        self.config_path = config_path
        payload = _bounded_json(config_path)
        if not isinstance(payload, dict):
            raise ValueError("motion readiness config must be a JSON object")
        self.config = RepositoryProbeConfig.model_validate(payload)
        timeout_s = self.config.timeout_s
        if not math.isfinite(timeout_s) or not 0.0 < timeout_s <= 30.0:
            raise ValueError("probe timeout must be within (0, 30] seconds")
        self.ros = ros_backend or LiveRosObservationBackend(timeout_s)
        self.usd = usd_backend or ExportedUsdObservationBackend()

    def runtime_binding(self) -> RuntimeBinding:
        values = self.config.runtime.model_dump(mode="json")
        _repository_source_identity(str(values["git_sha"]))
        scene_path = Path(values["scene_path"])
        if _sha256_file(scene_path) != values["scene_sha256"]:
            raise RuntimeError("reviewed scene digest differs from the live source file")
        clock_message, host_ns = self.ros.one_message(
            "/clock", "rosgraph_msgs/msg/Clock", "sensor_data"
        )
        values["capture_ros_ns"] = _stamp_ns(clock_message.clock)
        values["capture_host_monotonic_ns"] = host_ns
        configured_limitations = tuple(values.get("observation_limitations", ()))
        values["observation_limitations"] = tuple(
            sorted(
                set(configured_limitations)
                | {
                    "clearance_sources_not_live_observed",
                    "collision_filter_identity_not_live_observed",
                    "map_identity_not_live_observed",
                    "nav2_params_identity_not_live_observed",
                    "runtime_boot_id_not_live_observed",
                    "runtime_fingerprint_not_live_observed",
                    "simulation_epoch_not_live_observed",
                }
            )
        )
        return RuntimeBinding.model_validate(values)

    def motion_request(self, runtime: RuntimeBinding) -> MotionRequestBinding:
        request = self.config.motion_request
        half_window = runtime.max_evidence_age_ns // 2
        return MotionRequestBinding.create(
            authorization_nonce=request.authorization_nonce,
            site_id=runtime.site_id,
            start=request.start,
            goal=request.goal,
            planner_id=request.planner_id,
            planner_config_sha256=runtime.planner_config_sha256,
            nav2_params_sha256=runtime.nav2_params_sha256,
            product_config_sha256=runtime.product_config_sha256,
            scene_sha256=runtime.scene_sha256,
            map_sha256=runtime.map_sha256,
            runtime_fingerprint=runtime.runtime_fingerprint,
            collision_filter_sha256=runtime.collision_filter_sha256,
            simulation_epoch=runtime.simulation_epoch,
            runtime_boot_id=runtime.runtime_boot_id,
            valid_from_ros_ns=runtime.capture_ros_ns - half_window,
            valid_until_ros_ns=runtime.capture_ros_ns + half_window,
            valid_from_host_monotonic_ns=runtime.capture_host_monotonic_ns - half_window,
            valid_until_host_monotonic_ns=runtime.capture_host_monotonic_ns + half_window,
        )

    def planned_path(
        self,
        runtime: RuntimeBinding,
        request: MotionRequestBinding,
    ) -> PathEvidence:
        poses, plan_action, stamp_ns, host_ns = self.ros.compute_path(
            request.start,
            request.goal,
            self.config.map_frame,
            request.planner_id,
        )
        return PathEvidence.create(
            evidence_id="live-compute-path-to-pose",
            frame_id=plan_action.returned_path_frame_id,
            plan_action=plan_action,
            source_timestamp_ns=stamp_ns,
            received_host_monotonic_ns=host_ns,
            map_sha256=runtime.map_sha256,
            runtime_fingerprint=runtime.runtime_fingerprint,
            simulation_epoch=runtime.simulation_epoch,
            runtime_boot_id=runtime.runtime_boot_id,
            motion_request_sha256=request.content_sha256,
            nav2_params_sha256=runtime.nav2_params_sha256,
            poses=tuple(poses),
        )

    def effective_nav_footprint(self, runtime: RuntimeBinding) -> NavFootprintEvidence:
        footprint_config = self.config.nav_footprint
        footprint_name = footprint_config.footprint_parameter
        padding_name = footprint_config.padding_parameter
        components = []
        for node_name in footprint_config.nodes:
            raw_polygon, raw_padding, raw_frame = self.ros.parameters(
                node_name,
                (footprint_name, padding_name, "robot_base_frame"),
            )
            if not isinstance(raw_polygon, str):
                raise RuntimeError("live Nav2 footprint parameter is not a string polygon")
            decoded = json.loads(raw_polygon)
            if not isinstance(decoded, list):
                raise RuntimeError("live Nav2 footprint parameter is not a polygon list")
            if not isinstance(raw_padding, (int, float, str)):
                raise RuntimeError("live Nav2 footprint padding is not numeric")
            if not isinstance(raw_frame, str) or not raw_frame.strip("/"):
                raise RuntimeError("live Nav2 robot_base_frame is unavailable")
            configured = Polygon2(
                vertices=tuple(Point2(x=float(point[0]), y=float(point[1])) for point in decoded)
            )
            padding = float(raw_padding)
            components.append(
                NavFootprintComponent(
                    source=f"live-parameters:{node_name}",
                    frame_id=raw_frame.strip("/"),
                    configured_polygon=configured,
                    footprint_padding_m=padding,
                    effective_polygon=offset_convex_polygon(configured, padding),
                )
            )
        frames = {component.frame_id for component in components}
        if len(frames) != 1:
            raise RuntimeError("Nav2 costmaps disagree on robot_base_frame")
        combined = _convex_hull(
            [point for component in components for point in component.effective_polygon.vertices]
        )
        configured_union = _convex_hull(
            [point for component in components for point in component.configured_polygon.vertices]
        )
        clock, host_ns = self.ros.one_message("/clock", "rosgraph_msgs/msg/Clock", "sensor_data")
        return NavFootprintEvidence.create(
            evidence_id="live-effective-nav-footprints",
            frame_id=next(iter(frames)),
            source="live-parameters:global-and-local-costmaps",
            source_timestamp_ns=_stamp_ns(clock.clock),
            received_host_monotonic_ns=host_ns,
            nav2_params_sha256=runtime.nav2_params_sha256,
            runtime_fingerprint=runtime.runtime_fingerprint,
            simulation_epoch=runtime.simulation_epoch,
            runtime_boot_id=runtime.runtime_boot_id,
            axis_convention="x_forward_y_left_yaw_ccw",
            footprint_padding_m=max(component.footprint_padding_m for component in components),
            padding_applied=True,
            configured_polygon=configured_union,
            polygon=combined,
            components=tuple(components),
        )

    def usd_collision_geometry(
        self,
        runtime: RuntimeBinding,
    ) -> UsdCollisionGeometryEvidence:
        return self.usd.collision_geometry(self.config.usd, runtime)

    @staticmethod
    def _costmap_origin(metadata: Any) -> Point2:
        orientation = metadata.origin.orientation
        quaternion = tuple(
            float(value) for value in (orientation.x, orientation.y, orientation.z, orientation.w)
        )
        if not all(math.isfinite(value) for value in quaternion):
            raise RuntimeError("costmap origin quaternion is non-finite")
        norm = math.sqrt(sum(value * value for value in quaternion))
        if not math.isclose(norm, 1.0, abs_tol=1e-9):
            raise RuntimeError("costmap origin quaternion is not normalized")
        x, y, z, w = quaternion
        if not (
            math.isclose(x, 0.0, abs_tol=1e-9)
            and math.isclose(y, 0.0, abs_tol=1e-9)
            and math.isclose(z, 0.0, abs_tol=1e-9)
            and math.isclose(abs(w), 1.0, abs_tol=1e-9)
        ):
            raise RuntimeError("rotated costmap origins are unsupported and unsafe")
        return Point2(
            x=float(metadata.origin.position.x),
            y=float(metadata.origin.position.y),
        )

    def costmap_layers(
        self,
        runtime: RuntimeBinding,
    ) -> tuple[CostmapLayerEvidence, ...]:
        layers: list[CostmapLayerEvidence] = []
        for kind in ClearanceLayer:
            topic = self.config.costmap_topics[kind]
            message, host_ns = self.ros.one_message(
                topic, "nav2_msgs/msg/Costmap", "transient_local"
            )
            values = [int(value) for value in message.data]
            width = int(message.metadata.size_x)
            height = int(message.metadata.size_y)
            resolution = float(message.metadata.resolution)
            origin = self._costmap_origin(message.metadata)
            runs = _rle(values)
            cells: list[CostmapCell] = []
            for index, cost in enumerate(values):
                belongs = (
                    cost == 254
                    if kind in {ClearanceLayer.STATIC_LETHAL, ClearanceLayer.LIVE_OBSTACLE}
                    else 1 <= cost <= 253
                    if kind == ClearanceLayer.STATIC_INFLATION
                    else cost == 255
                )
                if belongs:
                    column = index % width
                    row = index // width
                    cells.append(
                        CostmapCell(
                            x=origin.x + (column + 0.5) * resolution,
                            y=origin.y + (row + 0.5) * resolution,
                            cost=cost,
                        )
                    )
            layers.append(
                CostmapLayerEvidence.create(
                    evidence_id=f"live-{kind.value}-costmap",
                    layer=kind,
                    frame_id=str(message.header.frame_id).lstrip("/"),
                    source_topic=topic,
                    source_message_type="nav2_msgs/msg/Costmap",
                    semantic_attestation="unavailable",
                    resolution_m=resolution,
                    origin=origin,
                    width=width,
                    height=height,
                    source_stamp_ns=_stamp_ns(message.header.stamp),
                    received_host_monotonic_ns=host_ns,
                    map_sha256=runtime.map_sha256,
                    runtime_fingerprint=runtime.runtime_fingerprint,
                    simulation_epoch=runtime.simulation_epoch,
                    runtime_boot_id=runtime.runtime_boot_id,
                    status=EvidenceStatus.OBSERVED,
                    cells=tuple(cells),
                    raw_costs_rle=runs,
                    raw_grid_sha256=costmap_rle_sha256(runs),
                )
            )
        return tuple(layers)

    def collision_timeline(self, runtime: RuntimeBinding) -> CollisionStreamEvidence:
        values = self.config.collision_stream
        topic = values.topic
        message_type = values.message_type
        monitored = tuple(sorted(values.monitored_prim_paths))
        collision_filter = values.collision_filter
        detected_type = self.ros.topic_type(topic)
        windows: tuple[CollisionObservationWindow, ...] = ()
        status = EvidenceStatus.MISSING
        source_capable = False
        clock_aligned = False
        presence = detected_type == message_type
        if presence:
            try:
                message, host_ns = self.ros.one_message(topic, message_type, values.qos)
            except TimeoutError:
                # Topic discovery without a fresh timestamped message is explicit
                # STALE Evidence. It produces a reconstructible BLOCK artifact.
                host_ns = time.monotonic_ns()
                windows = (
                    CollisionObservationWindow(
                        kind=CollisionWindowKind.PRE_DISPATCH,
                        observed_from_ros_ns=runtime.capture_ros_ns,
                        observed_until_ros_ns=runtime.capture_ros_ns,
                        observed_from_host_monotonic_ns=runtime.capture_host_monotonic_ns,
                        observed_until_host_monotonic_ns=host_ns,
                        raw_messages=(),
                    ),
                )
                status = EvidenceStatus.STALE
            else:
                from rosidl_runtime_py.convert import message_to_ordereddict

                raw = dict(message_to_ordereddict(message))
                header = raw.get("header")
                if isinstance(header, dict) and isinstance(header.get("stamp"), dict):
                    stamp = header["stamp"]
                    ros_ns = int(stamp["sec"]) * 1_000_000_000 + int(stamp["nanosec"])
                    source_capable = True
                    clock_aligned = (
                        abs(ros_ns - runtime.capture_ros_ns) <= runtime.max_evidence_age_ns
                    )
                    event = CollisionEvent(
                        ros_stamp_ns=ros_ns,
                        host_monotonic_ns=host_ns,
                        simulation_epoch=runtime.simulation_epoch,
                        runtime_boot_id=runtime.runtime_boot_id,
                        prim_a=raw.get("prim_a"),
                        prim_b=raw.get("prim_b"),
                        contact_point=_vector3(raw.get("contact_point")),
                        contact_normal=_vector3(raw.get("contact_normal")),
                        penetration_m=raw.get("penetration_m"),
                        impulse_ns=raw.get("impulse_ns"),
                        raw_message=raw,
                    )
                    windows = (
                        CollisionObservationWindow(
                            kind=CollisionWindowKind.PRE_DISPATCH,
                            observed_from_ros_ns=runtime.capture_ros_ns,
                            observed_until_ros_ns=ros_ns,
                            observed_from_host_monotonic_ns=(runtime.capture_host_monotonic_ns),
                            observed_until_host_monotonic_ns=host_ns,
                            raw_messages=(event,),
                        ),
                    )
                    status = EvidenceStatus.OBSERVED
        return CollisionStreamEvidence.create(
            evidence_id="live-collision-stream",
            topic=topic,
            message_type=message_type,
            qos=values.qos,
            status=status,
            source_timestamp_capable=source_capable,
            clock_aligned=clock_aligned,
            stream_presence_attested=presence,
            phase="no_motion",
            simulation_epoch=runtime.simulation_epoch,
            runtime_boot_id=runtime.runtime_boot_id,
            source_assurance="unknown",
            transport_security="local_process",
            scene_sha256=runtime.scene_sha256,
            map_sha256=runtime.map_sha256,
            nav2_params_sha256=runtime.nav2_params_sha256,
            runtime_fingerprint=runtime.runtime_fingerprint,
            robot_root_prim=values.robot_root_prim,
            monitored_prim_paths=monitored,
            monitored_prim_inventory_sha256=collision_prim_inventory_sha256(monitored),
            collision_filter=collision_filter,
            windows=windows,
        )

    def clearance_budget(self) -> ClearanceBudget:
        return self.config.clearance_budget

    def clearance_sources(self) -> tuple[ClearanceSourceEvidence, ...]:
        configured = self.config.clearance_sources
        return tuple(
            ClearanceSourceEvidence.create(
                **source.model_dump(mode="python", exclude={"content_sha256", "status"}),
                status=EvidenceStatus.UNAVAILABLE,
            )
            for source in configured
        )

    def execute(self, operation: str, context: dict[str, object]) -> object:
        if operation == "runtime_binding":
            return self.runtime_binding()
        if "runtime" not in context:
            raise ValueError("runtime context is required")
        runtime = RuntimeBinding.model_validate(context["runtime"])
        handlers: dict[str, Callable[[], object]] = {
            "motion_request": lambda: self.motion_request(runtime),
            "planned_path": lambda: self.planned_path(
                runtime, MotionRequestBinding.model_validate(context["motion_request"])
            ),
            "effective_nav_footprint": lambda: self.effective_nav_footprint(runtime),
            "usd_collision_geometry": lambda: self.usd_collision_geometry(runtime),
            "costmap_layers": lambda: self.costmap_layers(runtime),
            "collision_timeline": lambda: self.collision_timeline(runtime),
            "clearance_budget": self.clearance_budget,
            "clearance_sources": self.clearance_sources,
        }
        try:
            return handlers[operation]()
        except KeyError as exc:
            raise ValueError("unsupported observation operation") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation", required=True)
    parser.add_argument("--config", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    context_bytes = sys.stdin.buffer.read(_MAX_LOCAL_JSON_BYTES + 1)
    if len(context_bytes) > _MAX_LOCAL_JSON_BYTES:
        raise ValueError("probe context exceeds its size limit")
    context = json.loads(context_bytes)
    if not isinstance(context, dict):
        raise ValueError("probe context must be a JSON object")
    result = RepositoryIsaacProbe(args.config).execute(args.operation, context)
    payload: Any
    if isinstance(result, tuple):
        payload = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in result
        ]
    elif hasattr(result, "model_dump"):
        payload = result.model_dump(mode="json")
    else:
        payload = result
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
