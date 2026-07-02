from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path

from agents.memory import SessionABC


def _sessions_dir() -> Path:
    return Path.home() / ".config" / "jenai" / "sessions"


# Keep only the most recent N conversation items per session. Bounds the on-disk
# file and the number of tokens replayed to the model, so a long-lived session
# stays cheap instead of growing without limit.
_MAX_ITEMS = 200


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
        self._path = (directory or _sessions_dir()) / f"{session_id}.json"
        # Serialises the load→modify→save cycle so overlapping runs sharing a
        # session id can't clobber each other's appended items.
        self._lock = threading.Lock()

    def _load(self) -> list[dict]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self, items: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        capped = items[-_MAX_ITEMS:]
        # Write to a temp file then atomically replace, so an interrupted write
        # (esc/kill mid-run) can never leave a truncated file that would load as
        # empty and silently wipe the whole session.
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(capped, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._path)

    def _append(self, items: list[dict]) -> None:
        with self._lock:
            current = self._load()
            current.extend(items)
            self._save(current)

    def _pop(self) -> dict | None:
        with self._lock:
            current = self._load()
            if not current:
                return None
            item = current.pop()
            self._save(current)
            return item

    async def get_items(self, limit: int | None = None) -> list[dict]:
        items = await asyncio.to_thread(self._load)
        if limit is None:  # None means "all" — a falsy check would break limit=0
            return items
        return items[-limit:] if limit > 0 else []

    async def add_items(self, items: list[dict]) -> None:
        await asyncio.to_thread(self._append, items)

    async def pop_item(self) -> dict | None:
        return await asyncio.to_thread(self._pop)

    async def clear_session(self) -> None:
        await asyncio.to_thread(lambda: self._path.unlink(missing_ok=True))
