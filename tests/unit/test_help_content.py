from __future__ import annotations

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
