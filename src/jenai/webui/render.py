"""HTML rendering for the JenAI WebUI dashboard.

Pure functions from a status payload to HTML strings — no HTTP, no state —
so the server module stays focused on transport and the renderer can be
unit-tested (and reused by doctor's webui-assets check) in isolation.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any

from jenai.webui.commands import WEB_SLASH_COMMANDS
from jenai.webui.presentation import build_web_status_view


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


_KIND_DOT = {
    "control": "var(--accent)",
    "sensor": "var(--teal)",
    "nav": "var(--ok)",
    "tf": "var(--gold)",
    "debug": "var(--gold)",
    "infra": "var(--muted)",
    "unknown": "var(--muted)",
}


def _status_class(status: str) -> str:
    return {"pass": "ok", "warn": "warn", "fail": "bad"}.get(status, "muted")


def _pill(status: str, label: str) -> str:
    return f'<span class="pill p-{_status_class(status)}">{html.escape(label)}</span>'


def render_main(status: dict[str, Any]) -> str:
    """Render the dynamic dashboard body (also served at /fragment for refresh)."""
    view = build_web_status_view(status)
    stats = [
        ("模型服務", view.provider),
        ("模型", view.model),
        ("設定", view.config_label),
        ("地點", str(view.locations)),
    ]
    stats_html = "".join(
        f'<div class="stat"><span class="stat-k">{html.escape(key)}</span>'
        f'<span class="stat-v">{html.escape(value)}</span></div>'
        for key, value in stats
    )

    groups: dict[str, list[Any]] = {}
    order: list[str] = []
    for item in view.checks:
        if item.section not in groups:
            groups[item.section] = []
            order.append(item.section)
        groups[item.section].append(item)

    check_rows: list[str] = []
    for section in order:
        items = groups[section]
        check_rows.append(f'<div class="group">{html.escape(items[0].section_label)}</div>')
        for item in items:
            fix = f'<div class="fix">↳ {html.escape(item.fix)}</div>' if item.fix else ""
            check_rows.append(
                '<div class="check">'
                '<div class="check-main">'
                f'<span class="check-name">{html.escape(item.label)}</span>'
                f'<span class="check-msg">{html.escape(item.message)}</span>{fix}'
                "</div>"
                f"{_pill(item.status, item.status_label)}"
                "</div>"
            )
    doctor_html = "".join(check_rows)

    if not view.ros_available:
        ros_html = (
            '<div class="empty">目前找不到 ROS 2。請先執行 '
            '<span class="mono">JenAI doctor</span> 查看修復建議，再以 '
            '<span class="mono">jenai</span> 啟動並載入 ROS 2 Jazzy。</div>'
        )
    elif view.ros_error:
        ros_html = f'<div class="empty">ROS 2 錯誤：{html.escape(view.ros_error)}</div>'
    elif not view.ros_topics:
        ros_html = '<div class="empty">ROS graph 目前還沒有 topic。</div>'
    else:

        def _chip(topic: Any) -> str:
            return (
                '<div class="chip">'
                f'<span class="k-dot" style="background:{_KIND_DOT.get(topic.kind, "var(--muted)")}">'
                "</span>"
                f'<span class="chip-name">{html.escape(topic.name)}</span>'
                f'<span class="chip-kind">{html.escape(topic.kind)}</span>'
                "</div>"
            )

        main_topics = [topic for topic in view.ros_topics if topic.kind != "infra"]
        infra_topics = [topic for topic in view.ros_topics if topic.kind == "infra"]
        ros_html = f'<div class="chips">{"".join(_chip(topic) for topic in main_topics)}</div>'
        if infra_topics:
            ros_html += (
                '<details class="infra-fold"><summary>'
                f"infra（lifecycle／bond／rosout…）· {len(infra_topics)}</summary>"
                f'<div class="chips">{"".join(_chip(topic) for topic in infra_topics)}</div>'
                "</details>"
            )

    updated = datetime.now().astimezone().strftime("%H:%M:%S")
    return (
        f'<p class="summary">{html.escape(view.health_summary)}</p>'
        f'<div class="stats">{stats_html}</div>'
        '<section class="card">'
        '<div class="card-head"><h2>系統檢查</h2>'
        f'<div class="head-right">整體 {_pill(view.overall, view.overall_label)}</div></div>'
        f'<div class="checks">{doctor_html}</div>'
        "</section>"
        '<section class="card">'
        '<div class="card-head"><h2>ROS 2 Graph</h2>'
        f'<div class="head-right"><span class="count">{view.ros_count}</span> topics</div></div>'
        f"{ros_html}"
        "</section>"
        f'<div class="updated">更新於 {updated} · 每 5 秒自動重新整理</div>'
    )


# Static shell (head + hero + footer + refresh script). Kept as a plain string
# so the literal CSS/JS braces don't collide with f-string formatting; the
# dynamic body is spliced in at __MAIN__.
# Raw string: the embedded JS contains regex escapes (e.g. /^\s+/) that are
# not Python escape sequences.
_PAGE = r"""<!DOCTYPE html>
<html lang="zh-Hant-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>JenAI — 機器人監控</title>
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
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans TC',sans-serif;
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
  font-family:'Iowan Old Style','Noto Serif TC','Palatino Linotype',Georgia,serif; font-size:34px; line-height:1;
  color:var(--accent); transform:translateY(2px);
}
.hero h1{
  font-family:'Iowan Old Style','Noto Serif TC','Palatino Linotype',Georgia,serif;
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
.summary{font-family:'Iowan Old Style','Noto Serif TC','Palatino Linotype',Georgia,serif; font-weight:500; font-size:23px;
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
button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{
  outline:3px solid rgba(217,119,87,.35); outline-offset:2px;
}
.live.is-online .dot{background:var(--ok)}
.live.is-stale .dot{background:var(--warn);animation:none}
.live.is-offline .dot{background:var(--bad);animation:none}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
  clip:rect(0,0,0,0);white-space:nowrap;border:0}
.card:nth-child(2){animation-delay:.05s}
.card:nth-child(3){animation-delay:.12s}
.no-anim .card{animation:none}
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.card-head{display:flex; align-items:baseline; justify-content:space-between; margin-bottom:14px}
.card-head h2{font-family:'Iowan Old Style','Noto Serif TC','Palatino Linotype',Georgia,serif; font-weight:600; font-size:21px; margin:0}
.head-right{color:var(--muted); font-size:13px}
.count{font-weight:700; color:var(--ink); font-size:15px}
.group{margin:16px 0 6px; color:var(--muted); font-size:11px; font-weight:600;
  text-transform:uppercase; letter-spacing:.09em}
.group:first-child{margin-top:0}
.check{display:flex; align-items:flex-start; justify-content:space-between; gap:16px;
  padding:11px 0; border-top:1px solid var(--line)}
.check-name{font-weight:600; font-size:14px}
.check-msg{display:block; color:var(--ink-soft); font-size:13.5px; margin-top:1px; overflow-wrap:anywhere}
.fix{color:var(--accent); font-size:12.5px; margin-top:3px}
.pill{flex:none; font-size:10.5px; font-weight:700; text-transform:uppercase; letter-spacing:.06em;
  padding:3px 10px; border-radius:999px; white-space:nowrap}
.p-ok{color:var(--ok); background:var(--ok-bg)}
.p-warn{color:var(--warn); background:var(--warn-bg)}
.p-bad{color:var(--bad); background:var(--bad-bg)}
.p-muted{color:var(--muted); background:var(--muted-bg)}
.chips{display:flex; flex-wrap:wrap; gap:9px}
.chip{display:flex; align-items:center; gap:9px; background:#faf7f0; border:1px solid var(--line);
  border-radius:11px; padding:8px 12px; transition:transform .12s ease, box-shadow .12s ease;
  max-width:100%; min-width:0}
.chip:hover{transform:translateY(-1px); box-shadow:0 6px 16px -10px rgba(42,38,34,.35)}
.k-dot{width:8px;height:8px;border-radius:50%;flex:none}
.chip-name{font-family:'Iowan Old Style','Noto Serif TC',ui-monospace,monospace; font-size:13.5px;
  min-width:0; overflow-wrap:anywhere}
.chip-kind{color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.05em}
.infra-fold{margin-top:10px}
.infra-fold summary{color:var(--muted); font-size:12.5px; cursor:pointer}
.empty{color:var(--muted); font-size:14px; padding:6px 0}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace; background:#efe9dc;
  padding:1px 6px; border-radius:6px; font-size:13px}
.updated{color:var(--muted); font-size:12px; text-align:right; margin-top:4px}
footer{max-width:960px; margin:0 auto; padding:14px 32px 44px; color:var(--muted); font-size:12.5px}

/* Map (live pose + saved locations) */
#map{width:100%; height:280px; background:linear-gradient(180deg,#faf6ef,#f3ede2);
  border:1px solid var(--line); border-radius:12px; display:block;
  touch-action:none; cursor:grab}
.map-tools{margin-left:auto; display:flex; gap:6px}
.map-tools button{width:30px; height:30px; border:1px solid var(--line); border-radius:8px;
  background:var(--card); color:var(--ink-soft); font-size:15px; line-height:1; cursor:pointer}
.map-tools button:hover{border-color:var(--accent); color:var(--accent)}
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
#cmdform{display:flex; gap:8px; position:relative}
/* Slash palette: floats above the input, TUI-style (type "/" to open). */
#palette{display:none; position:absolute; bottom:100%; left:0; right:0; margin-bottom:8px;
  background:var(--card); border:1px solid var(--line); border-radius:12px; padding:6px;
  box-shadow:0 10px 28px rgba(0,0,0,.14); max-height:280px; overflow-y:auto; z-index:5}
.pal-row{display:flex; gap:12px; align-items:baseline; padding:7px 10px; border-radius:8px;
  cursor:pointer}
.pal-row.sel{background:rgba(217,119,87,.13)}
.pal-row.sel .pal-name::before{content:"❯ "; color:var(--accent); font-weight:700}
.pal-name{font-family:ui-monospace,Menlo,monospace; font-size:13px; color:var(--ink);
  white-space:nowrap}
.pal-desc{color:var(--muted); font-size:12.5px; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap}
.pal-hint{cursor:default}
.pal-hint .pal-name{color:var(--accent-ink)}
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
#cam-topic{font:inherit; font-size:12.5px; max-width:46%; padding:3px 6px; border-radius:7px; border:1px solid var(--line, #e3ded4); background:transparent}
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
@media(prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;
    scroll-behavior:auto!important;transition-duration:.01ms!important}
}

</style>
</head>
<body class="view-console">
<div class="topbar"></div>
<header class="hero">
  <div class="brand">
    <span class="logo">&#10043;</span>
    <div><h1>JenAI</h1><p class="tagline">ROS 2 機器人監控</p></div>
  </div>
  <div class="hero-right">
    <button id="estop" aria-label="立即停止機器人" title="立即取消導航並送出零速度命令">STOP</button>
    <div id="connection-status" class="live is-stale" role="status" aria-live="polite">
      <span class="dot" aria-hidden="true"></span><span id="connection-label">正在連線</span>
    </div>
  </div>
</header>
<nav id="tabs" role="tablist" aria-label="WebUI 功能">
  <button id="tab-console" class="tab active" role="tab" aria-selected="true" aria-controls="console" tabindex="0" data-view="console">互動</button>
  <button id="tab-camera" class="tab" role="tab" aria-selected="false" aria-controls="cameracard" tabindex="-1" data-view="camera">相機</button>
  <button id="tab-status" class="tab" role="tab" aria-selected="false" aria-controls="status-view" tabindex="-1" data-view="status">狀態</button>
  <button id="tab-api" class="tab" role="tab" aria-selected="false" aria-controls="apicard" tabindex="-1" data-view="api">API</button>
</nav>
<section id="console" class="card" role="tabpanel" aria-labelledby="tab-console">
  <div class="card-head"><h2>與 JenAI 互動</h2><span class="dim">輸入指令，或直接說明要完成的任務</span></div>
  <div id="transcript" role="log" aria-live="polite" aria-relevant="additions"></div>
  <form id="cmdform" autocomplete="off">
    <div id="palette" role="listbox" aria-label="可用指令"></div>
    <label class="sr-only" for="cmdinput">輸入 JenAI 指令或自然語言任務</label>
    <input id="cmdinput" aria-label="輸入 JenAI 指令或自然語言任務" placeholder="/status · /ros topics · 或直接描述任務…" autocomplete="off">
    <button type="submit" id="cmdsend">送出</button>
  </form>
</section>
<section id="mapcard" class="card" aria-label="機器人地圖">
  <div class="card-head"><h2>地圖</h2><span class="dim" id="map-meta" role="status" aria-live="polite">正在等待機器人位姿…</span>
    <div class="map-tools">
      <button type="button" id="map-zout" aria-label="縮小地圖" title="縮小">−</button>
      <button type="button" id="map-zin" aria-label="放大地圖" title="放大">+</button>
      <button type="button" id="map-zfit" aria-label="顯示全部地點" title="顯示全部">⤢</button>
    </div></div>
  <svg id="map" role="img" aria-label="機器人目前位姿與已儲存地點" viewBox="0 0 100 60" preserveAspectRatio="xMidYMid meet"></svg>
</section>
<section id="cameracard" class="card" role="tabpanel" aria-labelledby="tab-camera">
  <div class="card-head"><h2>相機</h2>
    <select id="cam-topic" aria-label="選擇相機 topic" title="選擇 /api/frame 使用的 image topic"><option value="__default__">vehicle.camera_topic（預設）</option></select>
    <span class="dim" id="cam-meta" role="status" aria-live="polite">切換到相機頁面後開始串流</span>
  </div>
  <div id="camwrap">
    <img id="rgb" alt="機器人相機最新畫面">
    <div id="odom-mini">
      <h3>里程計</h3>
      <div class="odom-row"><span>x</span><b id="od-x">–</b></div>
      <div class="odom-row"><span>y</span><b id="od-y">–</b></div>
      <div class="odom-row"><span>yaw</span><b id="od-yaw">–</b></div>
      <div class="odom-row"><span>frame</span><b id="od-frame">–</b></div>
      <div class="odom-row"><span>source</span><b id="od-src">–</b></div>
      <div class="odom-row"><span>更新</span><b id="od-ts">–</b></div>
    </div>
  </div>
</section>
<section id="apicard" class="card" role="tabpanel" aria-labelledby="tab-api">
  <div class="card-head"><h2>API</h2><span class="dim"><span class="mono">jenai web</span> 提供的 HTTP endpoints；token 可透過 Bearer、cookie 或 ?token= 傳入</span></div>
  <div class="api-row"><span class="api-m m-get">GET</span><span class="api-p">/</span><span class="api-d">此儀表板（?token= 首次授權）</span></div>
  <div class="api-row"><span class="api-m m-get">GET</span><span class="api-p">/api/status</span><span class="api-d">provider/doctor/ROS 狀態 JSON</span></div>
  <div class="api-row"><span class="api-m m-get">GET</span><span class="api-p">/api/map</span><span class="api-d">地點與即時位姿 JSON</span></div>
  <div class="api-row"><span class="api-m m-get">GET</span><span class="api-p">/api/frame?topic=…</span><span class="api-d">相機單幀 JPEG（預設 vehicle.camera_topic）</span></div>
  <div class="api-row"><span class="api-m m-post">POST</span><span class="api-p">/api/command　{"text": "…"}</span><span class="api-d">執行指令；動作類回傳 confirm_id</span></div>
  <div class="api-row"><span class="api-m m-post">POST</span><span class="api-p">/api/confirm　{"confirm_id": "…"}</span><span class="api-d">批准由 server 暫存的一次性動作</span></div>
  <div class="api-row"><span class="api-m m-post">POST</span><span class="api-p">/api/stop</span><span class="api-d">立即停止；唯一不需要 token 的 endpoint</span></div>
  <div class="api-row"><span class="api-m m-get">GET</span><span class="api-p">/api/topics</span><span class="api-d">即時 ROS graph topics（下表）</span></div>
  <div class="card-head" style="margin-top:14px"><h2 style="font-size:15px">ROS topics（即時）</h2><span class="dim" id="api-topics-meta">切換至此頁時載入…</span></div>
  <div id="api-topics" class="mono" style="font-size:12.5px; line-height:1.9; overflow-wrap:anywhere"></div>
  <div class="dim" style="margin-top:8px">程式化整合建議使用 <span class="mono">JenAI mcp</span>（MCP 協定，預設唯讀）；完整規格見 docs/validation/THREAT_MODEL.md 與 docs/COMMANDS.md。</div>
</section>
<main id="status-view" role="tabpanel" aria-labelledby="tab-status">__MAIN__</main>
<footer>所有會移動機器人的動作都必須先經過批准 · 由 <span class="mono">jenai web</span> 在 localhost 提供服務。</footer>
<script>
const tx = document.getElementById('transcript');
const connection = document.getElementById('connection-status');
const connectionLabel = document.getElementById('connection-label');
function el(cls, html){ const d=document.createElement('div'); if(cls)d.className=cls; if(html!=null)d.innerHTML=html; return d; }
function esc(s){ const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }
function scroll(){ tx.scrollTop = tx.scrollHeight; }
function setConnection(state, label){
  connection.classList.remove('is-online','is-stale','is-offline');
  connection.classList.add('is-' + state);
  connectionLabel.textContent = label;
}

function block(kind, node){ const b=el('blk blk-'+kind); b.appendChild(node); tx.appendChild(b); scroll(); return b; }

async function post(url, payload){
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const raw = await r.text();
  let data = null;
  try { data = raw ? JSON.parse(raw) : {}; }
  catch(err){ throw new Error(`伺服器回傳無法解析的內容（HTTP ${r.status}）`); }
  if(!r.ok){
    const detail = data && (data.error || data.message);
    throw new Error(detail || `請求失敗（HTTP ${r.status}）`);
  }
  setConnection('online', '已連線');
  return data;
}

function render(res){
  if(res.kind === 'confirm'){
    const wrap = el(); wrap.innerHTML = res.html;
    const danger = el('danger', '⚠ ' + esc(res.danger||'這個動作會操作機器人。'));
    wrap.appendChild(danger);
    const row = el('confirm-row');
    const yes = el(); yes.innerHTML='<button class="btn-approve">批准一次</button>';
    const no  = el(); no.innerHTML='<button class="btn-cancel">取消</button>';
    row.appendChild(yes); row.appendChild(no); wrap.appendChild(row);
    block('confirm', wrap);
    const yesButton = yes.querySelector('button');
    const noButton = no.querySelector('button');
    yesButton.onclick = async () => {
      yesButton.disabled = true; noButton.disabled = true;
      const busy = el('dim','執行中…'); wrap.appendChild(busy);
      try{
        const out = await post('api/confirm', {confirm_id: res.confirm_id});
        row.remove(); danger.remove();
        wrap.appendChild(el(out.kind === 'error' ? 'out danger' : 'out', out.html));
      }catch(err){
        setConnection('offline', '連線中斷');
        wrap.appendChild(el('danger', '無法確認動作結果：' + esc(err.message)));
        yesButton.disabled = false; noButton.disabled = false;
      }finally{
        busy.remove(); scroll();
      }
    };
    noButton.onclick = () => { row.remove(); danger.remove(); wrap.appendChild(el('dim','已取消。')); };
  } else {
    block(res.kind === 'error' ? 'error' : 'result', el('out', res.html));
  }
}

const estop = document.getElementById('estop');
estop.addEventListener('click', async () => {
  // No confirm dialog: an emergency stop must be one tap, always.
  estop.disabled = true; estop.textContent = '…';
  block('you', el('you-line', '<span class="you-mark">›</span> 立即停止'));
  try { render(await post('api/stop', {})); }
  catch(err){
    setConnection('offline', '連線中斷');
    block('error', el('out', '<p>無法確認停止命令是否送達。請立刻使用實體急停，並確認 jenai web 連線。</p>'));
  }
  finally { estop.disabled = false; estop.textContent = 'STOP'; }
});

const form = document.getElementById('cmdform');
const input = document.getElementById('cmdinput');

// Slash palette (TUI parity): type "/" → filtered command list; ↑/↓ select,
// Tab or click completes, Esc hides. SLASH is server-rendered from
// WEB_SLASH_COMMANDS so the palette can never promise an unimplemented command.
const SLASH = __SLASH__;
const pal = document.getElementById('palette');
let palIdx = 0, palMatches = [];
function palHide(){ pal.style.display='none'; palMatches=[]; }
function palRender(){
  pal.innerHTML = palMatches.map((c,i) =>
    '<div class="pal-row'+(i===palIdx?' sel':'')+'" data-i="'+i+'">' +
    '<span class="pal-name">'+esc(c.usage)+'</span>' +
    '<span class="pal-desc">'+esc(c.desc)+'</span></div>').join('');
  pal.style.display='block';
  pal.querySelectorAll('.pal-row').forEach(r => {
    r.onmousedown = (e) => { e.preventDefault(); palPick(+r.dataset.i); };
  });
}
function palSync(){
  const v = input.value.replace(/^\s+/, '').toLowerCase();
  palMatches = v.startsWith('/') ? SLASH.filter(c => c.name.startsWith(v)) : [];
  if(palMatches.length){ palIdx = Math.min(palIdx, palMatches.length-1); palRender(); return; }
  // Typing arguments of a known command → dim, non-interactive format hint
  // (completion inserts only the name; the format is a hint, never input).
  const hint = v.startsWith('/')
    ? SLASH.filter(c => c.usage !== c.name && v.startsWith(c.name + ' '))
           .sort((a,b) => b.name.length - a.name.length)[0]
    : null;
  if(!hint){ palHide(); return; }
  pal.innerHTML = '<div class="pal-row pal-hint">' +
    '<span class="pal-name">' + esc(hint.usage) + '</span>' +
    '<span class="pal-desc">' + esc(hint.desc) + '</span></div>';
  pal.style.display = 'block';
}
function palPick(i){
  input.value = palMatches[i].name + ' ';
  palHide(); input.focus(); palIdx = 0; palSync();
}
input.addEventListener('input', () => { palIdx = 0; palSync(); });
input.addEventListener('keydown', (e) => {
  if(pal.style.display !== 'block') return;
  if(!palMatches.length){
    // Hint mode: keep typing args; Esc dismisses, Tab stays in the input.
    if(e.key === 'Escape') palHide();
    else if(e.key === 'Tab') e.preventDefault();
    return;
  }
  if(e.key === 'ArrowDown'){ e.preventDefault(); palIdx = (palIdx+1) % palMatches.length; palRender(); }
  else if(e.key === 'ArrowUp'){ e.preventDefault(); palIdx = (palIdx-1+palMatches.length) % palMatches.length; palRender(); }
  else if(e.key === 'Tab'){ e.preventDefault(); palPick(palIdx); }
  else if(e.key === 'Escape'){ palHide(); }
});

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if(!text) return;
  input.value = '';
  palHide();
  block('you', el('you-line', '<span class="you-mark">›</span> ' + esc(text)));
  const send = document.getElementById('cmdsend'); send.disabled=true; send.textContent='…';
  try { render(await post('api/command', {text})); }
  catch(err){
    input.value = text;
    setConnection('offline', '連線中斷');
    block('error', el('out', '<p>連線失敗，指令尚未送出。請確認 jenai web 仍在執行後再重試。</p>'));
  }
  finally { send.disabled=false; send.textContent='送出'; input.focus(); }
});

const mapSvg = document.getElementById('map');
const mapMeta = document.getElementById('map-meta');
// Zoom/pan state: zoom=1 fits everything; center in world coords (null = auto).
let mapZoom = 1, mapCenter = null, mapData = null;
function mapDraw(data){
  if(data) mapData = data; else data = mapData;
  if(!data) return;
  const pts = data.locations.map(l => [l.x, l.y]);
  if(data.pose) pts.push([data.pose.x, data.pose.y]);
  if(!pts.length){
    mapMeta.textContent = data.pose_error === 'invalid_pose'
      ? '定位資料無效（pose 含 NaN／inf）；請檢查 AMCL 或 odometry'
      : '尚未儲存地點；請在 TUI 使用 /loc add here <name>';
    return;
  }
  const xs = pts.map(p=>p[0]), ys = pts.map(p=>p[1]);
  const pad = Math.max(1.0, (Math.max(...xs)-Math.min(...xs))*0.15, (Math.max(...ys)-Math.min(...ys))*0.15);
  const fx0 = Math.min(...xs)-pad, fx1 = Math.max(...xs)+pad;
  const fy0 = Math.min(...ys)-pad, fy1 = Math.max(...ys)+pad;
  const W = 100, H = 60;
  // view = fit bounds shrunk by zoom around center (default: fit centre)
  const cx = mapCenter ? mapCenter[0] : (fx0+fx1)/2;
  const cy = mapCenter ? mapCenter[1] : (fy0+fy1)/2;
  const spanX = (fx1-fx0)/mapZoom, spanY = (fy1-fy0)/mapZoom;
  const x0 = cx - spanX/2, x1 = cx + spanX/2;
  const y0 = cy - spanY/2, y1 = cy + spanY/2;
  const sc = Math.min(W/(x1-x0), H/(y1-y0));
  // world → svg: x right, y UP (RViz convention), centred
  const ox = (W - (x1-x0)*sc)/2, oy = (H - (y1-y0)*sc)/2;
  const X = wx => ox + (wx - x0)*sc;
  const Y = wy => H - (oy + (wy - y0)*sc);
  mapSvg._view = {x0, y0, x1, y1, sc, X, Y};
  let out = '';
  // Grid: pick a metre step that keeps ≲ 25 lines even on huge spans.
  const step = [1,2,5,10,20,50,100].find(s => (x1-x0)/s <= 25) || 200;
  for(let gx = Math.ceil(x0/step)*step; gx <= x1; gx += step)
    out += `<line class="grid" x1="${X(gx)}" y1="0" x2="${X(gx)}" y2="${H}"/>`;
  for(let gy = Math.ceil(y0/step)*step; gy <= y1; gy += step)
    out += `<line class="grid" x1="0" y1="${Y(gy)}" x2="${W}" y2="${Y(gy)}"/>`;
  // Labels: greedy declutter — draw a label only when its box doesn't overlap
  // one already placed; suppressed points keep the dot + a hover tooltip.
  const placed = [];
  let hidden = 0;
  for(const l of data.locations){
    const px = X(l.x), py = Y(l.y);
    out += `<circle class="loc-dot" cx="${px}" cy="${py}" r="1.1"><title>${esc(l.name)}</title></circle>`;
    const bw = 1.9 * String(l.name).length + 2, bh = 3.4;   // ~font 3px
    const box = {x: px+1.4, y: py-1.6, w: bw, h: bh};
    const clash = placed.some(b => box.x < b.x+b.w && b.x < box.x+box.w && box.y < b.y+b.h && b.y < box.y+box.h);
    if(!clash && px >= 0 && px <= W && py >= 0 && py <= H){
      placed.push(box);
      out += `<text class="loc-label" x="${px+1.8}" y="${py+1.1}">${esc(l.name)}</text>`;
    } else if(px >= 0 && px <= W && py >= 0 && py <= H){ hidden++; }
  }
  if(data.pose){
    const px = X(data.pose.x), py = Y(data.pose.y);
    const deg = -data.pose.yaw * 180 / Math.PI;
    out += `<circle class="robot-ring" cx="${px}" cy="${py}" r="2.6"/>`;
    out += `<polygon class="robot" points="2.4,0 -1.4,1.4 -1.4,-1.4" transform="translate(${px},${py}) rotate(${deg})"/>`;
    mapMeta.textContent = `機器人位於 (${data.pose.x.toFixed(2)}, ${data.pose.y.toFixed(2)}) · ${data.pose.frame_id} · ${data.pose.source}`
      + (hidden ? ` · 已隱藏 ${hidden} 個重疊標籤（放大可查看）` : '');
  } else if(data.pose_error === 'invalid_pose') {
    mapMeta.textContent = '定位資料無效（pose 含 NaN／inf）；請檢查 AMCL 或 odometry';
  } else {
    mapMeta.textContent = data.ros ? '尚無即時位姿；請確認 /amcl_pose 或 /odom 正在發布' : '這台主機目前無法使用 ROS 2';
  }
  mapSvg.innerHTML = out;
}
function mapSetZoom(z, focus){
  mapZoom = Math.min(64, Math.max(1, z));
  if(mapZoom === 1){ mapCenter = null; }
  else if(focus){ mapCenter = focus; }
  else if(!mapCenter && mapData){
    // First zoom-in centres on the robot when we have it — that's what you
    // zoom for; otherwise the locations' centroid.
    if(mapData.pose) mapCenter = [mapData.pose.x, mapData.pose.y];
  }
  mapDraw(null);
}
document.getElementById('map-zin').addEventListener('click', () => mapSetZoom(mapZoom*1.6));
document.getElementById('map-zout').addEventListener('click', () => mapSetZoom(mapZoom/1.6));
document.getElementById('map-zfit').addEventListener('click', () => { mapCenter = null; mapSetZoom(1); });
mapSvg.addEventListener('wheel', ev => {
  ev.preventDefault();
  mapSetZoom(mapZoom * (ev.deltaY < 0 ? 1.3 : 1/1.3));
}, {passive: false});
// Drag / touch-drag to pan (single pointer; SVG units → world via 1/sc).
let panFrom = null;
mapSvg.addEventListener('pointerdown', ev => {
  panFrom = [ev.clientX, ev.clientY];
  mapSvg.setPointerCapture(ev.pointerId);
});
mapSvg.addEventListener('pointermove', ev => {
  if(!panFrom || !mapSvg._view || mapZoom === 1) return;
  const v = mapSvg._view;
  const pxPerUnit = mapSvg.clientWidth / 100;   // viewBox width = 100
  const dx = (ev.clientX - panFrom[0]) / pxPerUnit / v.sc;
  const dy = (ev.clientY - panFrom[1]) / pxPerUnit / v.sc;
  panFrom = [ev.clientX, ev.clientY];
  const c = mapCenter || [(v.x0+v.x1)/2, (v.y0+v.y1)/2];
  mapCenter = [c[0] - dx, c[1] + dy];   // y is flipped in the projection
  mapDraw(null);
});
mapSvg.addEventListener('pointerup', () => { panFrom = null; });
async function pollMap(){
  try{
    const r = await fetch('api/map', {cache:'no-store'});
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    mapDraw(await r.json());
  }catch(e){
    mapMeta.textContent = mapData ? '地圖更新中斷；目前顯示上次取得的資料' : '無法取得機器人地圖資料';
  }
}
pollMap(); setInterval(pollMap, 2000);

async function refresh(){
  try{
    const r = await fetch('fragment', {cache:'no-store'});
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const htmlText = await r.text();
    const m = document.querySelector('main');
    m.classList.add('no-anim');
    m.style.opacity = '.55';
    m.innerHTML = htmlText;
    requestAnimationFrame(() => { m.style.opacity = '1'; });
    setConnection('online', '已連線');
  }catch(e){
    setConnection('offline', '連線中斷');
  }
}
refresh(); setInterval(refresh, 5000);

// Camera page: poll only while visible — every tick costs a bridge frame grab.
const rgb = document.getElementById('rgb');
const camMeta = document.getElementById('cam-meta');
let camTimer = null, camBusy = false;
rgb.addEventListener('error', () => { camMeta.textContent = '相機無法使用；請確認 RGB topic 或 vehicle.camera_topic'; });
rgb.addEventListener('load', () => { camMeta.textContent = '即時畫面 · 約 1 fps · ' + new Date().toLocaleTimeString(); });
async function camTick(){
  if(camBusy) return;               // a slow frame must not stack requests
  camBusy = true;
  try{
    rgb.src = 'api/frame?ts=' + Date.now() + camTopicParam();
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
        document.getElementById('od-src').textContent = d.ros ? '尚無位姿' : 'ROS 2 不可用';
      }
    }
  }catch(e){ camMeta.textContent = '資料更新中斷；目前顯示上次取得的畫面'; }
  finally{ camBusy = false; }
}
// Topic picker: camera_topic names vary per robot (/rgb vs /rgb/image vs
// /camera/image_raw) — list the live graph so nobody has to guess.
const camTopic = document.getElementById('cam-topic');
camTopic.value = localStorage.getItem('jenai-cam-topic') || '__default__';
camTopic.addEventListener('change', () => {
  localStorage.setItem('jenai-cam-topic', camTopic.value);
  camTick();
});
function camTopicParam(){
  return camTopic.value === '__default__' ? '' : '&topic=' + encodeURIComponent(camTopic.value);
}
async function camTopicsLoad(){
  try{
    const r = await fetch('api/topics', {cache:'no-store'});
    if(!r.ok) return;
    const d = await r.json();
    const names = (d.topics||[]).map(t => t.name);
    const imgish = names.filter(n => /rgb|image|camera|depth/i.test(n));
    const rest = names.filter(n => !imgish.includes(n));
    const keep = camTopic.value;
    camTopic.innerHTML = '<option value="__default__">vehicle.camera_topic（預設）</option>' +
      imgish.map(n => `<option>${esc(n)}</option>`).join('') +
      (rest.length ? '<optgroup label="其他 topics">' + rest.map(n => `<option>${esc(n)}</option>`).join('') + '</optgroup>' : '');
    if([...camTopic.options].some(o => o.value === keep)) camTopic.value = keep;
  }catch(e){/* picker keeps whatever it has */}
}
function camStart(){ if(!camTimer){ camTopicsLoad(); camTick(); camTimer = setInterval(camTick, 1000); } }
function camStop(){ if(camTimer){ clearInterval(camTimer); camTimer = null; } }

// API page: one-shot live topics listing per visit, grouped by kind with the
// plumbing (lifecycle/bond/rosout…) folded away — 110 raw Nav2 topics is
// noise, the ~20 domain topics are the signal.
async function apiTopicsLoad(){
  const box = document.getElementById('api-topics');
  const meta = document.getElementById('api-topics-meta');
  try{
    const r = await fetch('api/topics', {cache:'no-store'});
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    if(!d.available){ meta.textContent = '這台主機目前無法使用 ROS 2'; return; }
    if(d.error){ meta.textContent = d.error; return; }
    const order = ['control','sensor','nav','tf','debug','unknown'];
    const groups = {};
    for(const t of d.topics) (groups[t.kind] = groups[t.kind] || []).push(t.name);
    const infra = groups['infra'] || [];
    let html = '';
    for(const k of order){
      if(!groups[k] || !groups[k].length) continue;
      html += `<div class="dim" style="margin-top:8px">${k} · ${groups[k].length}</div>`
            + groups[k].map(esc).join('<br>');
    }
    if(infra.length){
      html += `<details style="margin-top:10px"><summary class="dim" style="cursor:pointer">`
            + `infra（lifecycle／bond／rosout…）· ${infra.length}</summary>`
            + infra.map(esc).join('<br>') + '</details>';
    }
    meta.textContent = `${d.count} topics · 顯示 ${d.count - infra.length} · 收合 ${infra.length} 個 infra topic`;
    box.innerHTML = html;
  }catch(e){ meta.textContent = '無法載入 topics；請確認 WebUI 連線'; }
}

// Console/Camera/Status/API tabs (multi-page on every screen size)
const tabs = [...document.querySelectorAll('#tabs .tab')];
function activateTab(tab, focusContent){
  tabs.forEach(item => {
    const active = item === tab;
    item.classList.toggle('active', active);
    item.setAttribute('aria-selected', String(active));
    item.tabIndex = active ? 0 : -1;
  });
  document.body.className = 'view-' + tab.dataset.view;
  document.getElementById('console').setAttribute('aria-hidden', String(tab.dataset.view !== 'console'));
  document.getElementById('mapcard').setAttribute('aria-hidden', String(tab.dataset.view !== 'console'));
  document.getElementById('cameracard').setAttribute('aria-hidden', String(tab.dataset.view !== 'camera'));
  document.getElementById('status-view').setAttribute('aria-hidden', String(tab.dataset.view !== 'status'));
  document.getElementById('apicard').setAttribute('aria-hidden', String(tab.dataset.view !== 'api'));
  if(tab.dataset.view === 'camera') camStart(); else camStop();
  if(tab.dataset.view === 'api') apiTopicsLoad();
  if(focusContent && tab.dataset.view === 'console') input.focus();
}
tabs.forEach((tab, index) => {
  tab.addEventListener('click', () => activateTab(tab, true));
  tab.addEventListener('keydown', event => {
    let next = null;
    if(event.key === 'ArrowRight') next = tabs[(index + 1) % tabs.length];
    else if(event.key === 'ArrowLeft') next = tabs[(index - 1 + tabs.length) % tabs.length];
    else if(event.key === 'Home') next = tabs[0];
    else if(event.key === 'End') next = tabs[tabs.length - 1];
    if(!next) return;
    event.preventDefault(); activateTab(next, false); next.focus();
  });
});
activateTab(tabs[0], false);
if(window.innerWidth > 640) input.focus();
</script>
</body>
</html>
"""


def render_dashboard_html(status: dict[str, Any]) -> str:
    """Render the full Claude-Desktop-style dashboard page."""
    slash_json = json.dumps(WEB_SLASH_COMMANDS, ensure_ascii=False)
    return _PAGE.replace("__MAIN__", render_main(status)).replace("__SLASH__", slash_json)
