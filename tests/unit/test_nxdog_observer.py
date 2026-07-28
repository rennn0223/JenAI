from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from pydantic import ValidationError

from jenai.adapters.nxdog import (
    NXDOG_API_URL_ENV,
    NXDogConfigurationError,
    NXDogFailureKind,
    NXDogObservation,
    NXDogObserver,
)


def _valid_payloads() -> dict[str, object]:
    return {
        "/nav_health": {"alive": True},
        "/get_ready_flag": {"ready_flag": True},
        "/current_map": {"current_map": "lab"},
        "/odom": {
            "odom": {
                "x": 1.25,
                "y": -0.5,
                "yaw": 0.75,
                "map": "lab",
                "map_tile": "lab_p0_n1",
            }
        },
        "/velocity": {"level": [0.1, -0.2, 0.3]},
        "/is_charging": {"is_charging": False},
    }


class _FakeTransport:
    def __init__(
        self,
        payloads: dict[str, object],
        *,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.payloads = payloads
        self.errors = errors or {}
        self.calls: list[tuple[str, float]] = []
        self._lock = threading.Lock()

    def get_json(self, endpoint: str, *, timeout_s: float) -> object:
        with self._lock:
            self.calls.append((endpoint, timeout_s))
        if endpoint in self.errors:
            raise self.errors[endpoint]
        return self.payloads[endpoint]


def test_observer_returns_one_typed_snapshot_through_the_external_interface() -> None:
    transport = _FakeTransport(_valid_payloads())

    result = NXDogObserver(
        "http://192.168.123.18:5088/",
        timeout_s=1.5,
        transport=transport,
    ).observe()

    assert result.complete is True
    assert result.base_url == "http://192.168.123.18:5088"
    assert result.nav_alive is True
    assert result.client_ready is True
    assert result.current_map == "lab"
    assert result.pose is not None
    assert result.pose.model_dump() == {
        "x": 1.25,
        "y": -0.5,
        "yaw": 0.75,
        "map_name": "lab",
        "map_tile": "lab_p0_n1",
    }
    assert result.velocity is not None
    assert result.velocity.model_dump() == {"vx": 0.1, "vy": -0.2, "wz": 0.3}
    assert result.charging is False
    assert result.source_timestamps_available is False
    assert result.cryptographic_map_identity_available is False
    assert {endpoint for endpoint, timeout in transport.calls if timeout == 1.5} == {
        "/nav_health",
        "/get_ready_flag",
        "/current_map",
        "/odom",
        "/velocity",
        "/is_charging",
    }


@pytest.mark.parametrize(
    "field",
    [
        "transport_authenticated",
        "source_timestamps_available",
        "cryptographic_map_identity_available",
    ],
)
def test_unavailable_evidence_properties_cannot_be_overridden(field: str) -> None:
    with pytest.raises(ValidationError):
        NXDogObservation.model_validate(
            {"captured_at": datetime.now(UTC), "base_url": "http://dog.local:5088", field: True}
        )


def test_false_and_null_vendor_values_are_valid_observations() -> None:
    payloads = _valid_payloads()
    payloads["/nav_health"] = {"alive": False}
    payloads["/get_ready_flag"] = {"ready_flag": False}
    payloads["/current_map"] = {"current_map": None}

    result = NXDogObserver("http://dog.local:5088", transport=_FakeTransport(payloads)).observe()

    assert result.complete is True
    assert result.nav_alive is False
    assert result.client_ready is False
    assert result.current_map is None


def test_partial_failures_do_not_erase_other_evidence() -> None:
    payloads = _valid_payloads()
    payloads["/velocity"] = {"level": [0.0, float("nan"), 0.0]}
    transport = _FakeTransport(
        payloads,
        errors={"/current_map": TimeoutError("timed out")},
    )

    result = NXDogObserver("http://dog.local:5088", transport=transport).observe()

    assert result.complete is False
    assert result.pose is not None
    assert result.velocity is None
    assert result.current_map is None
    assert result.failure_for("/current_map") is not None
    assert result.failure_for("/current_map").kind == NXDogFailureKind.TRANSPORT  # type: ignore[union-attr]
    assert result.failure_for("/velocity") is not None
    assert result.failure_for("/velocity").kind == NXDogFailureKind.INVALID_PAYLOAD  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("/nav_health", {"alive": 1}),
        ("/get_ready_flag", {"ready_flag": "yes"}),
        ("/current_map", {"current_map": ""}),
        ("/odom", {"odom": {"x": "1", "y": 2, "yaw": 0, "map": None, "map_tile": None}}),
        ("/velocity", {"level": [0, 0]}),
        ("/is_charging", {"is_charging": None}),
    ],
)
def test_every_malformed_endpoint_fails_closed(endpoint: str, payload: object) -> None:
    payloads = _valid_payloads()
    payloads[endpoint] = payload

    result = NXDogObserver(
        "http://dog.local:5088",
        transport=_FakeTransport(payloads),
    ).observe()

    failure = result.failure_for(endpoint)
    assert failure is not None
    assert failure.kind == NXDogFailureKind.INVALID_PAYLOAD


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "192.168.123.18:5088",
        "ftp://192.168.123.18",
        "http://user:secret@192.168.123.18:5088",
        "http://192.168.123.18:5088/api",
        "http://192.168.123.18:5088?token=secret",
    ],
)
def test_unsafe_or_ambiguous_base_urls_are_rejected(base_url: str) -> None:
    with pytest.raises(NXDogConfigurationError):
        NXDogObserver(base_url, transport=_FakeTransport(_valid_payloads()))


def test_environment_opt_in_is_required() -> None:
    with pytest.raises(NXDogConfigurationError, match=NXDOG_API_URL_ENV):
        NXDogObserver.from_environment({})


def test_environment_builds_the_same_observer_interface() -> None:
    observer = NXDogObserver.from_environment(
        {NXDOG_API_URL_ENV: "https://dog.example"},
        transport=_FakeTransport(_valid_payloads()),
    )

    assert observer.base_url == "https://dog.example"
    assert observer.uses_https is True
    assert observer.observe().complete is True


def test_real_http_adapter_reads_only_the_allowlisted_get_endpoints() -> None:
    payloads = _valid_payloads()
    requested: list[tuple[str, str]] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            requested.append(("GET", self.path))
            body = json.dumps(payloads[self.path]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        result = NXDogObserver(f"http://{host}:{port}", timeout_s=1.0).observe()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.complete is True
    assert set(requested) == {("GET", endpoint) for endpoint in payloads}


def test_real_http_adapter_rejects_redirects_before_an_action_endpoint_is_called() -> None:
    payloads = _valid_payloads()
    requested: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            requested.append(self.path)
            if self.path == "/nav_health":
                self.send_response(302)
                self.send_header("Location", "/stop")
                self.end_headers()
                return
            body = json.dumps(payloads[self.path]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        result = NXDogObserver(f"http://{host}:{port}", timeout_s=1.0).observe()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    failure = result.failure_for("/nav_health")
    assert failure is not None
    assert failure.kind == NXDogFailureKind.TRANSPORT
