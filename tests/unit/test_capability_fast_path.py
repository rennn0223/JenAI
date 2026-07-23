from __future__ import annotations

from jenai.capability_reporting import capability_card_report, is_capability_card_request
from jenai.config.store import build_minimal_config


def _config():
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="model",
        api_key_env="",
    )
    config.vehicle.type = "diff"
    config.vehicle.robot_id = "isaac-nova-carter"
    config.vehicle.display_name = "Isaac Sim Nova Carter"
    config.vehicle.limitations = [
        "Docking currently verifies pose only; charging feedback is unavailable in Isaac Sim."
    ]
    return config


def test_capability_questions_are_detected_without_matching_motion_commands() -> None:
    assert is_capability_card_request("請介紹你自己、能力、成熟度與限制")
    assert is_capability_card_request("Who are you and what can this robot do?")
    assert not is_capability_card_request("導航到 dock")
    assert not is_capability_card_request("幫我檢查目前位置與 Nav2 狀態")


def test_capability_report_never_turns_unvalidated_into_success() -> None:
    report = capability_card_report(_config(), language_hint="請介紹能力與限制")

    assert "Isaac Sim Nova Carter" in report
    assert "已實作，尚未完成產品驗證" in report
    assert "Dock" in report
    assert "充電" in report
    assert "本次即時可用" in report
    assert "驗證成功" not in report


def test_capability_report_supports_english_natural_language() -> None:
    report = capability_card_report(_config(), language_hint="What can you do?")

    assert "Registered capabilities" in report
    assert "implemented; product validation pending" in report
    assert "Known limitations" in report
