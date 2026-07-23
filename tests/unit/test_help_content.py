from __future__ import annotations

from jenai.tui.catalog import COMMAND_GROUPS, SLASH_COMMANDS
from jenai.tui.help_content import build_help_output


def test_help_lists_all_groups_by_default() -> None:
    output = build_help_output()
    names = {group.name for group in output.command_groups}
    assert {"ROS2", "Route", "Vision", "System"} <= names
    assert output.examples  # onboarding examples shown on the full view


def test_help_section_filter_narrows_groups() -> None:
    output = build_help_output("ros")
    assert len(output.command_groups) == 1
    assert output.command_groups[0].name == "ROS2"
    assert any("/ros topic-info" in cmd for cmd in output.command_groups[0].commands)


def test_help_vision_filter() -> None:
    output = build_help_output("vision")
    assert [g.name for g in output.command_groups] == ["Vision"]


def test_palette_and_help_are_generated_from_the_same_catalog() -> None:
    grouped = [command for _name, commands in COMMAND_GROUPS for command in commands]
    output = build_help_output()

    assert len(grouped) == len(SLASH_COMMANDS)
    assert {command.name for command in grouped} == {command.name for command in SLASH_COMMANDS}
    assert [group.name for group in output.command_groups] == [name for name, _ in COMMAND_GROUPS]
    assert [usage for group in output.command_groups for usage in group.commands] == [
        command.completion for command in grouped
    ]
