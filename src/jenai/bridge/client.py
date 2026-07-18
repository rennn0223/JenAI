"""venv-side asyncio client for the system-python rclpy bridge sidecar."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_BRIDGE_SCRIPT = Path(__file__).parent / "ros_bridge.py"
_DEFAULT_ROS_SETUP = "/opt/ros/jazzy/setup.bash"


class BridgeError(Exception):
    """Raised when the ROS bridge is unavailable or an operation fails."""


@dataclass(frozen=True)
class PoseInfo:
    x: float
    y: float
    yaw: float
    frame_id: str
    source: str


class RosBridgeClient:
    """Client for the system-python ROS bridge process.

    The bridge runs under /usr/bin/python3 (which has rclpy) with the ROS env
    sourced, completely outside the uv venv — that sidesteps the
    PYTHONPATH-shadowing problem while giving us live feedback streams the
    `ros2` CLI cannot provide.

    `domain_id` pins the bridge to that ROS_DOMAIN_ID, letting a second client
    talk to an isolated ROS graph (the digital twin) while the default client
    keeps whatever domain the environment has (the real robot).
    """

    def __init__(self, *, domain_id: int | None = None) -> None:
        self._domain_id = domain_id
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._reader_task: asyncio.Task | None = None
        self._event_handlers: dict[str, list[Callable[[dict], None]]] = {}
        self._watch_handlers: dict[int, Callable[[dict], None]] = {}
        self._ready = asyncio.Event()
        self._start_lock = asyncio.Lock()
        # Safety config survives the process: once set, EVERY spawn re-arms
        # the watchdog — including silent respawns via request(). Without
        # this, a crashed bridge would come back disarmed and no caller
        # would notice.
        self._safety: dict | None = None

    @staticmethod
    def available() -> bool:
        ros_setup = os.environ.get("ROS_SETUP", _DEFAULT_ROS_SETUP)
        return Path(ros_setup).is_file() or shutil.which("ros2") is not None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self, timeout: float = 10.0) -> None:
        """Spawn the bridge process and wait for its ready handshake.

        Idempotent and safe under concurrency (MCP tools may call in
        parallel): a lock ensures exactly one bridge process is spawned.
        Raises BridgeError when ROS is absent or the bridge never comes up.
        """
        async with self._start_lock:
            if self.running:
                return
            if not self.available():
                raise BridgeError("ROS2 is not installed (no setup script or ros2 on PATH).")
            await self._spawn(timeout)

    async def _spawn(self, timeout: float) -> None:
        ros_setup = os.environ.get("ROS_SETUP", _DEFAULT_ROS_SETUP)
        python = os.environ.get("JENAI_BRIDGE_PYTHON", "/usr/bin/python3")
        # Source ROS then exec the system python: works whether or not the
        # parent process already has the ROS environment.
        command = f'source "{ros_setup}" 2>/dev/null; exec "{python}" "{_BRIDGE_SCRIPT}"'
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")}
        if self._domain_id is not None:
            env["ROS_DOMAIN_ID"] = str(self._domain_id)
        self._proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        self._ready = asyncio.Event()
        self._reader_task = asyncio.create_task(self._read_loop())
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except TimeoutError:
            await self.stop()
            raise BridgeError("ROS bridge did not become ready (rclpy missing?).") from None
        if self._safety is not None:
            # Arm the dead-client watchdog on the fresh process. A failure
            # here means the bridge is already broken — fail the start rather
            # than hand out an unprotected, actuating bridge.
            try:
                await self._send_request("watchdog", params=self._safety)
            except BridgeError:
                await self.stop()
                raise

    async def stop(self) -> None:
        """Shut the bridge down cleanly, failing any in-flight requests."""
        proc, self._proc = self._proc, None
        reader_task, self._reader_task = self._reader_task, None
        if reader_task is not None:
            reader_task.cancel()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(BridgeError("bridge stopped"))
        self._pending.clear()
        if proc is not None and proc.returncode is None:
            try:
                proc.stdin.write(b'{"id": 0, "op": "shutdown"}\n')
                await proc.stdin.drain()
                await asyncio.wait_for(proc.wait(), 3.0)
            except (TimeoutError, OSError, ConnectionResetError):
                # kill() races the process's own exit — already-dead is fine.
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                # Reap the killed process — otherwise its transport lingers
                # until GC (zombie + "Exception ignored in __del__" noise).
                with contextlib.suppress(TimeoutError, OSError):
                    await asyncio.wait_for(proc.wait(), 2.0)
        if reader_task is not None:
            # Cancellation is not cleanup until the task has observed it.
            # Keep ownership until its stdout read has unwound so the event
            # loop never closes around a pending task/subprocess transport.
            with contextlib.suppress(asyncio.CancelledError):
                await reader_task

    async def _read_loop(self) -> None:
        # Capture the process stream for this task's lifetime. stop() clears
        # self._proc before cancelling us so a concurrent teardown cannot turn
        # the next read into an AttributeError while cancellation unwinds.
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        stdout = proc.stdout
        while True:
            line = await stdout.readline()
            if not line:  # bridge died
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(BridgeError("bridge process exited"))
                self._pending.clear()
                return
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "event" in payload:
                self._dispatch_event(payload)
                continue
            future = self._pending.pop(payload.get("id"), None)
            if future is not None and not future.done():
                if payload.get("ok"):
                    future.set_result(payload.get("result"))
                else:
                    future.set_exception(BridgeError(payload.get("error", "bridge error")))

    def _dispatch_event(self, payload: dict) -> None:
        event = payload["event"]
        if event == "ready":
            self._ready.set()
            return
        if event == "watch":
            handler = self._watch_handlers.get(payload.get("watch_id"))
            if handler is not None:
                handler(payload.get("data", {}))
            return
        for handler in self._event_handlers.get(event, []):
            handler(payload)

    def on_event(self, event: str, handler: Callable[[dict], None]) -> None:
        self._event_handlers.setdefault(event, []).append(handler)

    def off_event(self, event: str, handler: Callable[[dict], None]) -> None:
        handlers = self._event_handlers.get(event, [])
        if handler in handlers:
            handlers.remove(handler)

    async def request(self, op: str, timeout: float = 10.0, params: dict | None = None) -> dict:
        """Send one op to the bridge and await its response.

        `timeout` bounds the client-side wait; ops that also wait bridge-side
        (pose, capture_frame) take their own `timeout` inside `params`, so pass
        a client timeout comfortably larger than the bridge one.
        """
        if not self.running:
            await self.start()  # respawn re-arms the watchdog (see _spawn)
        return await self._send_request(op, timeout, params)

    async def _send_request(
        self, op: str, timeout: float = 10.0, params: dict | None = None
    ) -> dict:
        """request() without the auto-start — used inside start itself."""
        self._next_id += 1
        req_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        message = json.dumps({"id": req_id, "op": op, **(params or {})}) + "\n"
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(message.encode())
        await self._proc.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            self._pending.pop(req_id, None)
            raise BridgeError(f"bridge op '{op}' timed out after {timeout:.0f}s") from None

    # -- typed helpers -------------------------------------------------------

    async def ping(self) -> bool:
        result = await self.request("ping", timeout=5.0)
        return bool(result.get("pong"))

    async def get_pose(self, timeout: float = 3.0) -> PoseInfo:
        # /amcl_pose then /odom are each given `timeout`, so wait for both.
        result = await self.request(
            "pose", timeout=timeout * 2 + 2.0, params={"timeout": timeout}
        )
        return PoseInfo(
            x=result["x"],
            y=result["y"],
            yaw=result["yaw"],
            frame_id=result["frame_id"],
            source=result["source"],
        )

    async def nav_send(
        self, x: float, y: float, yaw: float = 0.0, frame_id: str = "map", tag: str = ""
    ) -> None:
        """Send a Nav2 goal; `tag` is echoed in nav_feedback/nav_result events so
        the caller can ignore stale events from an earlier (cancelled) goal."""
        await self.request(
            "nav_send",
            timeout=8.0,
            params={"x": x, "y": y, "yaw": yaw, "frame_id": frame_id, "tag": tag},
        )

    async def drive_to_pose(
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
        avoidance: dict | None = None,
    ) -> None:
        """Nav2-less point-to-point drive (odom→cmd_vel). Feedback/result flow
        through the SAME nav_feedback/nav_result events as nav_send, so
        navigate_live consumes them unchanged. `avoidance` (when enabled) folds
        a depth camera in for fail-closed local detours. See ros_bridge."""
        await self.request(
            "drive_to_pose",
            timeout=8.0,
            params={
                "x": x, "y": y, "yaw": yaw, "tag": tag,
                "cmd_vel_topic": cmd_vel_topic, "stamped": stamped,
                "max_linear": max_linear, "max_angular": max_angular,
                "tolerance": tolerance, "timeout": timeout,
                "avoidance": avoidance,
            },
        )

    async def nav_cancel(self) -> bool:
        result = await self.request("nav_cancel", timeout=5.0)
        return bool(result.get("canceled"))

    async def halt(self, cmd_vel_topic: str = "/cmd_vel", stamped: bool = False) -> bool:
        """EMERGENCY STOP: cancel any Nav2 goal and pulse zero velocity.
        Returns whether a navigation goal was canceled in the process."""
        result = await self.request(
            "halt", timeout=8.0, params={"cmd_vel_topic": cmd_vel_topic, "stamped": stamped}
        )
        return bool(result.get("nav_canceled"))

    async def configure_safety(
        self,
        *,
        watchdog_s: float = 6.0,
        cmd_vel_topic: str = "/cmd_vel",
        stamped: bool = False,
    ) -> None:
        """Arm the bridge-side watchdog: if the client goes quiet for
        `watchdog_s` while a Nav2 goal is active, the bridge halts the robot
        on its own. navigate_live's heartbeat keeps a healthy client alive.

        The config persists on this client: every (re)spawn re-arms
        automatically, so a crashed-and-respawned bridge can never silently
        run disarmed. Callers may invoke this before or after start().
        """
        self._safety = {
            "timeout": watchdog_s,
            "cmd_vel_topic": cmd_vel_topic,
            "stamped": stamped,
        }
        if self.running:
            await self._send_request("watchdog", params=self._safety)

    async def avoid_snapshot(
        self,
        path: str,
        depth_topic: str = "/depth",
        frames: int = 5,
        timeout: float = 10.0,
    ) -> dict:
        """Calibrate the avoidance floor reference (view must be EMPTY):
        save a per-pixel median depth frame to `path` (.npy) for
        AvoidanceProfile.floor_snapshot."""
        return await self.request(
            "avoid_snapshot",
            timeout=timeout + 3.0,
            params={
                "depth_topic": depth_topic,
                "path": path,
                "frames": frames,
                "timeout": timeout,
            },
        )

    async def capture_frame(self, topic: str, timeout: float = 5.0) -> Path:
        result = await self.request(
            "capture_frame", timeout=timeout + 3.0, params={"topic": topic, "timeout": timeout}
        )
        return Path(result["path"])

    async def watch(
        self,
        topic: str,
        msg_type: str,
        handler: Callable[[dict], None],
        throttle: float = 1.0,
    ) -> int:
        """Stream a topic's messages (as dicts) into `handler`, at most one per
        `throttle` seconds. `handler` runs on the reader task — keep it cheap
        and non-blocking (push to a queue if real work is needed). Returns a
        watch id for `unwatch`."""
        self._next_id += 1
        watch_id = self._next_id
        self._watch_handlers[watch_id] = handler
        await self.request(
            "watch",
            params={
                "watch_id": watch_id,
                "topic": topic,
                "msg_type": msg_type,
                "throttle": throttle,
            },
        )
        return watch_id

    async def unwatch(self, watch_id: int) -> None:
        self._watch_handlers.pop(watch_id, None)
        await self.request("unwatch", params={"watch_id": watch_id})
