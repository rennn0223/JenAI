from __future__ import annotations

import asyncio
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from jenai.adapters import ros2_adapter
from jenai.config.models import AppConfig
from jenai.doctor import run_doctor
from jenai.providers.chat import chat_model_name
from jenai.state.runs import RunStore
from jenai.tools.ros2_core import _kind_hint
from jenai.webui.commands import run_web_command, run_web_confirm
from jenai.webui.render import render_dashboard_html, render_main


class _PendingConfirms:
    """Server-side store binding a previewed robot action to a one-time token.

    The browser only ever receives an opaque ``confirm_id``; the actual action
    dict stays here and is executed exactly once, when that id is confirmed. So
    (a) a client cannot fabricate or tamper with what it confirms — it can only
    release an action the server already previewed and validated — and (b) a
    blind POST to ``/api/confirm`` without a valid, unused id does nothing. This
    turns the confirm step into a real server-side gate rather than a cosmetic
    button, without adding auth to what is still a localhost tool.
    """

    def __init__(self, max_entries: int = 32) -> None:
        self._items: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._max = max_entries

    def put(self, action: dict) -> str:
        token = secrets.token_urlsafe(16)
        with self._lock:
            self._items[token] = action
            while len(self._items) > self._max:  # bound memory; evict oldest
                self._items.pop(next(iter(self._items)), None)
        return token

    def pop(self, token: str) -> dict | None:
        if not token:
            return None
        with self._lock:
            return self._items.pop(token, None)


def _ros_snapshot() -> dict[str, Any]:
    """Best-effort live ROS2 graph snapshot for the dashboard."""
    if not ros2_adapter.is_available():
        return {"available": False, "topics": [], "count": 0, "error": None}
    try:
        names = ros2_adapter.list_topics()
    except ros2_adapter.Ros2AdapterError as exc:
        return {"available": True, "topics": [], "count": 0, "error": str(exc)}
    topics = [{"name": name, "kind": _kind_hint(name)} for name in names]
    return {"available": True, "topics": topics, "count": len(topics), "error": None}


def _locations_count(config: AppConfig, config_path: Path) -> int:
    try:
        from jenai.adapters.locations import ensure_locations_file, load_locations

        path = config.resolved_locations_path(config_path)
        if path is None:
            return 0
        ensure_locations_file(path)
        return len(load_locations(path))
    except Exception:
        return 0


def build_status_payload(
    config: AppConfig,
    config_path: Path,
    *,
    run_store: RunStore | None = None,
) -> dict[str, Any]:
    """Assemble the WebUI snapshot: provider/model, full doctor report and the
    live ROS2 graph. The transcript is drawn from a RunStore when one is passed
    (a stand-alone `jenai web` process starts with none).
    """
    doctor = run_doctor(config_path)
    profile = config.active_profile()
    runs = list(run_store.list_runs()) if run_store is not None else []
    transcript = [
        {
            "run_id": run.run_id,
            "status": str(run.status),
            "summary": run.user_input,
            "final_output": run.final_output,
        }
        for run in runs
    ]
    return {
        "provider": profile.name if profile else None,
        "provider_kind": profile.provider if profile else None,
        "model": chat_model_name(config),
        "config_complete": config.is_complete(),
        "locations": _locations_count(config, config_path),
        "doctor_overall": str(doctor.overall),
        "doctor": {
            "overall": str(doctor.overall),
            "items": [
                {
                    "section": item.section,
                    "check": item.check_name,
                    "status": str(item.status),
                    "message": item.message,
                    "fix": item.fix_suggestion,
                }
                for item in doctor.items
            ],
        },
        "ros": _ros_snapshot(),
        "run_count": len(transcript),
        "transcript": transcript,
    }


# -- HTML rendering -----------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    config: AppConfig
    config_path: Path
    run_store: RunStore | None = None
    pending: _PendingConfirms | None = None

    def log_message(self, *args: Any) -> None:  # silence default stderr logging
        pass

    def _status(self) -> dict[str, Any]:
        return build_status_payload(self.config, self.config_path, run_store=self.run_store)

    def _send(self, body: str, content_type: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        path = self.path.rstrip("/") or "/"
        if path == "/api/status":
            self._send(
                json.dumps(self._status(), ensure_ascii=False),
                "application/json; charset=utf-8",
            )
        elif path == "/fragment":
            self._send(render_main(self._status()), "text/html; charset=utf-8")
        else:
            self._send(render_dashboard_html(self._status()), "text/html; charset=utf-8")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            data = {}
        return data if isinstance(data, dict) else {}

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        path = self.path.rstrip("/") or "/"
        body = self._read_json()
        if path == "/api/command":
            result = asyncio.run(run_web_command(self.config, self.config_path, body.get("text", "")))
            # A previewed actuation is held server-side under a one-time id; the
            # browser never gets (or gets to alter) the raw action it confirms.
            if result.get("kind") == "confirm" and self.pending is not None:
                result["confirm_id"] = self.pending.put(result.pop("action", {}))
        elif path == "/api/confirm":
            action = self.pending.pop(body.get("confirm_id", "")) if self.pending else None
            if action is None:
                result = {
                    "kind": "error",
                    "html": "<p>This confirmation expired or was already used. Re-run the command.</p>",
                }
            else:
                result = asyncio.run(run_web_confirm(self.config, action))
        else:
            result = {"kind": "error", "html": "<p>Unknown endpoint.</p>"}
        self._send(json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")


def make_server(
    config: AppConfig,
    config_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8760,
    run_store: RunStore | None = None,
) -> ThreadingHTTPServer:
    handler = type(
        "JenAIWebHandler",
        (_Handler,),
        {
            "config": config,
            "config_path": config_path,
            "run_store": run_store,
            "pending": _PendingConfirms(),
        },
    )
    return ThreadingHTTPServer((host, port), handler)


def serve(
    config: AppConfig,
    config_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8760,
) -> None:  # pragma: no cover - blocking network loop
    server = make_server(config, config_path, host=host, port=port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
