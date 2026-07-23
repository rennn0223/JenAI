from __future__ import annotations

import json
import os
import sqlite3
import tarfile
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jenai.state import data_lifecycle
from jenai.state.audit import AuditStore
from jenai.state.data_lifecycle import (
    DataPaths,
    data_status,
    export_data,
    find_prune_candidates,
    prune_data,
    purge_targets,
)


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _paths(tmp_path: Path) -> DataPaths:
    return DataPaths(
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


def test_export_is_private_atomic_and_excludes_secrets(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    secret = "secret-value-123"
    paths.config.write_text(f'api_key = "{secret}"\n', encoding="utf-8")
    paths.credentials.write_text(f"NVIDIA_API_KEY={secret}\n", encoding="utf-8")
    paths.locations.write_text(
        '[[locations]]\nname = "Dock"\ndescription = "API_KEY=location-token"\n',
        encoding="utf-8",
    )
    paths.sessions.mkdir()
    (paths.sessions / "session.json").write_text(
        json.dumps({"content": f"I pasted {secret}"}), encoding="utf-8"
    )
    paths.reports.mkdir()
    (paths.reports / "patrol-1.json").write_text('{"ok": true}', encoding="utf-8")
    paths.traces.mkdir()
    (paths.traces / "traces.jsonl").write_text(
        '{"Authorization": "Bearer bearer-secret"}\n', encoding="utf-8"
    )

    output = tmp_path / "backup.tar.gz"
    exported, count = export_data(paths, output)

    assert exported == output
    assert count == 4
    assert _mode(output) == 0o600
    with tarfile.open(output, "r:gz") as archive:
        names = archive.getnames()
        assert "manifest.json" in names
        assert all("config.toml" not in name and ".env" not in name for name in names)
        content = b"\n".join(
            archive.extractfile(member).read() for member in archive.getmembers() if member.isfile()
        )
        assert secret.encode() not in content
        assert b"location-token" not in content
        assert b"bearer-secret" not in content
        assert b"[REDACTED]" in content
        assert all(member.mode == 0o600 for member in archive.getmembers())


def test_export_excludes_unmanaged_json_below_reports(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.reports.mkdir()
    tasks = paths.reports / "tasks"
    tasks.mkdir()
    (paths.reports / "patrol-owned.json").write_text("{}", encoding="utf-8")
    (tasks / "task-owned.json").write_text("{}", encoding="utf-8")
    (paths.reports / "customer-notes.json").write_text("{}", encoding="utf-8")
    archive_dir = paths.reports / "archive"
    archive_dir.mkdir()
    (archive_dir / "task-not-owned.json").write_text("{}", encoding="utf-8")

    output = tmp_path / "backup.tar.gz"
    _exported, count = export_data(paths, output)

    assert count == 2
    with tarfile.open(output, "r:gz") as archive:
        names = set(archive.getnames())
    assert "reports/patrol-owned.json" in names
    assert "reports/tasks/task-owned.json" in names
    assert "reports/customer-notes.json" not in names
    assert "reports/archive/task-not-owned.json" not in names


def test_export_failed_replace_preserves_existing_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    output = tmp_path / "backup.tar.gz"
    output.write_bytes(b"previous archive")

    def fail_replace(_source, _destination) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr("jenai.secure_files.os.replace", fail_replace)
    with pytest.raises(OSError, match="disk unavailable"):
        export_data(paths, output)

    assert output.read_bytes() == b"previous archive"
    assert list(tmp_path.glob(".backup.tar.gz.*.tmp")) == []


def test_status_is_read_only_and_reports_modes(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.locations.write_text("", encoding="utf-8")
    before = set(tmp_path.iterdir())

    rows = {row.category: row for row in data_status(paths)}

    assert set(tmp_path.iterdir()) == before
    assert rows["locations"].exists is True
    assert rows["locations"].files == 1
    assert rows["sessions"].exists is False


def test_purge_plan_protects_locations_config_and_credentials_by_default(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    default = dict(purge_targets(paths))
    assert set(default) == {"sessions", "pending_runs", "reports", "traces", "audit"}
    assert paths.locations not in default.values()
    assert paths.config not in default.values()
    assert paths.credentials not in default.values()

    opted_in = dict(
        purge_targets(
            paths,
            include_locations=True,
            include_config=True,
            include_credentials=True,
        )
    )
    assert set(opted_in) == {
        "sessions",
        "pending_runs",
        "reports",
        "traces",
        "audit",
        "locations",
        "config",
        "credentials",
    }


def test_prune_removes_old_files_and_only_old_trace_rows(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.sessions.mkdir()
    paths.reports.mkdir()
    paths.traces.mkdir()
    old_session = paths.sessions / "old.json"
    old_report = paths.reports / "patrol-old.json"
    recent_report = paths.reports / "patrol-recent.json"
    tasks_dir = paths.reports / "tasks"
    tasks_dir.mkdir()
    old_task_receipt = tasks_dir / "task-old.json"
    unrelated = paths.reports / "customer-notes.json"
    nested = paths.reports / "archive" / "task-not-owned.json"
    nested.parent.mkdir()
    for path in (old_session, old_report, recent_report, old_task_receipt):
        path.write_text("{}", encoding="utf-8")
    unrelated.write_text("{}", encoding="utf-8")
    nested.write_text("{}", encoding="utf-8")
    old_timestamp = datetime(2020, 1, 1, tzinfo=UTC).timestamp()
    recent_timestamp = datetime(2026, 7, 17, tzinfo=UTC).timestamp()
    os.utime(old_session, (old_timestamp, old_timestamp))
    os.utime(old_report, (old_timestamp, old_timestamp))
    os.utime(old_task_receipt, (old_timestamp, old_timestamp))
    os.utime(unrelated, (old_timestamp, old_timestamp))
    os.utime(nested, (old_timestamp, old_timestamp))
    os.utime(recent_report, (recent_timestamp, recent_timestamp))
    trace = paths.traces / "traces.jsonl"
    trace.write_text(
        '{"ts":"2020-01-01T00:00:00+00:00","event":"old"}\n'
        '{"ts":"2026-07-17T00:00:00+00:00","event":"recent"}\n'
        "malformed record is retained\n",
        encoding="utf-8",
    )
    now = datetime(2026, 7, 18, tzinfo=UTC)

    candidates = find_prune_candidates(paths, older_than_days=30, now=now)
    assert {(item.category, item.path.name) for item in candidates} == {
        ("sessions", "old.json"),
        ("reports", "patrol-old.json"),
        ("reports", "task-old.json"),
        ("traces", "traces.jsonl"),
    }
    assert next(item for item in candidates if item.category == "traces").stale_records == 1

    files, records = prune_data(candidates, older_than_days=30, now=now)
    assert (files, records) == (3, 1)
    assert not old_session.exists() and not old_report.exists()
    assert not old_task_receipt.exists()
    assert recent_report.exists()
    assert unrelated.exists()
    assert nested.exists()
    assert "old" not in trace.read_text(encoding="utf-8")
    assert "recent" in trace.read_text(encoding="utf-8")
    assert "malformed" in trace.read_text(encoding="utf-8")
    assert _mode(trace) == 0o600
    assert _mode(paths.traces) == 0o700


def test_pending_audit_and_config_backups_have_explicit_lifecycle(tmp_path: Path) -> None:
    backup = tmp_path / "config.toml.bak-20260718"
    backup.write_text("historic config", encoding="utf-8")
    paths = replace(_paths(tmp_path), config_backups=(backup,))

    paths.pending_runs.mkdir()
    pending = paths.pending_runs / "pending.json"
    pending.write_text('{"task":"private"}', encoding="utf-8")
    old = datetime(2020, 1, 1, tzinfo=UTC).timestamp()
    os.utime(pending, (old, old))

    audit = AuditStore(paths.audit)
    old_id = audit.record("old")
    recent_id = audit.record("recent")
    with closing(sqlite3.connect(paths.audit)) as connection, connection:
        connection.execute(
            "UPDATE audit_events SET occurred_at = ? WHERE event_id = ?",
            ("2020-01-01T00:00:00+00:00", old_id),
        )
        connection.execute(
            "UPDATE audit_events SET occurred_at = ? WHERE event_id = ?",
            ("2026-07-17T00:00:00+00:00", recent_id),
        )

    rows = {row.category: row for row in data_status(paths)}
    assert rows["pending_runs"].files == 1
    assert rows["audit"].files == 1
    assert rows["config_backups"].files == 1

    output = tmp_path / "managed.tar.gz"
    export_data(paths, output)
    with tarfile.open(output, "r:gz") as archive:
        names = set(archive.getnames())
    assert "pending_runs/pending.json" in names
    assert "audit/audit.sqlite3" in names
    assert all("bak-" not in name for name in names)

    candidates = find_prune_candidates(
        paths,
        older_than_days=30,
        now=datetime(2026, 7, 18, tzinfo=UTC),
    )
    assert ("pending_runs", pending) in {
        (candidate.category, candidate.path) for candidate in candidates
    }
    assert next(item for item in candidates if item.category == "audit").stale_records == 1
    files, records = prune_data(
        candidates,
        older_than_days=30,
        now=datetime(2026, 7, 18, tzinfo=UTC),
    )
    assert files == 1 and records == 1
    assert [event.event_id for event in audit.list_events(limit=10)] == [recent_id]

    default_targets = purge_targets(paths)
    assert backup not in dict(default_targets).values()
    all_targets = purge_targets(paths, include_config_backups=True)
    assert ("config_backup", backup) in all_targets


def test_audit_prune_closes_read_and_write_connections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    audit = AuditStore(paths.audit)
    old_id = audit.record("old")
    with closing(sqlite3.connect(paths.audit)) as connection, connection:
        connection.execute(
            "UPDATE audit_events SET occurred_at = ? WHERE event_id = ?",
            ("2020-01-01T00:00:00+00:00", old_id),
        )

    class TrackingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection
            self.closed = False

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def execute(self, *args, **kwargs):
            return self.connection.execute(*args, **kwargs)

        def executemany(self, *args, **kwargs):
            return self.connection.executemany(*args, **kwargs)

        def close(self) -> None:
            self.closed = True
            self.connection.close()

    real_connect = sqlite3.connect
    opened: list[TrackingConnection] = []

    def tracked_connect(*args, **kwargs):
        connection = TrackingConnection(real_connect(*args, **kwargs))
        opened.append(connection)
        return connection

    monkeypatch.setattr(data_lifecycle.sqlite3, "connect", tracked_connect)
    now = datetime(2026, 7, 18, tzinfo=UTC)

    candidates = find_prune_candidates(paths, older_than_days=30, now=now)
    files, records = prune_data(candidates, older_than_days=30, now=now)

    assert (files, records) == (0, 1)
    assert len(opened) == 2
    assert all(connection.closed for connection in opened)
