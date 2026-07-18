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
    background: #1c1b18;
    color: #d9d3c7;
}

#stage, #window {
    width: 100%;
    height: 100%;
    padding: 0;
    background: #1c1b18;
}

#body {
    height: 1fr;
    padding: 1 3 0 3;
    scrollbar-size-vertical: 1;
    scrollbar-background: #1c1b18;
    scrollbar-color: #332f28;
    scrollbar-color-hover: #3a352e;
    scrollbar-color-active: #3a352e;
}

#welcome {
    border: round #c15f3c;
    padding: 0 1;
    margin-bottom: 1;
    height: auto;
}

#welcome-content {
    height: auto;
    layout: horizontal;
}

#welcome-left {
    width: 40%;
    height: auto;
    padding: 1 2;
    align-horizontal: center;
}

#welcome-right {
    width: 60%;
    height: auto;
    padding: 1 2;
    border-left: solid #3a352e;
}

.heading {
    color: #f2ede1;
    text-style: bold;
    text-align: center;
    width: 100%;
    height: auto;
}

#welcome-greeting {
    margin-bottom: 1;
}

#pixel-mark {
    color: #d97757;
    text-align: center;
    width: 100%;
    height: auto;
    margin-bottom: 1;
}

#welcome-product {
    margin-bottom: 1;
}

.meta {
    color: #9c9689;
    text-align: center;
    width: 100%;
    height: auto;
}

.welcome-section-title {
    color: #d97757;
    text-style: bold;
    height: auto;
    margin-bottom: 1;
}

#welcome-quick-start {
    height: auto;
    color: #9c9689;
}

.recent-title {
    border-top: solid #3a352e;
    margin-top: 1;
    padding-top: 1;
}

#welcome-recent {
    text-align: left;
}

#welcome.narrow #welcome-content {
    layout: vertical;
}

#welcome.narrow #welcome-left {
    width: 100%;
}

#welcome.narrow #welcome-right {
    display: none;
}

#welcome.compact #pixel-mark,
#welcome.compact #welcome-product {
    display: none;
}

.prompt-line, .bullet-line {
    height: auto;
    margin: 0 0 1 0;
    color: #d9d3c7;
}

#events {
    height: auto;
    margin-bottom: 1;
}

.approval-card {
    background: #1c1b18;
    border-top: solid #c15f3c;
    border-bottom: solid #3a352e;
    padding: 1 0;
    margin-bottom: 1;
    height: auto;
}

#composer-wrap {
    height: auto;
    padding: 0 3 1 3;
    background: #1c1b18;
}

#palette {
    height: auto;
    max-height: 16;
    margin-bottom: 1;
    padding: 1 1 0 1;
    background: #1c1b18;
    border-top: solid #3a352e;
}

#composer-frame {
    height: 3;
    padding: 0 1;
    background: #1c1b18;
    border-top: solid #3a352e;
    border-bottom: solid #3a352e;
}

#composer {
    height: 1fr;
    background: #1c1b18;
    color: #f2ede1;
    border: none;
    padding: 0;
}

#composer:focus {
    border: none;
}

#spinner {
    height: auto;
    color: #d97757;
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
    color: #9c9689;
}

#status-right {
    width: auto;
    height: 1;
    color: #9c9689;
    text-align: right;
}
"""
