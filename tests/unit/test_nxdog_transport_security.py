from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from jenai.adapters.nxdog import (
    NXDogFailureKind,
    NXDogObserver,
    NXDogTransportError,
    UrllibNXDogTransport,
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
        "/velocity": {"level": [0.0, 0.0, 0.0]},
        "/is_charging": {"is_charging": False},
    }


def _start_server(
    handler: type[BaseHTTPRequestHandler],
    *,
    host: str = "127.0.0.1",
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer((host, 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop_server(server: ThreadingHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_observer_ignores_ambient_http_proxy_and_connects_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads = _valid_payloads()
    robot_requests: list[str] = []
    proxy_requests: list[str] = []

    class _RobotHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            robot_requests.append(self.path)
            body = json.dumps(payloads[self.path]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

    class _ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            proxy_requests.append(self.path)
            self.send_response(502)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

    robot_server, robot_thread = _start_server(_RobotHandler, host="0.0.0.0")
    proxy_server, proxy_thread = _start_server(_ProxyHandler)
    try:
        _, robot_port = robot_server.server_address
        proxy_host, proxy_port = proxy_server.server_address
        proxy_url = f"http://{proxy_host}:{proxy_port}"
        for variable in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            monkeypatch.setenv(variable, proxy_url)
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("no_proxy", "")
        monkeypatch.delenv("REQUEST_METHOD", raising=False)

        result = NXDogObserver(
            f"http://127.0.0.2:{robot_port}",
            timeout_s=1.0,
        ).observe()
    finally:
        _stop_server(robot_server, robot_thread)
        _stop_server(proxy_server, proxy_thread)

    assert result.complete is True
    assert set(robot_requests) == set(payloads)
    assert proxy_requests == []


@pytest.mark.parametrize("status_code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("target_kind", ["relative", "same_host", "external_host"])
def test_redirects_never_reach_action_targets(
    status_code: int,
    target_kind: str,
) -> None:
    payloads = _valid_payloads()
    robot_requests: list[str] = []
    external_requests: list[str] = []
    robot_base_url = ""

    class _ExternalHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            external_requests.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

    external_server, external_thread = _start_server(_ExternalHandler)
    external_host, external_port = external_server.server_address

    class _RobotHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            robot_requests.append(self.path)
            if self.path == "/nav_health":
                locations = {
                    "relative": "/stop",
                    "same_host": f"{robot_base_url}/stop",
                    "external_host": f"http://{external_host}:{external_port}/stop",
                }
                self.send_response(status_code)
                self.send_header("Location", locations[target_kind])
                self.end_headers()
                return
            body = json.dumps(payloads[self.path]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

    robot_server, robot_thread = _start_server(_RobotHandler)
    robot_host, robot_port = robot_server.server_address
    robot_base_url = f"http://{robot_host}:{robot_port}"
    try:
        result = NXDogObserver(robot_base_url, timeout_s=1.0).observe()
    finally:
        _stop_server(robot_server, robot_thread)
        _stop_server(external_server, external_thread)

    failure = result.failure_for("/nav_health")
    assert failure is not None
    assert failure.kind == NXDogFailureKind.REDIRECT_REJECTED
    assert "/stop" not in robot_requests
    assert external_requests == []


def test_action_endpoint_is_rejected_before_any_network_io() -> None:
    received: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            received.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

    server, thread = _start_server(_Handler)
    host, port = server.server_address
    try:
        transport = UrllibNXDogTransport(f"http://{host}:{port}")
        with pytest.raises(NXDogTransportError, match="not read-only"):
            transport.get_json("/stop", timeout_s=1.0)
    finally:
        _stop_server(server, thread)

    assert received == []


@pytest.mark.parametrize("body", [b"\xff", b"{not-json"])
def test_invalid_wire_payload_is_not_misclassified_as_transport(body: bytes) -> None:
    received: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            received.append(self.path)
            payload = (
                body
                if self.path == "/nav_health"
                else json.dumps(_valid_payloads()[self.path]).encode()
            )
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

    server, thread = _start_server(_Handler)
    try:
        host, port = server.server_address
        result = NXDogObserver(f"http://{host}:{port}", timeout_s=1.0).observe()
    finally:
        _stop_server(server, thread)

    failure = result.failure_for("/nav_health")
    assert failure is not None
    assert failure.kind == NXDogFailureKind.INVALID_PAYLOAD
    assert "UTF-8 JSON" in failure.message
    assert received.count("/nav_health") == 1


def test_http_status_has_its_own_failure_category() -> None:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/nav_health":
                self.send_response(503)
                self.end_headers()
                return
            payload = json.dumps(_valid_payloads()[self.path]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

    server, thread = _start_server(_Handler)
    try:
        host, port = server.server_address
        result = NXDogObserver(f"http://{host}:{port}", timeout_s=1.0).observe()
    finally:
        _stop_server(server, thread)

    failure = result.failure_for("/nav_health")
    assert failure is not None
    assert failure.kind == NXDogFailureKind.HTTP_STATUS


def test_unexpected_adapter_exception_is_not_misclassified_as_transport() -> None:
    class _BrokenTransport:
        def get_json(self, endpoint: str, *, timeout_s: float) -> object:
            del endpoint, timeout_s
            raise RuntimeError("programming defect")

    result = NXDogObserver(
        "http://dog.local:5088",
        transport=_BrokenTransport(),
    ).observe()

    assert result.failures
    assert {failure.kind for failure in result.failures} == {
        NXDogFailureKind.INTERNAL_ADAPTER_ERROR
    }
