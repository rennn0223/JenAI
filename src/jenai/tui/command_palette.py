"""Pure command-palette state and matching rules.

Textual owns rendering and keyboard events. This module owns the interaction
invariants, so command discovery can be tested without mounting an App.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple


class SlashCommand(NamedTuple):
    name: str
    description: str
    template: str = ""

    @property
    def completion(self) -> str:
        return self.template or self.name


class PaletteMode(StrEnum):
    HIDDEN = "hidden"
    MATCHES = "matches"
    HINT = "hint"


@dataclass(frozen=True)
class PaletteView:
    """Everything the UI needs to render one palette state."""

    mode: PaletteMode
    matches: tuple[SlashCommand, ...] = ()
    selected_index: int = 0
    hint: SlashCommand | None = None

    @property
    def visible(self) -> bool:
        return self.mode is not PaletteMode.HIDDEN


class CommandPaletteState:
    """Match commands and preserve a bounded selection behind one interface."""

    def __init__(self, commands: Iterable[SlashCommand]) -> None:
        self._commands = tuple(commands)
        if len({command.name for command in self._commands}) != len(self._commands):
            raise ValueError("Command palette names must be unique.")
        self._view = PaletteView(PaletteMode.HIDDEN)

    @property
    def commands(self) -> tuple[SlashCommand, ...]:
        return self._commands

    @property
    def view(self) -> PaletteView:
        return self._view

    def update(self, value: str) -> PaletteView:
        raw = value.lstrip()
        if not raw.startswith("/"):
            return self.hide()

        query = raw[1:].lower()
        name_matches = tuple(
            command for command in self._commands if command.name[1:].lower().startswith(query)
        )
        description_matches = tuple(
            command
            for command in self._commands
            if command not in name_matches and query in command.description.lower()
        )
        matches = name_matches + description_matches
        if matches:
            selected = min(self._view.selected_index, len(matches) - 1)
            self._view = PaletteView(PaletteMode.MATCHES, matches, selected)
            return self._view

        hint = self._argument_hint(raw)
        if hint is None:
            return self.hide()
        self._view = PaletteView(PaletteMode.HINT, hint=hint)
        return self._view

    def hide(self) -> PaletteView:
        self._view = PaletteView(PaletteMode.HIDDEN)
        return self._view

    def move(self, delta: int) -> PaletteView:
        if self._view.mode is not PaletteMode.MATCHES or not self._view.matches:
            return self._view
        selected = (self._view.selected_index + delta) % len(self._view.matches)
        self._view = PaletteView(PaletteMode.MATCHES, self._view.matches, selected)
        return self._view

    def should_complete(self, value: str) -> bool:
        if self._view.mode is not PaletteMode.MATCHES or not self._view.matches:
            return False
        known_values = {command.name for command in self._commands}
        known_values.update(command.completion for command in self._commands)
        return value not in known_values

    def selected_completion(self) -> str | None:
        if self._view.mode is not PaletteMode.MATCHES or not self._view.matches:
            return None
        return self._view.matches[self._view.selected_index].name + " "

    def _argument_hint(self, raw: str) -> SlashCommand | None:
        lowered = raw.lower()
        candidates = (
            command
            for command in self._commands
            if "<" in command.template and lowered.startswith(command.name.lower() + " ")
        )
        return max(candidates, key=lambda command: len(command.name), default=None)
