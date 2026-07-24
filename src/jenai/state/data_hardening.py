"""Read-only permission audit and explicit migration for legacy JenAI data."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from jenai.secure_files import PRIVATE_DIR_MODE, PRIVATE_FILE_MODE


class DataPathsLike(Protocol):
    @property
    def config(self) -> Path: ...

    @property
    def credentials(self) -> Path: ...

    @property
    def locations(self) -> Path: ...

    @property
    def sessions(self) -> Path: ...

    @property
    def pending_runs(self) -> Path: ...

    @property
    def reports(self) -> Path: ...

    @property
    def traces(self) -> Path: ...

    @property
    def audit(self) -> Path: ...

    @property
    def config_backups(self) -> tuple[Path, ...]: ...


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
    audits.append(_audit_collection("audit", paths.audit, audit_entries, audit_refusals))

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

    # Reports is user-visible, so harden only JenAI-owned formats instead of
    # recursively adopting every JSON document placed below this directory.
    report_entries, report_refusals = _inspect_directory(
        "reports",
        paths.reports,
        ("patrol-*.json", "area-patrol-*.json", "evidence-*.png"),
        protected_paths=protected_paths,
        protected_identities=protected_identities,
        recursive=False,
    )
    task_entries, task_refusals = _inspect_directory(
        "reports",
        paths.reports / "tasks",
        ("task-*.json",),
        protected_paths=protected_paths,
        protected_identities=protected_identities,
        recursive=False,
    )
    report_entries_by_path = {entry.path: entry for entry in (*report_entries, *task_entries)}
    all_report_refusals = [*report_refusals, *task_refusals]
    managed_report_entries = list(report_entries_by_path.values())
    refusals.extend(all_report_refusals)
    candidates.extend(_candidates("reports", managed_report_entries))
    audits.append(_audit("reports", paths.reports, managed_report_entries, all_report_refusals))

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


class _DirectoryInspector:
    """Safely enumerate one managed directory and its required parent chain."""

    def __init__(
        self,
        category: str,
        root: Path,
        patterns: tuple[str, ...],
        *,
        protected_paths: set[Path],
        protected_identities: frozenset[tuple[int, int]],
        recursive: bool,
    ) -> None:
        self._category = category
        self._root = root
        self._patterns = patterns
        self._protected_paths = protected_paths
        self._protected_identities = protected_identities
        self._recursive = recursive
        self._entries: dict[Path, _Entry] = {}
        self._refusals: list[HardenRefusal] = []

    def inspect(self) -> tuple[list[_Entry], list[HardenRefusal]]:
        if not self._root.exists() and not self._root.is_symlink():
            return [], []
        if self._root.absolute() in self._protected_paths:
            return [], [self._refusal("protected config/credential path")]
        root_entry, refusal = _inspect_directory_entry(self._category, self._root)
        if refusal is not None:
            return [], [refusal]
        if root_entry is None:  # pragma: no cover - defensive completeness
            return [], []
        self._entries[self._root] = root_entry
        matched = self._enumerate()
        if matched is not None:
            for path in sorted(matched):
                self._inspect_path(path)
        return list(self._entries.values()), self._refusals

    def _enumerate(self) -> set[Path] | None:
        matched: set[Path] = set()
        try:
            for pattern in self._patterns:
                iterator = (
                    self._root.rglob(pattern) if self._recursive else self._root.glob(pattern)
                )
                matched.update(iterator)
        except OSError:
            self._refusals.append(self._refusal("could not enumerate directory"))
            return None
        return matched

    def _inspect_path(self, path: Path) -> None:
        entry, refusal = _inspect_file(
            self._category,
            path,
            protected_paths=self._protected_paths,
            protected_identities=self._protected_identities,
        )
        if refusal is not None:
            self._refusals.append(refusal)
            return
        if entry is None:
            return
        self._entries[path] = entry
        self._add_parent_entries(path.parent)

    def _add_parent_entries(self, path: Path) -> None:
        for parent in _parents_within(path, self._root):
            if parent in self._entries:
                continue
            entry, refusal = _inspect_directory_entry(self._category, parent)
            if refusal is not None:
                self._refusals.append(refusal)
                break
            if entry is not None:
                self._entries[parent] = entry

    def _refusal(self, reason: str) -> HardenRefusal:
        return HardenRefusal(self._category, self._root, reason)


def _inspect_directory(
    category: str,
    root: Path,
    patterns: tuple[str, ...],
    *,
    protected_paths: set[Path],
    protected_identities: frozenset[tuple[int, int]],
    recursive: bool = True,
) -> tuple[list[_Entry], list[HardenRefusal]]:
    return _DirectoryInspector(
        category,
        root,
        patterns,
        protected_paths=protected_paths,
        protected_identities=protected_identities,
        recursive=recursive,
    ).inspect()


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


def _parents_within(path: Path, root: Path) -> Iterator[Path]:
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
