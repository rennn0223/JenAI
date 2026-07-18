import os
from pathlib import Path

from typer.testing import CliRunner

from jenai.cli.main import app
from jenai.state.data_lifecycle import DataPaths


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_data_harden_dry_run_cancel_then_yes(tmp_path: Path, monkeypatch) -> None:
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
    paths.config.write_text("config", encoding="utf-8")
    paths.credentials.write_text("API_KEY=secret", encoding="utf-8")
    paths.traces.mkdir()
    trace = paths.traces / "traces.jsonl"
    trace.write_text("{}\n", encoding="utf-8")
    os.chmod(paths.config, 0o644)
    os.chmod(paths.credentials, 0o644)
    os.chmod(paths.traces, 0o775)
    os.chmod(trace, 0o664)
    monkeypatch.setattr("jenai.cli.data.resolve_data_paths", lambda _config: paths)
    runner = CliRunner()

    dry_run = runner.invoke(app, ["data", "harden", "--dry-run"])
    assert dry_run.exit_code == 0
    assert "0775 -> 0700" in dry_run.stdout
    assert "0664 -> 0600" in dry_run.stdout
    assert "no permissions were changed" in dry_run.stdout
    assert (_mode(paths.traces), _mode(trace)) == (0o775, 0o664)

    cancelled = runner.invoke(app, ["data", "harden"], input="n\n")
    assert cancelled.exit_code == 0
    assert "cancelled" in cancelled.stdout
    assert (_mode(paths.traces), _mode(trace)) == (0o775, 0o664)

    applied = runner.invoke(app, ["data", "harden", "--yes"])
    assert applied.exit_code == 0
    assert "Hardened 2 path(s)" in applied.stdout
    assert (_mode(paths.traces), _mode(trace)) == (0o700, 0o600)
    assert _mode(paths.config) == 0o644
    assert _mode(paths.credentials) == 0o644


def test_data_status_json_exposes_insecure_child_count(tmp_path: Path, monkeypatch) -> None:
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
    paths.sessions.mkdir(mode=0o700)
    child = paths.sessions / "legacy.json"
    child.write_text("[]", encoding="utf-8")
    os.chmod(paths.sessions, 0o700)
    os.chmod(child, 0o644)
    monkeypatch.setattr("jenai.cli.data.resolve_data_paths", lambda _config: paths)

    result = CliRunner().invoke(app, ["data", "status", "--json"])

    assert result.exit_code == 0
    assert '"insecure": 1' in result.stdout
    assert '"permissions_ok": false' in result.stdout
    assert _mode(child) == 0o644
