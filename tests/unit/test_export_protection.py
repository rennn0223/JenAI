import os
import tarfile
from pathlib import Path

import pytest

from jenai.state.data_lifecycle import DataPaths, export_data


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


def test_export_refuses_to_overwrite_config_or_credentials(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.config.write_text("config must survive", encoding="utf-8")
    paths.credentials.write_text("API_KEY=must-survive", encoding="utf-8")

    with pytest.raises(ValueError, match="overlaps protected"):
        export_data(paths, paths.config)
    with pytest.raises(ValueError, match="overlaps protected"):
        export_data(paths, paths.credentials)

    assert paths.config.read_text(encoding="utf-8") == "config must survive"
    assert paths.credentials.read_text(encoding="utf-8") == "API_KEY=must-survive"


def test_export_skips_symlink_and_hardlink_to_config(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    secret = "config-only-secret"
    paths.config.write_text(secret, encoding="utf-8")
    paths.sessions.mkdir()
    os.link(paths.config, paths.sessions / "hardlink.json")
    (paths.sessions / "symlink.json").symlink_to(paths.config)
    output = tmp_path / "export.tar.gz"

    _, count = export_data(paths, output)

    assert count == 0
    with tarfile.open(output, "r:gz") as archive:
        content = b"".join(
            archive.extractfile(member).read() for member in archive.getmembers() if member.isfile()
        )
    assert secret.encode() not in content
