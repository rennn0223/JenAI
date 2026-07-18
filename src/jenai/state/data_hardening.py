"""Read-only permission audit and explicit migration for legacy JenAI data."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from jenai.secure_files import PRIVATE_DIR_MODE, PRIVATE_FILE_MODE


class DataPathsLike(Protocol):
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
class PermissionAudit:
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
class HardenCandidate:
    category: str
    path: Path
    kind: str
    current_mode: int
    target_mode: int
    device: int
    inode: int


@dataclass(frozen=True)
class HardenRefusal:
    category: str
    path: Path
    reason: str


@dataclass(frozen=True)
class HardenPlan:
    audits: tuple[PermissionAudit, ...]
    candidates: tuple[HardenCandidate, ...]
    refusals: tuple[HardenRefusal, ...]
    protected_file_identities: frozenset[tuple[int, int]]


@dataclass(frozen=True)
class HardenResult:
    hardened: int
    skipped: int


_CATEGORY_PATTERNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("sessions", "sessions", ("*.json", "*.json.lock")),
    ("pending_runs", "pending_runs", ("*.json",)),
    ("reports", "reports", ("patrol-*.json",)),
    ("traces", "traces", ("*.jsonl", "*.jsonl.lock")),
)


def build_hardening_plan(paths: DataPathsLike) -> HardenPlan:
    """Audit allow-listed operational paths without changing filesystem state."""
    protected_paths = {paths.config.absolute(), paths.credentials.absolute()}
    protected_identities = frozenset(_file_identities(paths.config, paths.credentials))
    candidates: list[HardenCandidate] = []
    refusals: list[HardenRefusal] = []
    audits: list[PermissionAudit] = []

    location_entries, location_refusals = _inspect_location(
        paths.locations,
        protected_paths=protected_paths,
        protected_identities=protected_identities,
    )
    refusals.extend(location_refusals)
    candidates.extend(_candidates("locations", location_entries))
    audits.append(
        _audit(
            "locations",
            paths.locations,
            location_entries,
            location_refusals,
        )
    )

    audit_paths = (
        paths.audit,
        *(
            paths.audit.with_name(paths.audit.name + suffix)
            for suffix in ("-journal", "-wal", "-shm")
        ),
    )
    audit_entries, audit_refusals = _inspect_files(
        "audit",
        audit_paths,
        protected_paths=protected_paths,
        protected_identities=protected_identities,
    )
    refusals.extend(audit_refusals)
    candidates.extend(_candidates("audit", audit_entries))
    audits.append(
        _audit_collection("audit", paths.audit, audit_entries, audit_refusals)
    )

    backup_entries, backup_refusals = _inspect_files(
        "config_backups",
        paths.config_backups,
        protected_paths=protected_paths,
        protected_identities=protected_identities,
    )
    refusals.extend(backup_refusals)
    candidates.extend(_candidates("config_backups", backup_entries))
    backup_pattern = paths.config.parent / f"{paths.config.name}.bak-*"
    audits.append(
        _audit_collection(
            "config_backups",
            backup_pattern,
            backup_entries,
            backup_refusals,
        )
    )

    for category, attribute, patterns in _CATEGORY_PATTERNS:
        root = getattr(paths, attribute)
        entries, category_refusals = _inspect_directory(
            category,
            root,
            patterns,
            protected_paths=protected_paths,
            protected_identities=protected_identities,
        )
        refusals.extend(category_refusals)
        candidates.extend(_candidates(category, entries))
        audits.append(_audit(category, root, entries, category_refusals))

    return HardenPlan(
        audits=tuple(audits),
        candidates=tuple(candidates),
        refusals=tuple(refusals),
        protected_file_identities=protected_identities,
    )


def apply_hardening(plan: HardenPlan) -> HardenResult:
    """Apply a reviewed plan after stable-fd identity and type revalidation."""
    hardened = 0
    skipped = 0
    for candidate in plan.candidates:
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if candidate.kind == "directory" and hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        try:
            fd = os.open(candidate.path, flags)
        except OSError:
            skipped += 1
            continue
        try:
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != (candidate.device, candidate.inode):
                skipped += 1
                continue
            if candidate.kind == "directory":
                safe_type = stat.S_ISDIR(info.st_mode)
            else:
                safe_type = (
                    stat.S_ISREG(info.st_mode)
                    and info.st_nlink == 1
                    and (info.st_dev, info.st_ino) not in plan.protected_file_identities
                )
            if not safe_type:
                skipped += 1
                continue
            os.fchmod(fd, candidate.target_mode)
            hardened += 1
        except OSError:
            skipped += 1
        finally:
            os.close(fd)
    return HardenResult(hardened=hardened, skipped=skipped)


@dataclass(frozen=True)
class _Entry:
    path: Path
    kind: str
    mode: int
    size: int
    device: int
    inode: int


def _inspect_files(
    category: str,
    paths: tuple[Path, ...],
    *,
    protected_paths: set[Path],
    protected_identities: frozenset[tuple[int, int]],
) -> tuple[list[_Entry], list[HardenRefusal]]:
    entries: list[_Entry] = []
    refusals: list[HardenRefusal] = []
    for path in paths:
        if not path.exists() and not path.is_symlink():
            continue
        entry, refusal = _inspect_file(
            category,
            path,
            protected_paths=protected_paths,
            protected_identities=protected_identities,
        )
        if entry is not None:
            entries.append(entry)
        if refusal is not None:
            refusals.append(refusal)
    return entries, refusals


def _inspect_location(
    path: Path,
    *,
    protected_paths: set[Path],
    protected_identities: frozenset[tuple[int, int]],
) -> tuple[list[_Entry], list[HardenRefusal]]:
    if not path.exists() and not path.is_symlink():
        return [], []
    entry, refusal = _inspect_file(
        "locations",
        path,
        protected_paths=protected_paths,
        protected_identities=protected_identities,
    )
    return ([entry] if entry else []), ([refusal] if refusal else [])


def _inspect_directory(
    category: str,
    root: Path,
    patterns: tuple[str, ...],
    *,
    protected_paths: set[Path],
    protected_identities: frozenset[tuple[int, int]],
) -> tuple[list[_Entry], list[HardenRefusal]]:
    if not root.exists() and not root.is_symlink():
        return [], []
    if root.absolute() in protected_paths:
        return [], [HardenRefusal(category, root, "protected config/credential path")]
    root_entry, root_refusal = _inspect_directory_entry(category, root)
    if root_refusal:
        return [], [root_refusal]
    if root_entry is None:  # pragma: no cover - defensive completeness
        return [], []

    entries: dict[Path, _Entry] = {root: root_entry}
    refusals: list[HardenRefusal] = []
    matched: set[Path] = set()
    for pattern in patterns:
        try:
            matched.update(root.rglob(pattern))
        except OSError:
            refusals.append(HardenRefusal(category, root, "could not enumerate directory"))
            return list(entries.values()), refusals

    for path in sorted(matched):
        entry, refusal = _inspect_file(
            category,
            path,
            protected_paths=protected_paths,
            protected_identities=protected_identities,
        )
        if refusal:
            refusals.append(refusal)
            continue
        if entry:
            entries[path] = entry
            for parent in _parents_within(path.parent, root):
                if parent in entries:
                    continue
                parent_entry, parent_refusal = _inspect_directory_entry(category, parent)
                if parent_refusal:
                    refusals.append(parent_refusal)
                    break
                if parent_entry:
                    entries[parent] = parent_entry
    return list(entries.values()), refusals


def _inspect_file(
    category: str,
    path: Path,
    *,
    protected_paths: set[Path],
    protected_identities: frozenset[tuple[int, int]],
) -> tuple[_Entry | None, HardenRefusal | None]:
    if path.absolute() in protected_paths:
        return None, HardenRefusal(category, path, "protected config/credential path")
    try:
        info = path.lstat()
    except OSError:
        return None, HardenRefusal(category, path, "could not inspect path")
    if stat.S_ISLNK(info.st_mode):
        return None, HardenRefusal(category, path, "symlink refused")
    if not stat.S_ISREG(info.st_mode):
        return None, HardenRefusal(category, path, "not a regular file")
    identity = (info.st_dev, info.st_ino)
    if identity in protected_identities:
        return None, HardenRefusal(category, path, "aliases config/credential inode")
    if info.st_nlink != 1:
        return None, HardenRefusal(category, path, "hardlink refused")
    return (
        _Entry(
            path=path,
            kind="file",
            mode=stat.S_IMODE(info.st_mode),
            size=info.st_size,
            device=info.st_dev,
            inode=info.st_ino,
        ),
        None,
    )


def _inspect_directory_entry(
    category: str, path: Path
) -> tuple[_Entry | None, HardenRefusal | None]:
    try:
        info = path.lstat()
    except OSError:
        return None, HardenRefusal(category, path, "could not inspect directory")
    if stat.S_ISLNK(info.st_mode):
        return None, HardenRefusal(category, path, "directory symlink refused")
    if not stat.S_ISDIR(info.st_mode):
        return None, HardenRefusal(category, path, "expected a directory")
    return (
        _Entry(
            path=path,
            kind="directory",
            mode=stat.S_IMODE(info.st_mode),
            size=0,
            device=info.st_dev,
            inode=info.st_ino,
        ),
        None,
    )


def _parents_within(path: Path, root: Path):
    current = path
    while current != root:
        try:
            current.relative_to(root)
        except ValueError:
            return
        yield current
        current = current.parent


def _candidates(category: str, entries: list[_Entry]) -> list[HardenCandidate]:
    output: list[HardenCandidate] = []
    for entry in entries:
        target = PRIVATE_DIR_MODE if entry.kind == "directory" else PRIVATE_FILE_MODE
        if entry.mode != target:
            output.append(
                HardenCandidate(
                    category=category,
                    path=entry.path,
                    kind=entry.kind,
                    current_mode=entry.mode,
                    target_mode=target,
                    device=entry.device,
                    inode=entry.inode,
                )
            )
    return output


def _audit(
    category: str,
    root: Path,
    entries: list[_Entry],
    refusals: list[HardenRefusal],
) -> PermissionAudit:
    insecure = sum(
        entry.mode != (PRIVATE_DIR_MODE if entry.kind == "directory" else PRIVATE_FILE_MODE)
        for entry in entries
    )
    files = [entry for entry in entries if entry.kind == "file"]
    root_entry = next((entry for entry in entries if entry.path == root), None)
    return PermissionAudit(
        category=category,
        path=root,
        exists=root.exists() and not root.is_symlink(),
        files=len(files),
        bytes=sum(entry.size for entry in files),
        mode=f"{root_entry.mode:04o}" if root_entry else "-",
        insecure=insecure,
        refused=len(refusals),
        permissions_ok=insecure == 0 and not refusals,
    )


def _audit_collection(
    category: str,
    display_path: Path,
    entries: list[_Entry],
    refusals: list[HardenRefusal],
) -> PermissionAudit:
    insecure = sum(entry.mode != PRIVATE_FILE_MODE for entry in entries)
    primary = next((entry for entry in entries if entry.path == display_path), None)
    return PermissionAudit(
        category=category,
        path=display_path,
        exists=bool(entries or refusals),
        files=len(entries),
        bytes=sum(entry.size for entry in entries),
        mode=f"{primary.mode:04o}" if primary is not None else "-",
        insecure=insecure,
        refused=len(refusals),
        permissions_ok=insecure == 0 and not refusals,
    )


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
