"""Grouped command content rendered by /help."""

from __future__ import annotations

from jenai import __version__
from jenai.schemas import CommandGroup, HelpOutput, KeyboardShortcut
from jenai.tui.catalog import COMMAND_GROUPS

_COMMAND_GROUPS = [
    CommandGroup(name=name, commands=[command.completion for command in commands])
    for name, commands in COMMAND_GROUPS
]

_EXAMPLES = [
    "/plan 巡邏 A 區並記錄異常",
    "/ros schema /cmd_vel",
    "/route 從應科大樓到機械系館",
    "/explore 5m goals=8 tag=room",
    "/model llama3.2  (or /model 2 after listing with /model)",
]

_KEYBOARD_SHORTCUTS = [
    KeyboardShortcut(key="Enter", action="送出輸入（忙碌時排入 queue）／選擇批准選項"),
    KeyboardShortcut(key="!", action="將後續內容當成 shell 指令執行"),
    KeyboardShortcut(key="Esc", action="中斷目前任務並繼續 queue／拒絕批准"),
    KeyboardShortcut(
        key="1 / 2 / 3",
        action="選擇畫面上的批准選項；host／P2 只能單次允許",
    ),
    KeyboardShortcut(key="Tab", action="補全選取的指令"),
    KeyboardShortcut(key="↑ / ↓", action="瀏覽歷史、指令選單或批准選項"),
    KeyboardShortcut(key="Shift+Tab", action="切換 permission mode；終端不支援時使用 /mode"),
]


def build_help_output(section: str | None = None) -> HelpOutput:
    groups = _COMMAND_GROUPS
    title = f"JenAI v{__version__} — ROS 2 機器人 Agent 終端"
    if section:
        lowered = section.strip().lower()
        groups = [g for g in _COMMAND_GROUPS if lowered in g.name.lower()]
        if groups:
            title = f"JenAI 說明：{groups[0].name}"

    return HelpOutput(
        title=title,
        summary=(
            "直接輸入自然語言即可規劃或執行機器人任務；也可用 /ros 查看 ROS 2、"
            "用 /route 前往已知地點，或用 /status 檢查 provider 與模型狀態。"
        ),
        command_groups=groups,
        examples=_EXAMPLES if not section else [],
        keyboard_shortcuts=_KEYBOARD_SHORTCUTS if not section else [],
    )
