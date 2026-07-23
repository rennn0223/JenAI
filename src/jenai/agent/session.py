"""Cross-restart conversation memory for the /run agent (JenAIFileSession)."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, cast

from agents import TResponseInputItem
from agents.memory import SessionABC

from jenai.secure_files import (
    PRIVATE_FILE_MODE,
    atomic_write_text,
    ensure_private_directory,
)


def _sessions_dir() -> Path:
    return Path.home() / ".config" / "jenai" / "sessions"


# Bound both item count and serialized size. Tool outputs (especially ROS scans)
# can be thousands of characters each, so an item-only cap still lets a stable
# project session grow into a large prompt whose prefill dominates local-model
# latency. Trimming advances to a user-message boundary to avoid replaying a
# dangling tool result without the call that produced it.
_MAX_ITEMS = 80
_MAX_BYTES = 64 * 1024

# A lock on each session path closes the gap between separate
# JenAIFileSession instances in one process. The on-disk lock below covers
# separate JenAI processes; both are needed because the TUI intentionally
# constructs fresh Session objects for start/resume/chat turns.
_PATH_LOCKS: dict[Path, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _lock_file(handle: BinaryIO) -> None:
    """Acquire an exclusive, blocking advisory lock on one byte."""
    if os.name == "nt":  # pragma: no cover - CI and ROS deployment are Linux
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        windows_lock = cast(Any, msvcrt)
        windows_lock.locking(handle.fileno(), windows_lock.LK_LOCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: BinaryIO) -> None:
    if os.name == "nt":  # pragma: no cover - CI and ROS deployment are Linux
        import msvcrt

        handle.seek(0)
        windows_lock = cast(Any, msvcrt)
        windows_lock.locking(handle.fileno(), windows_lock.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class JenAIFileSession(SessionABC):
    """Conversation memory for the agent.

    Implements the openai-agents `Session` protocol (``agents.memory.SessionABC``)
    so it can be passed straight to ``Runner.run(..., session=...)``. The SDK then
    automatically loads prior conversation items before a run and appends new ones
    after — so the agent remembers earlier `/run` tasks (e.g. a place it just
    looked up).

    Backed by one JSON file per session id under ``~/.config/jenai/sessions``.
    The interactive TUI uses a *stable* session id derived from the working
    directory (``state.session.create_session``), so memory persists across
    restarts for the same project. History is capped to the most recent
    ``_MAX_ITEMS`` items; reset it with ``/clear`` (which calls ``clear_session``).
    """

    def __init__(self, session_id: str, directory: Path | None = None) -> None:
        self.session_id = session_id
        self._path = ((directory or _sessions_dir()) / f"{session_id}.json").absolute()
        # Separate objects targeting one file must share the same in-process
        # lock; an instance-local lock would not serialize their transactions.
        with _PATH_LOCKS_GUARD:
            self._lock = _PATH_LOCKS.setdefault(self._path, threading.Lock())

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Serialize one read-modify-write transaction across instances/processes."""
        with self._lock:
            ensure_private_directory(self._path.parent)
            lock_path = self._path.with_suffix(self._path.suffix + ".lock")
            flags = os.O_RDWR | os.O_CREAT
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(lock_path, flags, PRIVATE_FILE_MODE)
            try:
                os.fchmod(fd, PRIVATE_FILE_MODE)
            except BaseException:
                os.close(fd)
                raise
            with os.fdopen(fd, "a+b") as handle:
                _lock_file(handle)
                try:
                    yield
                finally:
                    _unlock_file(handle)

    def _load(self) -> list[TResponseInputItem]:
        try:
            raw: Any = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        if not isinstance(raw, list):
            return []
        # SDK response-input items are JSON objects (TypedDict unions). Reject
        # malformed scalar entries rather than feeding them into a later run.
        return [cast(TResponseInputItem, item) for item in raw if isinstance(item, dict)]

    def _save(self, items: list[TResponseInputItem]) -> None:
        capped = items[-_MAX_ITEMS:]
        while capped:
            payload = json.dumps(capped, ensure_ascii=False)
            if len(payload.encode("utf-8")) <= _MAX_BYTES:
                break
            next_user = next(
                (
                    index
                    for index, item in enumerate(capped[1:], start=1)
                    if item.get("role") == "user"
                ),
                None,
            )
            # If no later user boundary exists, the newest turn cannot fit as a
            # coherent unit (for example one oversized sensor/tool result).
            # Drop it instead of persisting a dangling or over-budget item.
            capped = capped[next_user:] if next_user is not None else []
        # Write to a temp file then atomically replace, so an interrupted write
        # (esc/kill mid-run) can never leave a truncated file that would load as
        # empty and silently wipe the whole session.
        atomic_write_text(
            self._path,
            json.dumps(capped, ensure_ascii=False),
            harden_parent=True,
        )

    def _append(self, items: list[TResponseInputItem]) -> None:
        with self._transaction():
            current = self._load()
            current.extend(items)
            self._save(current)

    def _pop(self) -> TResponseInputItem | None:
        with self._transaction():
            current = self._load()
            if not current:
                return None
            item = current.pop()
            self._save(current)
            return item

    def _clear(self) -> None:
        with self._transaction():
            self._path.unlink(missing_ok=True)

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        items = await asyncio.to_thread(self._load)
        if limit is None:  # None means "all" — a falsy check would break limit=0
            return items
        return items[-limit:] if limit > 0 else []

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        await asyncio.to_thread(self._append, items)

    async def pop_item(self) -> TResponseInputItem | None:
        return await asyncio.to_thread(self._pop)

    async def clear_session(self) -> None:
        await asyncio.to_thread(self._clear)
