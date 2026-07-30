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
_RUN_STATUS_LABELS = {
    "idle": "等待中",
    "understanding": "理解需求",
    "planning": "規劃中",
    "running": "執行中",
    "awaiting_approval": "等待批准",
    "blocked": "已阻擋",
    "interrupted": "已中斷",
    "completed": "已完成",
    "failed": "失敗",
}

_TOOL_STATUS_LABELS = {
    "queued": "排隊中",
    "running": "執行中",
    "awaiting_approval": "等待批准",
    "succeeded": "已完成",
    "failed": "失敗",
    "rejected": "已拒絕",
}

_APPROVAL_STATUS_LABELS = {
    "pending": "待批准",
    "approved": "已批准",
    "rejected": "已拒絕",
}

_OUTCOME_LABELS = {
    "succeeded": "成功",
    "arrived_unverified": "已抵達，效果未驗證",
    "partial": "部分完成",
    "endpoint_mismatch": "終點不符",
    "blocked": "已阻擋",
    "unavailable": "不可用",
    "failed": "失敗",
    "cancelled": "已取消",
}

_TERMINAL_RUN_STATUSES = frozenset({"blocked", "interrupted", "completed", "failed"})

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
class WebApprovalView:
    approval_id: str
    title: str
    summary: str
    tool_name: str
    risk_level: str
    status: str
    status_label: str
    created_at: str


@dataclass(frozen=True, slots=True)
class WebToolCallView:
    tool_call_id: str
    tool_name: str
    input_summary: str
    status: str
    status_label: str
    output_summary: str | None
    started_at: str | None
    ended_at: str | None


@dataclass(frozen=True, slots=True)
class WebRunView:
    run_id: str
    status: str
    status_label: str
    outcome: str | None
    outcome_label: str | None
    summary: str
    final_output: str | None
    started_at: str
    finished_at: str | None
    approvals: tuple[WebApprovalView, ...]
    tool_calls: tuple[WebToolCallView, ...]

    @property
    def pending_approvals(self) -> tuple[WebApprovalView, ...]:
        return tuple(item for item in self.approvals if item.status == "pending")


def _status_token(value: object, labels: dict[str, str], default: str = "unknown") -> str:
    token = str(value or default).lower()
    return token if token in labels else default


def _optional_text(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


def _records(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


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
    runs: tuple[WebRunView, ...]

    @property
    def current_run(self) -> WebRunView | None:
        for run in reversed(self.runs):
            if run.status not in _TERMINAL_RUN_STATUSES:
                return run
        return self.runs[-1] if self.runs else None

    @property
    def pending_approvals(self) -> tuple[WebApprovalView, ...]:
        return tuple(item for run in reversed(self.runs) for item in run.pending_approvals)


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


def _build_runs(status: dict[str, Any]) -> tuple[WebRunView, ...]:
    runs: list[WebRunView] = []
    for raw in _records(status.get("transcript")):
        approvals: list[WebApprovalView] = []
        for item in _records(raw.get("approvals")):
            state = _status_token(item.get("status"), _APPROVAL_STATUS_LABELS)
            approvals.append(
                WebApprovalView(
                    approval_id=str(item.get("approval_id") or ""),
                    title=str(item.get("title") or "未命名批准"),
                    summary=str(item.get("summary") or ""),
                    tool_name=str(item.get("tool_name") or "未知工具"),
                    risk_level=str(item.get("risk_level") or "unknown"),
                    status=state,
                    status_label=_APPROVAL_STATUS_LABELS.get(state, "未知"),
                    created_at=str(item.get("created_at") or ""),
                )
            )

        tool_calls: list[WebToolCallView] = []
        for item in _records(raw.get("tool_calls")):
            state = _status_token(item.get("status"), _TOOL_STATUS_LABELS)
            tool_calls.append(
                WebToolCallView(
                    tool_call_id=str(item.get("tool_call_id") or ""),
                    tool_name=str(item.get("tool_name") or "未知工具"),
                    input_summary=str(item.get("input_summary") or ""),
                    status=state,
                    status_label=_TOOL_STATUS_LABELS.get(state, "未知"),
                    output_summary=_optional_text(item.get("output_summary")),
                    started_at=_optional_text(item.get("started_at")),
                    ended_at=_optional_text(item.get("ended_at")),
                )
            )

        run_state = _status_token(raw.get("status"), _RUN_STATUS_LABELS)
        raw_outcome = _optional_text(raw.get("outcome"))
        outcome = raw_outcome.lower() if raw_outcome else None
        runs.append(
            WebRunView(
                run_id=str(raw.get("run_id") or ""),
                status=run_state,
                status_label=_RUN_STATUS_LABELS.get(run_state, "未知"),
                outcome=outcome,
                outcome_label=_OUTCOME_LABELS.get(outcome) if outcome else None,
                summary=str(raw.get("summary") or "未命名任務"),
                final_output=_optional_text(raw.get("final_output")),
                started_at=str(raw.get("started_at") or ""),
                finished_at=_optional_text(raw.get("finished_at")),
                approvals=tuple(approvals),
                tool_calls=tuple(tool_calls),
            )
        )
    return tuple(runs)


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
        runs=_build_runs(status),
    )
