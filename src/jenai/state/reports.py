"""Patrol logs on disk + rendered reports (V1_GATE A8 / C2).

Every finished patrol is persisted as one JSON file under
``<config dir>/reports/``; ``/report`` renders the latest into a
deterministic markdown section and, when a provider is reachable, asks the
chat model for a short digest paragraph — degrading honestly to the
deterministic section alone when the LLM is unavailable.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from jenai.config.models import AppConfig
from jenai.providers.chat import ProviderChatError, ask_provider
from jenai.secure_files import atomic_write_text
from jenai.tools.skills import PatrolReport

_SUMMARY_PROMPT = (
    "你是巡邏機器人的值班紀錄員。根據以下巡邏 log(JSON),寫一段 3–5 句的中文日報:"
    "涵蓋路線、成功率、每個異常點(失敗或值得注意的觀察)。"
    "只根據 log 內容,不要編造;沒有異常就照實說沒有異常。\n\n"
)


def reports_dir(config_path: Path) -> Path:
    return config_path.parent / "reports"


def save_patrol_log(
    report: PatrolReport, config_path: Path, *, now: datetime | None = None
) -> Path:
    """Persist one finished patrol as reports/patrol-YYYYmmdd-HHMMSS.json."""
    moment = now or datetime.now().astimezone()
    stamp = moment.strftime("%Y%m%d-%H%M%S")
    payload = {
        "kind": "patrol",
        "saved_at": moment.isoformat(),
        "route": report.spec.points,
        "loops": report.spec.loops,
        "photo": report.spec.photo,
        "summary": report.summary,
        "results": [
            {
                "loop": r.loop,
                "point": r.point,
                "status": r.status,
                "detail": r.detail,
                "observation": r.observation,
            }
            for r in report.results
        ],
    }
    directory = reports_dir(config_path)
    path = directory / f"patrol-{stamp}.json"
    return atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2),
        harden_parent=True,
    )


def list_patrol_logs(config_path: Path) -> list[Path]:
    """Newest first; missing directory is just 'no logs yet'."""
    directory = reports_dir(config_path)
    if not directory.is_dir():
        return []
    return sorted(directory.glob("patrol-*.json"), reverse=True)


def load_patrol_log(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def render_patrol_markdown(log: dict) -> str:
    """Deterministic report body — correct with or without an LLM."""
    lines = [
        f"時間:{log.get('saved_at', '?')}",
        f"路線:{' → '.join(log.get('route', []))}"
        + (f" ×{log['loops']}" if log.get("loops", 1) > 1 else ""),
        f"結果:{log.get('summary', '?')}",
        "",
    ]
    for r in log.get("results", []):
        mark = "✓" if r.get("status") == "succeeded" else "✗"
        loop_tag = f"(loop {r['loop']}) " if log.get("loops", 1) > 1 else ""
        lines.append(f"{mark} {loop_tag}{r.get('point')}: {r.get('status')} — {r.get('detail')}")
        if r.get("observation"):
            lines.append(f"   👁 {r['observation']}")
    return "\n".join(lines)


async def summarize_patrol(config: AppConfig, log: dict) -> str | None:
    """One digest paragraph from the chat model; None when unavailable —
    the caller must show the deterministic body either way."""
    try:
        response = await ask_provider(
            config, _SUMMARY_PROMPT + json.dumps(log, ensure_ascii=False)
        )
    except ProviderChatError:
        return None
    return response.content.strip() or None
