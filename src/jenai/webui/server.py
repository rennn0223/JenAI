from __future__ import annotations

import html
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from jenai.adapters import ros2_adapter
from jenai.config.models import AppConfig
from jenai.doctor import run_doctor
from jenai.providers.chat import chat_model_name
from jenai.state.runs import RunStore
from jenai.tools.ros2_core import _kind_hint


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

_KIND_DOT = {
    "control": "var(--accent)",
    "sensor": "var(--teal)",
    "debug": "var(--gold)",
    "unknown": "var(--muted)",
}


def _status_class(status: str) -> str:
    return {"pass": "ok", "warn": "warn", "fail": "bad"}.get(str(status).lower(), "muted")


def _pill(status: str) -> str:
    return f'<span class="pill p-{_status_class(status)}">{html.escape(str(status))}</span>'


def render_main(status: dict[str, Any]) -> str:
    """Render the dynamic dashboard body (also served at /fragment for refresh)."""
    stats = [
        ("Provider", f"{status.get('provider') or '—'}"),
        ("Model", status.get("model") or "—"),
        ("Config", "complete" if status.get("config_complete") else "incomplete"),
        ("Locations", str(status.get("locations", 0))),
    ]
    stats_html = "".join(
        f'<div class="stat"><span class="stat-k">{html.escape(k)}</span>'
        f'<span class="stat-v">{html.escape(str(v))}</span></div>'
        for k, v in stats
    )

    # Defaults keep the renderer robust against a partial status dict (e.g. the
    # doctor WebUI smoke check renders with a minimal payload).
    doctor = status.get("doctor") or {"overall": "unknown", "items": []}
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for item in doctor["items"]:
        section = item["section"]
        if section not in groups:
            groups[section] = []
            order.append(section)
        groups[section].append(item)

    check_rows: list[str] = []
    for section in order:
        check_rows.append(f'<div class="group">{html.escape(section)}</div>')
        for item in groups[section]:
            fix = (
                f'<div class="fix">↳ {html.escape(item["fix"])}</div>' if item.get("fix") else ""
            )
            check_rows.append(
                '<div class="check">'
                '<div class="check-main">'
                f'<span class="check-name">{html.escape(item["check"])}</span>'
                f'<span class="check-msg">{html.escape(item["message"])}</span>{fix}'
                "</div>"
                f'{_pill(item["status"])}'
                "</div>"
            )
    doctor_html = "".join(check_rows)

    ros = status.get("ros") or {"available": False, "topics": [], "count": 0, "error": None}
    if not ros["available"]:
        ros_html = (
            '<div class="empty">ROS2 not detected on PATH. Launch with '
            '<span class="mono">jenai</span> so it sources ROS2 Jazzy first.</div>'
        )
    elif ros.get("error"):
        ros_html = f'<div class="empty">ROS2 error: {html.escape(ros["error"])}</div>'
    elif not ros["topics"]:
        ros_html = '<div class="empty">No topics on the graph yet.</div>'
    else:
        chips = "".join(
            '<div class="chip">'
            f'<span class="k-dot" style="background:{_KIND_DOT.get(t["kind"], "var(--muted)")}">'
            "</span>"
            f'<span class="chip-name">{html.escape(t["name"])}</span>'
            f'<span class="chip-kind">{html.escape(t["kind"])}</span>'
            "</div>"
            for t in ros["topics"]
        )
        ros_html = f'<div class="chips">{chips}</div>'

    updated = datetime.now().strftime("%H:%M:%S")
    return (
        f'<div class="stats">{stats_html}</div>'
        '<section class="card">'
        '<div class="card-head"><h2>Environment</h2>'
        f'<div class="head-right">overall {_pill(doctor["overall"])}</div></div>'
        f'<div class="checks">{doctor_html}</div>'
        "</section>"
        '<section class="card">'
        '<div class="card-head"><h2>ROS2 Graph</h2>'
        f'<div class="head-right"><span class="count">{ros["count"]}</span> topics</div></div>'
        f"{ros_html}"
        "</section>"
        f'<div class="updated">updated {updated} · auto-refresh 5s</div>'
    )


# Static shell (head + hero + footer + refresh script). Kept as a plain string
# so the literal CSS/JS braces don't collide with f-string formatting; the
# dynamic body is spliced in at __MAIN__.
_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JenAI — Console</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Instrument+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --paper:#f3f0e8; --card:#fffdf8; --ink:#2a2622; --ink-soft:#57524b; --muted:#8d887d;
  --line:#e6e0d3; --accent:#c05f3b; --teal:#3c7a76; --gold:#a9821f;
  --ok:#3f7d5b; --ok-bg:#e7efe8; --warn:#9c7420; --warn-bg:#f3ecd6;
  --bad:#b23f2e; --bad-bg:#f4e1db; --muted-bg:#ece7db;
}
*{box-sizing:border-box}
html,body{margin:0}
body{
  background:var(--paper);
  background-image:
    radial-gradient(1100px 560px at 82% -12%, rgba(192,95,59,.07), transparent 60%),
    radial-gradient(820px 480px at -12% 8%, rgba(60,122,118,.055), transparent 55%);
  color:var(--ink);
  font-family:'Instrument Sans',ui-sans-serif,-apple-system,'Segoe UI',sans-serif;
  font-size:15px; line-height:1.55; -webkit-font-smoothing:antialiased;
  min-height:100vh;
}
.topbar{height:3px;background:linear-gradient(90deg,var(--accent),#d98a5f 55%,var(--gold))}
.hero{
  max-width:960px; margin:0 auto; padding:40px 32px 18px;
  display:flex; align-items:flex-end; justify-content:space-between; gap:24px;
}
.brand{display:flex; align-items:center; gap:16px}
.logo{
  font-family:'Fraunces',Georgia,serif; font-size:34px; line-height:1;
  color:var(--accent); transform:translateY(2px);
}
.hero h1{
  font-family:'Fraunces','Iowan Old Style',Georgia,serif;
  font-weight:600; font-size:34px; letter-spacing:-.01em; margin:0;
}
.tagline{margin:2px 0 0; color:var(--muted); font-size:13.5px; letter-spacing:.02em}
.live{display:flex; align-items:center; gap:8px; color:var(--muted); font-size:12.5px;
  text-transform:uppercase; letter-spacing:.08em}
.live .dot{width:8px;height:8px;border-radius:50%;background:var(--accent);
  box-shadow:0 0 0 0 rgba(192,95,59,.5); animation:pulse 2.4s ease-out infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(192,95,59,.45)}70%{box-shadow:0 0 0 9px rgba(192,95,59,0)}100%{box-shadow:0 0 0 0 rgba(192,95,59,0)}}
main{max-width:960px; margin:0 auto; padding:8px 32px 24px; transition:opacity .28s ease}
.stats{display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin:10px 0 22px}
.stat{background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px 16px}
.stat-k{display:block; color:var(--muted); font-size:11.5px; text-transform:uppercase; letter-spacing:.07em}
.stat-v{display:block; margin-top:5px; font-size:16px; font-weight:600; overflow-wrap:anywhere}
.card{
  background:var(--card); border:1px solid var(--line); border-radius:18px;
  padding:22px 24px; margin-bottom:20px;
  box-shadow:0 1px 2px rgba(42,38,34,.04), 0 18px 40px -26px rgba(42,38,34,.22);
  animation:rise .5s cubic-bezier(.2,.7,.2,1) both;
}
.card:nth-child(2){animation-delay:.05s}
.card:nth-child(3){animation-delay:.12s}
.no-anim .card{animation:none}
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.card-head{display:flex; align-items:baseline; justify-content:space-between; margin-bottom:14px}
.card-head h2{font-family:'Fraunces',Georgia,serif; font-weight:600; font-size:21px; margin:0}
.head-right{color:var(--muted); font-size:13px}
.count{font-weight:700; color:var(--ink); font-size:15px}
.group{margin:16px 0 6px; color:var(--muted); font-size:11px; font-weight:600;
  text-transform:uppercase; letter-spacing:.09em}
.group:first-child{margin-top:0}
.check{display:flex; align-items:flex-start; justify-content:space-between; gap:16px;
  padding:11px 0; border-top:1px solid var(--line)}
.check-name{font-weight:600; font-size:14px}
.check-msg{display:block; color:var(--ink-soft); font-size:13.5px; margin-top:1px}
.fix{color:var(--accent); font-size:12.5px; margin-top:3px}
.pill{flex:none; font-size:10.5px; font-weight:700; text-transform:uppercase; letter-spacing:.06em;
  padding:3px 10px; border-radius:999px; white-space:nowrap}
.p-ok{color:var(--ok); background:var(--ok-bg)}
.p-warn{color:var(--warn); background:var(--warn-bg)}
.p-bad{color:var(--bad); background:var(--bad-bg)}
.p-muted{color:var(--muted); background:var(--muted-bg)}
.chips{display:flex; flex-wrap:wrap; gap:9px}
.chip{display:flex; align-items:center; gap:9px; background:#faf7f0; border:1px solid var(--line);
  border-radius:11px; padding:8px 12px; transition:transform .12s ease, box-shadow .12s ease}
.chip:hover{transform:translateY(-1px); box-shadow:0 6px 16px -10px rgba(42,38,34,.35)}
.k-dot{width:8px;height:8px;border-radius:50%;flex:none}
.chip-name{font-family:'Fraunces',ui-monospace,monospace; font-size:13.5px}
.chip-kind{color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.05em}
.empty{color:var(--muted); font-size:14px; padding:6px 0}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace; background:#efe9dc;
  padding:1px 6px; border-radius:6px; font-size:13px}
.updated{color:var(--muted); font-size:12px; text-align:right; margin-top:4px}
footer{max-width:960px; margin:0 auto; padding:14px 32px 44px; color:var(--muted); font-size:12.5px}
@media(max-width:640px){.stats{grid-template-columns:repeat(2,1fr)} .hero{flex-direction:column; align-items:flex-start}}
</style>
</head>
<body>
<div class="topbar"></div>
<header class="hero">
  <div class="brand">
    <span class="logo">&#10043;</span>
    <div><h1>JenAI</h1><p class="tagline">ROS2 Agent Console</p></div>
  </div>
  <div class="live"><span class="dot"></span>live monitor</div>
</header>
<main>__MAIN__</main>
<footer>Read-only monitor served by <span class="mono">jenai web</span> · this page does not control the robot.</footer>
<script>
async function refresh(){
  try{
    const r = await fetch('fragment', {cache:'no-store'});
    if(!r.ok) return;
    const htmlText = await r.text();
    const m = document.querySelector('main');
    m.classList.add('no-anim');
    m.style.opacity = '.55';
    m.innerHTML = htmlText;
    requestAnimationFrame(() => { m.style.opacity = '1'; });
  }catch(e){/* keep last good view */}
}
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


def render_dashboard_html(status: dict[str, Any]) -> str:
    """Render the full Claude-Desktop-style dashboard page."""
    return _PAGE.replace("__MAIN__", render_main(status))


class _Handler(BaseHTTPRequestHandler):
    config: AppConfig
    config_path: Path
    run_store: RunStore | None = None

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
        {"config": config, "config_path": config_path, "run_store": run_store},
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
