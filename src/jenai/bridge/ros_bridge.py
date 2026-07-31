#!/usr/bin/env python3
"""JenAI ROS bridge — runs under the SYSTEM python (which has rclpy), not the venv.

Speaks newline-delimited JSON over stdin/stdout:

  request:  {"id": 1, "op": "pose", ...params}
  response: {"id": 1, "ok": true, "result": {...}} | {"id": 1, "ok": false, "error": "..."}
  event:    {"event": "nav_feedback", ...}   (unsolicited, e.g. Nav2 progress)

This file must stay importable by a bare system python: standard library +
ROS packages only — never import jenai (the venv is not visible here).

Ops: ping, pose, map_cell, nav_plan, nav_send, drive_to_pose, nav_cancel,
request_nomotion_update, halt, capture_frame, watch, unwatch, shutdown.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import rclpy

if TYPE_CHECKING:
    # Relative imports give mypy the real helper signatures. Runtime uses the
    # bare siblings below because this file is launched as a system-Python script.
    from ._latched_observation import LatchedObservation
    from ._nav_plan import path_plan_payload
    from ._navigation_state import (
        NavigationGenerations,
        NavigationGoalToken,
        PoseJumpGuard,
        cancellation_is_confirmed,
        localization_halt_terminal,
        navigation_active,
        resolve_navigation_terminal,
        wait_for_cancel_acknowledgement,
    )
    from ._node_identity import bridge_node_name
    from ._occupancy import occupancy_grid_identity, sample_occupancy_cell
    from ._protocol import dispatch_request
    from ._runtime_identity import build_runtime_identity_payload
    from ._server import serve_requests
    from ._watchdog import WatchdogState
else:
    from _latched_observation import LatchedObservation
    from _nav_plan import path_plan_payload
    from _navigation_state import (
        NavigationGenerations,
        NavigationGoalToken,
        PoseJumpGuard,
        cancellation_is_confirmed,
        localization_halt_terminal,
        navigation_active,
        resolve_navigation_terminal,
        wait_for_cancel_acknowledgement,
    )
    from _node_identity import bridge_node_name
    from _occupancy import occupancy_grid_identity, sample_occupancy_cell
    from _protocol import dispatch_request
    from _runtime_identity import build_runtime_identity_payload
    from _server import serve_requests
    from _watchdog import WatchdogState
from rclpy.action import ActionClient
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSPresetProfiles, QoSProfile, ReliabilityPolicy
from rclpy.utilities import get_rmw_implementation_identifier

_STDOUT_LOCK = threading.Lock()
WirePayload = dict[str, Any]


def _emit(payload: WirePayload) -> None:
    with _STDOUT_LOCK:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()


def _new_frame_path(suffix: str) -> str:
    """A fresh temp file for one captured frame (caller deletes after use)."""
    fd, path = tempfile.mkstemp(prefix="jenai_frame_", suffix=suffix)
    os.close(fd)
    return path


def _yaw_from_quaternion(q: Any) -> float:
    # yaw (z-rotation) from quaternion; robots here move in the plane.
    siny = 2.0 * (q.w * q.z + q.x * q.y)
    cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny, cosy)


@dataclass(slots=True)
class _NavigationFeedbackRecorder:
    """Translate Nav2 feedback while retaining endpoint-verification evidence."""

    frame_id: str
    tag: str
    started: float
    final_pose: WirePayload | None = None

    def __call__(self, feedback: Any) -> None:
        details = feedback.feedback
        current = details.current_pose
        self.final_pose = {
            "x": float(current.pose.position.x),
            "y": float(current.pose.position.y),
            "yaw": _yaw_from_quaternion(current.pose.orientation),
            "frame_id": current.header.frame_id or self.frame_id,
            "source": "nav2_feedback",
        }
        _emit(
            {
                "event": "nav_feedback",
                "tag": self.tag,
                "distance_remaining": round(details.distance_remaining, 2),
                "recoveries": details.number_of_recoveries,
                "elapsed": round(time.monotonic() - self.started, 1),
            }
        )

    def snapshot(self) -> WirePayload | None:
        return dict(self.final_pose) if self.final_pose is not None else None


class BridgeNode(Node):  # type: ignore[misc]  # rclpy ships no typing metadata
    def __init__(self) -> None:
        # A TUI may keep a read-only bridge alive while a workflow starts its
        # own navigation/camera sidecars. Reusing one ROS node name makes
        # diagnostics ambiguous and can hide which process owns a publisher.
        super().__init__(bridge_node_name())
        self._nav_client: ActionClient | None = None
        self._plan_client: ActionClient | None = None
        self._nav_goal_handle = None
        self._nav_goal_handle_token: NavigationGoalToken | None = None
        self._nav_generations = NavigationGenerations()
        # True from nav_send until the acceptance callback runs: a goal in
        # this window has no handle yet but MUST still count as active, or a
        # halt/EOF right after nav_send would skip the cancel entirely.
        self._nav_pending = False
        self._cancel_on_accept_token: NavigationGoalToken | None = None
        self._watches: dict[int, object] = {}  # watch_id -> subscription
        # halt is callable from the stdin loop AND the watchdog thread; the
        # lock serializes them (rclpy entity churn from two non-executor
        # threads at once is not safe). Publishers are cached so an emergency
        # pulse never races DDS discovery on a freshly created publisher.
        self._halt_lock = threading.Lock()
        self._halt_publishers: dict[tuple[str, bool], object] = {}
        # Nav2-less point-to-point driver (open ground / ground-plane testing):
        # closed loop on /odom → /cmd_vel. Only one runs at a time.
        self._drive_cancel = threading.Event()
        self._drive_active = False
        # Fail-closed map-localization guard, armed only for this bridge's
        # active Nav2 goal. A fault tag suppresses Nav2's later canceled event
        # so clients receive the specific safety reason exactly once.
        self._pose_jump_guard = PoseJumpGuard()
        self._pose_jump_subscription = None
        self._localization_fault_tokens: set[NavigationGoalToken] = set()
        self._pose_jump_cmd_vel_topic = "/cmd_vel"
        self._pose_jump_stamped = False
        self._map_observation: LatchedObservation[Any] = LatchedObservation()
        self._map_subscription = None
        self._tf_buffer = None
        self._tf_listener = None
        self._ensure_tf_listener()

    def configure_pose_jump_guard(
        self,
        threshold_m: float,
        window_s: float,
        cmd_vel_topic: str,
        stamped: bool,
    ) -> WirePayload:
        """Configure the fail-closed AMCL discontinuity guard."""
        self._pose_jump_guard.configure(threshold_m=threshold_m, window_s=window_s)
        self._pose_jump_cmd_vel_topic = cmd_vel_topic
        self._pose_jump_stamped = stamped
        self._ensure_pose_jump_subscription()
        return {
            "pose_jump_threshold_m": float(threshold_m),
            "pose_jump_window_s": float(window_s),
        }

    def _ensure_pose_jump_subscription(self) -> None:
        if self._pose_jump_subscription is not None:
            return
        from geometry_msgs.msg import PoseWithCovarianceStamped
        from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy

        qos = QoSProfile(
            depth=1,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._pose_jump_subscription = self.create_subscription(
            PoseWithCovarianceStamped,
            "/amcl_pose",
            self._observe_amcl_pose,
            qos,
        )

    def _observe_amcl_pose(self, msg: Any) -> None:
        """Fail closed when map localization moves implausibly between samples."""
        pose = msg.pose.pose.position
        jump = self._pose_jump_guard.observe(pose.x, pose.y)
        if jump is None:
            return

        token = jump.token
        if not isinstance(token, NavigationGoalToken):
            # nav_send always arms with a token. Keep this defensive branch
            # fail-closed if a future caller arms the guard directly.
            token = self._nav_generations.active
            if token is None:
                return
        self._localization_fault_tokens.add(token)
        if math.isfinite(jump.distance_m):
            reason = (
                "Localization safety stop: /amcl_pose jumped "
                f"{jump.distance_m:.2f} m in {jump.elapsed_s:.2f} s "
                f"(limit {jump.threshold_m:.2f} m)."
            )
        else:
            reason = "Localization safety stop: /amcl_pose contained a non-finite position."

        def _halt_after_jump() -> None:
            try:
                halt_result = self.halt(
                    self._pose_jump_cmd_vel_topic,
                    self._pose_jump_stamped,
                )
                status, detail = localization_halt_terminal(
                    reason,
                    cancel_acknowledged=bool(halt_result.get("active_nav_canceled")),
                )
            except Exception as exc:
                status, detail = localization_halt_terminal(
                    reason,
                    cancel_acknowledged=False,
                    error=exc,
                )
            _emit(
                {
                    "event": "nav_result",
                    "tag": token.tag,
                    "status": status,
                    "reason": detail,
                }
            )

        threading.Thread(target=_halt_after_jump, daemon=True).start()

    # -- pose ---------------------------------------------------------------

    def _ensure_tf_listener(self) -> None:
        """Start buffering TF before a short navigation goal can finish."""
        from tf2_ros import Buffer, TransformListener

        if self._tf_buffer is None:
            self._tf_buffer = Buffer()
            self._tf_listener = TransformListener(self._tf_buffer, self, spin_thread=False)

    def _fresh_tf_pose(self, frame_id: str, base_frame: str, timeout: float) -> WirePayload:
        """Return the latest transform used by Nav2 for terminal-pose checks."""
        from rclpy.duration import Duration
        from rclpy.time import Time

        self._ensure_tf_listener()
        tf_buffer = self._tf_buffer
        if tf_buffer is None:
            raise RuntimeError("TF listener initialization did not provide a buffer")

        deadline = time.monotonic() + timeout

        def _stamp_ns(value: Any) -> int:
            stamp = value.header.stamp
            return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)

        try:
            initial = tf_buffer.lookup_transform(
                frame_id,
                base_frame,
                Time(),
                timeout=Duration(seconds=timeout),
            )
            initial_stamp_ns = _stamp_ns(initial)
            if initial_stamp_ns <= 0:
                raise RuntimeError("TF transform did not contain a positive timestamp")
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    raise RuntimeError(
                        f"No fresh TF transform from {frame_id} to {base_frame} arrived "
                        f"after the request within {timeout:.1f}s"
                    )
                transform = tf_buffer.lookup_transform(
                    frame_id,
                    base_frame,
                    Time(),
                    timeout=Duration(seconds=min(remaining, 0.1)),
                )
                stamp_ns = _stamp_ns(transform)
                if stamp_ns > initial_stamp_ns:
                    break
                time.sleep(min(0.02, remaining))
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"No current TF transform from {frame_id} to {base_frame}: {exc}"
            ) from exc
        translation = transform.transform.translation
        rotation = transform.transform.rotation
        return {
            "x": float(translation.x),
            "y": float(translation.y),
            "yaw": _yaw_from_quaternion(rotation),
            "frame_id": frame_id,
            "base_frame": base_frame,
            "source": f"/tf({frame_id}->{base_frame})",
            "initial_stamp_ns": initial_stamp_ns,
            "stamp_ns": stamp_ns,
            "fresh_after_request": True,
        }

    def get_pose(
        self,
        timeout: float = 2.0,
        *,
        fresh: bool = False,
        frame_id: str = "map",
        base_frame: str = "base_link",
    ) -> WirePayload:
        """Read pose without mislabeling a latched AMCL sample as fresh.

        ``fresh=True`` is fail-closed and uses Nav2's current TF chain. The
        observation path keeps the historical AMCL/odom fallback for status
        views where the last known stationary pose is still useful.
        """
        if fresh:
            return self._fresh_tf_pose(frame_id, base_frame, timeout)
        from geometry_msgs.msg import PoseWithCovarianceStamped
        from nav_msgs.msg import Odometry
        from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy

        def _try_topic(topic: str, msg_type: Any, frame_id: str, qos: Any) -> WirePayload | None:
            got: list[Any] = []
            event = threading.Event()

            def _cb(msg: Any) -> None:
                got.append(msg.pose.pose)
                event.set()

            sub = self.create_subscription(msg_type, topic, _cb, qos)
            try:
                if not event.wait(timeout):
                    return None
            finally:
                self.destroy_subscription(sub)
            pose = got[0]
            return {
                "x": pose.position.x,
                "y": pose.position.y,
                "yaw": _yaw_from_quaternion(pose.orientation),
                "frame_id": frame_id,
                "source": topic,
            }

        # AMCL latches the last pose (RELIABLE + TRANSIENT_LOCAL) and only
        # republishes on updates — a volatile subscriber gets nothing from a
        # stationary robot. Match Nav2's QoS so the latched sample arrives
        # immediately; /odom stays SENSOR_DATA (it is a continuous stream).
        latched = QoSProfile(
            depth=1,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )
        result = _try_topic("/amcl_pose", PoseWithCovarianceStamped, "map", latched) or _try_topic(
            "/odom", Odometry, "odom", QoSPresetProfiles.SENSOR_DATA.value
        )
        if result is None:
            raise RuntimeError("No pose received on /amcl_pose or /odom (are they publishing?)")
        return result

    def _ensure_map_subscription(self) -> None:
        if self._map_subscription is not None:
            return
        from nav_msgs.msg import OccupancyGrid
        from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy

        qos = QoSProfile(
            depth=1,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._map_subscription = self.create_subscription(
            OccupancyGrid,
            "/map",
            self._map_observation.observe,
            qos,
        )

    def _map_message(self, timeout: float) -> Any:
        self._ensure_map_subscription()
        try:
            message = self._map_observation.wait(timeout)
        except TimeoutError as exc:
            raise RuntimeError("No latched OccupancyGrid received on /map") from exc
        if self.count_publishers("/map") < 1:
            raise RuntimeError("No active OccupancyGrid publisher on /map")
        return message

    def map_cell(self, x: float, y: float, timeout: float = 3.0) -> WirePayload:
        """Read the latched static-map cell at one map-frame coordinate."""
        message = self._map_message(timeout)
        result = sample_occupancy_cell(
            message.data,
            width=int(message.info.width),
            height=int(message.info.height),
            resolution=float(message.info.resolution),
            origin_x=float(message.info.origin.position.x),
            origin_y=float(message.info.origin.position.y),
            origin_yaw=_yaw_from_quaternion(message.info.origin.orientation),
            x=float(x),
            y=float(y),
        )
        result.update({"frame_id": message.header.frame_id or "map", "source": "/map"})
        return result

    def map_identity(self, timeout: float = 3.0) -> WirePayload:
        """Read and fingerprint the complete latched static map."""
        message = self._map_message(timeout)
        origin = message.info.origin
        frame_id = message.header.frame_id or "map"
        resolution = float(message.info.resolution)
        origin_x = float(origin.position.x)
        origin_y = float(origin.position.y)
        origin_yaw = _yaw_from_quaternion(origin.orientation)
        digest = occupancy_grid_identity(
            message.data,
            width=int(message.info.width),
            height=int(message.info.height),
            resolution=resolution,
            origin_x=origin_x,
            origin_y=origin_y,
            origin_yaw=origin_yaw,
            frame_id=frame_id,
        )
        return {
            "algorithm": "sha256-occupancy-grid-v1",
            "digest": digest,
            "width": int(message.info.width),
            "height": int(message.info.height),
            "resolution": resolution,
            "origin_x": origin_x,
            "origin_y": origin_y,
            "origin_yaw": origin_yaw,
            "frame_id": frame_id,
            "source": "/map",
        }

    # -- Nav2 ---------------------------------------------------------------

    def _record_path_plan_result(
        self, future: Any, state: dict[str, Any], finished: threading.Event
    ) -> None:
        """Record one asynchronous planner result and always release its waiter."""
        try:
            state["result"] = path_plan_payload(future.result())
        except Exception as exc:
            state["error"] = f"Nav2 planning result failed: {exc}"
        finally:
            finished.set()

    def _accept_path_plan(
        self,
        future: Any,
        state: dict[str, Any],
        finished: threading.Event,
        timed_out: threading.Event,
    ) -> None:
        """Attach result handling to an accepted goal or record rejection."""
        try:
            handle = future.result()
            if not handle.accepted:
                state["error"] = "Nav2 rejected the path-planning request."
                finished.set()
                return
            state["handle"] = handle
            if timed_out.is_set():
                handle.cancel_goal_async()
                return
            handle.get_result_async().add_done_callback(
                lambda result: self._record_path_plan_result(result, state, finished)
            )
        except Exception as exc:
            state["error"] = f"Nav2 path-planning request failed: {exc}"
            finished.set()

    def nav_plan(
        self,
        x: float,
        y: float,
        yaw: float,
        frame_id: str = "map",
        timeout: float = 5.0,
    ) -> WirePayload:
        """Compute a Nav2 path without commanding the robot."""
        from nav2_msgs.action import ComputePathToPose

        if self._plan_client is None:
            self._plan_client = ActionClient(self, ComputePathToPose, "/compute_path_to_pose")
        if not self._plan_client.wait_for_server(timeout_sec=min(timeout, 10.0)):
            raise RuntimeError("Nav2 (/compute_path_to_pose) action server is not running.")

        goal = ComputePathToPose.Goal()
        goal.goal.header.frame_id = frame_id
        goal.goal.pose.position.x = float(x)
        goal.goal.pose.position.y = float(y)
        goal.goal.pose.orientation.z = math.sin(yaw / 2.0)
        goal.goal.pose.orientation.w = math.cos(yaw / 2.0)
        goal.use_start = False

        finished = threading.Event()
        timed_out = threading.Event()
        state: dict[str, Any] = {}
        self._plan_client.send_goal_async(goal).add_done_callback(
            lambda future: self._accept_path_plan(future, state, finished, timed_out)
        )
        if not finished.wait(timeout):
            timed_out.set()
            handle = state.get("handle")
            if handle is not None:
                handle.cancel_goal_async()
            raise RuntimeError(f"Nav2 path planning timed out after {timeout:.1f}s.")
        if "error" in state:
            raise RuntimeError(state["error"])
        return cast(WirePayload, state["result"])

    def nav_send(
        self, x: float, y: float, yaw: float, frame_id: str = "map", tag: str = ""
    ) -> WirePayload:
        """Send a tagged NavigateToPose goal and retain endpoint evidence.

        The tag prevents a late result from a cancelled generation from being
        mistaken for the outcome of the next goal.
        """
        from nav2_msgs.action import NavigateToPose

        self._ensure_pose_jump_subscription()
        if self._nav_client is None:
            self._nav_client = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        # Cold DDS discovery on the warehouse graph can exceed two seconds.
        # Ten seconds still returns early when Nav2 is present and fails honestly otherwise.
        if not self._nav_client.wait_for_server(timeout_sec=10.0):
            raise RuntimeError("Nav2 (/navigate_to_pose) action server is not running.")

        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = frame_id
        goal.pose.pose.position.x = float(x)
        goal.pose.pose.position.y = float(y)
        goal.pose.pose.orientation.z = math.sin(yaw / 2.0)
        goal.pose.pose.orientation.w = math.cos(yaw / 2.0)
        feedback = _NavigationFeedbackRecorder(frame_id, tag, time.monotonic())

        token = self._nav_generations.begin(tag)
        self._pose_jump_guard.arm(token)
        self._nav_pending = True
        self._cancel_on_accept_token = None
        try:
            send_future = self._nav_client.send_goal_async(goal, feedback_callback=feedback)
        except Exception:
            self._finish_unaccepted_navigation(token)
            raise
        send_future.add_done_callback(
            lambda future: self._accept_navigation_goal(future, token, tag, feedback)
        )
        return {"sent": True}

    def _finish_unaccepted_navigation(self, token: NavigationGoalToken) -> None:
        """Release pending state only when the rejected generation is current."""
        if self._nav_generations.finish(token):
            self._nav_pending = False
            self._pose_jump_guard.disarm(token)

    def _accept_navigation_goal(
        self,
        future: Any,
        token: NavigationGoalToken,
        tag: str,
        feedback: _NavigationFeedbackRecorder,
    ) -> None:
        """Bind an accepted handle to its generation and cancellation state."""
        try:
            handle = future.result()
        except Exception as exc:
            self._finish_unaccepted_navigation(token)
            _emit(
                {
                    "event": "nav_result",
                    "tag": tag,
                    "status": "failed",
                    "reason": f"Nav2 goal request failed: {exc}",
                }
            )
            return
        if not handle.accepted:
            self._finish_unaccepted_navigation(token)
            _emit({"event": "nav_result", "tag": tag, "status": "rejected"})
            return

        is_current = self._nav_generations.is_current(token)
        if is_current:
            self._nav_goal_handle = handle
            self._nav_goal_handle_token = token
            self._nav_pending = False
        cancel_requested = self._cancel_on_accept_token == token
        if cancel_requested:
            self._cancel_on_accept_token = None
        if not is_current or cancel_requested:
            handle.cancel_goal_async()
        handle.get_result_async().add_done_callback(
            lambda result: self._handle_navigation_result(
                result,
                handle=handle,
                token=token,
                tag=tag,
                final_pose=feedback.snapshot(),
            )
        )

    def _finish_navigation_goal(self, handle: Any, token: NavigationGoalToken) -> None:
        """Release only the goal named by ``token``; stale callbacks are harmless."""
        if self._nav_goal_handle is handle and self._nav_goal_handle_token == token:
            self._nav_goal_handle = None
            self._nav_goal_handle_token = None
        if self._nav_generations.finish(token):
            self._nav_pending = False
            if self._cancel_on_accept_token == token:
                self._cancel_on_accept_token = None
            self._pose_jump_guard.disarm(token)

    def _handle_navigation_result(
        self,
        result_future: Any,
        *,
        handle: Any,
        token: NavigationGoalToken,
        tag: str,
        final_pose: WirePayload | None = None,
    ) -> None:
        """Validate one terminal result and fail closed when its state is unknown."""
        if token in self._localization_fault_tokens:
            self._localization_fault_tokens.discard(token)
            self._finish_navigation_goal(handle, token)
            # The fail-closed localization halt thread owns the terminal event.
            return

        terminal = resolve_navigation_terminal(result_future)
        if terminal.error is None and terminal.status is not None:
            self._finish_navigation_goal(handle, token)
            event: WirePayload = {
                "event": "nav_result",
                "tag": tag,
                "status": terminal.status,
            }
            if final_pose is not None:
                event["final_pose"] = final_pose
            _emit(event)
            return

        # Keep the goal active and watchdog armed until the emergency halt
        # completes. A stale result belongs to a superseded goal and cannot
        # stop or finish the current one.
        if not self._nav_generations.is_current(token):
            return
        threading.Thread(
            target=self._halt_after_navigation_result_error,
            args=(
                handle,
                token,
                tag,
                terminal.error or "Nav2 terminal result had no status",
            ),
            daemon=True,
        ).start()

    def _halt_after_navigation_result_error(
        self,
        handle: Any,
        token: NavigationGoalToken,
        tag: str,
        terminal_error: str,
    ) -> None:
        """Emergency-stop an indeterminate Nav2 terminal, then emit one result."""
        try:
            halt_result = self.halt(
                self._pose_jump_cmd_vel_topic,
                self._pose_jump_stamped,
            )
            acknowledged = bool(halt_result.get("active_nav_canceled"))
            halt_detail = (
                "active-goal cancellation was acknowledged"
                if acknowledged
                else "active-goal cancellation was not acknowledged; "
                "zero-velocity pulses were still sent"
            )
        except Exception as exc:
            halt_detail = f"emergency halt raised {type(exc).__name__}: {exc}"
        finally:
            self._finish_navigation_goal(handle, token)
        _emit(
            {
                "event": "nav_result",
                "tag": tag,
                "status": "failed",
                "reason": (
                    f"Nav2 terminal result was unavailable ({terminal_error}); {halt_detail}."
                ),
            }
        )

    def drive_to_pose(
        self,
        x: float,
        y: float,
        yaw: float = 0.0,
        *,
        tag: str = "",
        cmd_vel_topic: str = "/cmd_vel",
        stamped: bool = False,
        max_linear: float = 1.0,
        max_angular: float = 2.0,
        tolerance: float = 0.3,
        odom_timeout_s: float = 1.0,
        timeout: float = 600.0,
        avoidance: dict[str, Any] | None = None,
    ) -> WirePayload:
        """Nav2-less point-to-point drive: closed loop on /odom → /cmd_vel.

        For open ground with no map/planner (e.g. an Isaac ground plane): the
        goal (x, y) is treated as ODOM-frame coordinates, valid when map≈odom
        (no localization offset). Emits nav_feedback/nav_result with `tag`, the
        SAME protocol as nav_send, so the client's navigate_live works unchanged.

        With `avoidance` enabled, a depth camera is folded into the loop as a
        pseudo-laserscan for stop-and-go local detours. Motion fails closed if
        fresh depth data is unavailable. This is not a global planner substitute.
        """
        if self._drive_active:
            raise RuntimeError("a drive_to_pose is already running")
        self._drive_cancel.clear()
        self._drive_active = True
        threading.Thread(
            target=self._drive_loop,
            args=(x, y, yaw, tag, cmd_vel_topic, stamped),
            kwargs={
                "max_linear": max_linear,
                "max_angular": max_angular,
                "tolerance": tolerance,
                "odom_timeout_s": odom_timeout_s,
                "timeout": timeout,
                "avoidance": avoidance,
            },
            daemon=True,
        ).start()
        return {"sent": True}

    def _drive_loop(
        self,
        gx: float,
        gy: float,
        gyaw: float,  # Position-only seeker intentionally ignores goal heading.
        tag: str,
        cmd_vel_topic: str,
        stamped: bool,
        *,
        max_linear: float,
        max_angular: float,
        tolerance: float,
        odom_timeout_s: float,
        timeout: float,
        avoidance: dict[str, Any] | None = None,
    ) -> None:
        # The stdlib-only sibling owns decisions; this method owns ROS sampling/publishing.
        from _drive_control import DirectDriveController, direct_drive_terminal_status
        from nav_msgs.msg import Odometry

        latest: dict[str, Any] = {}
        depth: dict[str, Any] = {}
        sub = None
        depth_sub = None
        status = "failed"  # Setup failures remain explicit terminal failures.
        avoid = avoidance if (avoidance and avoidance.get("enabled")) else None
        # Setup remains inside the try so partial rclpy failure still performs
        # fail-safe cleanup, clears the drive state, and emits one terminal result.
        try:

            def _odom_cb(msg: Any) -> None:
                pose = msg.pose.pose
                latest["x"] = pose.position.x
                latest["y"] = pose.position.y
                latest["yaw"] = _yaw_from_quaternion(pose.orientation)
                latest["updated_at"] = time.monotonic()

            sub = self.create_subscription(
                Odometry, "/odom", _odom_cb, QoSPresetProfiles.SENSOR_DATA.value
            )
            if avoid is not None:
                depth_sub = self._start_depth_scan(avoid, depth)
            # Entity creation shares the halt lock with emergency-stop callers.
            with self._halt_lock:
                publisher = self.ensure_halt_publisher(cmd_vel_topic, stamped)
            started = time.monotonic()
            last_feedback = 0.0
            controller = DirectDriveController(
                gx,
                gy,
                max_linear=max_linear,
                max_angular=max_angular,
                tolerance=tolerance,
                odom_timeout_s=odom_timeout_s,
                avoidance=avoid,
            )

            while True:
                now = time.monotonic()
                elapsed = now - started
                terminal = direct_drive_terminal_status(
                    cancelled=self._drive_cancel.is_set(),
                    elapsed=elapsed,
                    timeout=timeout,
                    odom_ready="x" in latest,
                    odom_timeout=odom_timeout_s,
                )
                if terminal is not None:
                    status = terminal
                    break
                if "x" not in latest:
                    time.sleep(0.05)
                    continue
                tick = controller.step(
                    now=now,
                    elapsed=elapsed,
                    x=latest["x"],
                    y=latest["y"],
                    yaw=latest["yaw"],
                    odom_updated_at=latest["updated_at"],
                    ranges=depth.get("ranges"),
                    angles=depth.get("angles"),
                    scan_updated_at=depth.get("updated_at"),
                )
                terminal, last_feedback = self._apply_drive_tick(
                    publisher,
                    tick,
                    stamped=stamped,
                    tag=tag,
                    started=started,
                    last_feedback=last_feedback,
                )
                if terminal is not None:
                    status = terminal
                    break
        finally:
            self._finish_direct_drive(
                cmd_vel_topic,
                stamped,
                subscriptions=(sub, depth_sub),
                tag=tag,
                status=status,
            )

    def _apply_drive_tick(
        self,
        publisher: Any,
        tick: Any,
        *,
        stamped: bool,
        tag: str,
        started: float,
        last_feedback: float,
    ) -> tuple[str | None, float]:
        """Publish one controller decision and its throttled feedback."""
        if tick.zero_first:
            self._pulse_zero(publisher, stamped, interval_s=0.05)
        if tick.status is not None:
            return str(tick.status), last_feedback
        if tick.action != "move":
            time.sleep(0.05)
            return None, last_feedback

        publisher.publish(self._velocity_message(stamped, tick.linear, tick.angular))
        now = time.monotonic()
        if now - last_feedback >= 0.5:
            last_feedback = now
            _emit(
                {
                    "event": "nav_feedback",
                    "tag": tag,
                    "distance_remaining": round(tick.distance_remaining, 2),
                    "recoveries": tick.recoveries,
                    "avoiding": tick.avoiding,
                    "elapsed": round(now - started, 1),
                }
            )
        time.sleep(0.05)
        return None, last_feedback

    def _velocity_message(self, stamped: bool, linear: float, angular: float) -> Any:
        """Create one bounded velocity message in the configured ROS family."""
        from geometry_msgs.msg import Twist, TwistStamped

        message = (TwistStamped if stamped else Twist)()
        twist = message.twist if stamped else message
        if stamped:
            message.header.stamp = self.get_clock().now().to_msg()
        twist.linear.x = float(linear)
        twist.angular.z = float(angular)
        return message

    def _pulse_zero(self, publisher: Any, stamped: bool, *, interval_s: float) -> None:
        """Publish redundant zero velocity samples to overcome DDS packet loss."""
        stop = self._velocity_message(stamped, 0.0, 0.0)
        for _ in range(3):
            publisher.publish(stop)
            time.sleep(interval_s)

    def _finish_direct_drive(
        self,
        cmd_vel_topic: str,
        stamped: bool,
        *,
        subscriptions: tuple[object | None, ...],
        tag: str,
        status: str,
    ) -> None:
        """Fail-safe terminal cleanup, valid even after partial ROS setup."""
        from _drive_control import terminal_status_after_halt

        halt_completed = False
        halt_error: Exception | None = None
        try:
            publisher = self.ensure_halt_publisher(cmd_vel_topic, stamped)
            self._pulse_zero(publisher, stamped, interval_s=0.02)
            halt_completed = True
        except Exception as exc:
            halt_error = exc
        for subscription in subscriptions:
            if subscription is not None:
                with contextlib.suppress(Exception):
                    self.destroy_subscription(subscription)
        # A failed final zero is a latched safety fault: keeping the drive
        # "active" makes the watchdog continue its redundant halt attempts and
        # prevents another direct-drive task until the sidecar is restarted.
        self._drive_active = not halt_completed
        terminal_status = terminal_status_after_halt(status, halt_completed=halt_completed)
        event: WirePayload = {"event": "nav_result", "tag": tag, "status": terminal_status}
        if halt_error is not None:
            event["reason"] = (
                "Final zero-velocity pulse failed: "
                f"{type(halt_error).__name__}: {halt_error}. "
                "The bridge is fault-latched and must be restarted before another direct drive."
            )
        _emit(event)

    def _start_depth_scan(self, avoid: dict[str, Any], out: dict[str, Any]) -> Any:
        """Subscribe to the depth camera and keep `out` updated with a
        pseudo-laserscan: nearest range per angular sector across the FOV.

        The depth image (32FC1, metres) is reduced to one range per column
        (nearest valid pixel in a central horizontal band), then grouped into
        `sectors` bins mapped to angles (image-left = robot-left = +yaw)."""
        import numpy as np
        from sensor_msgs.msg import Image

        n = int(avoid["sectors"])
        hfov = math.radians(avoid["hfov_deg"])
        lo, hi = avoid["band_lo"], avoid["band_hi"]
        min_valid = avoid["min_valid"]
        # Per-pixel floor reference (from the `avoid_snapshot` op, captured on
        # empty ground): a pixel is an obstacle only if it reads CLOSER than
        # its own reference. One mechanism covers the floor ring, the
        # vehicle's own body in frame, and short obstacles the scalar
        # floor_ref cannot separate from the ground (caught live: a cube
        # below camera height was invisible to every fixed band until
        # contact). A missing/mismatched file degrades to the scalar filter.
        snap = None
        snap_tol = float(avoid.get("floor_tol", 0.2))
        if avoid.get("floor_snapshot"):
            try:
                snap = np.load(avoid["floor_snapshot"])
            except Exception:
                snap = None
        # Sector centre angles: leftmost = +hfov/2, rightmost = -hfov/2.
        out["angles"] = [hfov * (0.5 - (i + 0.5) / n) for i in range(n)]

        def _cb(msg: Any) -> None:
            try:
                h, w = msg.height, msg.width
                # frombuffer reads the array.array/bytes buffer zero-copy (no
                # per-frame full-buffer copy). Honor msg.step: a padded row
                # stride (step > w*4) would otherwise mis-shape every frame and
                # silently disable avoidance on a real camera.
                row = (msg.step // 4) if msg.step else w
                buf = np.frombuffer(msg.data, dtype=np.float32).reshape(h, row)[:, :w]
                snapped = snap is not None and snap.shape == buf.shape
                if snapped:
                    snapshot = cast(Any, snap)
                    buf = np.where(buf >= snapshot - snap_tol, np.inf, buf)
                band = buf[int(h * lo) : int(h * hi), :]
                valid = np.where(
                    np.isfinite(band) & (band > min_valid) & (band < 100.0), band, np.inf
                )
                ranges: list[float] = []
                for chunk in np.array_split(valid, n, axis=1):
                    vals = chunk[np.isfinite(chunk)]
                    if snapped:
                        # After the snapshot filter the finite pixels are the
                        # obstacle candidates. A sector's range must not hinge
                        # on a lone pixel (caught live: a handful of self-view
                        # edge pixels read 0.3 m under their reference and
                        # kept every drive "blocked"): require a cluster of
                        # ~1% of the sector before it counts, then take the
                        # cluster's 10th percentile as its nearest face.
                        # Cost: obstacles thinner than ~1% of a sector are
                        # invisible — same order as the pre-existing band
                        # limits, and the honest floor of depth-only sensing.
                        if vals.size < max(20, chunk.size // 100):
                            ranges.append(float("inf"))
                            continue
                        k = max(0, int(vals.size * 0.1) - 1)
                        ranges.append(float(np.partition(vals, k)[k]))
                    else:
                        ranges.append(float(vals.min()) if vals.size else float("inf"))
                out["ranges"] = ranges
                out["updated_at"] = time.monotonic()
            except Exception:
                pass  # a malformed frame just leaves the last scan in place

        return self.create_subscription(
            Image, avoid["depth_topic"], _cb, QoSPresetProfiles.SENSOR_DATA.value
        )

    def nav_cancel(self) -> WirePayload:
        """Cancel our own goal AND everything else on the Nav2 action server.

        The own-handle path alone is not an emergency stop: a goal sent by a
        DIFFERENT process's bridge (TUI goal, WebUI stop) is invisible here,
        and Nav2's controller would keep streaming cmd_vel after our zero
        pulses. The server-side cancel-all covers every owner.
        """
        requested = False
        acknowledged = False
        active_goal_acknowledged = False
        had_drive_active = self._drive_active
        if had_drive_active:
            # The odom driver stops within one control tick; no server round-trip.
            self._drive_cancel.set()
            requested = True
            acknowledged = True
            active_goal_acknowledged = True
        active_token = self._nav_generations.active
        active_was_pending = self._nav_pending and active_token is not None
        if active_was_pending:
            self._cancel_on_accept_token = active_token
            requested = True
        handle = self._nav_goal_handle
        handle_token = self._nav_goal_handle_token
        if handle is not None:
            requested = True
            handle_acknowledged = self._cancel_goal_handle(handle)
            acknowledged = handle_acknowledged or acknowledged
            if handle_token == active_token:
                active_goal_acknowledged = handle_acknowledged
        # ALWAYS ask the server to cancel-all: a goal owned by a DIFFERENT
        # process's bridge is invisible to _nav_client, so gating this on our
        # own client would silently drop cross-process emergency-stop coverage.
        # (Bounded 2s wait; returns fast when no Nav2 server is present.)
        cancel_all_acknowledged = self._cancel_all_nav_goals()
        requested = requested or cancel_all_acknowledged
        acknowledged = acknowledged or cancel_all_acknowledged
        # A cancel-all acknowledgement can confirm the current accepted handle,
        # but must not let an old A handle stand in for a still-pending B goal.
        if (
            cancel_all_acknowledged
            and active_token is not None
            and not active_was_pending
            and handle_token == active_token
        ):
            active_goal_acknowledged = True
        if not requested:
            return {
                "canceled": False,
                "cancel_requested": False,
                "active_goal_canceled": False,
                "detail": "no active navigation goal",
            }
        has_owned_active = had_drive_active or active_token is not None
        canceled = cancellation_is_confirmed(
            has_owned_active=has_owned_active,
            any_acknowledged=acknowledged,
            active_goal_acknowledged=active_goal_acknowledged,
        )
        if not canceled:
            detail = (
                "another navigation cancellation was acknowledged, but the current active "
                "goal cancellation was not acknowledged"
                if acknowledged and has_owned_active
                else "navigation cancellation was requested but not acknowledged"
            )
            return {
                "canceled": False,
                "cancel_requested": True,
                "active_goal_canceled": False,
                "detail": detail,
            }
        return {
            "canceled": True,
            "cancel_requested": True,
            "active_goal_canceled": active_goal_acknowledged,
        }

    def request_nomotion_update(self, timeout: float = 2.0) -> WirePayload:
        """Request one bounded AMCL update while the robot remains stationary."""
        from std_srvs.srv import Empty

        client = self.create_client(Empty, "/request_nomotion_update")
        try:
            if not client.wait_for_service(timeout_sec=timeout):
                return {"acknowledged": False}
            future = client.call_async(Empty.Request())
            completed = threading.Event()
            future.add_done_callback(lambda _future: completed.set())
            if not completed.wait(timeout):
                return {"acknowledged": False}
            try:
                future.result()
            except Exception:
                return {"acknowledged": False}
            return {"acknowledged": True}
        finally:
            self.destroy_client(client)

    @staticmethod
    def _cancel_goal_handle(handle: Any, timeout: float = 2.0) -> bool:
        """Cancel one owned goal and require a positive CancelGoal response."""
        try:
            future = handle.cancel_goal_async()
        except Exception:
            return False
        return wait_for_cancel_acknowledgement(future, timeout)

    def _cancel_all_nav_goals(self, timeout: float = 2.0) -> bool:
        """Ask the Nav2 action server to cancel ALL goals (zeroed goal id),
        waiting bounded for the reply so shutdown paths can't cut it off."""
        from action_msgs.srv import CancelGoal

        client = self.create_client(CancelGoal, "/navigate_to_pose/_action/cancel_goal")
        try:
            if not client.wait_for_service(timeout_sec=timeout):
                return False  # no Nav2 → nothing to cancel
            future = client.call_async(CancelGoal.Request())  # zeroed = cancel all
            return wait_for_cancel_acknowledgement(future, timeout)
        except Exception:
            return False
        finally:
            self.destroy_client(client)

    @property
    def nav_active(self) -> bool:
        return navigation_active(
            has_goal_handle=self._nav_goal_handle is not None,
            nav_pending=self._nav_pending,
            drive_active=self._drive_active,
        )

    # -- emergency stop -------------------------------------------------------

    def ensure_halt_publisher(self, cmd_vel_topic: str, stamped: bool) -> Any:
        """Create (once) and cache the zero-velocity publisher.

        Called eagerly when the watchdog is configured so DDS discovery has
        completed long before an emergency needs it — a freshly created
        publisher can silently drop every pulse published before its first
        subscriber match.
        """
        key = (cmd_vel_topic, stamped)
        pub = self._halt_publishers.get(key)
        if pub is None:
            from geometry_msgs.msg import Twist, TwistStamped

            msg_type = TwistStamped if stamped else Twist
            pub = self.create_publisher(msg_type, cmd_vel_topic, 10)
            self._halt_publishers[key] = pub
        return pub

    def halt(
        self,
        cmd_vel_topic: str = "/cmd_vel",
        stamped: bool = False,
        pulses: int = 5,
        rate_hz: float = 20.0,
    ) -> WirePayload:
        """EMERGENCY STOP: pulse zero, cancel Nav2, then pulse zero again.

        Zero is pulsed (not sent once) because a single message can lose the
        race against a controller that is still streaming motion commands.
        Serialized under a lock: the stdin loop and the watchdog thread may
        both call this, and concurrent rclpy entity churn is not safe.
        """
        with self._halt_lock:
            from _safety_order import halt_in_order
            from geometry_msgs.msg import Twist, TwistStamped

            pub = self.ensure_halt_publisher(cmd_vel_topic, stamped)
            msg_type = TwistStamped if stamped else Twist

            def _send_zero(count: int) -> None:
                for _ in range(count):
                    msg = msg_type()  # all-zero twist
                    if stamped:
                        msg.header.stamp = self.get_clock().now().to_msg()
                    pub.publish(msg)
                    time.sleep(1.0 / rate_hz)

            cancel_result: WirePayload = {}

            def _cancel_navigation() -> bool:
                cancel_result.update(self.nav_cancel())
                return bool(cancel_result.get("canceled"))

            canceled = halt_in_order(
                _send_zero,
                _cancel_navigation,
                pulses=pulses,
            )
        return {
            "halted": True,
            "nav_canceled": canceled,
            "nav_cancel_requested": bool(cancel_result.get("cancel_requested")),
            "active_nav_canceled": bool(cancel_result.get("active_goal_canceled")),
        }

    # -- camera -------------------------------------------------------------

    def avoid_snapshot(
        self, depth_topic: str, path: str, frames: int = 5, timeout: float = 10.0
    ) -> WirePayload:
        """Calibrate the avoidance floor reference: per-pixel median depth.

        Run with the view EMPTY (no obstacles inside sensor range). The median
        over `frames` frames is each pixel's expected range — the ground plane
        plus any static self-view of the vehicle. Saved as .npy for the drive
        loop's per-pixel filter (`floor_snapshot`). Pixels with no valid
        return in any frame are stored as inf (never filtered → their raw
        readings pass through). Flat-ground assumption: recalibrate when the
        camera mount moves.
        """
        import warnings

        import numpy as np
        from sensor_msgs.msg import Image

        got: list[Any] = []
        event = threading.Event()

        def _cb(msg: Any) -> None:
            try:
                h, w = msg.height, msg.width
                row = (msg.step // 4) if msg.step else w
                buf = np.frombuffer(msg.data, dtype=np.float32).reshape(h, row)[:, :w]
                got.append(buf.copy())  # copy: msg buffer dies with the msg
                if len(got) >= frames:
                    event.set()
            except Exception:
                pass

        sub = self.create_subscription(Image, depth_topic, _cb, QoSPresetProfiles.SENSOR_DATA.value)
        try:
            if not event.wait(timeout):
                raise RuntimeError(
                    f"Got {len(got)}/{frames} depth frames on {depth_topic} within {timeout:.0f}s."
                )
        finally:
            self.destroy_subscription(sub)

        stack = np.stack(got[:frames])
        stack = np.where(np.isfinite(stack) & (stack > 0.0), stack, np.nan)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # all-NaN pixel → NaN median is fine
            ref = np.nanmedian(stack, axis=0)
        ref = np.where(np.isfinite(ref), ref, np.inf).astype(np.float32)
        np.save(path, ref)
        finite = ref[np.isfinite(ref)]
        return {
            "path": path,
            "frames": frames,
            "height": int(ref.shape[0]),
            "width": int(ref.shape[1]),
            "coverage": round(float(np.isfinite(ref).mean()), 3),
            "median_m": round(float(np.median(finite)), 2) if finite.size else None,
            "min_m": round(float(finite.min()), 2) if finite.size else None,
        }

    def capture_frame(self, topic: str, timeout: float = 5.0) -> WirePayload:
        """Grab one frame from an image topic; returns a temp file path."""
        from sensor_msgs.msg import CompressedImage, Image

        compressed = topic.endswith("/compressed") or "compressed" in topic
        msg_type = CompressedImage if compressed else Image
        got: list[Any] = []
        event = threading.Event()

        def _cb(msg: Any) -> None:
            if not got:
                got.append(msg)
                event.set()

        sub = self.create_subscription(msg_type, topic, _cb, QoSPresetProfiles.SENSOR_DATA.value)
        try:
            if not event.wait(timeout):
                raise RuntimeError(f"No image received on {topic} within {timeout:.0f}s.")
        finally:
            self.destroy_subscription(sub)

        msg = got[0]
        if compressed:
            path = _new_frame_path(".jpg")
            with open(path, "wb") as fh:
                fh.write(bytes(msg.data))
            return {"path": path, "width": None, "height": None, "encoding": msg.format}

        import numpy as np
        from PIL import Image as PILImage

        encoding = msg.encoding.lower()
        channels = 3 if encoding in ("rgb8", "bgr8") else 1
        if channels == 1 and encoding not in ("mono8", "8uc1"):
            raise RuntimeError(f"Unsupported image encoding '{msg.encoding}'.")

        # Real cameras often pad rows (msg.step > width*channels); reshape by
        # step first and slice off the padding, or frombuffer's length check
        # blows up on exactly the hardware this feature exists for.
        buf = np.frombuffer(bytes(msg.data), dtype=np.uint8)
        step = int(msg.step) or msg.width * channels
        rows = buf.reshape((msg.height, step))[:, : msg.width * channels]
        if channels == 3:
            arr = rows.reshape((msg.height, msg.width, 3))
            if encoding == "bgr8":
                arr = arr[:, :, ::-1]
            img = PILImage.fromarray(arr, "RGB")
        else:
            img = PILImage.fromarray(rows, "L")
        path = _new_frame_path(".png")
        img.save(path)
        return {"path": path, "width": msg.width, "height": msg.height, "encoding": msg.encoding}

    # -- generic topic watch (daemon rules) -----------------------------------

    def watch(
        self,
        watch_id: int,
        topic: str,
        msg_type: str,
        throttle: float = 1.0,
        qos_profile: str = "sensor_data",
    ) -> WirePayload:
        """Stream messages from a topic as events, at most one per `throttle` seconds."""
        from rosidl_runtime_py.convert import message_to_ordereddict
        from rosidl_runtime_py.utilities import get_message

        cls = get_message(msg_type)
        last_emit = [0.0]

        def _cb(msg: Any) -> None:
            now = time.monotonic()
            if now - last_emit[0] < throttle:
                return
            last_emit[0] = now
            _emit(
                {
                    "event": "watch",
                    "watch_id": watch_id,
                    "topic": topic,
                    "data": json.loads(json.dumps(message_to_ordereddict(msg), default=str)),
                }
            )

        if qos_profile == "sensor_data":
            qos = QoSPresetProfiles.SENSOR_DATA.value
        elif qos_profile == "transient_local":
            qos = QoSProfile(
                depth=10,
                reliability=ReliabilityPolicy.RELIABLE,
                durability=DurabilityPolicy.TRANSIENT_LOCAL,
            )
        else:
            raise ValueError(f"unsupported watch qos_profile: {qos_profile}")
        sub = self.create_subscription(cls, topic, _cb, qos)
        self._watches[watch_id] = sub
        return {"watch_id": watch_id}

    def unwatch(self, watch_id: int) -> WirePayload:
        sub = self._watches.pop(watch_id, None)
        if sub is not None:
            self.destroy_subscription(sub)
        return {"removed": sub is not None}


def _watchdog_loop(node: BridgeNode, state: WatchdogState, stop: threading.Event) -> None:
    while not stop.wait(0.5):
        if node.nav_active and state.should_halt():
            try:
                node.halt(state.cmd_vel_topic, state.stamped)
                _emit({"event": "watchdog_halt", "reason": "client went quiet mid-navigation"})
            except Exception as exc:
                _emit({"event": "watchdog_halt", "reason": f"halt failed: {exc}"})
            state.mark_halted()


def main() -> None:
    node: BridgeNode | None = None
    executor: MultiThreadedExecutor | None = None
    watchdog: WatchdogState | None = None
    watchdog_stop: threading.Event | None = None
    initialized = False
    try:
        rclpy.init()
        initialized = True
        node = BridgeNode()
        executor = MultiThreadedExecutor(num_threads=4)
        executor.add_node(node)
        spin = threading.Thread(target=executor.spin, daemon=True)
        spin.start()

        watchdog = WatchdogState()
        watchdog_stop = threading.Event()
        threading.Thread(
            target=_watchdog_loop,
            args=(node, watchdog, watchdog_stop),
            daemon=True,
        ).start()

        ready: WirePayload = {"event": "ready"}
        try:
            ready["runtime_identity"] = build_runtime_identity_payload(
                effective_rmw=get_rmw_implementation_identifier(),
            )
        except Exception as exc:
            # The bridge remains backward-compatible for normal callers.  The
            # differential harness explicitly requires identity and will fail
            # closed without exposing configuration values in this diagnostic.
            ready["runtime_identity_error"] = type(exc).__name__
        _emit(ready)

        serve_requests(
            sys.stdin,
            emit=_emit,
            dispatch=lambda op, params: dispatch_request(node, op, params, watchdog),
            touch_watchdog=watchdog.touch,
        )
    finally:
        if watchdog_stop is not None:
            watchdog_stop.set()
        # The client is gone (EOF, shutdown, or request-loop failure). A robot
        # still executing a goal must not keep driving unsupervised.
        if node is not None and watchdog is not None:
            with contextlib.suppress(Exception):
                if node.nav_active:
                    node.halt(watchdog.cmd_vel_topic, watchdog.stamped)
        if executor is not None:
            with contextlib.suppress(Exception):
                executor.shutdown()
        if node is not None:
            with contextlib.suppress(Exception):
                node.destroy_node()
        if initialized:
            with contextlib.suppress(Exception):
                rclpy.shutdown()


if __name__ == "__main__":
    main()
