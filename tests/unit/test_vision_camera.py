from __future__ import annotations

import asyncio
from pathlib import Path

from jenai.bridge import BridgeError
from jenai.config.store import build_minimal_config
from jenai.schemas import VisionOutput
from jenai.tui import JenAITuiApp


def _app(tmp_path: Path) -> JenAITuiApp:
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="gpt-test",
        api_key_env="",
    )
    return JenAITuiApp(config=config, config_path=tmp_path / "config.toml")


class _FakeBridge:
    def __init__(self, frame: Path | None) -> None:
        self._frame = frame
        self.requested_topics: list[str] = []

    async def capture_frame(self, topic: str, timeout: float = 5.0) -> Path:
        self.requested_topics.append(topic)
        if self._frame is None:
            raise BridgeError(f"No image received on {topic} within {timeout:.0f}s.")
        return self._frame


def test_vision_camera_captures_default_topic_and_analyzes(tmp_path: Path, monkeypatch) -> None:
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"fake")
    analyzed: list[str] = []

    async def fake_analyze(config, path, task_context=""):
        analyzed.append(str(path))
        return VisionOutput(source=str(path), summary="A red box on a green floor.")

    async def run() -> None:
        app = _app(tmp_path)
        fake = _FakeBridge(frame)

        async def fake_get_bridge():
            return fake

        monkeypatch.setattr(app, "_get_bridge", fake_get_bridge)
        monkeypatch.setattr("jenai.tui.robot_commands.analyze_image", fake_analyze)
        async with app.run_test():
            await app.handle_user_text("/vision camera")
            await app.handle_user_text("/vision camera /front_cam/image_raw")

        assert fake.requested_topics == ["/camera/image_raw", "/front_cam/image_raw"]
        assert analyzed == [str(frame), str(frame)]

    asyncio.run(run())


def test_vision_camera_reports_capture_failure(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        app = _app(tmp_path)
        fake = _FakeBridge(None)

        async def fake_get_bridge():
            return fake

        monkeypatch.setattr(app, "_get_bridge", fake_get_bridge)
        async with app.run_test():
            await app.handle_user_text("/vision camera")
        # No crash; the warning path was exercised (nothing analyzed).

    asyncio.run(run())


def test_vision_image_path_still_works(tmp_path: Path, monkeypatch) -> None:
    analyzed: list[str] = []

    async def fake_analyze(config, path, task_context=""):
        analyzed.append(str(path))
        return VisionOutput(source=str(path), summary="ok")

    async def run() -> None:
        app = _app(tmp_path)
        monkeypatch.setattr("jenai.tui.robot_commands.analyze_image", fake_analyze)
        async with app.run_test():
            await app.handle_user_text("/vision image /tmp/some.png")

        assert analyzed == ["/tmp/some.png"]

    asyncio.run(run())


def test_parse_json_reply_tolerates_fences_and_prose() -> None:
    from jenai.providers.chat import parse_json_reply

    assert parse_json_reply('{"a": 1}') == {"a": 1}
    assert parse_json_reply('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_reply('```\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_reply('Here is the result:\n{"a": 1}\nHope that helps!') == {"a": 1}
    assert parse_json_reply("no json here") is None
