from __future__ import annotations

from jenai.language import normalize_user_visible_text, output_language_for


def test_detects_chinese_operator_language() -> None:
    assert output_language_for("請檢查機器人狀態") == "zh-TW"
    assert output_language_for("check robot status") == "en"


def test_normalizes_any_chinese_model_output_to_taiwan_traditional() -> None:
    assert normalize_user_visible_text("机器人位于仓库区域。", "en") == "機器人位於倉庫區域。"


def test_normalizes_mixed_traditional_and_simplified_prose() -> None:
    assert normalize_user_visible_text("机器人位於倉庫。", "zh-TW") == "機器人位於倉庫。"
    assert normalize_user_visible_text("機器人在仓库巡檢。", "zh-TW") == "機器人在倉庫巡檢。"


def test_preserves_identifiers_and_english_text() -> None:
    text = "Call navigate_to on /cmd_vel after 2.5 s."
    assert normalize_user_visible_text(text, "en") == text


def test_normalizes_prose_without_rewriting_paths_code_or_named_values() -> None:
    text = '机器人要去 /仓库/后门，然后调用 `navigate_to("后门")`，任务 target=后门，最后回报状态。'

    assert normalize_user_visible_text(text, "zh-TW") == (
        '機器人要去 /仓库/后门，然後呼叫 `navigate_to("后门")`，任務 target=后门，最後回報狀態。'
    )


def test_preserves_taiwan_usage_that_opencc_can_overconvert() -> None:
    text = "批准平台的游標，不要干擾機械系館的秘密。"
    assert normalize_user_visible_text(text, "zh-TW") == text


def test_preserves_existing_traditional_chinese_and_proper_nouns() -> None:
    text = "機械系館的巡檢文件與權限模式。"
    assert normalize_user_visible_text(text, "zh-TW") == text


def test_uses_taiwan_localized_technical_terms() -> None:
    text = "默认网络、激光扫描、视频与鼠标。"
    assert normalize_user_visible_text(text, "zh-TW") == "預設網路、雷射掃描、影片與滑鼠。"


def test_preserved_terms_do_not_rewrite_distinct_taiwan_terms() -> None:
    text = "批准文件，但不要刪除檔案；平台不是平臺。"
    assert normalize_user_visible_text(text, "zh-TW") == text
