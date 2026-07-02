from __future__ import annotations

import asyncio
from pathlib import Path

from jenai.adapters.locations import save_locations
from jenai.config.store import build_minimal_config, save_config
from jenai.mcp_server import build_mcp_server
from jenai.schemas import Location, Pose2D


def _setup(tmp_path: Path):
    config = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )
    config_path = tmp_path / "config.toml"
    save_config(config, config_path)
    save_locations(
        [Location(name="Dock", aliases=["充電站"], pose=Pose2D(x=1, y=2, yaw=0))],
        tmp_path / "locations.toml",
    )
    return config, config_path


def test_actions_hidden_unless_operator_opts_in(tmp_path: Path) -> None:
    config, config_path = _setup(tmp_path)

    read_only = build_mcp_server(config, config_path)
    with_actions = build_mcp_server(config, config_path, allow_actions=True)

    ro_names = {t.name for t in asyncio.run(read_only.list_tools())}
    act_names = {t.name for t in asyncio.run(with_actions.list_tools())}

    assert "navigate_to" not in ro_names  # the safety default
    assert "navigate_to" in act_names
    assert {"ros_topics", "list_locations", "robot_pose", "camera_look"} <= ro_names


def test_list_locations_tool_reports_saved_places(tmp_path: Path) -> None:
    config, config_path = _setup(tmp_path)
    server = build_mcp_server(config, config_path)

    result = asyncio.run(server.call_tool("list_locations", {}))

    text = result[0][0].text if isinstance(result, tuple) else result[0].text
    assert "Dock" in text and "充電站" in text


def test_navigate_to_unknown_location_is_refused(tmp_path: Path) -> None:
    config, config_path = _setup(tmp_path)
    server = build_mcp_server(config, config_path, allow_actions=True)

    result = asyncio.run(server.call_tool("navigate_to", {"location": "nowhere"}))

    text = result[0][0].text if isinstance(result, tuple) else result[0].text
    assert "Unknown location" in text


def test_navigate_to_stub_adapter_reports_unavailable(tmp_path: Path) -> None:
    # route_adapter defaults to "stub": honest unavailable, robot does not move.
    config, config_path = _setup(tmp_path)
    server = build_mcp_server(config, config_path, allow_actions=True)

    result = asyncio.run(server.call_tool("navigate_to", {"location": "Dock"}))

    text = result[0][0].text if isinstance(result, tuple) else result[0].text
    assert "unavailable" in text
