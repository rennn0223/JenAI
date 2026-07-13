"""http.server WebUI: endpoints, token auth, one-shot confirm escrow, PoseCache."""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import secrets
import socket
import threading
import time
from collections.abc import Callable
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from jenai.adapters import ros2_adapter
from jenai.adapters.locations import LocationsFileError, load_locations
from jenai.bridge import BridgeError, RosBridgeClient
from jenai.config.models import AppConfig
from jenai.doctor import run_doctor
from jenai.providers.chat import chat_model_name
from jenai.state.runs import RunStore
from jenai.tools.ros2_core import _kind_hint
from jenai.tools.safety import halt_robot
from jenai.webui.commands import run_web_command, run_web_confirm
from jenai.webui.render import render_dashboard_html, render_main

_MAX_JSON_BODY = 64 * 1024


class StatusCache:
    """Coalesced health snapshots shared by every HTTP handler thread."""

    def __init__(
        self,
        *,
        doctor_ttl_s: float = 30.0,
        ros_ttl_s: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._doctor_ttl_s = max(0.0, doctor_ttl_s)
        self._ros_ttl_s = max(0.0, ros_ttl_s)
        self._clock = clock
        self._lock = threading.Lock()
        self._doctor: Any = None
        self._doctor_at: float | None = None
        self._ros: dict[str, Any] | None = None
        self._ros_at: float | None = None

    def doctor(self, config_path: Path):
        """Run the subprocess-backed doctor at most once per TTL."""
        with self._lock:
            now = self._clock()
            if (
                self._doctor is None
                or self._doctor_at is None
                or now - self._doctor_at >= self._doctor_ttl_s
            ):
                self._doctor = run_doctor(config_path, include_nav=False)
                self._doctor_at = now
            return self._doctor

    def ros_snapshot(self) -> dict[str, Any]:
        """List the ROS graph at most once per TTL across all browser tabs."""
        with self._lock:
            now = self._clock()
            if (
                self._ros is None
                or self._ros_at is None
                or now - self._ros_at >= self._ros_ttl_s
            ):
                self._ros = _ros_snapshot()
                self._ros_at = now
            return self._ros

    def build_status(
        self,
        config: AppConfig,
        config_path: Path,
        *,
        run_store: RunStore | None = None,
    ) -> dict[str, Any]:
        return build_status_payload(
            config,
            config_path,
            run_store=run_store,
            doctor_result=self.doctor(config_path),
            ros_snapshot=self.ros_snapshot(),
        )


class _PendingConfirms:
    """Server-side store binding a previewed robot action to a one-time token.

    The browser only ever receives an opaque ``confirm_id``; the actual action
    dict stays here and is executed exactly once, when that id is confirmed. So
    (a) a client cannot fabricate or tamper with what it confirms — it can only
    release an action the server already previewed and validated — and (b) a
    blind POST to ``/api/confirm`` without a valid, unused id does nothing. This
    turns the confirm step into a real server-side gate rather than a cosmetic
    button — a second layer under the session token auth (see THREAT_MODEL.md).
    """

    def __init__(self, max_entries: int = 32, ttl_s: float = 120.0) -> None:
        self._items: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()
        self._max = max_entries
        self._ttl_s = max(0.0, ttl_s)

    def _purge_expired(self, now: float) -> None:
        expired = [
            token for token, (created, _) in self._items.items()
            if now - created >= self._ttl_s
        ]
        for token in expired:
            self._items.pop(token, None)

    def put(self, action: dict) -> str:
        token = secrets.token_urlsafe(16)
        with self._lock:
            now = time.monotonic()
            self._purge_expired(now)
            self._items[token] = (now, action)
            while len(self._items) > self._max:  # bound memory; evict oldest
                self._items.pop(next(iter(self._items)), None)
        return token

    def pop(self, token: str) -> dict | None:
        if not token:
            return None
        with self._lock:
            item = self._items.pop(token, None)
            if item is None:
                return None
            created, action = item
            if time.monotonic() - created >= self._ttl_s:
                return None
            return action

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class PoseCache:
    """Latest robot pose, refreshed by a daemon thread owning one bridge client.

    The HTTP handlers are sync and per-request (`asyncio.run`), so they cannot
    share a loop-bound RosBridgeClient. One background thread runs its own
    event loop + bridge and publishes the latest pose here; /api/map just reads.
    """

    def __init__(self, refresh_s: float = 1.0, retry_after_s: float = 30.0) -> None:
        self.latest: dict[str, Any] | None = None
        self.pose_error: str | None = None
        self._refresh_s = refresh_s
        self._retry_after_s = retry_after_s
        self._started = False
        self._last_exit: float | None = None
        self._lock = threading.Lock()
        # Live loop + client refs while _loop runs: lets /api/stop submit the
        # halt to the ALREADY-RUNNING bridge instead of paying a 1–3s bridge
        # cold start in the middle of an emergency.
        self._loop_ref: asyncio.AbstractEventLoop | None = None
        self._client_ref: RosBridgeClient | None = None

    def submit(self, coro_factory, timeout: float = 10.0):
        """Run `coro_factory(client)` on the cache's live bridge loop.

        Returns the result, or None when no live bridge is available (caller
        falls back to its own path). Thread-safe: called from HTTP threads.
        """
        loop, client = self._loop_ref, self._client_ref
        if loop is None or client is None or not loop.is_running():
            return None
        try:
            future = asyncio.run_coroutine_threadsafe(coro_factory(client), loop)
            return future.result(timeout)
        except Exception:
            return None

    def ensure_started(self) -> None:
        if not RosBridgeClient.available():
            return  # don't latch: ROS may be sourced/installed later
        with self._lock:
            if self._started:
                return
            if (
                self._last_exit is not None
                and time.monotonic() - self._last_exit < self._retry_after_s
            ):
                # The last bridge attempt just died. Back off instead of letting
                # the map card's 2s polling respawn a bridge subprocess forever
                # when ROS looks present but the bridge can never come up.
                return
            self._started = True
        threading.Thread(target=self._run_loop, daemon=True).start()

    def _record_pose(self, pose: Any) -> None:
        """Publish one finite pose, or invalidate the cache explicitly."""
        if all(math.isfinite(value) for value in (pose.x, pose.y, pose.yaw)):
            self.latest = {
                "x": pose.x,
                "y": pose.y,
                "yaw": pose.yaw,
                "frame_id": pose.frame_id,
                "source": pose.source,
                "ts": time.time(),
            }
            self.pose_error = None
            return

        # Python's json encoder emits NaN/Infinity by default, which
        # response.json() rejects in the browser. More importantly, an invalid
        # localization must not leave a previously-valid marker looking current.
        self.latest = None
        self.pose_error = "invalid_pose"

    def _run_loop(self) -> None:
        try:
            asyncio.run(self._loop())
        finally:
            # Whatever ended the loop (bridge failed to start, crashed, ROS went
            # away), un-latch so a later /api/map request can try again — after
            # the backoff window. Clear the pose BEFORE un-latching (inside the
            # lock) so this dying thread can never clobber a pose a successor
            # thread has already published.
            with self._lock:
                self.latest = None
                self.pose_error = None
                self._last_exit = time.monotonic()
                self._started = False

    async def _loop(self) -> None:
        client = RosBridgeClient()
        try:
            await client.start()
            self._loop_ref = asyncio.get_running_loop()
            self._client_ref = client
            while True:
                try:
                    pose = await client.get_pose(timeout=2.0)
                    self._record_pose(pose)
                except BridgeError:
                    self.latest = None
                    self.pose_error = None
                await asyncio.sleep(self._refresh_s)
        except BridgeError:
            pass
        finally:
            self._loop_ref = None
            self._client_ref = None
            await client.stop()


def build_map_payload(
    config: AppConfig, config_path: Path, pose_cache: PoseCache | None
) -> dict[str, Any]:
    if pose_cache is not None:
        pose_cache.ensure_started()
    locations: list[dict[str, Any]] = []
    locations_path = config.resolved_locations_path(config_path)
    if locations_path is not None and locations_path.exists():
        try:
            locations = [
                {"name": loc.name, "x": loc.pose.x, "y": loc.pose.y, "frame_id": loc.frame_id}
                for loc in load_locations(locations_path)
            ]
        except LocationsFileError:
            pass
    pose = pose_cache.latest if pose_cache is not None else None
    pose_error = pose_cache.pose_error if pose_cache is not None else None
    # A pose older than 5s is stale (bridge hiccup / robot stopped publishing).
    if pose is not None and time.time() - pose.get("ts", 0) > 5.0:
        pose = None
    if pose is not None:
        try:
            finite = all(math.isfinite(float(pose[key])) for key in ("x", "y", "yaw"))
        except (KeyError, TypeError, ValueError):
            finite = False
        if not finite:
            # Defence in depth for tests, restored state, or another producer
            # assigning PoseCache.latest directly.
            pose = None
            pose_error = "invalid_pose"
    return {
        "locations": locations,
        "pose": pose,
        "pose_error": pose_error,
        "ros": RosBridgeClient.available(),
    }


def _ros_snapshot() -> dict[str, Any]:
    """Best-effort live ROS2 graph snapshot for the dashboard."""
    if not ros2_adapter.is_available():
        return {"available": False, "topics": [], "count": 0, "error": None}
    try:
        names = ros2_adapter.list_topics()
    except ros2_adapter.Ros2AdapterError as exc:
        return {"available": True, "topics": [], "count": 0, "error": str(exc)}
    topics = [{"name": name, "kind": _kind_hint(name)} for name in names]
    return {"available": True, "topics": topics, "count": len(topics), "error": None}


def _locations_count(config: AppConfig, config_path: Path) -> int:
    try:
        from jenai.adapters.locations import ensure_locations_file, load_locations

        path = config.resolved_locations_path(config_path)
        if path is None:
            return 0
        ensure_locations_file(path)
        return len(load_locations(path))
    except Exception:
        return 0


def build_status_payload(
    config: AppConfig,
    config_path: Path,
    *,
    run_store: RunStore | None = None,
    doctor_result: Any = None,
    ros_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the WebUI snapshot: provider/model, full doctor report and the
    live ROS2 graph. The transcript is drawn from a RunStore when one is passed
    (a stand-alone `jenai web` process starts with none).
    """
    # 5s-polled path: skip the nav-stack probes (seconds of ros2 CLI each).
    doctor = (
        doctor_result
        if doctor_result is not None
        else run_doctor(config_path, include_nav=False)
    )
    profile = config.active_profile()
    runs = list(run_store.list_runs()) if run_store is not None else []
    transcript = [
        {
            "run_id": run.run_id,
            "status": str(run.status),
            "summary": run.user_input,
            "final_output": run.final_output,
        }
        for run in runs
    ]
    return {
        "provider": profile.name if profile else None,
        "provider_kind": profile.provider if profile else None,
        "model": chat_model_name(config),
        "config_complete": config.is_complete(),
        "locations": _locations_count(config, config_path),
        "doctor_overall": str(doctor.overall),
        "doctor": {
            "overall": str(doctor.overall),
            "items": [
                {
                    "section": item.section,
                    "check": item.check_name,
                    "status": str(item.status),
                    "message": item.message,
                    "fix": item.fix_suggestion,
                }
                for item in doctor.items
            ],
        },
        "ros": ros_snapshot if ros_snapshot is not None else _ros_snapshot(),
        "run_count": len(transcript),
        "transcript": transcript,
    }


def _do_stop(config: AppConfig, pose_cache: PoseCache | None = None) -> dict[str, Any]:
    """Halt the robot from a sync HTTP handler.

    Fast path: submit the halt to the PoseCache's already-running bridge —
    no cold start in the middle of an emergency. Fallback: fresh bridge.
    """
    if pose_cache is not None:
        message = pose_cache.submit(lambda client: halt_robot(config, client))
        if message is not None:
            return {"kind": "result", "html": f"<p>🛑 {message}</p>"}

    async def run() -> str:
        bridge = RosBridgeClient()
        try:
            await bridge.start()
            return await halt_robot(config, bridge)
        finally:
            with contextlib.suppress(BridgeError):
                await bridge.stop()

    try:
        message = asyncio.run(run())
    except BridgeError as exc:
        return {"kind": "error", "html": f"<p>Stop unavailable (no ROS bridge): {exc}</p>"}
    return {"kind": "result", "html": f"<p>🛑 {message}</p>"}


# -- HTML rendering -----------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    config: AppConfig
    config_path: Path
    run_store: RunStore | None = None
    pending: _PendingConfirms | None = None
    pose_cache: PoseCache | None = None
    status_cache: StatusCache | None = None
    action_lock: threading.Lock | None = None
    token: str | None = None  # None = auth disabled (unit tests); CLI always sets one

    def log_message(self, *args: Any) -> None:  # silence default stderr logging
        pass

    def _status(self) -> dict[str, Any]:
        if self.status_cache is not None:
            return self.status_cache.build_status(
                self.config,
                self.config_path,
                run_store=self.run_store,
            )
        return build_status_payload(self.config, self.config_path, run_store=self.run_store)

    def _send(self, body: str, content_type: str, status: int = 200) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        # A token that arrived in the URL gets promoted to a session cookie so
        # the dashboard's /api fetches (which carry no query string) stay
        # authorized after the initial tokened link is opened.
        if getattr(self, "_grant_cookie", False):
            self.send_header(
                "Set-Cookie", f"jenai_token={self.token}; HttpOnly; SameSite=Strict; Path=/"
            )
        self.end_headers()
        self.wfile.write(encoded)

    def _route(self) -> str:
        return urlsplit(self.path).path.rstrip("/") or "/"

    def _presented_token(self) -> tuple[str, bool]:
        """Return (token the client presented, whether it came from the URL)."""
        bearer = self.headers.get("Authorization") or ""
        if bearer.startswith("Bearer "):
            return bearer[len("Bearer ") :], False
        try:
            cookies = SimpleCookie(self.headers.get("Cookie") or "")
        except Exception:  # malformed cookie header from an untrusted client
            cookies = SimpleCookie()
        if "jenai_token" in cookies:
            return cookies["jenai_token"].value, False
        query = parse_qs(urlsplit(self.path).query)
        if query.get("token"):
            return query["token"][0], True
        return "", False

    def _authorized(self) -> bool:
        if self.token is None:
            return True
        presented, from_query = self._presented_token()
        ok = secrets.compare_digest(presented.encode(), self.token.encode())
        # Promote to cookie ONLY on success — a Set-Cookie on the 401 path
        # would hand the real token to whoever guessed wrong.
        self._grant_cookie = ok and from_query
        return ok

    def _reject(self) -> None:
        # Same minimal body for missing and wrong tokens — nothing to enumerate.
        if self._route().startswith("/api/"):
            self._send('{"kind": "error", "html": "<p>Unauthorized.</p>"}',
                       "application/json; charset=utf-8", status=401)
        else:
            self._send(
                "<h1>401</h1><p>Open the tokened URL printed by <code>JenAI web</code>.</p>",
                "text/html; charset=utf-8",
                status=401,
            )

    def _send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_frame(self) -> None:
        """One camera frame as JPEG for the dashboard's ~1 fps snapshot stream."""
        query = parse_qs(urlsplit(self.path).query)
        topic = (query.get("topic") or [None])[0] or self.config.vehicle.camera_topic
        frame_path = None
        if self.pose_cache is not None:
            self.pose_cache.ensure_started()
            # Rides the PoseCache's live bridge/loop — no per-request spawn.
            frame_path = self.pose_cache.submit(
                lambda client: client.capture_frame(topic, timeout=5.0), timeout=8.0
            )
        if frame_path is None:
            self._send(
                json.dumps({"kind": "error", "html": f"<p>No frame from {topic}.</p>"}),
                "application/json; charset=utf-8",
                status=503,
            )
            return
        try:
            data = Path(frame_path).read_bytes()
        finally:
            with contextlib.suppress(OSError):
                Path(frame_path).unlink()  # each frame is a one-shot temp file
        self._send_bytes(data, "image/jpeg")

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        if not self._authorized():
            self._reject()
            return
        path = self._route()
        if path == "/api/frame":
            self._serve_frame()
        elif path == "/api/topics":
            # Live graph snapshot — feeds the Camera page's topic picker and
            # the API page's reference list.
            snapshot = (
                self.status_cache.ros_snapshot()
                if self.status_cache is not None
                else _ros_snapshot()
            )
            self._send(
                json.dumps(snapshot, ensure_ascii=False),
                "application/json; charset=utf-8",
            )
        elif path == "/api/status":
            self._send(
                json.dumps(self._status(), ensure_ascii=False),
                "application/json; charset=utf-8",
            )
        elif path == "/api/map":
            payload = build_map_payload(self.config, self.config_path, self.pose_cache)
            # Never emit JavaScript-only NaN/Infinity tokens: API responses are
            # standard JSON, and any missed non-finite value fails visibly here.
            self._send(
                json.dumps(payload, ensure_ascii=False, allow_nan=False),
                "application/json; charset=utf-8",
            )
        elif path == "/fragment":
            self._send(render_main(self._status()), "text/html; charset=utf-8")
        else:
            self._send(render_dashboard_html(self._status()), "text/html; charset=utf-8")

    def _drain_body(self) -> None:
        """Best-effort read of a small request body after the response is sent."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        if 0 < length <= _MAX_JSON_BODY:
            # Timeout is mandatory: a client claiming a length it never sends
            # would otherwise pin this handler thread forever (the endpoint is
            # unauthenticated). TimeoutError is an OSError, so it's suppressed.
            with contextlib.suppress(OSError):
                self.connection.settimeout(2.0)
                self.rfile.read(length)

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(
                '{"kind": "error", "html": "<p>Invalid Content-Length.</p>"}',
                "application/json; charset=utf-8",
                status=400,
            )
            return None
        if length < 0 or length > _MAX_JSON_BODY:
            self._send(
                '{"kind": "error", "html": "<p>Request body too large.</p>"}',
                "application/json; charset=utf-8",
                status=413,
            )
            return None
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send(
                '{"kind": "error", "html": "<p>Invalid JSON body.</p>"}',
                "application/json; charset=utf-8",
                status=400,
            )
            return None
        if not isinstance(data, dict):
            self._send(
                '{"kind": "error", "html": "<p>JSON body must be an object.</p>"}',
                "application/json; charset=utf-8",
                status=400,
            )
            return None
        return data

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        path = self._route()
        # EMERGENCY STOP is the one unauthenticated endpoint: stopping is
        # always safe, and a phone with a stale/lost cookie must still be able
        # to halt the robot. Worst case for an attacker is stopping it too.
        if path == "/api/stop":
            # A stop also revokes every preview created before it. Otherwise an
            # old confirmation card could restart motion after the emergency.
            if self.pending is not None:
                self.pending.clear()
            result = _do_stop(self.config, self.pose_cache)
            self._send(json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")
            # Drain a bounded body only AFTER the halt ran: closing the socket
            # with unread bytes RSTs the connection, and the browser would show
            # a network error for a stop that actually executed. An oversized
            # claim is left unread — stalling the thread for an attacker-sized
            # body is worse than one client-side error message.
            self._drain_body()
            return
        if not self._authorized():
            self._reject()
            return
        body = self._read_json()
        if body is None:
            return
        if path == "/api/command":
            result = asyncio.run(run_web_command(self.config, self.config_path, body.get("text", "")))
            # A previewed actuation is held server-side under a one-time id; the
            # browser never gets (or gets to alter) the raw action it confirms.
            if result.get("kind") == "confirm" and self.pending is not None:
                result["confirm_id"] = self.pending.put(result.pop("action", {}))
        elif path == "/api/confirm":
            acquired = self.action_lock is None or self.action_lock.acquire(blocking=False)
            if not acquired:
                result = {
                    "kind": "error",
                    "html": (
                        "<p>Another robot action is already running. "
                        "Try again when it finishes.</p>"
                    ),
                }
            else:
                try:
                    action = (
                        self.pending.pop(body.get("confirm_id", "")) if self.pending else None
                    )
                    if action is None:
                        result = {
                            "kind": "error",
                            "html": (
                                "<p>This confirmation expired or was already used. "
                                "Re-run the command.</p>"
                            ),
                        }
                    else:
                        result = asyncio.run(
                            run_web_confirm(self.config, action, config_path=self.config_path)
                        )
                finally:
                    if self.action_lock is not None:
                        self.action_lock.release()
        else:
            result = {"kind": "error", "html": "<p>Unknown endpoint.</p>"}
        self._send(json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")


def lan_addresses() -> list[str]:
    """Best-effort non-loopback IPv4 addresses of this host.

    Used to print the WebUI URLs that are actually reachable when bound to
    0.0.0.0 — never printed for a loopback bind (an unreachable URL would be
    a lie). UDP connect() sends no packets; it only resolves the route.
    """
    addresses: set[str] = set()
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            addresses.add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                addresses.add(ip)
    except OSError:
        pass
    return sorted(addresses)


def make_server(
    config: AppConfig,
    config_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8760,
    run_store: RunStore | None = None,
    token: str | None = None,
) -> ThreadingHTTPServer:
    handler = type(
        "JenAIWebHandler",
        (_Handler,),
        {
            "config": config,
            "config_path": config_path,
            "run_store": run_store,
            "pending": _PendingConfirms(),
            "pose_cache": PoseCache(),
            "status_cache": StatusCache(),
            "action_lock": threading.Lock(),
            "token": token,
        },
    )
    return ThreadingHTTPServer((host, port), handler)


def serve(
    config: AppConfig,
    config_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8760,
    token: str | None = None,
) -> None:  # pragma: no cover - blocking network loop
    server = make_server(config, config_path, host=host, port=port, token=token)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
