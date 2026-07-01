from __future__ import annotations

from jenai.schemas import SessionState


class InputHistory:
    """Session-scoped input history navigation (↑/↓), backed by `SessionState`.

    `history_cursor` points at the history entry currently shown, or
    `len(input_history)` when the composer is on a fresh (unnavigated) draft.
    """

    def __init__(self, session: SessionState) -> None:
        self._session = session

    def record(self, text: str) -> None:
        if text:
            self._session.input_history.append(text)
        self.reset_cursor()

    def previous(self) -> str | None:
        history = self._session.input_history
        if not history:
            return None

        cursor = self._session.history_cursor
        if cursor > 0:
            cursor -= 1
            self._session.history_cursor = cursor
        return history[cursor]

    def next(self) -> str:
        history = self._session.input_history
        cursor = self._session.history_cursor
        if cursor >= len(history):
            self._session.history_cursor = len(history)
            return ""

        cursor += 1
        self._session.history_cursor = cursor
        if cursor >= len(history):
            return ""
        return history[cursor]

    def reset_cursor(self) -> None:
        self._session.history_cursor = len(self._session.input_history)
