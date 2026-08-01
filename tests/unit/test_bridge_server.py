from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest

from jenai.bridge import _server


def test_server_serves_success_error_and_shutdown_without_losing_stream() -> None:
    emitted: list[dict[str, Any]] = []
    touched: list[None] = []

    def dispatch(op: str, params: dict[str, Any]) -> dict[str, Any]:
        if op == "boom":
            raise RuntimeError("synthetic failure")
        return {"op": op, **params}

    _server.serve_requests(
        [
            "\n",
            '{"id":1,"op":"ping"}\n',
            '{"id":2,"op":"boom"}\n',
            '{"id":3,"op":"halt","topic":"/cmd_vel"}\n',
            '{"id":4,"op":"shutdown"}\n',
            '{"id":5,"op":"must_not_run"}\n',
        ],
        emit=emitted.append,
        dispatch=dispatch,
        touch_watchdog=lambda: touched.append(None),
    )

    assert len(touched) == 4
    assert emitted == [
        {"id": 1, "ok": True, "result": {"op": "ping"}},
        {"id": 2, "ok": False, "error": "synthetic failure"},
        {
            "id": 3,
            "ok": True,
            "result": {"op": "halt", "topic": "/cmd_vel"},
        },
        {"id": 4, "ok": True, "result": {}},
    ]


def test_server_reports_malformed_request_then_continues() -> None:
    emitted: list[dict[str, Any]] = []

    _server.serve_requests(
        ["garbage\n", '{"id":2,"op":"ping"}\n'],
        emit=emitted.append,
        dispatch=lambda op, _params: {"op": op},
        touch_watchdog=lambda: None,
    )

    assert emitted[0]["id"] is None
    assert emitted[0]["ok"] is False
    assert "valid JSON" in emitted[0]["error"]
    assert emitted[1] == {"id": 2, "ok": True, "result": {"op": "ping"}}


def test_slow_read_only_operation_does_not_block_emergency_halt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deferred: list[tuple[Any, tuple[Any, ...]]] = []
    emitted: list[dict[str, Any]] = []
    dispatched: list[str] = []

    class DeferredThread:
        def __init__(
            self,
            *,
            target: Callable[..., None],
            args: tuple[Any, ...],
            daemon: bool,
        ) -> None:
            assert daemon is True
            self.target = target
            self.args = args

        def start(self) -> None:
            deferred.append((self.target, self.args))

    def dispatch(op: str, _params: dict[str, Any]) -> dict[str, Any]:
        dispatched.append(op)
        return {"op": op}

    monkeypatch.setattr(threading, "Thread", DeferredThread)
    _server.serve_requests(
        [
            '{"id":1,"op":"pose"}\n',
            '{"id":2,"op":"halt"}\n',
            '{"id":3,"op":"shutdown"}\n',
        ],
        emit=emitted.append,
        dispatch=dispatch,
        touch_watchdog=lambda: None,
    )

    assert dispatched == ["halt"]
    assert emitted[0] == {"id": 2, "ok": True, "result": {"op": "halt"}}
    assert emitted[1] == {"id": 3, "ok": True, "result": {}}

    target, args = deferred.pop()
    target(*args)
    assert dispatched == ["halt", "pose"]
    assert emitted[-1] == {"id": 1, "ok": True, "result": {"op": "pose"}}


def test_server_bounds_concurrent_slow_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    deferred: list[tuple[Any, tuple[Any, ...]]] = []
    emitted: list[dict[str, Any]] = []

    class DeferredThread:
        def __init__(
            self,
            *,
            target: Callable[..., None],
            args: tuple[Any, ...],
            daemon: bool,
        ) -> None:
            self.target = target
            self.args = args

        def start(self) -> None:
            deferred.append((self.target, self.args))

    monkeypatch.setattr(threading, "Thread", DeferredThread)
    _server.serve_requests(
        [
            '{"id":1,"op":"pose"}\n',
            '{"id":2,"op":"capture_frame"}\n',
            '{"id":3,"op":"pose"}\n',
            '{"id":4,"op":"shutdown"}\n',
        ],
        emit=emitted.append,
        dispatch=lambda op, _params: {"op": op},
        touch_watchdog=lambda: None,
    )

    assert len(deferred) == _server._MAX_SLOW_OPERATIONS
    assert emitted[0] == {
        "id": 3,
        "ok": False,
        "error": "bridge is busy with bounded observation operations",
    }
    assert emitted[1] == {"id": 4, "ok": True, "result": {}}


def test_nomotion_update_does_not_serialize_pose_dispatch() -> None:
    emitted: list[dict[str, Any]] = []
    nomotion_started = threading.Event()
    pose_started = threading.Event()
    release_nomotion = threading.Event()
    responses_finished = threading.Event()
    emitted_lock = threading.Lock()

    def emit(response: dict[str, Any]) -> None:
        with emitted_lock:
            emitted.append(response)
            if len(emitted) == 2:
                responses_finished.set()

    def dispatch(op: str, _params: dict[str, Any]) -> dict[str, Any]:
        if op == "request_nomotion_update":
            nomotion_started.set()
            assert release_nomotion.wait(timeout=2.0)
        elif op == "pose":
            pose_started.set()
        return {"op": op}

    server_thread = threading.Thread(
        target=_server.serve_requests,
        kwargs={
            "lines": [
                '{"id":1,"op":"request_nomotion_update"}\n',
                '{"id":2,"op":"pose"}\n',
            ],
            "emit": emit,
            "dispatch": dispatch,
            "touch_watchdog": lambda: None,
        },
    )
    server_thread.start()

    try:
        assert nomotion_started.wait(timeout=1.0)
        assert pose_started.wait(timeout=1.0)
    finally:
        release_nomotion.set()
        server_thread.join(timeout=2.0)

    assert responses_finished.wait(timeout=2.0)
    assert not server_thread.is_alive()
    assert {response["id"] for response in emitted} == {1, 2}
