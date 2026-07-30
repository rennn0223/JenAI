"""HTML projection for the WebUI's task monitoring card."""

from __future__ import annotations

import html

from jenai.webui.presentation import WebApprovalView, WebRunView, WebStatusView


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _run_state(run: WebRunView) -> str:
    if run.outcome_label:
        return f"{run.status_label} · {run.outcome_label}"
    return run.status_label


def _current_run(view: WebStatusView) -> str:
    run = view.current_run
    if run is None:
        return (
            '<div class="monitor-empty">目前沒有任務。從「互動」頁送出任務後，'
            "這裡會顯示執行狀態與結果。</div>"
        )
    result = f'<p class="monitor-result">{_esc(run.final_output)}</p>' if run.final_output else ""
    return (
        '<div class="monitor-current">'
        f'<span class="monitor-state state-{_esc(run.status)}">{_esc(_run_state(run))}</span>'
        f"<h3>{_esc(run.summary)}</h3>"
        f'<p class="monitor-id">{_esc(run.run_id)}</p>'
        f"{result}</div>"
    )


def _approval_actions(item: WebApprovalView) -> str:
    if item.confirm_id is None or not item.preview_complete:
        return (
            f'<span class="monitor-state state-awaiting_approval">{_esc(item.status_label)}</span>'
        )
    confirm_id = _esc(item.confirm_id)
    return (
        f'<div class="monitor-actions" data-confirm-id="{confirm_id}">'
        f'<button type="button" class="btn-cancel monitor-reject" '
        f'data-confirm-action="reject" data-confirm-id="{confirm_id}">取消</button>'
        f'<button type="button" class="btn-approve monitor-approve" '
        f'data-confirm-action="confirm" data-confirm-id="{confirm_id}">批准一次</button>'
        "</div>"
    )


def _approval_parameters(item: WebApprovalView) -> str:
    if not item.preview_complete:
        return '<p class="monitor-warning">批准內容無法完整重建，請重新送出指令。</p>'
    return (
        '<dl class="approval-parameters">'
        + "".join(
            f'<div data-approval-parameter="{_esc(parameter.label)}">'
            f"<dt>{_esc(parameter.label)}</dt><dd><span>{_esc(parameter.value)}</span></dd></div>"
            for parameter in item.parameters
        )
        + "</dl>"
    )


def _approval_queue(view: WebStatusView) -> str:
    pending = view.pending_approvals
    if not pending:
        return '<div class="monitor-empty">目前沒有待批准項目。</div>'
    return (
        '<div class="monitor-list">'
        + "".join(
            '<div class="monitor-item">'
            f"<div><strong>{_esc(item.title)}</strong>"
            f"<span>{_esc(item.summary or item.tool_name)}</span>"
            + _approval_parameters(item)
            + "</div>"
            + _approval_actions(item)
            + "</div>"
            for item in pending
        )
        + "</div>"
    )


def _tool_timeline(view: WebStatusView) -> str:
    run = view.current_run
    calls = run.tool_calls if run is not None else ()
    if not calls:
        return '<div class="monitor-empty">這項任務目前沒有工具呼叫紀錄。</div>'
    return (
        '<div class="monitor-list">'
        + "".join(
            '<div class="monitor-item">'
            f"<div><strong>{_esc(call.tool_name)}</strong>"
            f"<span>{_esc(call.output_summary or call.input_summary or '尚無摘要')}</span></div>"
            f'<span class="monitor-state state-{_esc(call.status)}">'
            f"{_esc(call.status_label)}"
            "</span>"
            "</div>"
            for call in calls
        )
        + "</div>"
    )


def _session_history(view: WebStatusView) -> str:
    if not view.runs:
        return '<div class="monitor-empty">本工作階段尚無任務紀錄。</div>'
    return (
        '<div class="monitor-list">'
        + "".join(
            '<div class="monitor-item">'
            f"<div><strong>{_esc(run.summary)}</strong>"
            f"<span>{_esc(run.final_output or run.run_id)}</span></div>"
            f'<span class="monitor-state state-{_esc(run.status)}">{_esc(_run_state(run))}</span>'
            "</div>"
            for run in reversed(view.runs[-8:])
        )
        + "</div>"
    )


def render_monitoring(view: WebStatusView) -> str:
    """Render current task, approvals, tools, and refreshable session history."""
    return (
        '<section class="card monitoring-card">'
        '<div class="card-head"><h2>任務監控</h2>'
        f'<div class="head-right"><span class="count">{len(view.runs)}</span> 筆任務</div></div>'
        '<div class="monitor-grid">'
        '<section class="monitor-panel monitor-primary"><h3 class="monitor-title">目前任務</h3>'
        f"{_current_run(view)}</section>"
        '<section class="monitor-panel"><h3 class="monitor-title">待批准</h3>'
        f"{_approval_queue(view)}</section>"
        '<section class="monitor-panel"><h3 class="monitor-title">工具時間軸</h3>'
        f"{_tool_timeline(view)}</section>"
        '<section class="monitor-panel"><h3 class="monitor-title">本工作階段紀錄</h3>'
        f"{_session_history(view)}</section>"
        "</div>"
        '<p class="monitor-note">此處顯示目前 WebUI 服務程序可觀察的執行紀錄；'
        "重新啟動服務後，請以 task receipt 與 audit 紀錄為準。</p>"
        "</section>"
    )
