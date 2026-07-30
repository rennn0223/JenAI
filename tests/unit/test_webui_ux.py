from __future__ import annotations

from jenai.webui.presentation import build_web_status_view
from jenai.webui.render import render_dashboard_html, render_main


def _status(*, overall: str = "pass", ros_available: bool = True) -> dict[str, object]:
    return {
        "provider": "local",
        "model": "qwen3.5",
        "config_complete": True,
        "locations": 2,
        "doctor": {
            "overall": overall,
            "items": [
                {
                    "section": "environment",
                    "check": "python",
                    "status": overall,
                    "message": "Python 3.12 detected.",
                    "fix": None,
                }
            ],
        },
        "ros": {
            "available": ros_available,
            "topics": [{"name": "/clock", "kind": "infra"}] if ros_available else [],
            "count": 1 if ros_available else 0,
            "error": None,
        },
    }


def test_status_projection_translates_internal_health_without_losing_detail() -> None:
    view = build_web_status_view(_status(overall="warn"))

    assert view.overall == "warn"
    assert view.overall_label == "注意"
    assert view.health_summary == "系統可以使用，另有 1 項提醒。"
    assert view.checks[0].section_label == "執行環境"
    assert view.checks[0].message == "Python 3.12 detected."


def test_status_projection_tolerates_partial_payload() -> None:
    view = build_web_status_view({})

    assert view.provider == "未設定"
    assert view.overall == "unknown"
    assert view.health_summary == "正在確認系統狀態…"
    assert not view.ros_available


def test_status_projection_treats_malformed_counts_as_unknown_zero() -> None:
    view = build_web_status_view(
        {
            "locations": "not-a-number",
            "ros": {"available": True, "count": None, "topics": []},
        }
    )

    assert view.locations == 0
    assert view.ros_count == 0


def test_dashboard_is_offline_ready_and_accessible() -> None:
    page = render_dashboard_html(_status())

    assert '<html lang="zh-Hant-TW">' in page
    assert "fonts.googleapis.com" not in page
    assert "fonts.gstatic.com" not in page
    assert 'role="tablist"' in page
    assert page.count('role="tab"') == 4
    assert 'aria-selected="true"' in page
    assert 'aria-live="polite"' in page
    assert 'aria-label="輸入 JenAI 指令或自然語言任務"' in page
    assert 'aria-label="立即停止機器人"' in page
    assert "@media(prefers-reduced-motion:reduce)" in page
    assert ">1</span> 個 topics" in page


def test_dashboard_keeps_card_and_focus_styles_as_separate_css_rules() -> None:
    page = render_dashboard_html(_status())

    assert ".card{\n  background:" in page
    assert "animation:rise .5s cubic-bezier(.2,.7,.2,1) both;\n}" in page
    assert "}\nbutton:focus-visible,input:focus-visible" in page
    assert (
        ".card{\n  background:var(--card); border:1px solid var(--line); "
        "border-radius:18px;\nbutton" not in page
    )


def test_status_fragment_uses_actionable_traditional_chinese_copy() -> None:
    fragment = render_main(_status(ros_available=False))

    assert "系統狀態正常，可以開始使用。" in fragment
    assert "ROS 2" in fragment
    assert "JenAI doctor" in fragment
    assert "Environment" not in fragment
    assert "ROS2 Graph" not in fragment


def test_command_failure_path_restores_input_and_reports_unknown_delivery() -> None:
    page = render_dashboard_html(_status())

    assert "input.value = text" in page
    assert "setConnection(" in page
    assert "throw new Error" in page
    assert "無法確認伺服器是否已接收這項指令" in page
    assert "請先查看目前狀態，再決定是否重試" in page
    assert "指令尚未送出" not in page


def test_confirmation_response_loss_requires_state_check_before_retry() -> None:
    page = render_dashboard_html(_status())
    catch_block = page.split("無法確認動作結果：", 1)[1].split("}finally{", 1)[0]

    assert "請勿重複批准" in catch_block
    assert "必要時使用 STOP" in catch_block
    assert "yesButton.disabled = false" not in catch_block
    assert "noButton.disabled = false" not in catch_block


def test_status_fragment_renders_durable_run_approval_and_tool_timeline() -> None:
    status = _status()
    status["transcript"] = [
        {
            "run_id": "run-1",
            "status": "awaiting_approval",
            "outcome": None,
            "summary": "前往 Dock",
            "final_output": None,
            "started_at": "2026-07-30T08:00:00Z",
            "finished_at": None,
            "approvals": [
                {
                    "approval_id": "approval-1",
                    "title": "前往 Dock",
                    "summary": "機器人將開始移動。",
                    "tool_name": "navigate",
                    "risk_level": "p1",
                    "status": "pending",
                    "created_at": "2026-07-30T08:00:01Z",
                }
            ],
            "tool_calls": [
                {
                    "tool_call_id": "tool-1",
                    "tool_name": "navigate",
                    "input_summary": "Dock",
                    "status": "awaiting_approval",
                    "output_summary": None,
                    "started_at": None,
                    "ended_at": None,
                }
            ],
        }
    ]

    fragment = render_main(status)

    assert "任務監控" in fragment
    assert "目前任務" in fragment
    assert "前往 Dock" in fragment
    assert "等待批准" in fragment
    assert "待批准" in fragment
    assert "機器人將開始移動。" in fragment
    assert "工具時間軸" in fragment
    assert "navigate" in fragment
    assert "本工作階段紀錄" in fragment
    assert ">1</span> 筆任務" in fragment
    assert "1 runs" not in fragment
