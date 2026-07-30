from __future__ import annotations

from pathlib import Path

from jenai.config.store import build_minimal_config
from jenai.schemas import ApprovalRequest
from jenai.tui.app import JenAITuiApp
from jenai.tui.catalog import SLASH_COMMANDS
from jenai.tui.panels import CommandPalette, WelcomePanel
from jenai.tui.widgets.approval_card import ApprovalCard


def _app() -> JenAITuiApp:
    return JenAITuiApp(
        config=build_minimal_config(
            provider_name="test",
            provider="openai",
            default_model="gpt-test",
            api_key_env="",
        ),
        config_path=Path("/tmp/jenai-tui-ux-copy/config.toml"),
    )


def test_welcome_leads_with_natural_language_and_clear_next_steps() -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test(size=(120, 30)):
            assert str(app.query_one("#welcome-greeting").render()) == "歡迎回來！"
            assert app.query_one("#composer").placeholder == "輸入任務，或按 / 查看指令"

            panel = app.query_one(WelcomePanel)
            quick_start = str(app.query_one("#welcome-quick-start").render())
            recent = str(app.query_one("#welcome-recent").render())

            assert panel
            assert "直接輸入任務" in quick_start
            assert "/doctor" in quick_start
            assert "/help" in quick_start
            assert "ROS 2" in quick_start
            assert "尚無操作紀錄" in recent

    import asyncio

    asyncio.run(run())


def test_command_palette_explains_commands_in_operator_language() -> None:
    descriptions = {command.name: command.description for command in SLASH_COMMANDS}

    assert "查看" in descriptions["/help"]
    assert "緊急停止" in descriptions["/stop"]
    assert "批准" in descriptions["/route"]
    assert "ROS 2" in descriptions["/ros topics"]

    palette = CommandPalette()
    palette.update_matches([], 0)
    assert "找不到相符指令" in str(palette.render())


def test_approval_card_uses_plain_traditional_chinese_without_changing_choices() -> None:
    request = ApprovalRequest(
        run_id="run-1",
        tool_call_id="tool-1",
        title="前往工作區",
        summary="送出已驗證的導航目標。",
        raw_action="/route 工作區",
        risk_level="p1",
        effect_scope="robot_control",
        justification="操作員提出導航要求。",
    )

    rendered = str(ApprovalCard(request).render())

    assert "可能移動已連線的實體機器人" in rendered
    assert "要繼續嗎？" in rendered
    assert "1. 本次允許" in rendered
    assert "這個 session 不再詢問" in rendered
    assert "3. 不允許" in rendered
    assert "Esc 取消" in rendered
