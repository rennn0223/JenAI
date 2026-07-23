"""Dependency-free stdin server for the system-Python ROS sidecar."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from typing import Any

if __package__:
    from ._wire import BridgePayload, RequestFrame, decode_request
else:  # pragma: no cover - exercised by the system-Python sidecar
    from _wire import BridgePayload, RequestFrame, decode_request  # type: ignore[no-redef]

Emitter = Callable[[BridgePayload], None]
Dispatcher = Callable[[str, BridgePayload], BridgePayload]
_SLOW_OPERATIONS = frozenset({"capture_frame", "map_cell", "map_identity", "nav_plan", "pose"})
_MAX_SLOW_OPERATIONS = 2


def serve_requests(
    lines: Iterable[str],
    *,
    emit: Emitter,
    dispatch: Dispatcher,
    touch_watchdog: Callable[[], Any],
) -> None:
    """Serve newline-delimited requests until EOF or an explicit shutdown.

    Long, read-only operations run on worker threads so an emergency halt can
    never sit behind a camera or pose timeout.  Every request gets one response;
    an operation error is isolated and the stream remains usable.
    """

    def respond(request: RequestFrame) -> None:
        try:
            result = dispatch(request.op, request.params)
            emit({"id": request.request_id, "ok": True, "result": result})
        except Exception as exc:
            emit({"id": request.request_id, "ok": False, "error": str(exc)})

    slow_slots = threading.BoundedSemaphore(_MAX_SLOW_OPERATIONS)

    def respond_slow(request: RequestFrame) -> None:
        try:
            respond(request)
        finally:
            slow_slots.release()

    for raw_line in lines:
        if not raw_line.strip():
            continue
        touch_watchdog()
        try:
            request = decode_request(raw_line)
        except Exception as exc:
            emit({"id": None, "ok": False, "error": str(exc)})
            continue

        if request.op == "shutdown":
            emit({"id": request.request_id, "ok": True, "result": {}})
            return
        if request.op in _SLOW_OPERATIONS:
            if not slow_slots.acquire(blocking=False):
                emit(
                    {
                        "id": request.request_id,
                        "ok": False,
                        "error": "bridge is busy with slow read-only operations",
                    }
                )
                continue
            threading.Thread(target=respond_slow, args=(request,), daemon=True).start()
        else:
            respond(request)
