#!/usr/bin/env python3
"""JenAI ROS bridge — runs under the SYSTEM python (which has rclpy), not the venv.

Speaks newline-delimited JSON over stdin/stdout:

  request:  {"id": 1, "op": "pose", ...params}
  response: {"id": 1, "ok": true, "result": {...}} | {"id": 1, "ok": false, "error": "..."}
  event:    {"event": "nav_feedback", ...}   (unsolicited, e.g. Nav2 progress)

This file must stay importable by a bare system python: standard library +
ROS packages only — never import jenai (the venv is not visible here).

Ops: ping, pose, nav_send, drive_to_pose, nav_cancel, halt, capture_frame, watch, unwatch, shutdown.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import threading
import time

import rclpy
from rclpy.action import ActionClient
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSPresetProfiles

_STDOUT_LOCK = threading.Lock()


def _emit(payload: dict) -> None:
    with _STDOUT_LOCK:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()


def _new_frame_path(suffix: str) -> str:
    """A fresh temp file for one captured frame (caller deletes after use)."""
    fd, path = tempfile.mkstemp(prefix="jenai_frame_", suffix=suffix)
    os.close(fd)
    return path


def _yaw_from_quaternion(q) -> float:
    # yaw (z-rotation) from quaternion; robots here move in the plane.
    siny = 2.0 * (q.w * q.z + q.x * q.y)
    cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny, cosy)


class BridgeNode(Node):
    def __init__(self) -> None:
        super().__init__("jenai_bridge")
        self._nav_client: ActionClient | None = None
        self._nav_goal_handle = None
        # True from nav_send until the acceptance callback runs: a goal in
        # this window has no handle yet but MUST still count as active, or a
        # halt/EOF right after nav_send would skip the cancel entirely.
        self._nav_pending = False
        self._cancel_on_accept = False
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

    # -- pose ---------------------------------------------------------------

    def get_pose(self, timeout: float = 2.0) -> dict:
        """Current robot pose from /amcl_pose, falling back to /odom."""
        from geometry_msgs.msg import PoseWithCovarianceStamped
        from nav_msgs.msg import Odometry

        def _try_topic(topic: str, msg_type, frame_id: str) -> dict | None:
            got: list = []
            event = threading.Event()

            def _cb(msg) -> None:
                got.append(msg.pose.pose)
                event.set()

            sub = self.create_subscription(
                msg_type, topic, _cb, QoSPresetProfiles.SENSOR_DATA.value
            )
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

        result = _try_topic("/amcl_pose", PoseWithCovarianceStamped, "map") or _try_topic(
            "/odom", Odometry, "odom"
        )
        if result is None:
            raise RuntimeError("No pose received on /amcl_pose or /odom (are they publishing?)")
        return result

    # -- Nav2 ---------------------------------------------------------------

    def nav_send(
        self, x: float, y: float, yaw: float, frame_id: str = "map", tag: str = ""
    ) -> dict:
        """Send a NavigateToPose goal; feedback/result events carry `tag`.

        The tag lets the client match events to the goal it sent — without it,
        a late result from a cancelled goal could be misread as the outcome of
        the next one.
        """
        from nav2_msgs.action import NavigateToPose

        if self._nav_client is None:
            self._nav_client = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        if not self._nav_client.wait_for_server(timeout_sec=2.0):
            raise RuntimeError("Nav2 (/navigate_to_pose) action server is not running.")

        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = frame_id
        goal.pose.pose.position.x = float(x)
        goal.pose.pose.position.y = float(y)
        goal.pose.pose.orientation.z = math.sin(yaw / 2.0)
        goal.pose.pose.orientation.w = math.cos(yaw / 2.0)

        started = time.monotonic()

        def _feedback(fb) -> None:
            f = fb.feedback
            _emit(
                {
                    "event": "nav_feedback",
                    "tag": tag,
                    "distance_remaining": round(f.distance_remaining, 2),
                    "recoveries": f.number_of_recoveries,
                    "elapsed": round(time.monotonic() - started, 1),
                }
            )

        self._nav_pending = True
        self._cancel_on_accept = False
        send_future = self._nav_client.send_goal_async(goal, feedback_callback=_feedback)

        def _on_accepted(fut) -> None:
            handle = fut.result()
            if not handle.accepted:
                self._nav_pending = False
                _emit({"event": "nav_result", "tag": tag, "status": "rejected"})
                return
            self._nav_goal_handle = handle
            self._nav_pending = False
            if self._cancel_on_accept:
                # A halt arrived while the goal was still in flight to the
                # server — cancel it the moment we can address it.
                self._cancel_on_accept = False
                handle.cancel_goal_async()
            result_future = handle.get_result_async()

            def _on_result(rfut) -> None:
                self._nav_goal_handle = None
                # GoalStatus: 4=SUCCEEDED, 5=CANCELED, 6=ABORTED
                status = {4: "succeeded", 5: "canceled", 6: "aborted"}.get(
                    rfut.result().status, "failed"
                )
                _emit({"event": "nav_result", "tag": tag, "status": status})

            result_future.add_done_callback(_on_result)

        send_future.add_done_callback(_on_accepted)
        return {"sent": True}

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
        timeout: float = 600.0,
    ) -> dict:
        """Nav2-less point-to-point drive: closed loop on /odom → /cmd_vel.

        For open ground with no map/planner (e.g. an Isaac ground plane): the
        goal (x, y) is treated as ODOM-frame coordinates, valid when map≈odom
        (no localization offset). Emits nav_feedback/nav_result with `tag`, the
        SAME protocol as nav_send, so the client's navigate_live works unchanged.
        Obstacle avoidance is NOT provided — this is a straight-line seeker, not
        a substitute for Nav2 in a cluttered map.
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
                "timeout": timeout,
            },
            daemon=True,
        ).start()
        return {"sent": True}

    def _drive_loop(
        self,
        gx: float,
        gy: float,
        gyaw: float,
        tag: str,
        cmd_vel_topic: str,
        stamped: bool,
        *,
        max_linear: float,
        max_angular: float,
        tolerance: float,
        timeout: float,
    ) -> None:
        from geometry_msgs.msg import Twist, TwistStamped
        from nav_msgs.msg import Odometry

        latest: dict = {}

        def _odom_cb(msg) -> None:
            p = msg.pose.pose
            latest["x"] = p.position.x
            latest["y"] = p.position.y
            latest["yaw"] = _yaw_from_quaternion(p.orientation)

        sub = self.create_subscription(
            Odometry, "/odom", _odom_cb, QoSPresetProfiles.SENSOR_DATA.value
        )
        with self._halt_lock:  # serialize rclpy entity creation vs halt()
            pub = self.ensure_halt_publisher(cmd_vel_topic, stamped)
        msg_type = TwistStamped if stamped else Twist
        started = time.monotonic()
        last_fb = 0.0
        status = "failed"
        try:
            while True:
                if self._drive_cancel.is_set():
                    status = "canceled"
                    break
                if time.monotonic() - started > timeout:
                    status = "aborted"
                    break
                if "x" not in latest:
                    time.sleep(0.05)  # wait for the first /odom sample
                    continue
                dx, dy = gx - latest["x"], gy - latest["y"]
                dist = math.hypot(dx, dy)
                if dist <= tolerance:
                    status = "succeeded"
                    break
                heading_err = math.atan2(
                    math.sin(math.atan2(dy, dx) - latest["yaw"]),
                    math.cos(math.atan2(dy, dx) - latest["yaw"]),
                )
                angular = max(-max_angular, min(max_angular, 1.5 * heading_err))
                # Gate forward speed by heading alignment (slow while turning
                # onto the bearing), but keep a crawl floor: an Ackermann can
                # only steer while rolling, so it must never fully stop to turn.
                align = max(0.2, math.cos(heading_err))
                forward = align * min(max_linear, 0.8 * dist + 0.2)
                msg = msg_type()
                twist = msg.twist if stamped else msg
                if stamped:
                    msg.header.stamp = self.get_clock().now().to_msg()
                twist.linear.x = float(forward)
                twist.angular.z = float(angular)
                pub.publish(msg)
                now = time.monotonic()
                if now - last_fb >= 0.5:
                    last_fb = now
                    _emit(
                        {
                            "event": "nav_feedback",
                            "tag": tag,
                            "distance_remaining": round(dist, 2),
                            "recoveries": 0,
                            "elapsed": round(now - started, 1),
                        }
                    )
                time.sleep(0.05)  # ~20 Hz control
        finally:
            stop = msg_type()
            if stamped:
                stop.header.stamp = self.get_clock().now().to_msg()
            for _ in range(3):  # pulse zero so the robot actually stops
                pub.publish(stop)
                time.sleep(0.02)
            self.destroy_subscription(sub)
            self._drive_active = False
            _emit({"event": "nav_result", "tag": tag, "status": status})

    def nav_cancel(self) -> dict:
        """Cancel our own goal AND everything else on the Nav2 action server.

        The own-handle path alone is not an emergency stop: a goal sent by a
        DIFFERENT process's bridge (TUI goal, WebUI stop) is invisible here,
        and Nav2's controller would keep streaming cmd_vel after our zero
        pulses. The server-side cancel-all covers every owner.
        """
        canceled = False
        if self._drive_active:
            # The odom driver stops within one control tick; no server round-trip.
            self._drive_cancel.set()
            canceled = True
        if self._nav_pending:
            self._cancel_on_accept = True
            canceled = True
        handle = self._nav_goal_handle
        if handle is not None:
            handle.cancel_goal_async()
            canceled = True
        # Only pay the Nav2 cancel-all service wait if we ever used Nav2 —
        # a pure odom-driver session has no action server to ask.
        if self._nav_client is not None and self._cancel_all_nav_goals():
            canceled = True
        if not canceled:
            return {"canceled": False, "detail": "no active navigation goal"}
        return {"canceled": True}

    def _cancel_all_nav_goals(self, timeout: float = 2.0) -> bool:
        """Ask the Nav2 action server to cancel ALL goals (zeroed goal id),
        waiting bounded for the reply so shutdown paths can't cut it off."""
        from action_msgs.srv import CancelGoal

        client = self.create_client(CancelGoal, "/navigate_to_pose/_action/cancel_goal")
        try:
            if not client.wait_for_service(timeout_sec=timeout):
                return False  # no Nav2 → nothing to cancel
            future = client.call_async(CancelGoal.Request())  # zeroed = cancel all
            done = threading.Event()
            future.add_done_callback(lambda _f: done.set())
            done.wait(timeout)
            response = future.result() if future.done() else None
            return bool(response is not None and response.goals_canceling)
        except Exception:
            return False
        finally:
            self.destroy_client(client)

    @property
    def nav_active(self) -> bool:
        return self._nav_goal_handle is not None or self._nav_pending or self._drive_active

    # -- emergency stop -------------------------------------------------------

    def ensure_halt_publisher(self, cmd_vel_topic: str, stamped: bool):
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
    ) -> dict:
        """EMERGENCY STOP: cancel any Nav2 goal, then pulse zero velocity.

        Zero is pulsed (not sent once) because a single message can lose the
        race against a controller that is still streaming motion commands.
        Serialized under a lock: the stdin loop and the watchdog thread may
        both call this, and concurrent rclpy entity churn is not safe.
        """
        with self._halt_lock:
            canceled = self.nav_cancel().get("canceled", False)

            from geometry_msgs.msg import Twist, TwistStamped

            pub = self.ensure_halt_publisher(cmd_vel_topic, stamped)
            # Best effort: give a cold publisher a moment to match its
            # subscriber instead of pulsing into the void.
            for _ in range(20):
                if pub.get_subscription_count() > 0:
                    break
                time.sleep(0.05)
            msg_type = TwistStamped if stamped else Twist
            for _ in range(max(1, pulses)):
                msg = msg_type()  # all-zero twist
                if stamped:
                    msg.header.stamp = self.get_clock().now().to_msg()
                pub.publish(msg)
                time.sleep(1.0 / rate_hz)
        return {"halted": True, "nav_canceled": canceled}

    # -- camera -------------------------------------------------------------

    def capture_frame(self, topic: str, timeout: float = 5.0) -> dict:
        """Grab one frame from an image topic; returns a temp file path."""
        from sensor_msgs.msg import CompressedImage, Image

        compressed = topic.endswith("/compressed") or "compressed" in topic
        msg_type = CompressedImage if compressed else Image
        got: list = []
        event = threading.Event()

        def _cb(msg) -> None:
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

    def watch(self, watch_id: int, topic: str, msg_type: str, throttle: float = 1.0) -> dict:
        """Stream messages from a topic as events, at most one per `throttle` seconds."""
        from rosidl_runtime_py.convert import message_to_ordereddict
        from rosidl_runtime_py.utilities import get_message

        cls = get_message(msg_type)
        last_emit = [0.0]

        def _cb(msg) -> None:
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

        sub = self.create_subscription(cls, topic, _cb, QoSPresetProfiles.SENSOR_DATA.value)
        self._watches[watch_id] = sub
        return {"watch_id": watch_id}

    def unwatch(self, watch_id: int) -> dict:
        sub = self._watches.pop(watch_id, None)
        if sub is not None:
            self.destroy_subscription(sub)
        return {"removed": sub is not None}


class WatchdogState:
    """Client-liveness watchdog: halt the robot when the client goes quiet.

    Disabled (timeout 0) until the client opts in via the `watchdog` op, so
    read-only uses of the bridge never publish anything.
    """

    RETRY_S = 2.0  # re-halt cadence while the client stays dead and nav stays active

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.last_rx = time.monotonic()
        self.last_halt: float | None = None
        self.timeout_s = 0.0
        self.cmd_vel_topic = "/cmd_vel"
        self.stamped = False

    def touch(self) -> None:
        with self.lock:
            self.last_rx = time.monotonic()
            self.last_halt = None  # client is back — watchdog state resets

    def configure(self, req: dict) -> dict:
        with self.lock:
            self.timeout_s = float(req.get("timeout", 0.0))
            self.cmd_vel_topic = str(req.get("cmd_vel_topic", "/cmd_vel"))
            self.stamped = bool(req.get("stamped", False))
        return {"watchdog_s": self.timeout_s}

    def should_halt(self) -> bool:
        """Expired, and either never halted or the retry cadence elapsed —
        an unacknowledged cancel must be retried in seconds, not timeout_s."""
        with self.lock:
            if self.timeout_s <= 0 or time.monotonic() - self.last_rx <= self.timeout_s:
                return False
            return self.last_halt is None or time.monotonic() - self.last_halt > self.RETRY_S

    def mark_halted(self) -> None:
        with self.lock:
            self.last_halt = time.monotonic()


def _watchdog_loop(node: BridgeNode, state: WatchdogState, stop: threading.Event) -> None:
    while not stop.wait(0.5):
        if node.nav_active and state.should_halt():
            try:
                node.halt(state.cmd_vel_topic, state.stamped)
                _emit({"event": "watchdog_halt", "reason": "client went quiet mid-navigation"})
            except Exception as exc:
                _emit({"event": "watchdog_halt", "reason": f"halt failed: {exc}"})
            state.mark_halted()


def _handle(node: BridgeNode, op: str, req: dict, watchdog: WatchdogState) -> dict:
    if op == "ping":
        return {"pong": True}
    if op == "pose":
        return node.get_pose(float(req.get("timeout", 2.0)))
    if op == "nav_send":
        return node.nav_send(
            req["x"], req["y"], req.get("yaw", 0.0), req.get("frame_id", "map"), req.get("tag", "")
        )
    if op == "drive_to_pose":
        return node.drive_to_pose(
            req["x"],
            req["y"],
            req.get("yaw", 0.0),
            tag=req.get("tag", ""),
            cmd_vel_topic=req.get("cmd_vel_topic", "/cmd_vel"),
            stamped=bool(req.get("stamped", False)),
            max_linear=float(req.get("max_linear", 1.0)),
            max_angular=float(req.get("max_angular", 2.0)),
            tolerance=float(req.get("tolerance", 0.3)),
            timeout=float(req.get("timeout", 600.0)),
        )
    if op == "nav_cancel":
        return node.nav_cancel()
    if op == "halt":
        return node.halt(
            req.get("cmd_vel_topic", "/cmd_vel"),
            bool(req.get("stamped", False)),
        )
    if op == "watchdog":
        result = watchdog.configure(req)
        # Pre-create the zero-velocity publisher NOW so DDS discovery is done
        # long before an emergency halt needs it.
        with node._halt_lock:
            node.ensure_halt_publisher(watchdog.cmd_vel_topic, watchdog.stamped)
        return result
    if op == "capture_frame":
        return node.capture_frame(req["topic"], float(req.get("timeout", 5.0)))
    if op == "watch":
        return node.watch(
            req["watch_id"], req["topic"], req["msg_type"], float(req.get("throttle", 1.0))
        )
    if op == "unwatch":
        return node.unwatch(req["watch_id"])
    raise RuntimeError(f"unknown op '{op}'")


def main() -> None:
    rclpy.init()
    node = BridgeNode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    spin = threading.Thread(target=executor.spin, daemon=True)
    spin.start()

    watchdog = WatchdogState()
    watchdog_stop = threading.Event()
    threading.Thread(
        target=_watchdog_loop, args=(node, watchdog, watchdog_stop), daemon=True
    ).start()

    _emit({"event": "ready"})

    # Ops that block for seconds must not hold up the request loop: an
    # emergency halt queued behind a camera capture would arrive seconds
    # late. They are read-only, so running them on worker threads is safe;
    # responses are matched by id client-side, order doesn't matter.
    slow_ops = {"capture_frame", "pose"}

    def _serve(req_id, op, req) -> None:
        try:
            _emit({"id": req_id, "ok": True, "result": _handle(node, op, req, watchdog)})
        except Exception as exc:  # keep serving; report the failure to the client
            _emit({"id": req_id, "ok": False, "error": str(exc)})

    for line in sys.stdin:  # EOF (parent died/closed) ends the bridge
        line = line.strip()
        if not line:
            continue
        watchdog.touch()
        req_id = None
        try:
            req = json.loads(line)
            req_id, op = req.get("id"), req.get("op")
            if op == "shutdown":
                _emit({"id": req_id, "ok": True, "result": {}})
                break
            if op in slow_ops:
                threading.Thread(target=_serve, args=(req_id, op, req), daemon=True).start()
            else:
                _serve(req_id, op, req)
        except Exception as exc:  # malformed line — report, keep serving
            _emit({"id": req_id, "ok": False, "error": str(exc)})

    watchdog_stop.set()
    # The client is gone (EOF or shutdown). A robot still executing a goal must
    # not keep driving unsupervised — same contract as the in-band watchdog.
    if node.nav_active:
        try:
            node.halt(watchdog.cmd_vel_topic, watchdog.stamped)
        except Exception:
            pass

    executor.shutdown()
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
