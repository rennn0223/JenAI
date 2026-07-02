from __future__ import annotations

import asyncio
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
    """

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._reader_task: asyncio.Task | None = None
        self._event_handlers: dict[str, list[Callable[[dict], None]]] = {}
        self._watch_handlers: dict[int, Callable[[dict], None]] = {}
        self._ready = asyncio.Event()

    @staticmethod
    def available() -> bool:
        ros_setup = os.environ.get("ROS_SETUP", _DEFAULT_ROS_SETUP)
        return Path(ros_setup).is_file() or shutil.which("ros2") is not None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self, timeout: float = 10.0) -> None:
        if self.running:
            return
        if not self.available():
            raise BridgeError("ROS2 is not installed (no setup script or ros2 on PATH).")

        ros_setup = os.environ.get("ROS_SETUP", _DEFAULT_ROS_SETUP)
        python = os.environ.get("JENAI_BRIDGE_PYTHON", "/usr/bin/python3")
        # Source ROS then exec the system python: works whether or not the
        # parent process already has the ROS environment.
        command = f'source "{ros_setup}" 2>/dev/null; exec "{python}" "{_BRIDGE_SCRIPT}"'
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")}
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

    async def stop(self) -> None:
        proc, self._proc = self._proc, None
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
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
                proc.kill()

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            line = await self._proc.stdout.readline()
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

    def clear_event_handlers(self, event: str) -> None:
        self._event_handlers.pop(event, None)

    async def request(self, op: str, timeout: float = 10.0, params: dict | None = None) -> dict:
        if not self.running:
            await self.start()
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

    async def nav_send(self, x: float, y: float, yaw: float = 0.0, frame_id: str = "map") -> None:
        await self.request(
            "nav_send", timeout=8.0, params={"x": x, "y": y, "yaw": yaw, "frame_id": frame_id}
        )

    async def nav_cancel(self) -> bool:
        result = await self.request("nav_cancel", timeout=5.0)
        return bool(result.get("canceled"))

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
