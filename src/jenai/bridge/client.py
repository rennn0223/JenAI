"""venv-side asyncio client for the system-python rclpy bridge sidecar."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._wire import EventFrame, ResponseFrame, WireProtocolError, decode_frame, encode_request

_BRIDGE_SCRIPT = Path(__file__).parent / "ros_bridge.py"
_DEFAULT_ROS_SETUP = "/opt/ros/jazzy/setup.bash"
_BRIDGE_LAUNCHER = 'source "$1" 2>/dev/null; exec "$2" "$3"'
_STDERR_TAIL_LIMIT = 16 * 1024
_ERROR_DIAGNOSTIC_LIMIT = 2_000

logger = logging.getLogger(__name__)

BridgePayload = dict[str, Any]
EventHandler = Callable[[BridgePayload], None]


class BridgeError(Exception):
    """Raised when the ROS bridge is unavailable or an operation fails."""


def _require_bool(result: BridgePayload, field: str, operation: str) -> bool:
    value = result.get(field)
    if type(value) is not bool:
        raise BridgeError(f"invalid {operation} response: {field} must be a boolean")
    return value


def _require_int(result: BridgePayload, field: str, operation: str) -> int:
    value = result.get(field)
    if type(value) is not int:
        raise BridgeError(f"invalid {operation} response: {field} must be an integer")
    return value


def _require_finite_float(result: BridgePayload, field: str, operation: str) -> float:
    value = result.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BridgeError(f"invalid {operation} response: {field} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise BridgeError(f"invalid {operation} response: {field} must be finite")
    return parsed


def _require_text(
    result: BridgePayload,
    field: str,
    operation: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = result.get(field)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        qualifier = "text" if allow_empty else "non-empty text"
        raise BridgeError(f"invalid {operation} response: {field} must be {qualifier}")
    return value


def _require_finite_input(
    operation: str,
    field: str,
    value: object,
    *,
    context: str = "request",
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BridgeError(f"invalid {operation} {context}: {field} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise BridgeError(f"invalid {operation} {context}: {field} must be finite")
    return parsed


def _require_nonempty_input(operation: str, field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BridgeError(f"invalid {operation} request: {field} must be non-empty text")
    return value


def _require_bool_input(operation: str, field: str, value: object) -> bool:
    if type(value) is not bool:
        raise BridgeError(f"invalid {operation} request: {field} must be a boolean")
    return value


def _require_int_input(
    operation: str,
    field: str,
    value: object,
    *,
    minimum: int | None = None,
) -> int:
    if type(value) is not int:
        raise BridgeError(f"invalid {operation} request: {field} must be an integer")
    if minimum is not None and value < minimum:
        raise BridgeError(f"invalid {operation} request: {field} must be at least {minimum}")
    return value


def _require_positive_input(operation: str, field: str, value: object) -> float:
    parsed = _require_finite_input(operation, field, value)
    if parsed <= 0.0:
        raise BridgeError(f"invalid {operation} request: {field} must be positive")
    return parsed


def _bridge_process_args(ros_setup: str, python: str) -> tuple[str, ...]:
    """Build shell arguments without interpolating configuration into code.

    The setup and interpreter paths are positional shell arguments.  Treating
    them as data prevents quotes or shell metacharacters in environment values
    from changing the command that launches the bridge.
    """
    return (
        "bash",
        "-c",
        _BRIDGE_LAUNCHER,
        "jenai-ros-bridge",
        ros_setup,
        python,
        str(_BRIDGE_SCRIPT),
    )


@dataclass(frozen=True)
class PoseInfo:
    x: float
    y: float
    yaw: float
    frame_id: str
    source: str

    @classmethod
    def from_payload(cls, result: BridgePayload) -> PoseInfo:
        return cls(
            x=_require_finite_float(result, "x", "pose"),
            y=_require_finite_float(result, "y", "pose"),
            yaw=_require_finite_float(result, "yaw", "pose"),
            frame_id=_require_text(result, "frame_id", "pose"),
            source=_require_text(result, "source", "pose"),
        )


@dataclass(frozen=True)
class MapCellInfo:
    """Bounded evidence for one queried static-map cell."""

    in_bounds: bool
    free: bool
    value: int | None
    cell_x: int
    cell_y: int
    width: int
    height: int
    resolution: float
    origin_x: float
    origin_y: float
    frame_id: str
    source: str

    @classmethod
    def from_payload(cls, result: BridgePayload) -> MapCellInfo:
        """Validate the sidecar's occupancy evidence without coercing types.

        The map-cell result is a motion-safety gate. Values such as ``"false"``
        and ``100`` must never become truthy booleans accidentally, and a
        contradictory ``free=true, value=100`` response must fail closed.
        """

        in_bounds = _require_bool(result, "in_bounds", "map_cell")
        free = _require_bool(result, "free", "map_cell")
        cell_x = _require_int(result, "cell_x", "map_cell")
        cell_y = _require_int(result, "cell_y", "map_cell")
        width = _require_int(result, "width", "map_cell")
        height = _require_int(result, "height", "map_cell")
        resolution = _require_finite_float(result, "resolution", "map_cell")
        origin_x = _require_finite_float(result, "origin_x", "map_cell")
        origin_y = _require_finite_float(result, "origin_y", "map_cell")
        frame_id = _require_text(result, "frame_id", "map_cell")
        source = _require_text(result, "source", "map_cell")

        raw_value = result.get("value")
        if raw_value is None:
            value = None
        elif type(raw_value) is int:
            value = raw_value
        else:
            raise BridgeError("invalid map_cell response: value must be an integer or null")

        if width <= 0 or height <= 0:
            raise BridgeError("invalid map_cell response: map dimensions must be positive")
        if resolution <= 0.0:
            raise BridgeError("invalid map_cell response: resolution must be positive")

        coordinates_in_bounds = 0 <= cell_x < width and 0 <= cell_y < height
        if in_bounds:
            if not coordinates_in_bounds:
                raise BridgeError(
                    "invalid map_cell response: in-bounds coordinates exceed map dimensions"
                )
            if value is None or not -1 <= value <= 100:
                raise BridgeError(
                    "invalid map_cell response: in-bounds occupancy must be between -1 and 100"
                )
            if free != (value == 0):
                raise BridgeError(
                    "invalid map_cell response: free flag contradicts occupancy value"
                )
        else:
            if coordinates_in_bounds:
                raise BridgeError(
                    "invalid map_cell response: out-of-bounds coordinates are inside the map"
                )
            if free or value is not None:
                raise BridgeError(
                    "invalid map_cell response: out-of-bounds cells cannot be free or occupied"
                )

        return cls(
            in_bounds=in_bounds,
            free=free,
            value=value,
            cell_x=cell_x,
            cell_y=cell_y,
            width=width,
            height=height,
            resolution=resolution,
            origin_x=origin_x,
            origin_y=origin_y,
            frame_id=frame_id,
            source=source,
        )


@dataclass(frozen=True)
class MapIdentityInfo:
    """Canonical identity and geometry of the active static map."""

    algorithm: str
    digest: str
    width: int
    height: int
    resolution: float
    origin_x: float
    origin_y: float
    origin_yaw: float
    frame_id: str
    source: str

    @classmethod
    def from_payload(cls, result: BridgePayload) -> MapIdentityInfo:
        operation = "map_identity"
        algorithm = _require_text(result, "algorithm", operation)
        digest = _require_text(result, "digest", operation)
        width = _require_int(result, "width", operation)
        height = _require_int(result, "height", operation)
        resolution = _require_finite_float(result, "resolution", operation)
        origin_x = _require_finite_float(result, "origin_x", operation)
        origin_y = _require_finite_float(result, "origin_y", operation)
        origin_yaw = _require_finite_float(result, "origin_yaw", operation)
        frame_id = _require_text(result, "frame_id", operation)
        source = _require_text(result, "source", operation)

        if algorithm != "sha256-occupancy-grid-v1":
            raise BridgeError("invalid map_identity response: unsupported algorithm")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise BridgeError("invalid map_identity response: digest must be lowercase SHA-256")
        if width <= 0 or height <= 0:
            raise BridgeError("invalid map_identity response: map dimensions must be positive")
        if resolution <= 0.0:
            raise BridgeError("invalid map_identity response: resolution must be positive")

        return cls(
            algorithm=algorithm,
            digest=digest,
            width=width,
            height=height,
            resolution=resolution,
            origin_x=origin_x,
            origin_y=origin_y,
            origin_yaw=origin_yaw,
            frame_id=frame_id,
            source=source,
        )


@dataclass(frozen=True)
class NavPlanInfo:
    """Bounded evidence from Nav2's read-only path planner."""

    feasible: bool
    pose_count: int
    path_length_m: float
    planning_time_s: float
    error_code: int
    error_name: str
    error_message: str

    @classmethod
    def from_payload(cls, result: BridgePayload) -> NavPlanInfo:
        feasible = _require_bool(result, "feasible", "nav_plan")
        pose_count = _require_int(result, "pose_count", "nav_plan")
        path_length_m = _require_finite_float(result, "path_length_m", "nav_plan")
        planning_time_s = _require_finite_float(result, "planning_time_s", "nav_plan")
        error_code = _require_int(result, "error_code", "nav_plan")
        error_name = _require_text(result, "error_name", "nav_plan")
        error_message = _require_text(
            result,
            "error_message",
            "nav_plan",
            allow_empty=True,
        )
        if pose_count < 0 or path_length_m < 0.0 or planning_time_s < 0.0 or error_code < 0:
            raise BridgeError(
                "invalid nav_plan response: counts, lengths and times cannot be negative"
            )
        if feasible and (pose_count == 0 or error_code != 0):
            raise BridgeError(
                "invalid nav_plan response: feasible path requires poses and a zero error code"
            )
        return cls(
            feasible=feasible,
            pose_count=pose_count,
            path_length_m=path_length_m,
            planning_time_s=planning_time_s,
            error_code=error_code,
            error_name=error_name,
            error_message=error_message,
        )


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
        self._pending: dict[int, asyncio.Future[BridgePayload]] = {}
        self._next_id = 0
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail = bytearray()
        self._event_handlers: dict[str, list[EventHandler]] = {}
        self._watch_handlers: dict[int, EventHandler] = {}
        self._ready = asyncio.Event()
        self._start_lock = asyncio.Lock()
        # Safety config survives the process: once set, EVERY spawn re-arms
        # the watchdog — including silent respawns via request(). Without
        # this, a crashed bridge would come back disarmed and no caller
        # would notice.
        self._safety: BridgePayload | None = None

    @staticmethod
    def available() -> bool:
        ros_setup = os.environ.get("ROS_SETUP", _DEFAULT_ROS_SETUP)
        return Path(ros_setup).is_file() or shutil.which("ros2") is not None

    @property
    def running(self) -> bool:
        return (
            self._proc is not None
            and self._proc.returncode is None
            and self._reader_task is not None
            and not self._reader_task.done()
        )

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
        # Source ROS then exec system Python.  Paths stay positional arguments,
        # never interpolated shell source, so environment values cannot inject
        # an extra command into this safety-critical process boundary.
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")}
        if self._domain_id is not None:
            env["ROS_DOMAIN_ID"] = str(self._domain_id)
        self._proc = await asyncio.create_subprocess_exec(
            *_bridge_process_args(ros_setup, python),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._stderr_tail = bytearray()
        stderr = self._proc.stderr
        if stderr is None:
            await self.stop()
            raise BridgeError("ROS bridge stderr pipe was not created")
        self._stderr_task = asyncio.create_task(self._drain_stderr(stderr, self._stderr_tail))
        self._ready = asyncio.Event()
        self._reader_task = asyncio.create_task(self._read_loop())
        reader_task = self._reader_task
        ready_waiter = asyncio.create_task(self._ready.wait())
        try:
            await asyncio.wait(
                {ready_waiter, reader_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not ready_waiter.done() or not self.running:
                detail = (
                    "ROS bridge exited before its ready handshake."
                    if reader_task.done()
                    else "ROS bridge did not become ready before the startup timeout."
                )
                await self.stop()
                raise BridgeError(detail + self._diagnostic_suffix())
        finally:
            ready_waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ready_waiter
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
        stderr_task, self._stderr_task = self._stderr_task, None
        if reader_task is not None:
            reader_task.cancel()
        self._fail_pending(BridgeError("bridge stopped"))
        if proc is not None and proc.returncode is None:
            try:
                stdin = proc.stdin
                if stdin is None:
                    raise BrokenPipeError("bridge stdin is unavailable")
                stdin.write(b'{"id": 0, "op": "shutdown"}\n')
                await stdin.drain()
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
        if stderr_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(stderr_task), 1.0)
            except TimeoutError:
                stderr_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stderr_task

    @staticmethod
    async def _drain_stderr(
        stream: asyncio.StreamReader,
        destination: bytearray,
    ) -> None:
        """Drain stderr continuously while retaining only a bounded tail."""
        while chunk := await stream.read(4096):
            destination.extend(chunk)
            overflow = len(destination) - _STDERR_TAIL_LIMIT
            if overflow > 0:
                del destination[:overflow]

    def _diagnostic_suffix(self) -> str:
        """Return a small human-readable subprocess diagnostic, if present."""
        text = self._stderr_tail.decode("utf-8", errors="replace").strip()
        if not text:
            return ""
        if len(text) > _ERROR_DIAGNOSTIC_LIMIT:
            text = "…" + text[-_ERROR_DIAGNOSTIC_LIMIT:]
        return f" Bridge stderr: {text}"

    async def _read_loop(self) -> None:
        # Capture the process stream for this task's lifetime. stop() clears
        # self._proc before cancelling us so a concurrent teardown cannot turn
        # the next read into an AttributeError while cancellation unwinds.
        proc = self._proc
        if proc is None or proc.stdout is None:
            self._fail_pending(BridgeError("ROS bridge stdout pipe is unavailable"))
            return
        stdout = proc.stdout
        try:
            while True:
                line = await stdout.readline()
                if not line:
                    raise BridgeError("bridge process exited")
                frame = decode_frame(line)
                if frame is None:
                    logger.warning("Ignoring a malformed JSON frame from the ROS bridge.")
                    continue
                if isinstance(frame, EventFrame):
                    self._dispatch_event(frame.payload)
                    continue
                if not isinstance(frame, ResponseFrame):
                    raise BridgeError("bridge emitted an unsupported frame type")
                future = self._pending.pop(frame.request_id, None)
                if future is not None and not future.done():
                    if frame.ok:
                        future.set_result(frame.result)
                    else:
                        future.set_exception(BridgeError(frame.error or "bridge error"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A dead stdout reader makes the process unusable even if the OS
            # still reports it alive.  Terminate and reap it so request() can
            # safely start a fresh, watchdog-armed sidecar next time.
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(OSError):
                    await proc.wait()
            stderr_task = self._stderr_task
            if stderr_task is not None and not stderr_task.done():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(stderr_task), 1.0)
            message = str(exc) + self._diagnostic_suffix()
            error = BridgeError(message)
            self._fail_pending(error)
            if self._proc is proc:
                self._proc = None
            logger.warning("ROS bridge reader stopped: %s", error)

    def _fail_pending(self, error: BridgeError) -> None:
        """Fail and forget every request owned by the current bridge."""
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    @staticmethod
    def _invoke_handler(handler: EventHandler, payload: BridgePayload) -> None:
        """Keep one consumer callback from taking down the bridge reader."""
        try:
            handler(payload)
        except Exception:
            logger.exception("ROS bridge event handler failed")

    def _dispatch_event(self, payload: BridgePayload) -> None:
        event = str(payload["event"])
        if event == "ready":
            self._ready.set()
            return
        if event == "watch":
            watch_id = payload.get("watch_id")
            handler = (
                self._watch_handlers.get(watch_id)
                if isinstance(watch_id, int) and not isinstance(watch_id, bool)
                else None
            )
            if handler is not None:
                data = payload.get("data", {})
                self._invoke_handler(handler, data if isinstance(data, dict) else {})
            return
        # Snapshot the list: a callback may unsubscribe itself safely.
        for handler in tuple(self._event_handlers.get(event, ())):
            self._invoke_handler(handler, payload)

    def on_event(self, event: str, handler: EventHandler) -> None:
        self._event_handlers.setdefault(event, []).append(handler)

    def off_event(self, event: str, handler: EventHandler) -> None:
        handlers = self._event_handlers.get(event, [])
        if handler in handlers:
            handlers.remove(handler)

    async def request(
        self, op: str, timeout: float = 10.0, params: BridgePayload | None = None
    ) -> BridgePayload:
        """Send one op to the bridge and await its response.

        `timeout` bounds the client-side wait; ops that also wait bridge-side
        (pose, capture_frame) take their own `timeout` inside `params`, so pass
        a client timeout comfortably larger than the bridge one.
        """
        _require_nonempty_input("bridge", "op", op)
        _require_positive_input("bridge", "timeout", timeout)
        if params is not None and not isinstance(params, dict):
            raise BridgeError("invalid bridge request: params must be an object")
        if not self.running:
            await self.start()  # respawn re-arms the watchdog (see _spawn)
        return await self._send_request(op, timeout, params)

    async def _send_request(
        self, op: str, timeout: float = 10.0, params: BridgePayload | None = None
    ) -> BridgePayload:
        """request() without the auto-start — used inside start itself."""
        _require_nonempty_input("bridge", "op", op)
        _require_positive_input("bridge", "timeout", timeout)
        if params is not None and not isinstance(params, dict):
            raise BridgeError("invalid bridge request: params must be an object")
        self._next_id += 1
        req_id = self._next_id
        future: asyncio.Future[BridgePayload] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        try:
            message = encode_request(req_id, op, params)
        except WireProtocolError as exc:
            self._pending.pop(req_id, None)
            raise BridgeError(f"invalid bridge request: {exc}") from exc
        try:
            proc = self._proc
            if proc is None or proc.stdin is None:
                raise BridgeError("bridge process is not writable")
            proc.stdin.write(message)
            await proc.stdin.drain()
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            raise BridgeError(f"bridge op '{op}' timed out after {timeout:.0f}s") from None
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            raise BridgeError(f"bridge op '{op}' could not be sent: {exc}") from exc
        finally:
            # Cancellation, timeout and transport failures must not leak a
            # future that a later response could accidentally resolve.
            self._pending.pop(req_id, None)

    # -- typed helpers -------------------------------------------------------

    async def ping(self) -> bool:
        result = await self.request("ping", timeout=5.0)
        return _require_bool(result, "pong", "ping")

    async def get_pose(self, timeout: float = 3.0) -> PoseInfo:
        # /amcl_pose then /odom are each given `timeout`, so wait for both.
        parsed_timeout = _require_finite_input("pose", "timeout", timeout)
        if parsed_timeout <= 0.0:
            raise BridgeError("invalid pose request: timeout must be positive")
        result = await self.request("pose", timeout=timeout * 2 + 2.0, params={"timeout": timeout})
        return PoseInfo.from_payload(result)

    async def map_cell(self, x: float, y: float, *, timeout: float = 3.0) -> MapCellInfo:
        """Read one cell from the latched static map without commanding motion."""
        for name, value in (("x", x), ("y", y), ("timeout", timeout)):
            _require_finite_input("map_cell", name, value, context="query")
        if timeout <= 0.0:
            raise BridgeError("invalid map_cell query: timeout must be positive")
        result = await self.request(
            "map_cell",
            timeout=timeout + 2.0,
            params={"x": x, "y": y, "timeout": timeout},
        )
        return MapCellInfo.from_payload(result)

    async def map_identity(self, *, timeout: float = 3.0) -> MapIdentityInfo:
        """Read the canonical identity of the latched static map."""
        parsed_timeout = _require_finite_input("map_identity", "timeout", timeout, context="query")
        if parsed_timeout <= 0.0:
            raise BridgeError("invalid map_identity query: timeout must be positive")
        result = await self.request(
            "map_identity", timeout=timeout + 2.0, params={"timeout": timeout}
        )
        return MapIdentityInfo.from_payload(result)

    async def nav_send(
        self, x: float, y: float, yaw: float = 0.0, frame_id: str = "map", tag: str = ""
    ) -> None:
        """Send a Nav2 goal; `tag` is echoed in nav_feedback/nav_result events so
        the caller can ignore stale events from an earlier (cancelled) goal."""
        for name, value in (("x", x), ("y", y), ("yaw", yaw)):
            _require_finite_input("nav_send", name, value)
        _require_nonempty_input("nav_send", "frame_id", frame_id)
        if not isinstance(tag, str):
            raise BridgeError("invalid nav_send request: tag must be text")
        await self.request(
            "nav_send",
            timeout=8.0,
            params={"x": x, "y": y, "yaw": yaw, "frame_id": frame_id, "tag": tag},
        )

    async def nav_plan(
        self,
        x: float,
        y: float,
        yaw: float = 0.0,
        *,
        frame_id: str = "map",
        timeout: float = 5.0,
    ) -> NavPlanInfo:
        """Ask Nav2 for a path without sending a movement goal."""
        for name, value in (("x", x), ("y", y), ("yaw", yaw), ("timeout", timeout)):
            _require_finite_input("nav_plan", name, value)
        if timeout <= 0.0:
            raise BridgeError("invalid nav_plan request: timeout must be positive")
        _require_nonempty_input("nav_plan", "frame_id", frame_id)
        result = await self.request(
            "nav_plan",
            timeout=timeout + 3.0,
            params={
                "x": x,
                "y": y,
                "yaw": yaw,
                "frame_id": frame_id,
                "timeout": timeout,
            },
        )
        return NavPlanInfo.from_payload(result)

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
        odom_timeout_s: float = 1.0,
        timeout: float = 600.0,
        avoidance: BridgePayload | None = None,
    ) -> None:
        """Nav2-less point-to-point drive (odom→cmd_vel). Feedback/result flow
        through the SAME nav_feedback/nav_result events as nav_send, so
        navigate_live consumes them unchanged. `avoidance` (when enabled) folds
        a depth camera in for fail-closed local detours. See ros_bridge."""
        for name, value in (("x", x), ("y", y), ("yaw", yaw)):
            _require_finite_input("drive_to_pose", name, value)
        _require_nonempty_input("drive_to_pose", "cmd_vel_topic", cmd_vel_topic)
        _require_bool_input("drive_to_pose", "stamped", stamped)
        for name, value in (
            ("max_linear", max_linear),
            ("max_angular", max_angular),
            ("tolerance", tolerance),
            ("odom_timeout_s", odom_timeout_s),
            ("timeout", timeout),
        ):
            _require_positive_input("drive_to_pose", name, value)
        if not isinstance(tag, str):
            raise BridgeError("invalid drive_to_pose request: tag must be text")
        if avoidance is not None and not isinstance(avoidance, dict):
            raise BridgeError("invalid drive_to_pose request: avoidance must be an object or null")
        await self.request(
            "drive_to_pose",
            timeout=8.0,
            params={
                "x": x,
                "y": y,
                "yaw": yaw,
                "tag": tag,
                "cmd_vel_topic": cmd_vel_topic,
                "stamped": stamped,
                "max_linear": max_linear,
                "max_angular": max_angular,
                "tolerance": tolerance,
                "odom_timeout_s": odom_timeout_s,
                "timeout": timeout,
                "avoidance": avoidance,
            },
        )

    async def nav_cancel(self) -> bool:
        result = await self.request("nav_cancel", timeout=5.0)
        return _require_bool(result, "canceled", "nav_cancel")

    async def halt(self, cmd_vel_topic: str = "/cmd_vel", stamped: bool = False) -> bool:
        """EMERGENCY STOP: cancel any Nav2 goal and pulse zero velocity.
        Returns whether a navigation goal was canceled in the process."""
        _require_nonempty_input("halt", "cmd_vel_topic", cmd_vel_topic)
        _require_bool_input("halt", "stamped", stamped)
        result = await self.request(
            "halt", timeout=8.0, params={"cmd_vel_topic": cmd_vel_topic, "stamped": stamped}
        )
        halted = _require_bool(result, "halted", "halt")
        nav_canceled = _require_bool(result, "nav_canceled", "halt")
        if not halted:
            raise BridgeError(
                "invalid halt response: sidecar did not confirm zero-velocity delivery"
            )
        return nav_canceled

    async def configure_safety(
        self,
        *,
        watchdog_s: float = 6.0,
        cmd_vel_topic: str = "/cmd_vel",
        stamped: bool = False,
        pose_jump_threshold_m: float = 5.0,
        pose_jump_window_s: float = 2.0,
    ) -> None:
        """Arm the bridge-side watchdog: if the client goes quiet for
        `watchdog_s` while a Nav2 goal is active, the bridge halts the robot
        on its own. navigate_live's heartbeat keeps a healthy client alive.

        The config persists on this client: every (re)spawn re-arms
        automatically, so a crashed-and-respawned bridge can never silently
        run disarmed. Callers may invoke this before or after start().
        """
        for name, value in (
            ("watchdog_s", watchdog_s),
            ("pose_jump_threshold_m", pose_jump_threshold_m),
            ("pose_jump_window_s", pose_jump_window_s),
        ):
            _require_positive_input("configure_safety", name, value)
        _require_nonempty_input("configure_safety", "cmd_vel_topic", cmd_vel_topic)
        _require_bool_input("configure_safety", "stamped", stamped)
        self._safety = {
            "timeout": watchdog_s,
            "cmd_vel_topic": cmd_vel_topic,
            "stamped": stamped,
            "pose_jump_threshold_m": pose_jump_threshold_m,
            "pose_jump_window_s": pose_jump_window_s,
        }
        if self.running:
            await self._send_request("watchdog", params=self._safety)

    async def avoid_snapshot(
        self,
        path: str,
        depth_topic: str = "/depth",
        frames: int = 5,
        timeout: float = 10.0,
    ) -> BridgePayload:
        """Calibrate the avoidance floor reference (view must be EMPTY):
        save a per-pixel median depth frame to `path` (.npy) for
        AvoidanceProfile.floor_snapshot."""
        _require_nonempty_input("avoid_snapshot", "path", path)
        _require_nonempty_input("avoid_snapshot", "depth_topic", depth_topic)
        _require_int_input("avoid_snapshot", "frames", frames, minimum=1)
        _require_positive_input("avoid_snapshot", "timeout", timeout)
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
        _require_nonempty_input("capture_frame", "topic", topic)
        _require_positive_input("capture_frame", "timeout", timeout)
        result = await self.request(
            "capture_frame", timeout=timeout + 3.0, params={"topic": topic, "timeout": timeout}
        )
        return Path(_require_text(result, "path", "capture_frame"))

    async def watch(
        self,
        topic: str,
        msg_type: str,
        handler: EventHandler,
        throttle: float = 1.0,
    ) -> int:
        """Stream a topic's messages (as dicts) into `handler`, at most one per
        `throttle` seconds. `handler` runs on the reader task — keep it cheap
        and non-blocking (push to a queue if real work is needed). Returns a
        watch id for `unwatch`."""
        _require_nonempty_input("watch", "topic", topic)
        _require_nonempty_input("watch", "msg_type", msg_type)
        parsed_throttle = _require_finite_input("watch", "throttle", throttle)
        if parsed_throttle < 0.0:
            raise BridgeError("invalid watch request: throttle cannot be negative")
        if not callable(handler):
            raise BridgeError("invalid watch request: handler must be callable")
        self._next_id += 1
        watch_id = self._next_id
        self._watch_handlers[watch_id] = handler
        try:
            await self.request(
                "watch",
                params={
                    "watch_id": watch_id,
                    "topic": topic,
                    "msg_type": msg_type,
                    "throttle": throttle,
                },
            )
        except BaseException:
            # A rejected request or caller cancellation never leaves a callback
            # that can no longer be unregistered from the sidecar.
            self._watch_handlers.pop(watch_id, None)
            raise
        return watch_id

    async def unwatch(self, watch_id: int) -> None:
        _require_int_input("unwatch", "watch_id", watch_id, minimum=1)
        self._watch_handlers.pop(watch_id, None)
        await self.request("unwatch", params={"watch_id": watch_id})
