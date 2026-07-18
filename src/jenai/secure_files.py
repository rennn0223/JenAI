"""Private, crash-safe local file primitives used by JenAI state stores.

JenAI state can contain conversation text, robot locations and execution
details.  Callers use these helpers instead of relying on the process umask:
owned directories are 0700, files are 0600, replacement writes are atomic,
and a failed replacement leaves the previous file untouched.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def ensure_private_directory(path: Path, *, harden_existing: bool = True) -> Path:
    """Create ``path`` privately and optionally harden an existing directory.

    Every newly created component is chmod'd explicitly because ``mkdir`` mode
    is still affected by umask and ``parents=True`` does not apply the requested
    mode to intermediate parents.  Existing ancestors are left alone; only the
    requested directory is hardened, avoiding surprising permission changes to
    a user-selected project directory.
    """
    path = path.expanduser()
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent

    for directory in reversed(missing):
        directory.mkdir(mode=PRIVATE_DIR_MODE, exist_ok=True)
        os.chmod(directory, PRIVATE_DIR_MODE)

    if not path.is_dir():
        raise NotADirectoryError(path)
    if harden_existing:
        os.chmod(path, PRIVATE_DIR_MODE)
    return path


def ensure_private_parent(path: Path, *, harden_existing: bool = False) -> Path:
    """Ensure a file parent exists without changing an existing project root."""
    return ensure_private_directory(path.parent, harden_existing=harden_existing)


def atomic_write_bytes(
    path: Path,
    payload: bytes,
    *,
    harden_parent: bool = False,
) -> Path:
    """Atomically replace ``path`` with private bytes in the same directory."""
    path = path.expanduser()
    ensure_private_parent(path, harden_existing=harden_parent)
    fd, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        os.fchmod(fd, PRIVATE_FILE_MODE)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return path


@contextmanager
def atomic_output_path(
    path: Path,
    *,
    harden_parent: bool = False,
) -> Iterator[Path]:
    """Yield a private temporary path and atomically publish it on success.

    This streaming variant is for archives and other outputs that should not be
    buffered fully in memory. Any exception (including a failed ``replace``)
    removes only the temporary file and preserves a pre-existing destination.
    """
    path = path.expanduser()
    ensure_private_parent(path, harden_existing=harden_parent)
    fd, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    os.fchmod(fd, PRIVATE_FILE_MODE)
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        yield temporary_path
        os.chmod(temporary_path, PRIVATE_FILE_MODE)
        with temporary_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def atomic_write_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    harden_parent: bool = False,
) -> Path:
    return atomic_write_bytes(
        path,
        text.encode(encoding),
        harden_parent=harden_parent,
    )


@contextmanager
def private_append_file(path: Path, *, harden_parent: bool = True) -> Iterator[int]:
    """Open a private, append-only descriptor without following a final symlink.

    The caller writes complete records while holding its own logical lock.  The
    file is never opened with truncation, so an append failure cannot destroy
    earlier trace records.
    """
    path = path.expanduser()
    ensure_private_parent(path, harden_existing=harden_parent)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, PRIVATE_FILE_MODE)
    try:
        os.fchmod(fd, PRIVATE_FILE_MODE)
        yield fd
        os.fsync(fd)
    finally:
        os.close(fd)


def write_all(fd: int, payload: bytes) -> None:
    """Write all bytes to an already-open descriptor or raise."""
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:  # pragma: no cover - defensive OS contract check
            raise OSError("short write while persisting JenAI state")
        view = view[written:]


def private_mode(path: Path) -> str:
    """Return a stable octal mode for status output (or ``-`` if absent)."""
    try:
        return f"{path.stat().st_mode & 0o777:04o}"
    except OSError:
        return "-"


def _fsync_directory(directory: Path) -> None:
    """Durably record a rename where the platform permits directory fsync."""
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(directory, flags)
    except OSError:  # pragma: no cover - platform/filesystem dependent
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - platform/filesystem dependent
        pass
    finally:
        os.close(fd)
