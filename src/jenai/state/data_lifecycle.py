"""Inventory, export, retention and deletion for JenAI's local user data.

Credential stores and the application config are deliberately outside the
default managed-data set.  They are exposed only as separately named purge
targets so a routine cleanup cannot remove the ability to start JenAI.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import sqlite3
import stat
import tarfile
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jenai.agent.session import _sessions_dir
from jenai.agent.tracing import _traces_path
from jenai.config import ConfigError, load_config
from jenai.secure_files import PRIVATE_FILE_MODE, atomic_output_path, atomic_write_text
from jenai.state.data_hardening import build_hardening_plan
from jenai.state.reports import reports_dir


@dataclass(frozen=True)
class DataPaths:
    config: Path
    credentials: Path
    locations: Path
    sessions: Path
    pending_runs: Path
    reports: Path
    traces: Path
    audit: Path
    config_backups: tuple[Path, ...]


@dataclass(frozen=True)
class DataStatus:
    category: str
    path: Path
    exists: bool
    files: int
    bytes: int
    mode: str
    insecure: int
    refused: int
    permissions_ok: bool


@dataclass(frozen=True)
class PruneCandidate:
    category: str
    path: Path
    stale_records: int = 0


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)"
    r"[\"']?\s*[:=]\s*[\"']?)([^\"'\s,}]+)"
)
_BEARER = re.compile(r"(?i)([\"']?authorization[\"']?\s*:\s*[\"']?\s*bearer\s+)([^\s\"']+)")


def resolve_data_paths(config_path: Path) -> DataPaths:
    """Resolve managed data without requiring a healthy provider or ROS stack."""
    config_path = config_path.expanduser().absolute()
    try:
        loaded = load_config(config_path)
        locations = loaded.resolved_locations_path(config_path)
    except ConfigError:
        locations = None
    location_path = locations or config_path.parent / "locations.toml"
    env_override = os.environ.get("JENAI_ENV_FILE")
    credentials = (
        Path(env_override).expanduser().absolute() if env_override else config_path.parent / ".env"
    )
    try:
        config_backups = tuple(
            sorted(
                candidate.absolute()
                for candidate in config_path.parent.glob(f"{config_path.name}.bak-*")
            )
        )
    except OSError:
        config_backups = ()
    return DataPaths(
        config=config_path,
        credentials=credentials,
        locations=location_path.expanduser().absolute(),
        sessions=_sessions_dir().expanduser().absolute(),
        pending_runs=(config_path.parent / "pending-runs").absolute(),
        reports=reports_dir(config_path).expanduser().absolute(),
        traces=_traces_path().parent.expanduser().absolute(),
        audit=(config_path.parent / "audit.sqlite3").absolute(),
        config_backups=config_backups,
    )


def data_status(paths: DataPaths) -> list[DataStatus]:
    """Return a read-only inventory including child permission drift."""
    plan = build_hardening_plan(paths)
    return [
        DataStatus(
            category=audit.category,
            path=audit.path,
            exists=audit.exists,
            files=audit.files,
            bytes=audit.bytes,
            mode=audit.mode,
            insecure=audit.insecure,
            refused=audit.refused,
            permissions_ok=audit.permissions_ok,
        )
        for audit in plan.audits
    ]


def export_data(paths: DataPaths, destination: Path) -> tuple[Path, int]:
    """Write a private tar.gz containing operational data, never config/.env.

    Only allow-listed regular files are copied. Known credential values and
    common credential assignments are redacted from text payloads as defense in
    depth, including if a token was accidentally pasted into a conversation.
    """
    destination = destination.expanduser().absolute()
    candidates = list(_export_files(paths))
    protected_paths = {
        paths.config.absolute(),
        paths.credentials.absolute(),
        *(backup.absolute() for backup in paths.config_backups),
    }
    source_paths = {source.absolute() for _category, source, _relative in candidates}
    if destination in protected_paths or destination in source_paths:
        raise ValueError("Export destination overlaps protected or managed JenAI data")

    secret_values = _known_secret_values(paths.credentials)
    protected_identities = _file_identities(
        paths.config,
        paths.credentials,
        *paths.config_backups,
    )
    exported_count = 0
    included: set[str] = set()
    with atomic_output_path(destination) as temporary:
        with tarfile.open(temporary, mode="w:gz") as archive:
            for category, source, relative in candidates:
                payload = _read_regular_file(source, protected_identities)
                if payload is None:
                    continue
                payload = _redact(payload, secret_values)
                _add_bytes(archive, f"{category}/{relative.as_posix()}", payload)
                exported_count += 1
                included.add(category)
            manifest = {
                "schema_version": 1,
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "includes": sorted(included),
                "excludes": [
                    "config",
                    "credentials",
                    "config backups",
                    "API keys and secret values",
                ],
                "redaction": "known secret values and credential assignments",
            }
            _add_bytes(
                archive,
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2).encode(),
            )
    return destination, exported_count


def purge_targets(
    paths: DataPaths,
    *,
    include_locations: bool = False,
    include_config: bool = False,
    include_credentials: bool = False,
    include_config_backups: bool = False,
) -> list[tuple[str, Path]]:
    """Build the explicit purge plan; config and credentials are opt-in only."""
    targets: list[tuple[str, Path]] = [
        ("sessions", paths.sessions),
        ("pending_runs", paths.pending_runs),
        ("reports", paths.reports),
        ("traces", paths.traces),
        ("audit", paths.audit),
    ]
    targets.extend(
        ("audit_sidecar", sidecar)
        for sidecar in _audit_sidecars(paths.audit)
        if sidecar.exists() or sidecar.is_symlink()
    )
    if (
        include_locations
        and (not _same_path(paths.locations, paths.config) or include_config)
        and (not _same_path(paths.locations, paths.credentials) or include_credentials)
    ):
        targets.append(("locations", paths.locations))
    if include_config:
        targets.append(("config", paths.config))
    if include_credentials:
        targets.append(("credentials", paths.credentials))
    if include_config_backups:
        targets.extend(("config_backup", backup) for backup in paths.config_backups)

    deduplicated: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for category, path in targets:
        key = path.absolute()
        if key not in seen:
            seen.add(key)
            deduplicated.append((category, path))
    return deduplicated


def purge_data(
    targets: list[tuple[str, Path]],
    *,
    protected_paths: tuple[Path, ...] = (),
) -> list[tuple[str, Path]]:
    """Remove confirmed targets while preserving every non-opted-in path.

    Protection is enforced again during deletion (not only in the CLI plan),
    including when a credential or locations file sits inside a data directory.
    File-shaped categories are never recursively removed if their configured
    path unexpectedly resolves to a directory.
    """
    protected = tuple(path.absolute() for path in protected_paths)
    directory_categories = {"sessions", "pending_runs", "reports", "traces"}
    removed: list[tuple[str, Path]] = []
    for category, path in targets:
        if _is_protected(path, protected):
            continue
        if path.is_symlink():
            if _contains_protected(path, protected):
                continue
            path.unlink(missing_ok=True)
            removed.append((category, path))
        elif category in directory_categories and path.is_dir():
            _remove_tree_preserving(path, protected)
            if not path.exists():
                removed.append((category, path))
        elif path.is_file():
            path.unlink()
            removed.append((category, path))
    return removed


def find_prune_candidates(
    paths: DataPaths,
    *,
    older_than_days: int,
    now: datetime | None = None,
) -> list[PruneCandidate]:
    """Find session/report files and trace records older than the retention age."""
    if older_than_days < 1:
        raise ValueError("older_than_days must be at least 1")
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    cutoff = moment - timedelta(days=older_than_days)
    protected = {
        paths.config.absolute(),
        paths.credentials.absolute(),
        paths.locations.absolute(),
        *(backup.absolute() for backup in paths.config_backups),
    }
    candidates: list[PruneCandidate] = []
    for category, directory, pattern in (
        ("sessions", paths.sessions, "*.json"),
        ("pending_runs", paths.pending_runs, "*.json"),
        ("reports", paths.reports, "patrol-*.json"),
    ):
        for path in _safe_glob(directory, pattern):
            if path.absolute() in protected:
                continue
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            except OSError:
                continue
            if modified < cutoff:
                candidates.append(PruneCandidate(category, path))

    for path in _safe_glob(paths.traces, "*.jsonl"):
        if path.absolute() in protected:
            continue
        stale = _count_stale_trace_records(path, cutoff)
        if stale:
            candidates.append(PruneCandidate("traces", path, stale_records=stale))

    if paths.audit.absolute() not in protected:
        stale_audit = _count_stale_audit_records(paths.audit, cutoff)
        if stale_audit:
            candidates.append(
                PruneCandidate("audit", paths.audit, stale_records=stale_audit)
            )
    return candidates


def prune_data(
    candidates: list[PruneCandidate],
    *,
    older_than_days: int,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Apply a confirmed retention plan; return removed files and trace rows."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    cutoff = moment - timedelta(days=older_than_days)
    removed_files = 0
    removed_records = 0
    for candidate in candidates:
        if candidate.category == "audit":
            removed_records += _prune_audit_records(candidate.path, cutoff)
            continue
        if candidate.category != "traces":
            if candidate.path.is_file() and not candidate.path.is_symlink():
                candidate.path.unlink()
                removed_files += 1
            continue
        kept, removed = _filter_trace_records(candidate.path, cutoff)
        if removed:
            atomic_write_text(
                candidate.path,
                "".join(kept),
                harden_parent=True,
            )
            removed_records += removed
    return removed_files, removed_records


def _export_files(paths: DataPaths):
    protected = {
        paths.config.absolute(),
        paths.credentials.absolute(),
        *(backup.absolute() for backup in paths.config_backups),
    }
    if paths.locations.is_file() and not paths.locations.is_symlink():
        if paths.locations.absolute() not in protected:
            yield "locations", paths.locations, Path(paths.locations.name)
    if paths.audit.is_file() and not paths.audit.is_symlink():
        if paths.audit.absolute() not in protected:
            yield "audit", paths.audit, Path(paths.audit.name)
    for category, directory, pattern in (
        ("sessions", paths.sessions, "*.json"),
        ("pending_runs", paths.pending_runs, "*.json"),
        ("reports", paths.reports, "patrol-*.json"),
        ("traces", paths.traces, "*.jsonl"),
    ):
        for source in _safe_glob(directory, pattern):
            if source.absolute() not in protected:
                yield category, source, source.relative_to(directory)


def _safe_glob(directory: Path, pattern: str):
    if not directory.is_dir() or directory.is_symlink():
        return
    for path in sorted(directory.rglob(pattern)):
        if path.is_file() and not path.is_symlink():
            yield path


def _file_identities(*paths: Path) -> set[tuple[int, int]]:
    identities: set[tuple[int, int]] = set()
    for path in paths:
        try:
            info = path.stat()
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            identities.add((info.st_dev, info.st_ino))
    return identities


def _read_regular_file(path: Path, protected_identities: set[tuple[int, int]]) -> bytes | None:
    """Read one stable regular-file descriptor without following a final symlink."""
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return None
        if (info.st_dev, info.st_ino) in protected_identities:
            return None
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            return handle.read()
    except OSError:
        return None
    finally:
        if fd >= 0:
            os.close(fd)


def _known_secret_values(credentials: Path) -> set[bytes]:
    values: set[str] = set()
    try:
        lines = credentials.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        _key, separator, value = stripped.removeprefix("export ").partition("=")
        if separator:
            value = value.strip().strip("\"'")
            if len(value) >= 4:
                values.add(value)
    for key, value in os.environ.items():
        if re.search(r"(?i)(key|token|secret|password)$", key) and len(value) >= 4:
            values.add(value)
    return {value.encode() for value in values}


def _redact(payload: bytes, secret_values: set[bytes]) -> bytes:
    redacted = payload
    for secret in sorted(secret_values, key=len, reverse=True):
        redacted = redacted.replace(secret, b"[REDACTED]")
    try:
        text = redacted.decode("utf-8")
    except UnicodeDecodeError:
        return redacted
    text = _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", text)
    text = _BEARER.sub(r"\1[REDACTED]", text)
    return text.encode()


def _add_bytes(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(payload)
    info.mode = PRIVATE_FILE_MODE
    info.mtime = int(datetime.now(UTC).timestamp())
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    archive.addfile(info, io.BytesIO(payload))


def _count_stale_audit_records(path: Path, cutoff: datetime) -> int:
    identity = _single_link_regular_identity(path)
    if identity is None:
        return 0
    try:
        with closing(
            sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True)
        ) as connection, connection:
            if _single_link_regular_identity(path) != identity:
                return 0
            rows = connection.execute(
                "SELECT occurred_at FROM audit_events"
            ).fetchall()
    except (OSError, sqlite3.Error):
        return 0
    return sum(
        timestamp is not None and timestamp < cutoff
        for (value,) in rows
        if (timestamp := _parse_timestamp(value)) is not None
    )


def _prune_audit_records(path: Path, cutoff: datetime) -> int:
    identity = _single_link_regular_identity(path)
    if identity is None:
        return 0
    try:
        with closing(sqlite3.connect(path)) as connection, connection:
            if _single_link_regular_identity(path) != identity:
                return 0
            rows = connection.execute(
                "SELECT event_id, occurred_at FROM audit_events"
            ).fetchall()
            stale_ids = [
                int(event_id)
                for event_id, value in rows
                if (timestamp := _parse_timestamp(value)) is not None
                and timestamp < cutoff
            ]
            if stale_ids:
                connection.executemany(
                    "DELETE FROM audit_events WHERE event_id = ?",
                    ((event_id,) for event_id in stale_ids),
                )
        os.chmod(path, PRIVATE_FILE_MODE)
    except (OSError, sqlite3.Error):
        return 0
    return len(stale_ids)


def _single_link_regular_identity(path: Path) -> tuple[int, int] | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        return None
    return info.st_dev, info.st_ino


def _audit_sidecars(path: Path) -> tuple[Path, ...]:
    return tuple(path.with_name(path.name + suffix) for suffix in ("-journal", "-wal", "-shm"))


def _count_stale_trace_records(path: Path, cutoff: datetime) -> int:
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return 0
    return sum(_trace_is_stale(line, cutoff) for line in lines)


def _filter_trace_records(path: Path, cutoff: datetime) -> tuple[list[str], int]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return [], 0
    kept = [line for line in lines if not _trace_is_stale(line, cutoff)]
    return kept, len(lines) - len(kept)


def _trace_is_stale(line: str, cutoff: datetime) -> bool:
    try:
        value = json.loads(line).get("ts")
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return False
    timestamp = _parse_timestamp(value)
    return timestamp is not None and timestamp < cutoff


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.astimezone()
    return timestamp


def _remove_tree_preserving(root: Path, protected: tuple[Path, ...]) -> None:
    """Remove one managed directory without crossing protected descendants."""
    for child in root.iterdir():
        if _is_protected(child, protected):
            continue
        if child.is_symlink() or child.is_file():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            if _contains_protected(child, protected):
                _remove_tree_preserving(child, protected)
            else:
                shutil.rmtree(child)
    try:
        root.rmdir()
    except OSError:
        pass


def _is_protected(path: Path, protected: tuple[Path, ...]) -> bool:
    return any(path.absolute() == candidate for candidate in protected)


def _contains_protected(path: Path, protected: tuple[Path, ...]) -> bool:
    base = path.absolute()
    for candidate in protected:
        try:
            candidate.relative_to(base)
        except ValueError:
            continue
        return True
    return False


def _same_path(first: Path, second: Path) -> bool:
    return first.absolute() == second.absolute()
