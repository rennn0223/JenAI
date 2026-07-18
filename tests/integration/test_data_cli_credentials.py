from pathlib import Path

from typer.testing import CliRunner

from jenai.cli.main import app
from jenai.state.data_lifecycle import DataPaths


def test_credentials_require_their_own_purge_flag(tmp_path: Path, monkeypatch) -> None:
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
    paths.config.write_text("keep config", encoding="utf-8")
    paths.credentials.write_text("API_KEY=delete-only-with-flag", encoding="utf-8")
    monkeypatch.setattr("jenai.cli.data.resolve_data_paths", lambda _config: paths)
    runner = CliRunner()

    default = runner.invoke(app, ["data", "purge", "--yes"])
    assert default.exit_code == 0
    assert paths.config.exists() and paths.credentials.exists()

    explicit = runner.invoke(
        app,
        ["data", "purge", "--include-credentials", "--yes"],
    )
    assert explicit.exit_code == 0
    assert paths.config.exists()
    assert not paths.credentials.exists()
