from __future__ import annotations

import pytest

from jenai.tui.command_palette import CommandPaletteState, PaletteMode, SlashCommand


def _commands() -> list[SlashCommand]:
    return [
        SlashCommand("/status", "Show robot status"),
        SlashCommand("/ros topics", "List ROS topics"),
        SlashCommand("/ros pub", "Publish once", "/ros pub <topic> <payload>"),
        SlashCommand("/loc add", "Save location", "/loc add <name>"),
    ]


def test_palette_matches_names_before_descriptions() -> None:
    state = CommandPaletteState(_commands())

    view = state.update("/ros")

    assert view.mode is PaletteMode.MATCHES
    assert [command.name for command in view.matches] == ["/ros topics", "/ros pub"]


def test_palette_shows_longest_argument_hint() -> None:
    state = CommandPaletteState(_commands())

    view = state.update("/ros pub /cmd_vel")

    assert view.mode is PaletteMode.HINT
    assert view.hint is not None
    assert view.hint.name == "/ros pub"


def test_palette_selection_wraps_and_completes_only_partial_input() -> None:
    state = CommandPaletteState(_commands())
    state.update("/ros")

    view = state.move(-1)

    assert view.selected_index == 1
    assert state.should_complete("/r") is True
    assert state.should_complete("/ros pub <topic> <payload>") is False
    assert state.selected_completion() == "/ros pub "


def test_palette_hides_for_plain_text_and_unknown_commands() -> None:
    state = CommandPaletteState(_commands())

    assert state.update("robot status").mode is PaletteMode.HIDDEN
    assert state.update("/does-not-exist").mode is PaletteMode.HIDDEN


def test_palette_rejects_duplicate_command_names() -> None:
    with pytest.raises(ValueError, match="unique"):
        CommandPaletteState(
            [
                SlashCommand("/status", "one"),
                SlashCommand("/status", "two"),
            ]
        )
