from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from jenai.cli.main import app
from jenai.state.data_lifecycle import DataPaths

runner = CliRunner()


def _paths(tmp_path: Path) -> DataPaths:
    paths = DataPaths(
        config=tmp_path / "config.toml",
        credentials=tmp_path / ".env",
        locations=tmp_path / "locations.toml",
        sessions=tmp_path / "sessions",
        pending_runs=tmp_path / "pending-runs",
        reports=tmp_path / "reports",
        traces=tmp_path / "traces",
        audit=tmp_path / "audit.sqlite3",
        config_backups=(),
    )
    paths.config.write_text("config stays", encoding="utf-8")
    paths.credentials.write_text("API_KEY=secret-stays", encoding="utf-8")
    paths.locations.write_text("locations stay", encoding="utf-8")
    for directory, filename in (
        (paths.sessions, "session.json"),
        (paths.pending_runs, "pending.json"),
        (paths.reports, "patrol-old.json"),
        (paths.traces, "traces.jsonl"),
    ):
        directory.mkdir()
        (directory / filename).write_text("{}\n", encoding="utf-8")
    return paths


def test_data_status_json_is_read_only(tmp_path: Path, monkeypatch) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr("jenai.cli.data.resolve_data_paths", lambda _config: paths)

    result = runner.invoke(app, ["data", "status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert {row["category"] for row in payload} == {
        "locations",
        "audit",
        "config_backups",
        "sessions",
        "pending_runs",
        "reports",
        "traces",
    }
    assert paths.sessions.exists() and paths.config.exists()


def test_data_purge_dry_run_cancel_and_confirm_are_safe(tmp_path: Path, monkeypatch) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr("jenai.cli.data.resolve_data_paths", lambda _config: paths)

    dry_run = runner.invoke(app, ["data", "purge", "--dry-run"])
    assert dry_run.exit_code == 0
    assert "nothing was deleted" in dry_run.stdout
    assert (
        paths.sessions.exists()
        and paths.pending_runs.exists()
        and paths.reports.exists()
        and paths.traces.exists()
    )

    cancelled = runner.invoke(app, ["data", "purge"], input="n\n")
    assert cancelled.exit_code == 0
    assert "cancelled" in cancelled.stdout
    assert (
        paths.sessions.exists()
        and paths.pending_runs.exists()
        and paths.reports.exists()
        and paths.traces.exists()
    )

    confirmed = runner.invoke(app, ["data", "purge", "--yes"])
    assert confirmed.exit_code == 0
    assert not paths.sessions.exists()
    assert not paths.pending_runs.exists()
    assert not paths.reports.exists()
    assert not paths.traces.exists()
    assert paths.locations.exists()
    assert paths.config.exists()
    assert paths.credentials.exists()


def test_data_prune_dry_run_then_confirm(tmp_path: Path, monkeypatch) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr("jenai.cli.data.resolve_data_paths", lambda _config: paths)
    old = datetime(2020, 1, 1, tzinfo=UTC).timestamp()
    session = paths.sessions / "session.json"
    report = paths.reports / "patrol-old.json"
    os.utime(session, (old, old))
    os.utime(report, (old, old))
    (paths.traces / "traces.jsonl").write_text(
        '{"ts":"2020-01-01T00:00:00+00:00"}\n', encoding="utf-8"
    )

    dry_run = runner.invoke(
        app,
        ["data", "prune", "--older-than-days", "30", "--dry-run"],
    )
    assert dry_run.exit_code == 0
    assert "nothing was deleted" in dry_run.stdout
    assert session.exists() and report.exists()

    confirmed = runner.invoke(
        app,
        ["data", "prune", "--older-than-days", "30", "--yes"],
    )
    assert confirmed.exit_code == 0
    assert not session.exists() and not report.exists()
    assert (paths.traces / "traces.jsonl").read_text(encoding="utf-8") == ""
    assert paths.config.exists() and paths.credentials.exists()
