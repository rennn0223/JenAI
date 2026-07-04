"""HTML rendering for the JenAI WebUI dashboard.

Pure functions from a status payload to HTML strings — no HTTP, no state —
so the server module stays focused on transport and the renderer can be
unit-tested (and reused by doctor's webui-assets check) in isolation.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


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


_CHECK_LABELS = {
    "python": "Python",
    "uv": "uv",
    "virtual_env": "Virtual env",
    "config_file": "Config file",
    "ros2_cli": "ROS2 command",
    "active_provider": "Provider",
    "api_key": "API key",
    "model_bindings": "Models",
    "locations_file": "Locations file",
    "assets": "WebUI assets",
}


def _health_summary(doctor: dict) -> str:
    items = doctor.get("items", [])
    fails = sum(1 for i in items if str(i["status"]).lower() == "fail")
    warns = sum(1 for i in items if str(i["status"]).lower() == "warn")
    if not items:
        return "Getting your setup ready…"
    if fails:
        return f"{fails} thing{'s' if fails != 1 else ''} need{'' if fails == 1 else 's'} attention."
    if warns:
        return f"Running fine — {warns} minor note{'s' if warns != 1 else ''}."
    return "Everything looks healthy."


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
        check_rows.append(f'<div class="group">{html.escape(section.capitalize())}</div>')
        for item in groups[section]:
            fix = (
                f'<div class="fix">↳ {html.escape(item["fix"])}</div>' if item.get("fix") else ""
            )
            name = _CHECK_LABELS.get(item["check"], item["check"].replace("_", " ").capitalize())
            check_rows.append(
                '<div class="check">'
                '<div class="check-main">'
                f'<span class="check-name">{html.escape(name)}</span>'
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
        f'<p class="summary">{html.escape(_health_summary(doctor))}</p>'
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
  --paper:#f7f4ee; --card:#fffefb; --ink:#26231d; --ink-soft:#57524b; --muted:#928c80;
  --line:#e9e3d6; --accent:#d97757; --accent-ink:#bf6144; --teal:#3f7a72; --gold:#b0842a;
  --ok:#5a8a5f; --ok-bg:#e9f0e7; --warn:#a67a22; --warn-bg:#f4ecd6;
  --bad:#c15f3c; --bad-bg:#f6e3da; --muted-bg:#efe9dc;
}
*{box-sizing:border-box}
html,body{margin:0}
body{
  background:var(--paper);
  background-image:
    radial-gradient(1100px 560px at 82% -12%, rgba(217,119,87,.09), transparent 60%),
    radial-gradient(820px 480px at -12% 8%, rgba(63,122,114,.05), transparent 55%);
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
.hero-right{display:flex; align-items:center; gap:14px}
#estop{background:#8c2f28; color:#fff; border:1px solid #b1443b; border-radius:10px;
  padding:10px 18px; font-weight:700; font-size:14px; letter-spacing:.08em; cursor:pointer;
  box-shadow:0 2px 10px rgba(140,47,40,.45)}
#estop:hover{background:#a83a31}
#estop:disabled{opacity:.6; cursor:wait}
.live{display:flex; align-items:center; gap:8px; color:var(--muted); font-size:12.5px;
  text-transform:uppercase; letter-spacing:.08em}
.live .dot{width:8px;height:8px;border-radius:50%;background:var(--accent);
  box-shadow:0 0 0 0 rgba(192,95,59,.5); animation:pulse 2.4s ease-out infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(192,95,59,.45)}70%{box-shadow:0 0 0 9px rgba(192,95,59,0)}100%{box-shadow:0 0 0 0 rgba(192,95,59,0)}}
main{max-width:960px; margin:0 auto; padding:8px 32px 24px; transition:opacity .28s ease}
.summary{font-family:'Fraunces',Georgia,serif; font-weight:500; font-size:23px;
  line-height:1.3; color:var(--ink); margin:2px 0 20px}
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

/* Map (live pose + saved locations) */
#map{width:100%; height:280px; background:linear-gradient(180deg,#faf6ef,#f3ede2);
  border:1px solid var(--line); border-radius:12px; display:block}
#map .loc-dot{fill:#8a7f6f}
#map .loc-label{fill:#6b6254; font:600 3.2px ui-sans-serif,system-ui; letter-spacing:.02em}
#map .robot{fill:var(--accent)}
#map .robot-ring{fill:none; stroke:var(--accent); stroke-opacity:.35; stroke-width:.6}
#map .grid{stroke:#e5dccb; stroke-width:.25}

/* Console (interactive command area) */
#console{max-width:960px; margin:0 auto 8px}
#transcript{max-height:340px; overflow-y:auto; margin-bottom:12px}
#transcript:empty{display:none}
.blk{margin:10px 0; animation:rise .35s cubic-bezier(.2,.7,.2,1) both}
.you-line{color:var(--ink); font-weight:500}
.you-mark{color:var(--accent); font-weight:700; margin-right:6px}
.blk .out, .blk .out p{margin:2px 0; color:var(--ink-soft); font-size:14px; line-height:1.5}
.blk-error .out{color:var(--bad)}
.blk .out ul, .cmd-list{list-style:none; padding:0; margin:4px 0}
.blk .out li{padding:4px 0; border-top:1px solid var(--line); font-size:13.5px}
.blk .out .dim, .dim{color:var(--muted)}
.blk .out code, .blk .out pre{font-family:ui-monospace,Menlo,monospace; font-size:12.5px;
  background:#efe9dc; border-radius:6px; padding:1px 6px}
.blk .out pre{display:block; padding:8px 10px; overflow-x:auto; white-space:pre-wrap}
.blk-confirm{border-left:3px solid var(--accent); padding-left:12px}
.danger{color:var(--bad); font-size:13px; margin:6px 0}
.confirm-row{display:flex; gap:8px; margin-top:6px}
.btn-approve,.btn-cancel,#cmdsend{font:inherit; font-weight:600; font-size:13.5px; cursor:pointer;
  border-radius:10px; padding:7px 16px; border:1px solid transparent}
.btn-approve{background:var(--accent); color:#fff}
.btn-approve:hover{background:var(--accent-ink)}
.btn-cancel{background:transparent; color:var(--muted); border-color:var(--line)}
#cmdform{display:flex; gap:8px}
#cmdinput{flex:1; font:inherit; font-size:14.5px; color:var(--ink); background:var(--paper);
  border:1px solid var(--line); border-radius:12px; padding:10px 14px; outline:none}
#cmdinput:focus{border-color:var(--accent); box-shadow:0 0 0 3px rgba(217,119,87,.14)}
#cmdsend{background:var(--card); color:var(--ink); border-color:var(--line)}
#cmdsend:hover{border-color:var(--accent)}
#cmdsend:disabled{opacity:.5; cursor:default}

/* Segmented Console/Status tabs — only shown on mobile */
#tabs{display:flex; gap:6px; max-width:960px; margin:0 auto 10px; padding:0 32px}
/* Views are exclusive on every screen size — the dashboard is multi-page. */
body:not(.view-status) main{display:none}
body:not(.view-console) #console{display:none}
body:not(.view-console) #mapcard{display:none}
body:not(.view-camera) #cameracard{display:none}
body:not(.view-api) #apicard{display:none}
#camwrap{display:grid; grid-template-columns:minmax(0,3fr) minmax(150px,1fr); gap:14px; align-items:start}
#rgb{width:100%; border-radius:10px; background:#0d0c0a; min-height:180px; object-fit:contain}
#odom-mini{font-size:13px; border:1px solid var(--line, #e3ded4); border-radius:10px; padding:10px 12px}
#odom-mini h3{margin:0 0 6px; font-size:12px; letter-spacing:.4px; text-transform:uppercase; opacity:.6}
.odom-row{display:flex; justify-content:space-between; padding:2px 0}
.odom-row b{font-variant-numeric:tabular-nums}
.api-row{display:flex; gap:10px; align-items:baseline; padding:7px 10px; border:1px solid var(--line, #e3ded4); border-radius:8px; margin-bottom:6px}
.api-m{font-weight:700; font-size:11px; letter-spacing:.5px; padding:2px 8px; border-radius:5px; color:#fff; min-width:44px; text-align:center}
.m-get{background:#3f9d63}.m-post{background:#3d86c6}
.api-p{font-family:ui-monospace,monospace; font-size:13px}
.api-d{font-size:12px; opacity:.7; margin-left:auto; text-align:right}
@media(max-width:640px){ #camwrap{grid-template-columns:1fr} .api-d{display:none} }
.tab{flex:1; font:inherit; font-weight:600; font-size:14px; cursor:pointer; padding:9px;
  border-radius:11px; border:1px solid var(--line); background:var(--card); color:var(--muted)}
.tab.active{background:var(--accent); color:#fff; border-color:var(--accent)}

/* ---- Mobile app layout (phone) ---- */
@media(max-width:640px){
  .topbar{height:2px}
  .hero{position:sticky; top:0; z-index:6; margin:0; padding:12px 16px;
    background:var(--paper); border-bottom:1px solid var(--line)}
  .hero h1{font-size:26px}
  .logo{font-size:26px}
  #tabs{display:flex; position:sticky; top:56px; z-index:5; padding:10px 16px;
    background:var(--paper); margin:0}
  main, #console, footer{max-width:100%; padding-left:16px; padding-right:16px}
  .stats{grid-template-columns:repeat(2,1fr)}

  /* Console becomes a full-height chat with a fixed input bar */
  #console{margin:0; border:none; background:transparent; box-shadow:none; padding:0}
  #console>.card-head{display:none}
  #transcript{max-height:none; padding:0 16px 84px}
  #transcript:empty{display:block; min-height:30vh}
  #cmdform{position:fixed; left:0; right:0; bottom:0; z-index:7; gap:8px;
    padding:10px 16px calc(10px + env(safe-area-inset-bottom));
    background:var(--paper); border-top:1px solid var(--line)}
  #cmdinput{font-size:16px; padding:12px 14px}   /* 16px avoids iOS zoom */
  #cmdsend{padding:12px 16px}
  .btn-approve,.btn-cancel{padding:11px 18px}

  /* One view at a time on a phone */
  body.view-status #cmdform{display:none}
}
</style>
</head>
<body class="view-console">
<div class="topbar"></div>
<header class="hero">
  <div class="brand">
    <span class="logo">&#10043;</span>
    <div><h1>JenAI</h1><p class="tagline">ROS2 Agent Console</p></div>
  </div>
  <div class="hero-right">
    <button id="estop" title="Emergency stop: cancel navigation, zero velocity">STOP</button>
    <div class="live"><span class="dot"></span>live</div>
  </div>
</header>
<nav id="tabs">
  <button class="tab active" data-view="console">Console</button>
  <button class="tab" data-view="camera">Camera</button>
  <button class="tab" data-view="status">Status</button>
  <button class="tab" data-view="api">API</button>
</nav>
<section id="console" class="card">
  <div class="card-head"><h2>Console</h2><span class="dim">type a command, or ask in plain language</span></div>
  <div id="transcript"></div>
  <form id="cmdform" autocomplete="off">
    <input id="cmdinput" placeholder="/drive 前進兩秒 · /ros topics · or ask anything…" autocomplete="off">
    <button type="submit" id="cmdsend">Send</button>
  </form>
</section>
<section id="mapcard" class="card">
  <div class="card-head"><h2>Map</h2><span class="dim" id="map-meta">waiting for robot pose…</span></div>
  <svg id="map" viewBox="0 0 100 60" preserveAspectRatio="xMidYMid meet"></svg>
</section>
<section id="cameracard" class="card">
  <div class="card-head"><h2>Camera</h2><span class="dim" id="cam-meta">switch here to start streaming…</span></div>
  <div id="camwrap">
    <img id="rgb" alt="camera frame">
    <div id="odom-mini">
      <h3>Odometry</h3>
      <div class="odom-row"><span>x</span><b id="od-x">–</b></div>
      <div class="odom-row"><span>y</span><b id="od-y">–</b></div>
      <div class="odom-row"><span>yaw</span><b id="od-yaw">–</b></div>
      <div class="odom-row"><span>frame</span><b id="od-frame">–</b></div>
      <div class="odom-row"><span>source</span><b id="od-src">–</b></div>
      <div class="odom-row"><span>updated</span><b id="od-ts">–</b></div>
    </div>
  </div>
</section>
<section id="apicard" class="card">
  <div class="card-head"><h2>API</h2><span class="dim">HTTP endpoints served by <span class="mono">jenai web</span> — token via Bearer / cookie / ?token=</span></div>
  <div class="api-row"><span class="api-m m-get">GET</span><span class="api-p">/</span><span class="api-d">此儀表板(?token= 首次授權)</span></div>
  <div class="api-row"><span class="api-m m-get">GET</span><span class="api-p">/api/status</span><span class="api-d">provider/doctor/ROS 狀態 JSON</span></div>
  <div class="api-row"><span class="api-m m-get">GET</span><span class="api-p">/api/map</span><span class="api-d">地點 + 即時位姿 JSON</span></div>
  <div class="api-row"><span class="api-m m-get">GET</span><span class="api-p">/api/frame?topic=…</span><span class="api-d">相機單幀 JPEG(預設 vehicle.camera_topic)</span></div>
  <div class="api-row"><span class="api-m m-post">POST</span><span class="api-p">/api/command　{"text": "…"}</span><span class="api-d">跑指令;動作類回 confirm_id</span></div>
  <div class="api-row"><span class="api-m m-post">POST</span><span class="api-p">/api/confirm　{"confirm_id": "…"}</span><span class="api-d">批准一次性動作(server 端持有)</span></div>
  <div class="api-row"><span class="api-m m-post">POST</span><span class="api-p">/api/stop</span><span class="api-d">緊急停止 — 唯一免 token</span></div>
  <div class="dim" style="margin-top:8px">程式化整合建議走 <span class="mono">JenAI mcp</span>(MCP 協定,預設唯讀);完整規格見 docs/THREAT_MODEL.md 與 docs/COMMANDS.md。</div>
</section>
<main>__MAIN__</main>
<footer>Actions that move the robot always ask you to confirm first · served by <span class="mono">jenai web</span> (localhost).</footer>
<script>
const tx = document.getElementById('transcript');
function el(cls, html){ const d=document.createElement('div'); if(cls)d.className=cls; if(html!=null)d.innerHTML=html; return d; }
function esc(s){ const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }
function scroll(){ tx.scrollTop = tx.scrollHeight; }

function block(kind, node){ const b=el('blk blk-'+kind); b.appendChild(node); tx.appendChild(b); scroll(); return b; }

async function post(url, payload){
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  return r.json();
}

function render(res){
  if(res.kind === 'confirm'){
    const wrap = el(); wrap.innerHTML = res.html;
    const danger = el('danger', '⚠ ' + esc(res.danger||'This will act on the robot.'));
    wrap.appendChild(danger);
    const row = el('confirm-row');
    const yes = el(); yes.innerHTML='<button class="btn-approve">Approve</button>';
    const no  = el(); no.innerHTML='<button class="btn-cancel">Cancel</button>';
    row.appendChild(yes); row.appendChild(no); wrap.appendChild(row);
    const b = block('confirm', wrap);
    yes.querySelector('button').onclick = async () => {
      row.remove(); danger.remove();
      const busy = el('dim','running…'); wrap.appendChild(busy);
      const out = await post('api/confirm', {confirm_id: res.confirm_id});
      busy.remove(); wrap.appendChild(el('out', out.html));
      scroll();
    };
    no.querySelector('button').onclick = () => { row.remove(); danger.remove(); wrap.appendChild(el('dim','Cancelled.')); };
  } else {
    block(res.kind === 'error' ? 'error' : 'result', el('out', res.html));
  }
}

const estop = document.getElementById('estop');
estop.addEventListener('click', async () => {
  // No confirm dialog: an emergency stop must be one tap, always.
  estop.disabled = true; estop.textContent = '…';
  block('you', el('you-line', '<span class="you-mark">›</span> EMERGENCY STOP'));
  try { render(await post('api/stop', {})); }
  catch(err){ block('error', el('out', '<p>Network error.</p>')); }
  finally { estop.disabled = false; estop.textContent = 'STOP'; }
});

const form = document.getElementById('cmdform');
const input = document.getElementById('cmdinput');
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if(!text) return;
  input.value='';
  block('you', el('you-line', '<span class="you-mark">›</span> ' + esc(text)));
  const send = document.getElementById('cmdsend'); send.disabled=true; send.textContent='…';
  try { render(await post('api/command', {text})); }
  catch(err){ block('error', el('out', '<p>Network error.</p>')); }
  finally { send.disabled=false; send.textContent='Send'; input.focus(); }
});

const mapSvg = document.getElementById('map');
const mapMeta = document.getElementById('map-meta');
function mapDraw(data){
  const pts = data.locations.map(l => [l.x, l.y]);
  if(data.pose) pts.push([data.pose.x, data.pose.y]);
  if(!pts.length){ mapMeta.textContent = 'no locations yet — save one with /loc add here <name> in the TUI'; return; }
  const xs = pts.map(p=>p[0]), ys = pts.map(p=>p[1]);
  const pad = Math.max(1.0, (Math.max(...xs)-Math.min(...xs))*0.15, (Math.max(...ys)-Math.min(...ys))*0.15);
  const x0 = Math.min(...xs)-pad, x1 = Math.max(...xs)+pad;
  const y0 = Math.min(...ys)-pad, y1 = Math.max(...ys)+pad;
  const W = 100, H = 60;
  const sc = Math.min(W/(x1-x0), H/(y1-y0));
  // world → svg: x right, y UP (RViz convention), centred
  const ox = (W - (x1-x0)*sc)/2, oy = (H - (y1-y0)*sc)/2;
  const X = wx => ox + (wx - x0)*sc;
  const Y = wy => H - (oy + (wy - y0)*sc);
  let out = '';
  for(let gx = Math.ceil(x0); gx <= Math.floor(x1); gx++)
    out += `<line class="grid" x1="${X(gx)}" y1="0" x2="${X(gx)}" y2="${H}"/>`;
  for(let gy = Math.ceil(y0); gy <= Math.floor(y1); gy++)
    out += `<line class="grid" x1="0" y1="${Y(gy)}" x2="${W}" y2="${Y(gy)}"/>`;
  for(const l of data.locations){
    out += `<circle class="loc-dot" cx="${X(l.x)}" cy="${Y(l.y)}" r="1.1"/>`;
    out += `<text class="loc-label" x="${X(l.x)+1.8}" y="${Y(l.y)+1.1}">${esc(l.name)}</text>`;
  }
  if(data.pose){
    const px = X(data.pose.x), py = Y(data.pose.y);
    const deg = -data.pose.yaw * 180 / Math.PI;
    out += `<circle class="robot-ring" cx="${px}" cy="${py}" r="2.6"/>`;
    out += `<polygon class="robot" points="2.4,0 -1.4,1.4 -1.4,-1.4" transform="translate(${px},${py}) rotate(${deg})"/>`;
    mapMeta.textContent = `robot at (${data.pose.x.toFixed(2)}, ${data.pose.y.toFixed(2)}) · ${data.pose.frame_id} · ${data.pose.source}`;
  } else {
    mapMeta.textContent = data.ros ? 'no live pose (is the robot publishing /amcl_pose or /odom?)' : 'ROS2 not available on this host';
  }
  mapSvg.innerHTML = out;
}
async function pollMap(){
  try{ const r = await fetch('api/map', {cache:'no-store'}); if(r.ok) mapDraw(await r.json()); }
  catch(e){ /* keep last drawing */ }
}
pollMap(); setInterval(pollMap, 2000);

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

// Camera page: poll only while visible — every tick costs a bridge frame grab.
const rgb = document.getElementById('rgb');
const camMeta = document.getElementById('cam-meta');
let camTimer = null, camBusy = false;
rgb.addEventListener('error', () => { camMeta.textContent = 'camera unavailable — is the RGB topic publishing? (vehicle.camera_topic)'; });
rgb.addEventListener('load', () => { camMeta.textContent = 'live · ~1 fps snapshot stream'; });
async function camTick(){
  if(camBusy) return;               // a slow frame must not stack requests
  camBusy = true;
  try{
    rgb.src = 'api/frame?ts=' + Date.now();
    const r = await fetch('api/map', {cache:'no-store'});
    if(r.ok){
      const d = await r.json();
      if(d.pose){
        document.getElementById('od-x').textContent = d.pose.x.toFixed(2) + ' m';
        document.getElementById('od-y').textContent = d.pose.y.toFixed(2) + ' m';
        document.getElementById('od-yaw').textContent = d.pose.yaw.toFixed(2) + ' rad';
        document.getElementById('od-frame').textContent = d.pose.frame_id;
        document.getElementById('od-src').textContent = d.pose.source;
        document.getElementById('od-ts').textContent = new Date().toLocaleTimeString();
      } else {
        document.getElementById('od-src').textContent = d.ros ? 'no pose yet' : 'no ROS';
      }
    }
  }catch(e){/* keep last values */}
  finally{ camBusy = false; }
}
function camStart(){ if(!camTimer){ camTick(); camTimer = setInterval(camTick, 1000); } }
function camStop(){ if(camTimer){ clearInterval(camTimer); camTimer = null; } }

// Console/Camera/Status/API tabs (multi-page on every screen size)
document.querySelectorAll('#tabs .tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('#tabs .tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    document.body.className = 'view-' + t.dataset.view;
    if(t.dataset.view === 'camera') camStart(); else camStop();
    if(t.dataset.view === 'console') input.focus();
  });
});
if(window.innerWidth > 640) input.focus();
</script>
</body>
</html>
"""


def render_dashboard_html(status: dict[str, Any]) -> str:
    """Render the full Claude-Desktop-style dashboard page."""
    return _PAGE.replace("__MAIN__", render_main(status))
