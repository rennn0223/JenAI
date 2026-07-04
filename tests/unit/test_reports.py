from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from jenai.config.store import build_minimal_config
from jenai.state.reports import (
    list_patrol_logs,
    load_patrol_log,
    render_patrol_markdown,
    save_patrol_log,
    summarize_patrol,
)
from jenai.tools.skills import PatrolReport, PatrolSpec, PatrolStepResult


def _report() -> PatrolReport:
    report = PatrolReport(spec=PatrolSpec(points=["A", "B"], loops=2, photo=True))
    report.results = [
        PatrolStepResult(1, "A", "succeeded", "reached", observation="path clear"),
        PatrolStepResult(1, "B", "failed", "Nav2 aborted"),
        PatrolStepResult(2, "A", "succeeded", "reached"),
        PatrolStepResult(2, "B", "succeeded", "reached"),
    ]
    return report


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    path = save_patrol_log(_report(), config_path, now=datetime(2026, 7, 4, 12, 0))
    assert path.parent == tmp_path / "reports"
    log = load_patrol_log(path)
    assert log["route"] == ["A", "B"]
    assert log["loops"] == 2
    assert log["summary"] == "Patrol finished: 3/4 waypoints reached."
    assert log["results"][1]["status"] == "failed"
    assert log["results"][0]["observation"] == "path clear"


def test_list_logs_newest_first_and_empty_dir(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    assert list_patrol_logs(config_path) == []  # no dir yet — honest empty
    save_patrol_log(_report(), config_path, now=datetime(2026, 7, 4, 8, 0))
    save_patrol_log(_report(), config_path, now=datetime(2026, 7, 4, 9, 0))
    logs = list_patrol_logs(config_path)
    assert [p.name for p in logs] == ["patrol-20260704-090000.json", "patrol-20260704-080000.json"]


def test_load_rejects_corrupt_log(tmp_path: Path) -> None:
    bad = tmp_path / "patrol-x.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_patrol_log(bad) is None
    bad.write_text('"a bare string"', encoding="utf-8")
    assert load_patrol_log(bad) is None


def test_render_markdown_is_honest_about_failures(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    path = save_patrol_log(_report(), config_path, now=datetime(2026, 7, 4, 12, 0))
    body = render_patrol_markdown(load_patrol_log(path))
    assert "A → B ×2" in body
    assert "3/4" in body
    assert "✗ (loop 1) B: failed — Nav2 aborted" in body
    assert "👁 path clear" in body


def test_summarize_uses_provider_and_degrades_honestly(monkeypatch, tmp_path: Path) -> None:
    import jenai.state.reports as reports_module
    from jenai.providers.chat import ProviderChatError

    config = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )
    log = {"route": ["A"], "results": []}

    async def ok_provider(cfg, prompt, **kwargs):
        assert json.dumps(log, ensure_ascii=False) in prompt
        return SimpleNamespace(content="  巡邏完成,無異常。  ")

    async def dead_provider(cfg, prompt, **kwargs):
        raise ProviderChatError("provider offline")

    import asyncio

    monkeypatch.setattr(reports_module, "ask_provider", ok_provider)
    assert asyncio.run(summarize_patrol(config, log)) == "巡邏完成,無異常。"

    monkeypatch.setattr(reports_module, "ask_provider", dead_provider)
    assert asyncio.run(summarize_patrol(config, log)) is None
