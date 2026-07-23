from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenai.adapters.locations import save_locations
from jenai.agent.session import JenAIFileSession
from jenai.agent.tracing import FileTracingProcessor
from jenai.schemas import Location, Pose2D
from jenai.state.reports import save_patrol_log
from jenai.tools.skills import PatrolReport, PatrolSpec


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _location(name: str = "Dock") -> Location:
    return Location(name=name, frame_id="map", pose=Pose2D(x=1, y=2, yaw=0))


def test_session_directory_lock_and_data_are_private(tmp_path: Path) -> None:
    directory = tmp_path / "nested" / "sessions"
    session = JenAIFileSession("private", directory=directory)
    asyncio.run(session.add_items([{"role": "user", "content": "private"}]))

    assert _mode(tmp_path / "nested") == 0o700
    assert _mode(directory) == 0o700
    assert _mode(directory / "private.json") == 0o600
    assert _mode(directory / "private.json.lock") == 0o600


def test_session_failed_replace_preserves_previous_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = JenAIFileSession("atomic", directory=tmp_path)
    asyncio.run(session.add_items([{"content": "keep"}]))
    path = tmp_path / "atomic.json"
    original = path.read_bytes()

    def fail_replace(_source, _destination) -> None:
        raise OSError("simulated failure")

    monkeypatch.setattr("jenai.secure_files.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated failure"):
        asyncio.run(session.add_items([{"content": "do not publish"}]))

    assert path.read_bytes() == original
    assert list(tmp_path.glob(".atomic.json.*.tmp")) == []


def test_locations_are_private_and_failed_replace_keeps_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "private" / "locations.toml"
    save_locations([_location()], path)
    original = path.read_bytes()
    assert _mode(path.parent) == 0o700
    assert _mode(path) == 0o600

    def fail_replace(_source, _destination) -> None:
        raise OSError("simulated failure")

    monkeypatch.setattr("jenai.secure_files.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated failure"):
        save_locations([_location("Changed")], path)
    assert path.read_bytes() == original


def test_report_directory_and_file_are_private(tmp_path: Path) -> None:
    report = PatrolReport(spec=PatrolSpec(points=["Dock"], loops=1, photo=False))
    path = save_patrol_log(
        report,
        tmp_path / "config.toml",
        now=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
    )
    assert _mode(path.parent) == 0o700
    assert _mode(path) == 0o600


def test_trace_append_is_private_and_failure_does_not_truncate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "private" / "traces.jsonl"
    processor = FileTracingProcessor(path)
    processor.on_trace_start(SimpleNamespace(trace_id="t1", name="first"))
    original = path.read_bytes()
    assert _mode(path.parent) == 0o700
    assert _mode(path) == 0o600

    def fail_write(_fd, _payload) -> None:
        raise OSError("simulated append failure")

    monkeypatch.setattr("jenai.agent.tracing.write_all", fail_write)
    processor.on_trace_end(SimpleNamespace(trace_id="t1"))
    assert path.read_bytes() == original
