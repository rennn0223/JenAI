from __future__ import annotations

import asyncio

from jenai.config.store import build_minimal_config
from jenai.tools import drive_core
from jenai.tools.drive_core import DriveIntent, extract_drive_command


def _config():
    return build_minimal_config(
        provider_name="test", provider="openai", default_model="gpt-test", api_key_env=""
    )


def test_regex_forward_with_duration() -> None:
    intent = drive_core._extract_via_regex("前進兩秒")
    assert intent.linear_x > 0 and intent.angular_z == 0.0
    intent2 = drive_core._extract_via_regex("go forward for 3 seconds")
    assert intent2.duration_s == 3.0


def test_regex_reverse_and_turns() -> None:
    assert drive_core._extract_via_regex("後退").linear_x < 0
    assert drive_core._extract_via_regex("左轉").angular_z > 0
    assert drive_core._extract_via_regex("turn right").angular_z < 0


def test_regex_stop() -> None:
    intent = drive_core._extract_via_regex("停")
    assert intent.linear_x == 0.0 and intent.angular_z == 0.0


def test_regex_returns_none_for_unrelated() -> None:
    assert drive_core._extract_via_regex("what is the weather") is None


def test_to_payload_shape() -> None:
    payload = DriveIntent(0.5, -0.3, 2.0, "x").to_payload()
    assert payload == {
        "linear": {"x": 0.5, "y": 0.0, "z": 0.0},
        "angular": {"x": 0.0, "y": 0.0, "z": -0.3},
    }


def test_extract_prefers_regex_over_llm(monkeypatch) -> None:
    async def boom(*a, **k):  # LLM must not be called when regex matches
        raise AssertionError("LLM should not be called for a regex match")

    monkeypatch.setattr(drive_core, "ask_json", boom)
    intent = asyncio.run(extract_drive_command(_config(), "前進"))
    assert intent.linear_x > 0


def test_extract_falls_back_to_llm(monkeypatch) -> None:
    async def fake_json(config, prompt, *, binding="chat"):
        return {"linear_x": 0.3, "angular_z": 0.0, "duration_s": 4, "description": "creep"}

    monkeypatch.setattr(drive_core, "ask_json", fake_json)
    # No regex keyword -> must defer to the LLM.
    intent = asyncio.run(extract_drive_command(_config(), "do a gentle crawl"))
    assert intent.linear_x == 0.3 and intent.duration_s == 4.0 and intent.description == "creep"


def test_extract_llm_bad_output_returns_none(monkeypatch) -> None:
    async def fake_json(config, prompt, *, binding="chat"):
        return None

    monkeypatch.setattr(drive_core, "ask_json", fake_json)
    assert asyncio.run(extract_drive_command(_config(), "do a barrel roll")) is None
