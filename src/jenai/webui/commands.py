"""WebUI command execution + server-side confirm-action escrow."""

from __future__ import annotations

import html as _html
import json
import logging
from pathlib import Path
from typing import Any

from jenai.adapters import ros2_adapter
from jenai.adapters.locations import (
    LocationNotFoundError,
    find_location,
    load_locations_tolerant,
)
from jenai.capabilities import has_registered_capability
from jenai.config.models import AppConfig
from jenai.doctor import run_doctor
from jenai.providers.chat import ProviderChatError, ask_provider, chat_model_name
from jenai.schemas import Location, RunStatus, TaskOutcome
from jenai.state.audit import AuditStore
from jenai.task_results import navigation_output_result, navigation_receipt_text
from jenai.tools import ros2_core
from jenai.tools.drive_core import extract_drive_command
from jenai.tools.navigation_gateway import execute_navigation
from jenai.tools.route_core import route_preview

logger = logging.getLogger(__name__)

TWIST = "geometry_msgs/msg/Twist"
WebAction = dict[str, Any]
WebResponse = dict[str, Any]


def _parse_payload_object(payload_json: str) -> dict[str, Any]:
    """Decode a ROS payload without allowing scalar or array-shaped messages."""

    payload = json.loads(payload_json)
    if not isinstance(payload, dict):
        raise ValueError("payload 必須是 JSON object")
    return payload


# Console palette data — MUST mirror what _slash()/_ros()/_loc() actually
# handle. Extend both together (the palette is the promise, _slash is the
# implementation; a phantom row here is the /ros state bug all over again).
WEB_SLASH_COMMANDS: list[dict[str, str]] = [
    {"name": "/help", "usage": "/help", "desc": "指令總覽"},
    {"name": "/status", "usage": "/status", "desc": "provider / model / config 狀態"},
    {"name": "/doctor", "usage": "/doctor", "desc": "環境健檢(逐項 pass/warn/fail)"},
    {"name": "/ros topics", "usage": "/ros topics", "desc": "列出 ROS graph topics"},
    {"name": "/ros topic-info", "usage": "/ros topic-info <topic>", "desc": "type 與 pub/sub 數"},
    {"name": "/ros schema", "usage": "/ros schema <topic>", "desc": "訊息欄位摘要 + 範例 payload"},
    {"name": "/ros echo", "usage": "/ros echo <topic> [count]", "desc": "擷取 N 筆訊息快照"},
    {"name": "/ros pub", "usage": "/ros pub <topic> <json>", "desc": "發布一筆訊息(需確認)"},
    {"name": "/ros drive", "usage": "/ros drive <topic> <json> [秒]", "desc": "定頻駕駛(需確認)"},
    {"name": "/drive", "usage": "/drive 前進兩秒", "desc": "自然語言駕駛(需確認)"},
    {"name": "/route", "usage": "/route from A to B", "desc": "解析並送導航(需確認)"},
    {"name": "/loc list", "usage": "/loc list", "desc": "列出已知地點"},
    {"name": "/loc show", "usage": "/loc show <name>", "desc": "地點詳細資料"},
]


def _esc(text: Any) -> str:
    return _html.escape(str(text))


def _p(text: str) -> str:
    return "<p>" + _esc(text).replace("\n", "<br>") + "</p>"


def _result(
    html: str,
    *,
    run_status: RunStatus = RunStatus.COMPLETED,
    outcome: TaskOutcome = TaskOutcome.SUCCEEDED,
) -> WebResponse:
    return {
        "kind": "result",
        "html": html,
        "run_status": run_status.value,
        "outcome": outcome.value,
    }


def _error(
    text: str,
    *,
    run_status: RunStatus = RunStatus.FAILED,
    outcome: TaskOutcome = TaskOutcome.FAILED,
) -> WebResponse:
    return {
        "kind": "error",
        "html": _p(text),
        "run_status": run_status.value,
        "outcome": outcome.value,
    }


def _confirm(html: str, action: WebAction, danger: str) -> WebResponse:
    return {"kind": "confirm", "html": html, "action": action, "danger": danger}


def _isnum(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _load_locations(config: AppConfig, config_path: Path) -> list[Location]:
    locations, _error = load_locations_tolerant(config.resolved_locations_path(config_path))
    return locations


async def run_web_command(config: AppConfig, config_path: Path, text: str) -> WebResponse:
    """Execute a WebUI command. Read/chat commands return a result; commands that
    actuate the robot return a `confirm` block the client must approve first.
    """
    text = (text or "").strip()
    if not text:
        return _error("請輸入指令或一般語言問題。")
    try:
        if text.startswith("/"):
            return await _slash(config, config_path, text)
        resp = await ask_provider(config, text)
        return _result(_p(resp.content))
    except ProviderChatError as exc:
        return _error(str(exc))
    except ros2_adapter.Ros2AdapterError as exc:
        return _error(f"ROS 2 錯誤：{exc}")
    except Exception as exc:  # keep the dashboard alive
        return _error(f"錯誤：{exc}")


async def _slash(config: AppConfig, config_path: Path, text: str) -> WebResponse:
    cmd, _, rest = text.partition(" ")
    rest = rest.strip()
    if cmd == "/help":
        return _result(
            "<p>可用指令：</p><ul>"
            "<li><code>/ros topics</code>、<code>/ros topic-info /cmd_vel</code>、"
            "<code>/ros schema /cmd_vel</code>、<code>/ros echo /odom</code></li>"
            "<li><code>/drive 前進兩秒</code> · <code>/ros drive /cmd_vel {...} 2</code> · "
            "<code>/ros pub /cmd_vel {...}</code></li>"
            "<li><code>/route from A to B</code> · <code>/loc list</code> · "
            "<code>/doctor</code> · <code>/status</code></li>"
            "<li>也可以直接用一般語言描述任務。</li></ul>"
        )
    if cmd == "/status":
        profile = config.active_profile()
        return _result(
            _p(
                f"供應商：{profile.name if profile else '—'}\n"
                f"模型：{chat_model_name(config)}\n"
                f"設定：{'完成' if config.is_complete() else '未完成'}"
            )
        )
    if cmd == "/doctor":
        result = run_doctor(config_path)
        rows = "".join(
            f"<li>{_esc(i.section)} · {_esc(i.check_name)} — "
            f"<b>{_esc(i.status)}</b> {_esc(i.message)}</li>"
            for i in result.items
        )
        return _result(f"<p>整體狀態：<b>{_esc(result.overall)}</b></p><ul>{rows}</ul>")
    if cmd == "/ros":
        return await _ros(config, rest)
    if cmd == "/drive":
        return await _drive_nl(config, rest)
    if cmd == "/route":
        return await _route(config, config_path, rest)
    if cmd == "/loc":
        return _loc(config, config_path, rest)
    return _error(f"不支援的指令：{cmd}。請使用 /help 查看可用指令。")


async def _ros(config: AppConfig, rest: str) -> WebResponse:
    op, _, arg = rest.partition(" ")
    handlers = {
        "topics": _ros_topics,
        "topic-info": _ros_topic_info,
        "schema": _ros_schema,
        "echo": _ros_echo,
        "pub": _ros_pub,
        "drive": _ros_drive,
    }
    handler = handlers.get(op)
    if handler is None:
        return _error(f"不支援的 ROS 指令：/ros {op}")
    return await handler(config, arg.strip())


async def _ros_topics(config: AppConfig, _arg: str) -> WebResponse:
    topics_out = await ros2_core.ros_topics(config)
    if not topics_out.topics:
        return _result(_p("ROS graph 目前沒有 topic。"))
    items = "".join(
        f"<li><b>{_esc(topic.name)}</b> <span class='dim'>{_esc(topic.kind_hint)}</span></li>"
        for topic in topics_out.topics
    )
    return _result(f"<ul class='cmd-list'>{items}</ul>")


async def _ros_topic_info(config: AppConfig, arg: str) -> WebResponse:
    info_out = await ros2_core.ros_topic_info(config, arg)
    if not info_out.message_type:
        return _result(_p(info_out.summary))
    return _result(
        _p(
            f"{info_out.name}\n{info_out.message_type}\n"
            f"{info_out.publisher_count} 個 publisher · "
            f"{info_out.subscriber_count} 個 subscriber"
        )
    )


async def _ros_schema(config: AppConfig, arg: str) -> WebResponse:
    schema_out = await ros2_core.ros_schema(config, arg)
    rows = "".join(
        f"<li><b>{_esc(field.field_name)}</b> <span class='dim'>{_esc(field.field_type)}</span> "
        f"— {_esc(field.description)}</li>"
        for field in schema_out.field_summary
    )
    example = _esc(json.dumps(schema_out.example_payload, ensure_ascii=False))
    return _result(
        f"<p><b>{_esc(schema_out.message_type)}</b></p><ul class='cmd-list'>{rows}</ul>"
        f"<p class='dim'>範例：<code>{example}</code></p>"
    )


async def _ros_echo(config: AppConfig, arg: str) -> WebResponse:
    parts = arg.split()
    topic = parts[0] if parts else ""
    limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    echo_out = await ros2_core.ros_echo(config, topic, limit=limit)
    if not echo_out.messages:
        return _result(_p(echo_out.summary))
    return _result("".join(f"<pre>{_esc(m.get('raw', ''))}</pre>" for m in echo_out.messages))


async def _ros_pub(config: AppConfig, arg: str) -> WebResponse:
    topic, _, payload_json = arg.partition(" ")
    if not payload_json.strip():
        return _error("用法：/ros pub <topic> <json>")
    try:
        payload = _parse_payload_object(payload_json)
    except ValueError as exc:
        return _error(f"Payload 無效：{exc}")
    validation = await ros2_core.ros_pub_validate(topic, payload)
    if not validation.ok:
        return _error(validation.error.message if validation.error else "驗證失敗。")
    is_motion = topic.strip("/") == config.vehicle.cmd_vel_topic.strip(
        "/"
    ) or ros2_core.is_velocity_message_type(validation.message_type)
    if is_motion:
        target = "實體機器人" if config.deployment_mode == "physical" else "模擬器中的機器人"
        danger = f"這會發布速度指令至 {topic}，可能使{target}立即移動。"
    else:
        danger = f"這會發布一筆訊息至 {topic}。"
    return _confirm(
        _p(f"發布至 {topic}\n{json.dumps(payload, ensure_ascii=False)}"),
        {
            "type": "pub",
            "topic": topic,
            "message_type": validation.message_type,
            "payload": payload,
        },
        danger=danger,
    )


async def _ros_drive(config: AppConfig, arg: str) -> WebResponse:
    parts = arg.split()
    if len(parts) < 2:
        return _error("用法：/ros drive <topic> <json> [秒]")
    duration = 1.0
    if len(parts) >= 3 and _isnum(parts[-1]):
        duration = float(parts[-1])
        payload_json = " ".join(parts[1:-1])
    else:
        payload_json = " ".join(parts[1:])
    topic = parts[0]
    try:
        payload = _parse_payload_object(payload_json)
    except ValueError as exc:
        return _error(f"Payload 無效：{exc}")
    return _confirm(
        _p(f"在 {topic} 駕駛 {duration:g} 秒\n{json.dumps(payload, ensure_ascii=False)}"),
        {
            "type": "drive",
            "topic": topic,
            "message_type": TWIST,
            "payload": payload,
            "duration": duration,
        },
        danger=f"這會透過 {topic} 驅動機器人 {duration:g} 秒。",
    )


async def _drive_nl(config: AppConfig, rest: str) -> WebResponse:
    if not rest:
        return _error("用法：/drive 前進兩秒")
    intent = await extract_drive_command(config, rest)
    if intent is None:
        return _error(f"無法將「{rest}」理解為駕駛指令。")
    return _confirm(
        _p(
            f"駕駛 · {intent.description}\n"
            f"linear.x={intent.linear_x:g}, angular.z={intent.angular_z:g}, "
            f"{intent.duration_s:g}s"
        ),
        {
            "type": "drive",
            "topic": config.vehicle.cmd_vel_topic,
            "message_type": TWIST,
            "payload": intent.to_payload(),
            "duration": intent.duration_s,
        },
        danger=f"這會驅動機器人：{intent.description}。",
    )


async def _route(config: AppConfig, config_path: Path, rest: str) -> WebResponse:
    if not rest:
        return _error("用法：/route from A to B")
    if not has_registered_capability(config, "navigate"):
        return _error("此機器人設定未註冊導航能力。")

    locations = _load_locations(config, config_path)
    out = await route_preview(config, locations, rest)
    if not out.outgoing_action:
        return _error(
            out.route_preview,
            run_status=RunStatus.BLOCKED,
            outcome=TaskOutcome.BLOCKED,
        )
    return _confirm(
        _p(out.route_preview),
        {"type": "route", "outgoing_action": out.outgoing_action},
        danger="這會送出導航目標。",
    )


def _loc(config: AppConfig, config_path: Path, rest: str) -> WebResponse:
    op, _, arg = rest.partition(" ")
    locations = _load_locations(config, config_path)
    if op == "list":
        if not locations:
            return _result(_p("尚未設定地點。"))
        items = "".join(
            f"<li><b>{_esc(loc.name)}</b> <span class='dim'>"
            f"{_esc(', '.join(loc.aliases))}</span></li>"
            for loc in locations
        )
        return _result(f"<ul class='cmd-list'>{items}</ul>")
    if op == "show":
        try:
            loc = find_location(locations, arg.strip())
        except LocationNotFoundError as exc:
            names = ", ".join(c.name for c in exc.candidates)
            return _result(_p(f"找不到「{arg}」。" + (f"你是否要找：{names}？" if names else "")))
        return _result(
            _p(
                f"{loc.name}\n別名：{', '.join(loc.aliases) or '（無）'}\n"
                f"pose: x={loc.pose.x}, y={loc.pose.y}, yaw={loc.pose.yaw}"
            )
        )
    return _error("用法：/loc list | /loc show <name>")


class _WebActionExecutor:
    """Execute one confirmed action through a shared audit/error boundary."""

    def __init__(
        self,
        config: AppConfig,
        config_path: Path | None,
        audit_store: AuditStore | None,
    ) -> None:
        self.config = config
        self.config_path = config_path
        self.audit_store = audit_store

    def _audit(
        self,
        action_type: str,
        event_type: str,
        status: str,
        *,
        outcome: str | None = None,
    ) -> None:
        if self.audit_store is None:
            return
        details = {"source": "webui", "action_type": action_type}
        if outcome is not None:
            details["outcome"] = outcome
        try:
            self.audit_store.record(
                event_type,
                status=status,
                details=details,
            )
        except Exception:
            logger.warning("WebUI action audit failed", exc_info=True)

    async def execute(self, action: WebAction) -> WebResponse:
        kind = str(action.get("type") or "")
        action_type = kind or "unknown"
        self._audit(action_type, "approval_resolved", "approved")
        handlers = {
            "drive": self._drive,
            "pub": self._publish,
            "route": self._route,
        }
        handler = handlers.get(kind)
        if handler is None:
            self._audit(action_type, "tool_updated", "failed")
            return _error("不支援的動作。")
        try:
            return await handler(action)
        except Exception as exc:
            self._audit(action_type, "tool_updated", "failed")
            return _error(f"錯誤：{exc}")

    def _ros_action_response(
        self, action_type: str, execution_status: str, message: str
    ) -> WebResponse:
        self._audit(action_type, "tool_updated", execution_status)
        if execution_status.strip().lower() == "succeeded":
            return _result(_p(message or "動作已完成。"))
        return _error(message or "動作未完成。")

    async def _drive(self, action: WebAction) -> WebResponse:
        drive_out = await ros2_core.ros_drive(
            action["topic"],
            action["message_type"],
            action["payload"],
            duration_s=float(action.get("duration", 1.0)),
            max_linear=self.config.vehicle.max_linear,
            max_angular=self.config.vehicle.max_angular,
        )
        return self._ros_action_response(
            "drive", drive_out.execution_status, drive_out.result_message
        )

    async def _publish(self, action: WebAction) -> WebResponse:
        pub_out = await ros2_core.ros_pub_execute(
            action["topic"],
            action["message_type"],
            action["payload"],
            max_linear=self.config.vehicle.max_linear,
            max_angular=self.config.vehicle.max_angular,
        )
        return self._ros_action_response("pub", pub_out.execution_status, pub_out.result_message)

    async def _route(self, action: WebAction) -> WebResponse:
        if not has_registered_capability(self.config, "navigate"):
            self._audit("route", "tool_updated", "blocked")
            return _error(
                "此機器人設定未註冊導航能力。",
                run_status=RunStatus.BLOCKED,
                outcome=TaskOutcome.BLOCKED,
            )
        route_out = await execute_navigation(
            self.config,
            action["outgoing_action"],
            config_path=self.config_path,
            audit_store=self.audit_store,
        )
        task_result = navigation_output_result(route_out)
        self._audit(
            "route",
            "tool_updated",
            task_result.run_status.value,
            outcome=task_result.outcome.value,
        )
        return _result(
            _p(navigation_receipt_text(route_out)),
            run_status=task_result.run_status,
            outcome=task_result.outcome,
        )


async def run_web_confirm(
    config: AppConfig,
    action: WebAction,
    *,
    config_path: Path | None = None,
) -> WebResponse:
    """Execute a previously-previewed actuation after the user confirmed it."""
    audit_store = (
        AuditStore.best_effort(config_path.parent / "audit.sqlite3")
        if config_path is not None
        else None
    )
    executor = _WebActionExecutor(config, config_path, audit_store)
    return await executor.execute(action)
