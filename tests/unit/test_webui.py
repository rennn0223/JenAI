from __future__ import annotations

import asyncio
import json
import threading
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


def test_render_dashboard_html_renders_doctor_and_ros(tmp_path: Path) -> None:
    payload = build_status_payload(_config(), tmp_path / "config.toml")
    html = render_dashboard_html(payload)
    assert "<h1>JenAI</h1>" in html
    assert "Environment" in html
    assert "ROS2 Graph" in html
    # Doctor check names surface with friendly (humanized) labels.
    assert "Python" in html


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

    async def fake_drive(topic, message_type, payload, *, duration_s=1.0):
        called["duration"] = duration_s
        return RosPubOutput(
            topic=topic, message_type=message_type,
            execution_status="succeeded", result_message="drove then stopped",
        )

    monkeypatch.setattr(commands.ros2_core, "ros_drive", fake_drive)
    action = {
        "type": "drive", "topic": "/cmd_vel",
        "message_type": "geometry_msgs/msg/Twist",
        "payload": {"linear": {"x": 0.2}}, "duration": 2.0,
    }
    res = asyncio.run(run_web_confirm(_config(), action))
    assert res["kind"] == "result" and "drove" in res["html"]
    assert called["duration"] == 2.0


def test_web_command_unknown_is_error(tmp_path: Path) -> None:
    res = asyncio.run(run_web_command(_config(), tmp_path / "c.toml", "/frobnicate"))
    assert res["kind"] == "error"


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
