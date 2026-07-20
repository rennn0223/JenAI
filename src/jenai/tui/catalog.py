"""Stable TUI command catalog and visual tokens.

Keeping these declarations outside the App shell makes the interaction engine
reviewable without changing one pixel of the user-approved visual design.
"""

from __future__ import annotations

import re

from jenai.tui.panels import SlashCommand

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
    SlashCommand("/help", "Show available JenAI commands"),
    SlashCommand("/status", "Show provider, model, config, and doctor state"),
    SlashCommand("/stop", "EMERGENCY STOP: cancel navigation and zero velocity"),
    SlashCommand("/doctor", "Run setup and environment checks"),
    SlashCommand("/providers", "List configured provider profiles"),
    SlashCommand("/model", "List provider models and switch (Ollama etc.)", "/model <name|number>"),
    SlashCommand("/models", "Show model bindings"),
    SlashCommand("/provider", "Show or switch the active provider profile", "/provider <name>"),
    SlashCommand("/permissions", "Show which commands require approval"),
    SlashCommand("/config", "Show config file details"),
    SlashCommand("/plan", "Plan a task without executing any tools", "/plan <task>"),
    SlashCommand("/run", "Execute a task, calling tools as needed", "/run <task>"),
    SlashCommand("/why", "Explain the current run's last decision"),
    SlashCommand("/review", "Re-plan and critique the current plan"),
    SlashCommand("/abort", "Abort the active run and continue the queue"),
    SlashCommand("/queue", "Show or clear queued commands", "/queue [clear]"),
    SlashCommand("/ros topics", "List ROS2 topics"),
    SlashCommand(
        "/ros topic-info", "Show a topic's type/publishers/subscribers", "/ros topic-info <topic>"
    ),
    SlashCommand("/ros schema", "Summarize a ROS2 topic's message schema", "/ros schema <topic>"),
    SlashCommand("/ros echo", "Snapshot recent messages on a topic", "/ros echo <topic> [count]"),
    SlashCommand(
        "/ros pub", "Publish once to a ROS2 topic (needs approval)", "/ros pub <topic> <payload>"
    ),
    SlashCommand(
        "/ros drive",
        "Drive for N seconds then auto-stop (needs approval)",
        "/ros drive <topic> <payload> [seconds]",
    ),
    SlashCommand("/drive", "Drive by plain language (needs approval)", "/drive 前進兩秒"),
    SlashCommand(
        "/mission", "Run a multi-step mission (needs approval)", "/mission kitchen, lobby"
    ),
    SlashCommand(
        "/patrol",
        "Loop waypoints, optional photo report (needs approval)",
        "/patrol A, B x2 photo",
    ),
    SlashCommand(
        "/explore",
        "Bounded low-repeat exploration of saved places (needs approval)",
        "/explore 5m goals=8 tag=room photo",
    ),
    SlashCommand("/dock", "Return to the charging dock (needs approval)"),
    SlashCommand("/report", "Show the latest patrol report (+LLM digest)", "/report [list]"),
    SlashCommand("/skills", "List file-defined user skills (skills/*.toml)"),
    SlashCommand("/route", "Resolve and send a navigation route (needs approval)", "/route <text>"),
    SlashCommand("/loc list", "List known locations"),
    SlashCommand(
        "/loc add",
        "Save a location: robot's position (here) or GPS lat/lon",
        "/loc add here <name> · /loc add gps <name> <lat> <lon>",
    ),
    SlashCommand("/loc show", "Show a location's details", "/loc show <name>"),
    SlashCommand("/loc move", "Re-save a location at the robot's position", "/loc move <name>"),
    SlashCommand(
        "/loc rename", "Rename a location", "/loc rename <old> <new> (spaces: old -> new)"
    ),
    SlashCommand("/loc rm", "Delete a location", "/loc rm <name>"),
    SlashCommand("/vision image", "Analyze a local image with the VLM", "/vision image <path>"),
    SlashCommand(
        "/vision camera", "Capture a camera frame and describe it", "/vision camera [topic]"
    ),
    SlashCommand(
        "/perception start",
        "Continuous camera→VLM scene analysis (observe only)",
        "/perception start [topic] [hz]",
    ),
    SlashCommand("/perception stop", "Stop the perception loop"),
    SlashCommand("/shell", "Run a host shell command (needs approval)", "/shell <cmd>"),
    SlashCommand(
        "/mode",
        "Set/cycle permission mode (Shift+Tab fallback)",
        "/mode [approve|plan|auto]",
    ),
    SlashCommand("/clear", "Clear the output area"),
    SlashCommand("/quit", "Exit JenAI"),
]

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
    height: 15;
    layout: horizontal;
}

#welcome-left {
    width: 42%;
    height: 100%;
    padding: 2 2 1 2;
    align-horizontal: center;
}

#welcome-right {
    width: 58%;
    height: 100%;
    padding: 2 3 1 3;
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
    margin-bottom: 1;
}

#pixel-mark {
    color: #e8683f;
    text-align: center;
    width: 100%;
    height: auto;
    margin-bottom: 1;
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
