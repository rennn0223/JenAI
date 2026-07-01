from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agents.memory import SessionABC


def _sessions_dir() -> Path:
    return Path.home() / ".config" / "jenai" / "sessions"


class JenAIFileSession(SessionABC):
    """Conversation memory for the agent.

    Implements the openai-agents `Session` protocol (``agents.memory.SessionABC``)
    so it can be passed straight to ``Runner.run(..., session=...)``. The SDK then
    automatically loads prior conversation items before a run and appends new ones
    after — so within a session the agent remembers earlier `/run` tasks (e.g. a
    place it just looked up).

    Backed by one JSON file per session id under ``~/.config/jenai/sessions``.
    Persistence is durable; passing a *stable* session id (rather than the default
    per-launch id) would extend recall across restarts.
    """

    def __init__(self, session_id: str, directory: Path | None = None) -> None:
        self.session_id = session_id
        self._path = (directory or _sessions_dir()) / f"{session_id}.json"

    def _load(self) -> list[dict]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self, items: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    async def get_items(self, limit: int | None = None) -> list[dict]:
        items = await asyncio.to_thread(self._load)
        return items[-limit:] if limit else items

    async def add_items(self, items: list[dict]) -> None:
        current = await asyncio.to_thread(self._load)
        current.extend(items)
        await asyncio.to_thread(self._save, current)

    async def pop_item(self) -> dict | None:
        current = await asyncio.to_thread(self._load)
        if not current:
            return None
        item = current.pop()
        await asyncio.to_thread(self._save, current)
        return item

    async def clear_session(self) -> None:
        await asyncio.to_thread(lambda: self._path.unlink(missing_ok=True))
