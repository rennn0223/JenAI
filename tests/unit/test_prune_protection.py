import os
from datetime import UTC, datetime
from pathlib import Path

from jenai.state.data_lifecycle import DataPaths, find_prune_candidates


def test_prune_never_selects_credentials_config_or_locations(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    reports = tmp_path / "reports"
    traces = tmp_path / "traces"
    sessions.mkdir()
    reports.mkdir()
    traces.mkdir()
    config = sessions / "config.json"
    credentials = sessions / "credentials.json"
    locations = reports / "patrol-locations.json"
    generated = sessions / "generated.json"
    for path in (config, credentials, locations, generated):
        path.write_text("{}", encoding="utf-8")
        old = datetime(2020, 1, 1, tzinfo=UTC).timestamp()
        os.utime(path, (old, old))
    paths = DataPaths(
        config=config,
        credentials=credentials,
        locations=locations,
        sessions=sessions,
        pending_runs=tmp_path / "pending-runs",
        reports=reports,
        traces=traces,

        audit=tmp_path / "audit.sqlite3",

        config_backups=(),
    )

    candidates = find_prune_candidates(
        paths,
        older_than_days=30,
        now=datetime(2026, 7, 18, tzinfo=UTC),
    )

    assert [candidate.path for candidate in candidates] == [generated]
