from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from jenai.state.data_hardening import apply_hardening, build_hardening_plan
from jenai.state.data_lifecycle import DataPaths, data_status


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


def test_status_detects_insecure_child_below_private_directory(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.sessions.mkdir(mode=0o700)
    child = paths.sessions / "legacy.json"
    child.write_text("[]", encoding="utf-8")
    os.chmod(paths.sessions, 0o700)
    os.chmod(child, 0o644)

    row = next(item for item in data_status(paths) if item.category == "sessions")

    assert row.mode == "0700"
    assert row.files == 1
    assert row.insecure == 1
    assert row.refused == 0
    assert row.permissions_ok is False
    assert _mode(child) == 0o644  # status is strictly read-only


def test_plan_and_apply_harden_legacy_directory_and_files_only(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.config.write_text("config", encoding="utf-8")
    paths.credentials.write_text("API_KEY=secret", encoding="utf-8")
    paths.sessions.mkdir()
    child = paths.sessions / "legacy.json"
    child.write_text("[]", encoding="utf-8")
    os.chmod(paths.config, 0o644)
    os.chmod(paths.credentials, 0o644)
    os.chmod(paths.sessions, 0o775)
    os.chmod(child, 0o664)

    plan = build_hardening_plan(paths)
    assert {(item.path, item.target_mode) for item in plan.candidates} == {
        (paths.sessions, 0o700),
        (child, 0o600),
    }

    result = apply_hardening(plan)

    assert (result.hardened, result.skipped) == (2, 0)
    assert _mode(paths.sessions) == 0o700
    assert _mode(child) == 0o600
    assert _mode(paths.config) == 0o644
    assert _mode(paths.credentials) == 0o644


def test_harden_refuses_symlink_and_hardlink_aliases(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.config.write_text("config", encoding="utf-8")
    paths.sessions.mkdir(mode=0o700)
    hardlink = paths.sessions / "hardlink.json"
    os.link(paths.config, hardlink)
    symlink = paths.sessions / "symlink.json"
    symlink.symlink_to(paths.config)
    backup_hardlink = tmp_path / "config.toml.bak-hardlink"
    os.link(paths.config, backup_hardlink)
    backup_symlink = tmp_path / "config.toml.bak-symlink"
    backup_symlink.symlink_to(paths.config)
    paths = replace(paths, config_backups=(backup_hardlink, backup_symlink))
    os.chmod(paths.config, 0o644)
    os.chmod(paths.sessions, 0o700)

    plan = build_hardening_plan(paths)

    reasons = {item.path: item.reason for item in plan.refusals}
    assert "config/credential" in reasons[hardlink]
    assert reasons[symlink] == "symlink refused"
    assert "config/credential" in reasons[backup_hardlink]
    assert reasons[backup_symlink] == "symlink refused"
    refused = {hardlink, symlink, backup_hardlink, backup_symlink}
    assert all(item.path not in refused for item in plan.candidates)
    assert _mode(paths.config) == 0o644


def test_apply_revalidates_inode_if_candidate_is_swapped_to_symlink(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    paths.config.write_text("config", encoding="utf-8")
    os.chmod(paths.config, 0o644)
    paths.sessions.mkdir(mode=0o700)
    child = paths.sessions / "legacy.json"
    child.write_text("[]", encoding="utf-8")
    os.chmod(paths.sessions, 0o700)
    os.chmod(child, 0o644)
    plan = build_hardening_plan(paths)
    assert [item.path for item in plan.candidates] == [child]

    child.unlink()
    child.symlink_to(paths.config)
    result = apply_hardening(plan)

    assert (result.hardened, result.skipped) == (0, 1)
    assert _mode(paths.config) == 0o644
