"""Stable TUI command catalog and visual tokens.

Keeping these declarations outside the App shell makes the interaction engine
reviewable without changing one pixel of the user-approved visual design.
"""

from __future__ import annotations

import re

from jenai.tui.command_palette import SlashCommand

_CASUAL_GREETING = re.compile(
    r"(?:hi|hello|hey)(?:\s+(?:there|jenai))?"
    r"|(?:嗨|你好|哈囉|哈啰|早安|午安|晚安)(?:\s*jenai)?[啊呀嗎么]?",
    re.IGNORECASE,
)


def is_casual_greeting(value: str) -> bool:
    """Return true only for a standalone greeting, never an action prefixed by one."""
    normalized = value.strip().strip("!?！？。、,.~～👋 ")
    return bool(_CASUAL_GREETING.fullmatch(normalized))


SLASH_COMMANDS = [
    SlashCommand("/help", "查看 JenAI 指令與快捷鍵"),
    SlashCommand("/status", "查看模型、設定與環境狀態"),
    SlashCommand("/stop", "緊急停止：取消導航並送出零速度"),
    SlashCommand("/doctor", "檢查設定、ROS 2 與執行環境"),
    SlashCommand("/providers", "列出已設定的 provider profiles"),
    SlashCommand("/model", "列出或切換 provider 模型（Ollama 等）", "/model <name|number>"),
    SlashCommand("/models", "查看模型綁定"),
    SlashCommand("/provider", "查看或切換啟用中的 provider profile", "/provider <name>"),
    SlashCommand("/permissions", "查看哪些指令需要批准"),
    SlashCommand("/config", "查看設定檔資訊"),
    SlashCommand("/plan", "規劃任務，不執行任何工具", "/plan <task>"),
    SlashCommand("/run", "執行任務，依需要呼叫工具", "/run <task>"),
    SlashCommand("/why", "說明目前 run 的上一個決策"),
    SlashCommand("/review", "重新規劃並檢視目前計畫"),
    SlashCommand("/abort", "中止目前 run，繼續處理 queue"),
    SlashCommand("/queue", "查看或清除排隊中的指令", "/queue [clear]"),
    SlashCommand("/ros topics", "列出 ROS 2 topics"),
    SlashCommand(
        "/ros topic-info", "查看 topic 的型別、publishers 與 subscribers", "/ros topic-info <topic>"
    ),
    SlashCommand("/ros schema", "摘要 ROS 2 topic 的訊息 schema", "/ros schema <topic>"),
    SlashCommand("/ros echo", "擷取 topic 最近的訊息", "/ros echo <topic> [count]"),
    SlashCommand("/ros pub", "發布一次 ROS 2 topic 訊息（需要批准）", "/ros pub <topic> <payload>"),
    SlashCommand(
        "/ros drive",
        "移動 N 秒後自動停止（需要批准）",
        "/ros drive <topic> <payload> [seconds]",
    ),
    SlashCommand("/drive", "用自然語言控制短距離移動（需要批准）", "/drive 前進兩秒"),
    SlashCommand("/mission", "執行多步驟任務（需要批准）", "/mission kitchen, lobby"),
    SlashCommand(
        "/patrol",
        "巡邏多個 waypoints，可選擇拍照報告（需要批准）",
        "/patrol A, B x2 photo",
    ),
    SlashCommand(
        "/explore",
        "在已儲存地點間進行有界探索（需要批准）",
        "/explore 5m goals=8 tag=room photo",
    ),
    SlashCommand("/dock", "返回充電 Dock（需要批准）"),
    SlashCommand(
        "/report",
        "查看巡邏報告或結構化 task receipts",
        "/report [list|task [list]|event]",
    ),
    SlashCommand("/skills", "列出檔案定義的使用者 skills（skills/*.toml）"),
    SlashCommand("/route", "解析並送出導航路線（需要批准）", "/route <text>"),
    SlashCommand("/loc list", "列出已知地點"),
    SlashCommand(
        "/loc add",
        "儲存地點：機器人目前位置（here）或 GPS lat/lon",
        "/loc add here <name> · /loc add gps <name> <lat> <lon>",
    ),
    SlashCommand("/loc show", "查看地點詳細資訊", "/loc show <name>"),
    SlashCommand("/loc move", "以機器人目前位置重新儲存地點", "/loc move <name>"),
    SlashCommand("/loc rename", "重新命名地點", "/loc rename <old> <new> (spaces: old -> new)"),
    SlashCommand("/loc rm", "刪除地點", "/loc rm <name>"),
    SlashCommand("/vision image", "使用 VLM 分析本機圖片", "/vision image <path>"),
    SlashCommand("/vision camera", "擷取 camera frame 並描述內容", "/vision camera [topic]"),
    SlashCommand(
        "/perception start",
        "持續進行 camera→VLM 場景分析（僅觀察）",
        "/perception start [topic] [hz]",
    ),
    SlashCommand("/perception stop", "停止 perception loop"),
    SlashCommand("/shell", "執行 host shell 指令（需要批准）", "/shell <cmd>"),
    SlashCommand(
        "/mode",
        "設定或切換 permission mode（Shift+Tab 備援）",
        "/mode [approve|plan|auto]",
    ),
    SlashCommand("/clear", "清除輸出區域"),
    SlashCommand("/quit", "離開 JenAI"),
]

# One command catalog feeds both completion and /help.  Group membership keeps
# only stable command names; descriptions and usages live exactly once above.
# Import-time validation turns omissions/duplicates into an immediate developer
# error instead of letting the operator discover a phantom or undocumented row.
_COMMAND_GROUP_MEMBERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Safety", ("/stop",)),
    ("Session", ("/help", "/status", "/doctor", "/queue", "/clear", "/quit")),
    ("Planning", ("/plan", "/run", "/why", "/review", "/abort")),
    (
        "ROS2",
        (
            "/ros topics",
            "/ros topic-info",
            "/ros schema",
            "/ros echo",
            "/ros pub",
            "/ros drive",
        ),
    ),
    (
        "Route",
        (
            "/route",
            "/loc list",
            "/loc add",
            "/loc show",
            "/loc move",
            "/loc rename",
            "/loc rm",
        ),
    ),
    (
        "Robot tasks",
        ("/drive", "/mission", "/patrol", "/explore", "/dock", "/report", "/skills"),
    ),
    (
        "Vision",
        ("/vision image", "/vision camera", "/perception start", "/perception stop"),
    ),
    ("System", ("/shell", "/mode", "/config")),
    (
        "Provider / Model",
        ("/providers", "/model", "/models", "/provider", "/permissions"),
    ),
)


def _grouped_commands() -> tuple[tuple[str, tuple[SlashCommand, ...]], ...]:
    by_name = {command.name: command for command in SLASH_COMMANDS}
    if len(by_name) != len(SLASH_COMMANDS):
        raise RuntimeError("duplicate names in the TUI command catalog")

    grouped: list[tuple[str, tuple[SlashCommand, ...]]] = []
    seen: set[str] = set()
    for group_name, names in _COMMAND_GROUP_MEMBERS:
        duplicates = seen.intersection(names)
        if duplicates:
            raise RuntimeError(f"commands assigned to multiple help groups: {sorted(duplicates)}")
        missing = set(names).difference(by_name)
        if missing:
            raise RuntimeError(f"unknown commands in help groups: {sorted(missing)}")
        seen.update(names)
        grouped.append((group_name, tuple(by_name[name] for name in names)))

    ungrouped = set(by_name).difference(seen)
    if ungrouped:
        raise RuntimeError(f"commands missing a help group: {sorted(ungrouped)}")
    return tuple(grouped)


COMMAND_GROUPS = _grouped_commands()

TUI_CSS = """
Screen {
    background: #0b0b0b;
    color: #d9d4cc;
}

#stage, #window {
    width: 100%;
    height: 100%;
    padding: 0;
    background: #0b0b0b;
}

#body {
    height: 1fr;
    padding: 1 2 0 2;
    scrollbar-size-vertical: 1;
    scrollbar-background: #0b0b0b;
    scrollbar-color: #302b28;
    scrollbar-color-hover: #403733;
    scrollbar-color-active: #403733;
}

#welcome {
    border: round #e8683f;
    border-title-color: #e8683f;
    border-title-style: bold;
    padding: 0;
    margin: 0 0 1 0;
    min-height: 15;
    height: auto;
}

#welcome-content {
    height: 19;
    layout: horizontal;
}

#welcome-left {
    width: 50%;
    height: 100%;
    padding: 1 1 0 1;
    align-horizontal: center;
}

#welcome-right {
    width: 50%;
    height: 100%;
    padding: 1 3 0 3;
    border-left: solid #553027;
}

.heading {
    color: #f2ede4;
    text-style: bold;
    text-align: center;
    width: 100%;
    height: auto;
}

#welcome-greeting {
    margin-bottom: 0;
}

#pixel-mark {
    color: #e8683f;
    text-align: center;
    width: 100%;
    height: auto;
    margin-bottom: 0;
}

.meta {
    color: #b8b2a7;
    text-align: center;
    width: 100%;
    height: auto;
}

.welcome-section-title {
    color: #e8683f;
    text-style: bold;
    height: auto;
    margin-bottom: 1;
}

#welcome-quick-start {
    height: auto;
    color: #d9d4cc;
}

.recent-title {
    border-top: solid #4a403b;
    margin-top: 1;
    padding-top: 1;
}

#welcome-recent {
    color: #b8b2a7;
    text-align: left;
}

#welcome.narrow #welcome-content {
    layout: vertical;
    height: auto;
}

#welcome.narrow #welcome-left {
    width: 100%;
    height: auto;
}

#welcome.narrow #welcome-right {
    display: none;
}

#welcome.compact #pixel-mark {
    display: none;
}

#welcome.compact #welcome-content {
    height: auto;
}

.prompt-line, .bullet-line {
    height: auto;
    margin: 0 0 1 0;
    color: #d9d4cc;
}

#events {
    height: auto;
    margin-bottom: 1;
}

.approval-card {
    background: #0b0b0b;
    border-top: solid #e8683f;
    border-bottom: solid #4a403b;
    padding: 1 0;
    margin-bottom: 1;
    height: auto;
}

#composer-wrap {
    height: auto;
    padding: 0 2 1 2;
    background: #0b0b0b;
}

#palette {
    height: auto;
    max-height: 16;
    margin-bottom: 1;
    padding: 1 1 0 1;
    background: #0b0b0b;
    border-top: solid #4a403b;
}

#composer-frame {
    height: 3;
    padding: 0 1;
    background: #0b0b0b;
    border-top: solid #4a403b;
    border-bottom: solid #4a403b;
}

#composer-line {
    height: 1fr;
    align-vertical: middle;
}

#composer-prompt {
    width: 2;
    height: 1;
    color: #e8683f;
    text-style: bold;
}

#composer {
    height: 1fr;
    width: 1fr;
    background: #0b0b0b;
    color: #f2ede4;
    border: none;
    padding: 0;
}

#composer:focus {
    border: none;
}

#spinner {
    height: auto;
    color: #e8683f;
    margin-bottom: 1;
    display: none;
}

#spinner.active {
    display: block;
}

#statusbar {
    height: 1;
    margin-top: 1;
}

#status-left {
    width: 1fr;
    height: 1;
    color: #7a756c;
}

#status-right {
    width: auto;
    height: 1;
    color: #7a756c;
    text-align: right;
}
"""
