from __future__ import annotations

import asyncio
import http.client
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from jenai.config.store import build_minimal_config
from jenai.state.runs import RunStore
from jenai.webui import build_status_payload, render_dashboard_html
from jenai.webui.commands import run_web_command, run_web_confirm
from jenai.webui.server import make_server


def _config():
    return build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="gpt-test",
        api_key_env="",
    )


def test_status_payload_includes_provider_and_transcript(tmp_path: Path) -> None:
    store = RunStore()
    run = store.create_run("session-1", "patrol area A")
    store.finish(run, status=run.status, final_output="done")

    payload = build_status_payload(_config(), tmp_path / "config.toml", run_store=store)

    assert payload["provider"] == "test"
    assert payload["model"] == "gpt-test"
    assert payload["run_count"] == 1
    assert payload["transcript"][0]["summary"] == "patrol area A"


def test_status_payload_empty_transcript_without_store(tmp_path: Path) -> None:
    payload = build_status_payload(_config(), tmp_path / "config.toml")
    assert payload["run_count"] == 0
    assert payload["transcript"] == []


def test_status_payload_includes_doctor_and_ros(tmp_path: Path) -> None:
    payload = build_status_payload(_config(), tmp_path / "config.toml")
    assert payload["doctor"]["items"], "doctor report should list checks"
    assert {"section", "check", "status", "message"} <= set(payload["doctor"]["items"][0])
    assert "available" in payload["ros"]
    assert isinstance(payload["ros"]["topics"], list)


def test_status_cache_coalesces_doctor_and_ros_probes(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    from jenai.webui import server as server_module
    from jenai.webui.server import StatusCache

    now = [100.0]
    calls = {"doctor": 0, "ros": 0}

    def fake_doctor(_path, *, include_nav):
        assert include_nav is False
        calls["doctor"] += 1
        return SimpleNamespace(overall="pass", items=[])

    def fake_ros():
        calls["ros"] += 1
        return {"available": True, "topics": [], "count": 0, "error": None}

    monkeypatch.setattr(server_module, "run_doctor", fake_doctor)
    monkeypatch.setattr(server_module, "_ros_snapshot", fake_ros)
    cache = StatusCache(doctor_ttl_s=30.0, ros_ttl_s=2.0, clock=lambda: now[0])
    config_path = tmp_path / "config.toml"

    cache.build_status(_config(), config_path)
    cache.build_status(_config(), config_path)
    cache.ros_snapshot()  # /api/topics shares the same ROS snapshot
    assert calls == {"doctor": 1, "ros": 1}

    now[0] += 2.0
    cache.build_status(_config(), config_path)
    assert calls == {"doctor": 1, "ros": 2}

    now[0] += 28.0
    cache.build_status(_config(), config_path)
    assert calls == {"doctor": 2, "ros": 3}


def test_status_cache_coalesces_concurrent_doctor_refresh(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    from jenai.webui import server as server_module
    from jenai.webui.server import StatusCache

    entered = threading.Event()
    release = threading.Event()
    second_returned = threading.Event()
    calls = 0

    def slow_doctor(_path, *, include_nav):
        nonlocal calls
        assert include_nav is False
        calls += 1
        entered.set()
        release.wait(timeout=5)
        return SimpleNamespace(overall="pass", items=[])

    monkeypatch.setattr(server_module, "run_doctor", slow_doctor)
    cache = StatusCache()
    config_path = tmp_path / "config.toml"
    first = threading.Thread(target=cache.doctor, args=(config_path,))

    def second_call() -> None:
        cache.doctor(config_path)
        second_returned.set()

    second = threading.Thread(target=second_call)
    first.start()
    assert entered.wait(timeout=5)
    second.start()
    assert not second_returned.wait(timeout=0.1)
    assert calls == 1

    release.set()
    first.join(timeout=5)
    second.join(timeout=5)
    assert second_returned.is_set()
    assert calls == 1


def test_render_dashboard_html_renders_doctor_and_ros(tmp_path: Path) -> None:
    payload = build_status_payload(_config(), tmp_path / "config.toml")
    html = render_dashboard_html(payload)
    assert "<h1>JenAI</h1>" in html
    assert "Environment" in html
    assert "ROS2 Graph" in html
    # Doctor check names surface with friendly (humanized) labels.
    assert "Python" in html


def test_dashboard_embeds_slash_palette(tmp_path: Path) -> None:
    """The console palette is server-rendered from WEB_SLASH_COMMANDS —
    placeholder substituted, and every entry dispatches to a real handler."""
    from jenai.webui.commands import WEB_SLASH_COMMANDS

    payload = build_status_payload(_config(), tmp_path / "config.toml")
    html = render_dashboard_html(payload)
    assert "__SLASH__" not in html  # placeholder replaced
    assert 'id="palette"' in html
    assert "/ros schema" in html  # palette data actually embedded

    # Palette ≡ implementation: every first token must be a _slash dispatch
    # key — a phantom row here is the /ros state doc bug all over again.
    dispatch = {"/help", "/status", "/doctor", "/ros", "/drive", "/route", "/loc"}
    for entry in WEB_SLASH_COMMANDS:
        assert entry["name"].split()[0] in dispatch


def test_lan_addresses_never_returns_loopback() -> None:
    from jenai.webui.server import lan_addresses

    addrs = lan_addresses()
    assert isinstance(addrs, list)
    assert all(isinstance(a, str) and not a.startswith("127.") for a in addrs)


def test_web_access_lines_honest_per_bind(monkeypatch) -> None:
    """Loopback bind prints SSH/how-to hints but NO LAN URL (it wouldn't
    work); 0.0.0.0 prints one working URL per interface."""
    from jenai.cli.main import _web_access_lines

    loop = "\n".join(_web_access_lines("127.0.0.1", 8760, "tok"))
    assert "http://127.0.0.1:8760/?token=tok" in loop
    assert "ssh -L 8760:127.0.0.1:8760" in loop
    assert "--host 0.0.0.0" in loop
    assert "區網開" not in loop  # 綁 loopback 時不得印出打不開的區網網址

    monkeypatch.setattr("jenai.webui.server.lan_addresses", lambda: ["192.168.1.5"])
    lan = "\n".join(_web_access_lines("0.0.0.0", 8760, "tok"))
    assert "http://192.168.1.5:8760/?token=tok" in lan
    assert "http://127.0.0.1:8760/?token=tok" in lan


def test_render_main_is_a_standalone_fragment(tmp_path: Path) -> None:
    from jenai.webui.server import render_main

    payload = build_status_payload(_config(), tmp_path / "config.toml")
    fragment = render_main(payload)
    assert "<html" not in fragment  # no page shell
    assert "Environment" in fragment and "ROS2 Graph" in fragment


def test_webui_server_serves_html_and_json(tmp_path: Path) -> None:
    server = make_server(_config(), tmp_path / "config.toml", port=0)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    try:
        host, port = server.server_address
        base = f"http://{host}:{port}"
        with urllib.request.urlopen(f"{base}/api/status", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        assert data["provider"] == "test"
    finally:
        thread.join(timeout=5)
        server.server_close()


def test_web_command_drive_asks_confirm_not_execute(tmp_path: Path) -> None:
    # Regex path (no LLM): a drive command must return a confirm block, not run.
    res = asyncio.run(run_web_command(_config(), tmp_path / "c.toml", "/drive 前進兩秒"))
    assert res["kind"] == "confirm"
    assert res["action"]["type"] == "drive"
    assert res["action"]["payload"]["linear"]["x"] > 0
    assert "danger" in res


def test_web_ros_commands_reject_non_object_payloads(tmp_path: Path) -> None:
    config_path = tmp_path / "c.toml"

    drive = asyncio.run(run_web_command(_config(), config_path, "/ros drive /cmd_vel [] 1"))
    publish = asyncio.run(run_web_command(_config(), config_path, "/ros pub /cmd_vel []"))

    assert drive["kind"] == "error"
    assert publish["kind"] == "error"
    assert "JSON object" in drive["html"]
    assert "JSON object" in publish["html"]


def test_web_command_topics_is_read(monkeypatch, tmp_path: Path) -> None:
    from jenai.schemas import RosTopicsOutput, TopicItem
    from jenai.webui import commands

    async def fake_topics(config):
        return RosTopicsOutput(topics=[TopicItem(name="/cmd_vel", kind_hint="control")])

    monkeypatch.setattr(commands.ros2_core, "ros_topics", fake_topics)
    res = asyncio.run(run_web_command(_config(), tmp_path / "c.toml", "/ros topics"))
    assert res["kind"] == "result"
    assert "/cmd_vel" in res["html"]


def test_web_confirm_executes_drive(monkeypatch, tmp_path: Path) -> None:
    from jenai.schemas import RosPubOutput
    from jenai.webui import commands

    called = {}

    async def fake_drive(topic, message_type, payload, *, duration_s=1.0, **limits):
        called["duration"] = duration_s
        return RosPubOutput(
            topic=topic,
            message_type=message_type,
            execution_status="succeeded",
            result_message="drove then stopped",
        )

    monkeypatch.setattr(commands.ros2_core, "ros_drive", fake_drive)
    action = {
        "type": "drive",
        "topic": "/cmd_vel",
        "message_type": "geometry_msgs/msg/Twist",
        "payload": {"linear": {"x": 0.2}},
        "duration": 2.0,
    }
    config_path = tmp_path / "config.toml"
    res = asyncio.run(run_web_confirm(_config(), action, config_path=config_path))
    assert res["kind"] == "result" and "drove" in res["html"]
    assert called["duration"] == 2.0
    from jenai.state.audit import AuditStore

    events = AuditStore(tmp_path / "audit.sqlite3").list_events()
    assert [(event.event_type, event.status) for event in reversed(events)] == [
        ("approval_resolved", "approved"),
        ("tool_updated", "succeeded"),
    ]
    assert all(event.details["source"] == "webui" for event in events)


def test_web_command_unknown_is_error(tmp_path: Path) -> None:
    res = asyncio.run(run_web_command(_config(), tmp_path / "c.toml", "/frobnicate"))
    assert res["kind"] == "error"


def test_pending_confirms_is_one_time_and_bounded() -> None:
    from jenai.webui.server import _PendingConfirms

    store = _PendingConfirms(max_entries=2)
    token = store.put({"type": "drive"})
    assert store.pop(token) == {"type": "drive"}
    assert store.pop(token) is None  # one-time use
    assert store.pop("never-issued") is None
    # Oldest entries are evicted past the cap (bounded memory).
    ids = [store.put({"n": i}) for i in range(3)]
    assert store.pop(ids[0]) is None
    assert store.pop(ids[2]) == {"n": 2}

    expired = _PendingConfirms(ttl_s=0)
    assert expired.pop(expired.put({"type": "drive"})) is None

    token = store.put({"type": "drive"})
    store.clear()
    assert store.pop(token) is None


def test_confirm_endpoint_rejects_unknown_id(tmp_path: Path) -> None:
    # A blind POST to /api/confirm without a server-issued id must not actuate.
    server = make_server(_config(), tmp_path / "config.toml", port=0)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    try:
        host, port = server.server_address
        body = json.dumps({"confirm_id": "bogus"}).encode()
        req = urllib.request.Request(
            f"http://{host}:{port}/api/confirm",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode())
        assert payload["kind"] == "error"
        assert "expired" in payload["html"].lower() or "used" in payload["html"].lower()
    finally:
        thread.join(timeout=5)
        server.server_close()


def test_confirm_endpoint_rejects_overlapping_robot_action(tmp_path: Path) -> None:
    server = make_server(_config(), tmp_path / "config.toml", port=0)
    handler = server.RequestHandlerClass
    action = {"type": "drive"}
    confirm_id = handler.pending.put(action)
    assert handler.action_lock.acquire(blocking=False)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    try:
        host, port = server.server_address
        body = json.dumps({"confirm_id": confirm_id}).encode()
        req = urllib.request.Request(
            f"http://{host}:{port}/api/confirm",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode())
        assert payload["kind"] == "error"
        assert "already running" in payload["html"]
        # A busy response does not consume the one-shot approval; it can be
        # retried after the current robot action finishes.
        assert handler.pending.pop(confirm_id) == action
    finally:
        handler.action_lock.release()
        thread.join(timeout=5)
        server.server_close()


def test_dashboard_has_command_console(tmp_path: Path) -> None:
    html = render_dashboard_html(build_status_payload(_config(), tmp_path / "c.toml"))
    assert 'id="cmdinput"' in html and 'id="transcript"' in html


def test_webui_server_serves_dashboard_html(tmp_path: Path) -> None:
    server = make_server(_config(), tmp_path / "config.toml", port=0)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    try:
        host, port = server.server_address
        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=5) as resp:
            body = resp.read().decode("utf-8")
        assert "<h1>JenAI</h1>" in body
    finally:
        thread.join(timeout=5)
        server.server_close()


def test_api_map_payload_includes_locations_and_handles_no_pose(tmp_path) -> None:
    from jenai.adapters.locations import save_locations
    from jenai.config.store import build_minimal_config, save_config
    from jenai.schemas import Location, Pose2D
    from jenai.webui.server import build_map_payload

    config = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )
    config_path = tmp_path / "config.toml"
    save_config(config, config_path)
    save_locations(
        [Location(name="Kitchen", pose=Pose2D(x=2, y=1, yaw=0))], tmp_path / "locations.toml"
    )

    payload = build_map_payload(config, config_path, pose_cache=None)

    assert payload["locations"] == [{"name": "Kitchen", "x": 2.0, "y": 1.0, "frame_id": "map"}]
    assert payload["pose"] is None


def test_api_map_pose_staleness(tmp_path) -> None:
    import time

    from jenai.config.store import build_minimal_config, save_config
    from jenai.webui.server import PoseCache, build_map_payload

    config = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )
    config_path = tmp_path / "config.toml"
    save_config(config, config_path)

    cache = PoseCache()
    cache._started = True  # don't spawn a bridge in tests
    cache.latest = {
        "x": 1.0,
        "y": 2.0,
        "yaw": 0.0,
        "frame_id": "map",
        "source": "/odom",
        "ts": time.time(),
    }
    assert build_map_payload(config, config_path, cache)["pose"] is not None

    cache.latest["ts"] = time.time() - 60  # stale → treated as no pose
    assert build_map_payload(config, config_path, cache)["pose"] is None


def test_api_map_rejects_non_finite_pose_and_stays_valid_json(tmp_path) -> None:
    import time
    from types import SimpleNamespace

    from jenai.config.store import build_minimal_config, save_config
    from jenai.webui.server import PoseCache, build_map_payload

    config = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )
    config_path = tmp_path / "config.toml"
    save_config(config, config_path)

    cache = PoseCache()
    cache._started = True  # don't spawn a bridge in tests
    cache.latest = {"x": 1.0, "y": 2.0, "yaw": 0.0, "ts": time.time()}
    cache._record_pose(
        SimpleNamespace(
            x=float("nan"),
            y=2.0,
            yaw=0.0,
            frame_id="map",
            source="/amcl_pose",
        )
    )

    payload = build_map_payload(config, config_path, cache)

    assert payload["pose"] is None
    assert payload["pose_error"] == "invalid_pose"
    assert "NaN" not in json.dumps(payload, allow_nan=False)
    assert "localization invalid (pose contains NaN/inf)" in render_dashboard_html({})


def test_pose_cache_backs_off_after_bridge_failure(monkeypatch) -> None:
    """A bridge that keeps dying must not be respawned by every 2s map poll —
    only after the backoff window (and the stale pose must be cleared)."""
    import time as _time
    from types import SimpleNamespace

    from jenai.webui import server as server_module
    from jenai.webui.server import PoseCache

    monkeypatch.setattr(server_module.RosBridgeClient, "available", staticmethod(lambda: True))
    spawned: list[int] = []
    monkeypatch.setattr(
        server_module.threading,
        "Thread",
        lambda **kw: SimpleNamespace(start=lambda: spawned.append(1)),
    )

    cache = PoseCache(retry_after_s=30.0)
    cache.ensure_started()
    assert len(spawned) == 1

    async def dead_loop() -> None:
        raise RuntimeError("bridge never came up")

    cache.latest = {"x": 1.0}
    monkeypatch.setattr(cache, "_loop", dead_loop)
    try:
        cache._run_loop()
    except RuntimeError:
        pass

    assert cache.latest is None  # stale pose cleared on exit
    cache.ensure_started()
    assert len(spawned) == 1  # within the backoff window: no respawn

    cache._last_exit = _time.monotonic() - 31.0  # window elapsed
    cache.ensure_started()
    assert len(spawned) == 2  # retried after backoff


# --- token auth -------------------------------------------------------------


def _tokened_server(tmp_path: Path, n_requests: int):
    server = make_server(_config(), tmp_path / "config.toml", port=0, token="s3cret")
    threads = [threading.Thread(target=server.handle_request) for _ in range(n_requests)]
    for t in threads:
        t.start()
    host, port = server.server_address
    return server, threads, f"http://{host}:{port}"


def _get(url: str, headers: dict | None = None) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def test_webui_auth_rejects_missing_and_wrong_token(tmp_path: Path) -> None:
    server, threads, base = _tokened_server(tmp_path, 3)
    try:
        assert _get(f"{base}/")[0] == 401
        assert _get(f"{base}/api/status")[0] == 401
        assert _get(f"{base}/?token=wrong")[0] == 401
    finally:
        for t in threads:
            t.join(timeout=5)
        server.server_close()


def test_webui_auth_accepts_bearer_cookie_and_query(tmp_path: Path) -> None:
    server, threads, base = _tokened_server(tmp_path, 3)
    try:
        status, _, _ = _get(f"{base}/api/status", {"Authorization": "Bearer s3cret"})
        assert status == 200
        status, _, _ = _get(f"{base}/api/status", {"Cookie": "jenai_token=s3cret"})
        assert status == 200
        # Query token authorizes the page AND grants the session cookie the
        # dashboard's /api fetches rely on.
        status, headers, _ = _get(f"{base}/?token=s3cret")
        assert status == 200
        assert "jenai_token=s3cret" in headers.get("Set-Cookie", "")
    finally:
        for t in threads:
            t.join(timeout=5)
        server.server_close()


def test_webui_stop_works_without_token(monkeypatch, tmp_path: Path) -> None:
    import jenai.webui.server as srv

    monkeypatch.setattr(srv, "_do_stop", lambda *a, **k: {"kind": "info", "html": "stopped"})
    server, threads, base = _tokened_server(tmp_path, 1)
    pending = server.RequestHandlerClass.pending
    confirm_id = pending.put({"type": "drive"})
    try:
        # Deliberately claim a large body but send none: STOP must execute before
        # reading Content-Length and must revoke actions previewed before it.
        req = urllib.request.Request(
            f"{base}/api/stop",
            data=None,
            headers={"Content-Length": str(10 * 1024 * 1024)},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            assert json.loads(resp.read())["kind"] == "info"
        assert pending.pop(confirm_id) is None
    finally:
        for t in threads:
            t.join(timeout=5)
        server.server_close()


def test_webui_rejects_non_object_json_body(tmp_path: Path) -> None:
    # Valid JSON that is not an object must 400 like malformed JSON, not be
    # silently coerced to {} and produce a misleading downstream error.
    server, threads, base = _tokened_server(tmp_path, 1)
    try:
        req = urllib.request.Request(
            f"{base}/api/command",
            data=b"[1, 2, 3]",
            headers={"Authorization": "Bearer s3cret"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected HTTP 400 for a non-object JSON body")
        except urllib.error.HTTPError as err:
            assert err.code == 400
            assert json.loads(err.read())["kind"] == "error"
    finally:
        for t in threads:
            t.join(timeout=5)
        server.server_close()


def test_webui_stop_with_small_body_still_returns_response(monkeypatch, tmp_path: Path) -> None:
    # The stop handler answers before reading the body, then drains it — a
    # normal browser POST (tiny JSON body) must still get the JSON response.
    import jenai.webui.server as srv

    monkeypatch.setattr(srv, "_do_stop", lambda *a, **k: {"kind": "info", "html": "stopped"})
    server, threads, base = _tokened_server(tmp_path, 1)
    try:
        req = urllib.request.Request(f"{base}/api/stop", data=b"{}", method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            assert json.loads(resp.read())["kind"] == "info"
    finally:
        for t in threads:
            t.join(timeout=5)
        server.server_close()


def test_webui_rejects_oversized_json_body(tmp_path: Path) -> None:
    server, threads, _ = _tokened_server(tmp_path, 1)
    host, port = server.server_address
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request(
            "POST",
            "/api/command",
            body=b"{}",
            headers={"Authorization": "Bearer s3cret", "Content-Length": str(64 * 1024 + 1)},
        )
        response = conn.getresponse()
        assert response.status == 413
        response.read()
    finally:
        conn.close()
        for thread in threads:
            thread.join(timeout=5)
        server.server_close()


def test_webui_no_token_configured_stays_open(tmp_path: Path) -> None:
    # token=None (unit-test/backwards-compat mode) must not demand auth.
    server = make_server(_config(), tmp_path / "config.toml", port=0)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    try:
        host, port = server.server_address
        assert _get(f"http://{host}:{port}/api/status")[0] == 200
    finally:
        thread.join(timeout=5)
        server.server_close()


def test_webui_wrong_query_token_gets_no_cookie(tmp_path: Path) -> None:
    # Regression: the 401 path must never Set-Cookie — that would hand the
    # real token to whoever guessed wrong.
    server, threads, base = _tokened_server(tmp_path, 1)
    try:
        status, headers, _ = _get(f"{base}/?token=wrong")
        assert status == 401
        assert "Set-Cookie" not in headers
    finally:
        for t in threads:
            t.join(timeout=5)
        server.server_close()


# --- camera frame endpoint + multi-page dashboard ---------------------------


def test_api_frame_honestly_503_without_bridge(tmp_path: Path) -> None:
    server = make_server(_config(), tmp_path / "config.toml", port=0)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    try:
        host, port = server.server_address
        status, headers, body = _get(f"http://{host}:{port}/api/frame")
        assert status == 503
        assert json.loads(body)["kind"] == "error"
    finally:
        thread.join(timeout=5)
        server.server_close()


def test_api_frame_serves_jpeg_from_bridge(tmp_path: Path) -> None:
    class StubCache:
        def ensure_started(self) -> None:
            pass

        def submit(self, coro_factory, timeout: float = 10.0):
            frame = tmp_path / "frame.jpg"
            frame.write_bytes(b"\xff\xd8fakejpeg\xff\xd9")
            return frame

    server = make_server(_config(), tmp_path / "config.toml", port=0)
    server.RequestHandlerClass.pose_cache = StubCache()
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    try:
        host, port = server.server_address
        status, headers, body = _get(f"http://{host}:{port}/api/frame")
        assert status == 200
        assert headers["Content-Type"] == "image/jpeg"
        assert body.startswith(b"\xff\xd8")
        assert not (tmp_path / "frame.jpg").exists()  # one-shot temp is cleaned
    finally:
        thread.join(timeout=5)
        server.server_close()


def test_api_frame_requires_token(tmp_path: Path) -> None:
    server, threads, base = _tokened_server(tmp_path, 1)
    try:
        assert _get(f"{base}/api/frame")[0] == 401  # frames are not public
    finally:
        for t in threads:
            t.join(timeout=5)
        server.server_close()


def test_dashboard_has_camera_and_api_pages(tmp_path: Path) -> None:
    from jenai.webui.render import render_dashboard_html

    html = render_dashboard_html(build_status_payload(_config(), tmp_path / "config.toml"))
    for needle in (
        'data-view="camera"',
        'data-view="api"',
        'id="rgb"',
        'id="odom-mini"',
        "api/frame",
        "/api/stop",
    ):
        assert needle in html


def test_api_topics_endpoint_and_camera_picker(tmp_path: Path, monkeypatch) -> None:
    import jenai.webui.server as srv

    monkeypatch.setattr(
        srv,
        "_ros_snapshot",
        lambda: {
            "available": True,
            "topics": [{"name": "/rgb/image", "kind": "sensor"}],
            "count": 1,
            "error": None,
        },
    )
    server = make_server(_config(), tmp_path / "config.toml", port=0)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    try:
        host, port = server.server_address
        status, _, body = _get(f"http://{host}:{port}/api/topics")
        assert status == 200
        assert json.loads(body)["topics"][0]["name"] == "/rgb/image"
    finally:
        thread.join(timeout=5)
        server.server_close()

    from jenai.webui.render import render_dashboard_html

    html = render_dashboard_html(build_status_payload(_config(), tmp_path / "config.toml"))
    for needle in ('id="cam-topic"', "api/topics", 'id="api-topics"'):
        assert needle in html
