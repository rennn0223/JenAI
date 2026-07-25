from __future__ import annotations

from jenai.tui.app import JenAITuiApp
from jenai.tui.catalog import SLASH_COMMANDS
from jenai.tui.command_dispatch import ResolvedCommand, resolve_command


def test_resolve_builtin_command_preserves_argument() -> None:
    assert resolve_command("/run", "inspect the map") == ResolvedCommand(
        "_show_run", "inspect the map"
    )


def test_resolve_ros_and_location_subcommands_strip_nested_argument() -> None:
    assert resolve_command("/ros", "pub /cmd_vel {linear: 0}") == ResolvedCommand(
        "_show_ros_pub", "/cmd_vel {linear: 0}"
    )
    assert resolve_command("/loc", "rename old -> new") == ResolvedCommand(
        "_show_loc_rename", "old -> new"
    )


def test_resolve_user_skill_is_case_insensitive() -> None:
    assert resolve_command("/Inspect", "", user_skills={"inspect"}) == ResolvedCommand(
        "_run_user_skill", "inspect"
    )


def test_resolve_unknown_command_and_subcommand_returns_none() -> None:
    assert resolve_command("/unknown", "") is None
    assert resolve_command("/ros", "unknown") is None


def test_every_catalog_command_has_a_dispatch_route() -> None:
    for catalog_command in SLASH_COMMANDS:
        command, _, argument = catalog_command.name.partition(" ")
        if command == "/clear":
            continue  # Clearing is handled synchronously before normal dispatch.
        resolved = resolve_command(command, argument)
        assert resolved is not None, catalog_command.name
        assert hasattr(JenAITuiApp, resolved.handler_name), catalog_command.name
