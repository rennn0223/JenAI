"""WebUI command execution + server-side confirm-action escrow."""

from __future__ import annotations

import html as _html
import json
from pathlib import Path
from typing import Any

from jenai.adapters import ros2_adapter
from jenai.adapters.locations import (
    LocationNotFoundError,
    find_location,
    load_locations_tolerant,
)
from jenai.config.models import AppConfig
from jenai.doctor import run_doctor
from jenai.providers.chat import ProviderChatError, ask_provider, chat_model_name
from jenai.tools import ros2_core
from jenai.tools.drive_core import extract_drive_command
from jenai.tools.route_core import route_execute, route_preview

TWIST = "geometry_msgs/msg/Twist"


def _esc(text: Any) -> str:
    return _html.escape(str(text))


def _p(text: str) -> str:
    return "<p>" + _esc(text).replace("\n", "<br>") + "</p>"


def _result(html: str) -> dict:
    return {"kind": "result", "html": html}


def _error(text: str) -> dict:
    return {"kind": "error", "html": _p(text)}


def _confirm(html: str, action: dict, danger: str) -> dict:
    return {"kind": "confirm", "html": html, "action": action, "danger": danger}


def _isnum(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _load_locations(config: AppConfig, config_path: Path):
    locations, _error = load_locations_tolerant(config.resolved_locations_path(config_path))
    return locations


async def run_web_command(config: AppConfig, config_path: Path, text: str) -> dict:
    """Execute a WebUI command. Read/chat commands return a result; commands that
    actuate the robot return a `confirm` block the client must approve first.
    """
    text = (text or "").strip()
    if not text:
        return _error("Type a command or a plain-language question.")
    try:
        if text.startswith("/"):
            return await _slash(config, config_path, text)
        resp = await ask_provider(config, text)
        return _result(_p(resp.content))
    except ProviderChatError as exc:
        return _error(str(exc))
    except ros2_adapter.Ros2AdapterError as exc:
        return _error(f"ROS2: {exc}")
    except Exception as exc:  # keep the dashboard alive
        return _error(f"Error: {exc}")


async def _slash(config: AppConfig, config_path: Path, text: str) -> dict:
    cmd, _, rest = text.partition(" ")
    rest = rest.strip()
    if cmd == "/help":
        return _result(
            "<p>Try:</p><ul>"
            "<li><code>/ros topics</code>, <code>/ros topic-info /cmd_vel</code>, "
            "<code>/ros schema /cmd_vel</code>, <code>/ros echo /odom</code></li>"
            "<li><code>/drive 前進兩秒</code> · <code>/ros drive /cmd_vel {...} 2</code> · "
            "<code>/ros pub /cmd_vel {...}</code></li>"
            "<li><code>/route from A to B</code> · <code>/loc list</code> · "
            "<code>/doctor</code> · <code>/status</code></li>"
            "<li>Or just ask a question in plain language.</li></ul>"
        )
    if cmd == "/status":
        profile = config.active_profile()
        return _result(
            _p(
                f"Provider: {profile.name if profile else '—'}\n"
                f"Model: {chat_model_name(config)}\n"
                f"Config: {'complete' if config.is_complete() else 'incomplete'}"
            )
        )
    if cmd == "/doctor":
        result = run_doctor(config_path)
        rows = "".join(
            f"<li>{_esc(i.section)} · {_esc(i.check_name)} — "
            f"<b>{_esc(i.status)}</b> {_esc(i.message)}</li>"
            for i in result.items
        )
        return _result(f"<p>Overall: <b>{_esc(result.overall)}</b></p><ul>{rows}</ul>")
    if cmd == "/ros":
        return await _ros(config, rest)
    if cmd == "/drive":
        return await _drive_nl(config, rest)
    if cmd == "/route":
        return await _route(config, config_path, rest)
    if cmd == "/loc":
        return _loc(config, config_path, rest)
    return _error(f"Unknown command: {cmd}. Try /help.")


async def _ros(config: AppConfig, rest: str) -> dict:
    op, _, arg = rest.partition(" ")
    arg = arg.strip()
    if op == "topics":
        out = await ros2_core.ros_topics(config)
        if not out.topics:
            return _result(_p("No topics on the graph."))
        items = "".join(
            f"<li><b>{_esc(t.name)}</b> <span class='dim'>{_esc(t.kind_hint)}</span></li>"
            for t in out.topics
        )
        return _result(f"<ul class='cmd-list'>{items}</ul>")
    if op == "topic-info":
        out = await ros2_core.ros_topic_info(config, arg)
        if not out.message_type:
            return _result(_p(out.summary))
        return _result(
            _p(
                f"{out.name}\n{out.message_type}\n"
                f"{out.publisher_count} publisher(s) · {out.subscriber_count} subscriber(s)"
            )
        )
    if op == "schema":
        out = await ros2_core.ros_schema(config, arg)
        rows = "".join(
            f"<li><b>{_esc(f.field_name)}</b> <span class='dim'>{_esc(f.field_type)}</span> "
            f"— {_esc(f.description)}</li>"
            for f in out.field_summary
        )
        example = _esc(json.dumps(out.example_payload, ensure_ascii=False))
        return _result(
            f"<p><b>{_esc(out.message_type)}</b></p><ul class='cmd-list'>{rows}</ul>"
            f"<p class='dim'>example: <code>{example}</code></p>"
        )
    if op == "echo":
        parts = arg.split()
        topic = parts[0] if parts else ""
        limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        out = await ros2_core.ros_echo(config, topic, limit=limit)
        if not out.messages:
            return _result(_p(out.summary))
        return _result("".join(f"<pre>{_esc(m.get('raw', ''))}</pre>" for m in out.messages))
    if op == "pub":
        topic, _, payload_json = arg.partition(" ")
        if not payload_json.strip():
            return _error("Usage: /ros pub &lt;topic&gt; &lt;json&gt;")
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            return _error(f"Invalid JSON: {exc}")
        validation = await ros2_core.ros_pub_validate(topic, payload)
        if not validation.ok:
            return _error(validation.error.message if validation.error else "Validation failed.")
        return _confirm(
            _p(f"Publish to {topic}\n{json.dumps(payload, ensure_ascii=False)}"),
            {
                "type": "pub",
                "topic": topic,
                "message_type": validation.message_type,
                "payload": payload,
            },
            danger=f"This publishes one message to {topic}.",
        )
    if op == "drive":
        parts = arg.split()
        if len(parts) < 2:
            return _error("Usage: /ros drive &lt;topic&gt; &lt;json&gt; [seconds]")
        duration = 1.0
        if len(parts) >= 3 and _isnum(parts[-1]):
            duration = float(parts[-1])
            payload_json = " ".join(parts[1:-1])
        else:
            payload_json = " ".join(parts[1:])
        topic = parts[0]
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            return _error(f"Invalid JSON: {exc}")
        return _confirm(
            _p(f"Drive {topic} for {duration:g}s\n{json.dumps(payload, ensure_ascii=False)}"),
            {
                "type": "drive",
                "topic": topic,
                "message_type": TWIST,
                "payload": payload,
                "duration": duration,
            },
            danger=f"This drives the robot on {topic} for {duration:g}s.",
        )
    return _error(f"Unknown: /ros {op}")


async def _drive_nl(config: AppConfig, rest: str) -> dict:
    if not rest:
        return _error("Usage: /drive 前進兩秒")
    intent = await extract_drive_command(config, rest)
    if intent is None:
        return _error(f"Couldn't understand '{rest}' as a drive command.")
    return _confirm(
        _p(
            f"Drive · {intent.description}\n"
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
        danger=f"This drives the robot: {intent.description}.",
    )


async def _route(config: AppConfig, config_path: Path, rest: str) -> dict:
    if not rest:
        return _error("Usage: /route from A to B")
    locations = _load_locations(config, config_path)
    out = await route_preview(config, locations, rest)
    if not out.outgoing_action:
        return _result(_p(out.route_preview))
    return _confirm(
        _p(out.route_preview),
        {"type": "route", "outgoing_action": out.outgoing_action},
        danger="This sends a navigation goal.",
    )


def _loc(config: AppConfig, config_path: Path, rest: str) -> dict:
    op, _, arg = rest.partition(" ")
    locations = _load_locations(config, config_path)
    if op == "list":
        if not locations:
            return _result(_p("No locations configured."))
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
            return _result(_p(f"'{arg}' not found." + (f" Did you mean: {names}?" if names else "")))
        return _result(
            _p(
                f"{loc.name}\naliases: {', '.join(loc.aliases) or '(none)'}\n"
                f"pose: x={loc.pose.x}, y={loc.pose.y}, yaw={loc.pose.yaw}"
            )
        )
    return _error("Usage: /loc list | /loc show &lt;name&gt;")


async def run_web_confirm(config: AppConfig, action: dict) -> dict:
    """Execute a previously-previewed actuation after the user confirmed it."""
    try:
        kind = action.get("type")
        if kind == "drive":
            out = await ros2_core.ros_drive(
                action["topic"],
                action["message_type"],
                action["payload"],
                duration_s=float(action.get("duration", 1.0)),
                max_linear=config.vehicle.max_linear,
                max_angular=config.vehicle.max_angular,
            )
            return _result(_p(out.result_message or "done"))
        if kind == "pub":
            out = await ros2_core.ros_pub_execute(
                action["topic"],
                action["message_type"],
                action["payload"],
                max_linear=config.vehicle.max_linear,
                max_angular=config.vehicle.max_angular,
            )
            return _result(_p(out.result_message or "done"))
        if kind == "route":
            out = await route_execute(config, action["outgoing_action"])
            return _result(_p(out.route_preview))
        return _error("Unknown action.")
    except Exception as exc:
        return _error(f"Error: {exc}")
