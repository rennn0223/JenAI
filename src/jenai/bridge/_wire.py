"""Typed newline-delimited JSON frames for the ROS bridge boundary.

This module intentionally uses only the Python standard library.  The client
runs inside JenAI's virtual environment, while ``ros_bridge.py`` runs under the
system interpreter that owns ``rclpy``; both sides can therefore share this
wire contract without sharing runtime dependencies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

BridgePayload = dict[str, Any]
_RESERVED_REQUEST_FIELDS = frozenset({"id", "op"})


class WireProtocolError(ValueError):
    """A JSON object violated the bridge's wire-level contract."""


@dataclass(frozen=True, slots=True)
class EventFrame:
    """An unsolicited bridge event."""

    name: str
    payload: BridgePayload


@dataclass(frozen=True, slots=True)
class ResponseFrame:
    """A response correlated with one client request."""

    request_id: int
    ok: bool
    result: BridgePayload
    error: str | None = None


BridgeFrame = EventFrame | ResponseFrame


@dataclass(frozen=True, slots=True)
class RequestFrame:
    """A validated request received by the sidecar."""

    request_id: int
    op: str
    params: BridgePayload


def encode_request(request_id: int, op: str, params: BridgePayload | None = None) -> bytes:
    """Encode one request while protecting its correlation and operation fields."""
    if isinstance(request_id, bool) or request_id <= 0:
        raise WireProtocolError("request id must be a positive integer")
    if not isinstance(op, str) or not op.strip():
        raise WireProtocolError("operation must be a non-empty string")

    fields = dict(params or {})
    conflicts = _RESERVED_REQUEST_FIELDS.intersection(fields)
    if conflicts:
        names = ", ".join(sorted(conflicts))
        raise WireProtocolError(f"request params contain reserved field(s): {names}")
    fields.update({"id": request_id, "op": op})
    return (json.dumps(fields, separators=(",", ":")) + "\n").encode()


def decode_frame(line: bytes) -> BridgeFrame | None:
    """Decode and validate one sidecar frame.

    Non-JSON diagnostic output is ignored because ROS dependencies may print
    notices to stdout on some installations.  Once a JSON object claims to be
    a protocol frame, however, malformed fields raise ``WireProtocolError`` so
    the client fails fast instead of waiting for an unrelated timeout.
    """
    try:
        payload = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    if "event" in payload:
        name = payload.get("event")
        if not isinstance(name, str) or not name:
            raise WireProtocolError("event name must be a non-empty string")
        return EventFrame(name=name, payload=payload)

    request_id = payload.get("id")
    ok = payload.get("ok")
    if isinstance(request_id, bool) or not isinstance(request_id, int):
        raise WireProtocolError("response id must be an integer")
    if not isinstance(ok, bool):
        raise WireProtocolError("response ok field must be a boolean")

    if ok:
        result = payload.get("result", {})
        if not isinstance(result, dict):
            raise WireProtocolError("successful response result must be an object")
        return ResponseFrame(request_id=request_id, ok=True, result=result)

    error = payload.get("error", "bridge error")
    if not isinstance(error, str):
        raise WireProtocolError("failed response error must be a string")
    return ResponseFrame(request_id=request_id, ok=False, result={}, error=error)


def decode_request(line: str | bytes) -> RequestFrame:
    """Decode one client request for the sidecar's stdin server."""
    try:
        payload = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WireProtocolError("request is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise WireProtocolError("request must be an object")

    request_id = payload.get("id")
    op = payload.get("op")
    if isinstance(request_id, bool) or not isinstance(request_id, int) or request_id < 0:
        raise WireProtocolError("request id must be a non-negative integer")
    if not isinstance(op, str) or not op.strip():
        raise WireProtocolError("operation must be a non-empty string")
    params = {key: value for key, value in payload.items() if key not in _RESERVED_REQUEST_FIELDS}
    return RequestFrame(request_id=request_id, op=op, params=params)
