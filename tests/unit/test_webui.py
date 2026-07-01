from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

from jenai.config.store import build_minimal_config
from jenai.state.runs import RunStore
from jenai.webui import build_status_payload, render_dashboard_html
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


def test_render_dashboard_html_renders_doctor_and_ros(tmp_path: Path) -> None:
    payload = build_status_payload(_config(), tmp_path / "config.toml")
    html = render_dashboard_html(payload)
    assert "<h1>JenAI</h1>" in html
    assert "Environment" in html
    assert "ROS2 Graph" in html
    # Doctor check names surface in the page.
    assert "python" in html


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
