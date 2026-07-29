"""Typed, user-facing projection for the WebUI status page.

The server payload is an integration boundary assembled from doctor, ROS and
configuration data.  The browser should not need to understand those raw
shapes or translate internal enum values.  This module keeps that mapping in
one pure, testable place while leaving transport and runtime behaviour alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_STATUS_LABELS = {
    "pass": "正常",
    "warn": "注意",
    "fail": "失敗",
    "unknown": "未知",
}

_SECTION_LABELS = {
    "config": "設定",
    "environment": "執行環境",
    "locations": "地點",
    "nav": "Nav2",
    "nxdog": "NXDog",
    "provider": "模型服務",
    "ros2": "ROS 2",
    "site": "Site Profile",
    "twin": "Digital Twin",
    "webui": "WebUI",
}

_CHECK_LABELS = {
    "active_provider": "模型服務",
    "api_key": "API key",
    "assets": "WebUI 資源",
    "config_file": "設定檔",
    "env_file": "環境變數檔",
    "locations_file": "地點檔案",
    "model_bindings": "模型綁定",
    "python": "Python",
    "ros2_cli": "ROS 2 指令",
    "uv": "uv",
    "virtual_env": "虛擬環境",
}


def normalize_status(value: object) -> str:
    """Return one stable status token understood by the visual system."""
    token = str(value).lower()
    return token if token in _STATUS_LABELS else "unknown"


def status_label(value: object) -> str:
    """Translate an internal doctor status into operator-facing language."""
    return _STATUS_LABELS[normalize_status(value)]


def _nonnegative_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(0, parsed)


@dataclass(frozen=True, slots=True)
class HealthCheckView:
    section: str
    section_label: str
    check: str
    label: str
    status: str
    status_label: str
    message: str
    fix: str | None


@dataclass(frozen=True, slots=True)
class RosTopicView:
    name: str
    kind: str


@dataclass(frozen=True, slots=True)
class WebStatusView:
    provider: str
    model: str
    config_label: str
    locations: int
    overall: str
    overall_label: str
    health_summary: str
    checks: tuple[HealthCheckView, ...]
    ros_available: bool
    ros_error: str | None
    ros_topics: tuple[RosTopicView, ...]
    ros_count: int


def _health_summary(checks: tuple[HealthCheckView, ...]) -> str:
    if not checks:
        return "正在確認系統狀態…"
    failures = sum(item.status == "fail" for item in checks)
    warnings = sum(item.status == "warn" for item in checks)
    if failures:
        return f"有 {failures} 項問題需要處理。"
    if warnings:
        return f"系統可以使用，另有 {warnings} 項提醒。"
    return "系統狀態正常，可以開始使用。"


def build_web_status_view(status: dict[str, Any]) -> WebStatusView:
    """Convert the tolerant server payload into one stable UI projection."""
    doctor = status.get("doctor")
    if not isinstance(doctor, dict):
        doctor = {}
    raw_items = doctor.get("items")
    if not isinstance(raw_items, list):
        raw_items = []

    checks: list[HealthCheckView] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        section = str(raw.get("section") or "unknown")
        check = str(raw.get("check") or "unknown")
        state = normalize_status(raw.get("status"))
        raw_fix = raw.get("fix")
        checks.append(
            HealthCheckView(
                section=section,
                section_label=_SECTION_LABELS.get(section, section.replace("_", " ").title()),
                check=check,
                label=_CHECK_LABELS.get(check, check.replace("_", " ").title()),
                status=state,
                status_label=status_label(state),
                message=str(raw.get("message") or ""),
                fix=str(raw_fix) if raw_fix else None,
            )
        )

    ros = status.get("ros")
    if not isinstance(ros, dict):
        ros = {}
    raw_topics = ros.get("topics")
    if not isinstance(raw_topics, list):
        raw_topics = []
    topics = tuple(
        RosTopicView(
            name=str(topic.get("name") or ""),
            kind=str(topic.get("kind") or "unknown"),
        )
        for topic in raw_topics
        if isinstance(topic, dict) and topic.get("name")
    )
    raw_error = ros.get("error")
    overall = normalize_status(doctor.get("overall"))

    return WebStatusView(
        provider=str(status.get("provider") or "未設定"),
        model=str(status.get("model") or "未設定"),
        config_label="完成" if status.get("config_complete") else "未完成",
        locations=_nonnegative_int(status.get("locations")),
        overall=overall,
        overall_label=status_label(overall),
        health_summary=_health_summary(tuple(checks)),
        checks=tuple(checks),
        ros_available=bool(ros.get("available")),
        ros_error=str(raw_error) if raw_error else None,
        ros_topics=topics,
        ros_count=_nonnegative_int(ros.get("count"), default=len(topics)),
    )
