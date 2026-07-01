from __future__ import annotations

import asyncio

import pytest

from jenai.config.store import build_minimal_config
from jenai.tools import vision_core
from jenai.tools.vision_core import VisionError


def _config():
    return build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="gpt-test",
        api_key_env="",
    )


def test_analyze_image_rejects_missing_file() -> None:
    with pytest.raises(VisionError):
        asyncio.run(vision_core.analyze_image(_config(), "/no/such/file.png"))


def test_analyze_image_rejects_non_image(tmp_path) -> None:
    text_file = tmp_path / "notes.txt"
    text_file.write_text("hello", encoding="utf-8")
    with pytest.raises(VisionError, match="not a supported image"):
        asyncio.run(vision_core.analyze_image(_config(), str(text_file)))


def test_analyze_image_maps_vlm_json(monkeypatch, tmp_path) -> None:
    image = tmp_path / "frame.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n fake pixels")

    async def fake_vision_json(config, prompt, data_url, **kw):
        assert data_url.startswith("data:image/png;base64,")
        return {
            "summary": "A robot in a hallway.",
            "objects": ["robot", "door"],
            "anomalies": ["spill"],
            "relevance_to_task": "navigation",
            "next_action_suggestions": ["avoid spill"],
        }

    monkeypatch.setattr(vision_core, "ask_vision_json", fake_vision_json)

    output = asyncio.run(
        vision_core.analyze_image(_config(), str(image), task_context="patrol")
    )
    assert output.summary == "A robot in a hallway."
    assert output.objects == ["robot", "door"]
    assert output.anomalies == ["spill"]
    assert output.next_action_suggestions == ["avoid spill"]


def test_analyze_image_degrades_when_model_unavailable(monkeypatch, tmp_path) -> None:
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"fake jpeg")

    async def fake_vision_json(config, prompt, data_url, **kw):
        return None

    monkeypatch.setattr(vision_core, "ask_vision_json", fake_vision_json)

    output = asyncio.run(vision_core.analyze_image(_config(), str(image)))
    assert "unavailable" in output.summary.lower()
    assert output.source.endswith("frame.jpg")
