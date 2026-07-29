from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import pytest

from jenai.runtime.contracts import RuntimeEventKind, RuntimeSource
from jenai.runtime.http import make_runtime_server
from jenai.runtime.journal import RuntimeJournal


@contextmanager
def _running_server() -> Iterator[tuple[str, RuntimeJournal]]:
    journal = RuntimeJournal(runtime_id="runtime_http_test")
    journal.publish(
        RuntimeEventKind.RUNTIME_STARTED,
        source=RuntimeSource.SYSTEM,
        summary="Runtime ready.",
    )
    server = make_runtime_server(
        journal,
        host="127.0.0.1",
        port=0,
        token="test-runtime-token",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", journal
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _get(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={"Authorization": "Bearer test-runtime-token"},
    )


def test_runtime_http_requires_authentication_for_health() -> None:
    with _running_server() as (base_url, _):
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{base_url}/v1/health")  # noqa: S310

    assert caught.value.code == 401
    assert caught.value.headers["WWW-Authenticate"] == "Bearer"


def test_runtime_http_exposes_typed_health_snapshot_and_replay() -> None:
    with _running_server() as (base_url, journal):
        with urllib.request.urlopen(_get(f"{base_url}/v1/health")) as response:  # noqa: S310
            health = json.load(response)
            assert response.headers["Content-Type"] == "application/json; charset=utf-8"
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Content-Type-Options"] == "nosniff"

        journal.publish(
            RuntimeEventKind.ACTION_PROGRESS,
            source=RuntimeSource.SYSTEM,
            summary="Navigation in progress.",
        )
        with urllib.request.urlopen(  # noqa: S310
            _get(f"{base_url}/v1/events?after_sequence=1")
        ) as response:
            page = json.load(response)
        with urllib.request.urlopen(_get(f"{base_url}/v1/runtime")) as response:  # noqa: S310
            snapshot = json.load(response)

    assert health == {
        "schema_version": 1,
        "status": "ok",
        "runtime_id": "runtime_http_test",
        "state": "ready",
        "safety_epoch": 0,
        "last_sequence": 1,
    }
    assert [event["sequence"] for event in page["events"]] == [2]
    assert page["after_sequence"] == 1
    assert page["last_sequence"] == 2
    assert snapshot["runtime_id"] == "runtime_http_test"
    assert snapshot["state"] == "ready"


def test_runtime_http_rejects_invalid_cursor_and_unknown_routes() -> None:
    with _running_server() as (base_url, _):
        with pytest.raises(urllib.error.HTTPError) as bad_cursor:
            urllib.request.urlopen(_get(f"{base_url}/v1/events?after_sequence=nope"))  # noqa: S310
        with pytest.raises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(_get(f"{base_url}/v1/ros/topics"))  # noqa: S310

        bad_cursor_body = json.load(bad_cursor.value)
        missing_body = json.load(missing.value)

    assert bad_cursor.value.code == 400
    assert bad_cursor_body == {
        "schema_version": 1,
        "error": "invalid_request",
        "message": "after_sequence 必須是大於或等於 0 的整數。",
    }
    assert missing.value.code == 404
    assert missing_body["error"] == "not_found"
    assert "/v1/ros/topics" not in missing_body["message"]


def test_runtime_http_server_is_loopback_only_by_default() -> None:
    journal = RuntimeJournal(runtime_id="runtime_http_test")
    server: ThreadingHTTPServer = make_runtime_server(
        journal,
        port=0,
        token="test-runtime-token",
    )
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_runtime_http_rejects_non_loopback_binding() -> None:
    journal = RuntimeJournal(runtime_id="runtime_http_test")

    with pytest.raises(ValueError, match="127\\.0\\.0\\.1"):
        make_runtime_server(
            journal,
            host="0.0.0.0",
            port=0,
            token="test-runtime-token",
        )
