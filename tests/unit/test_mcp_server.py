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


def _text(result) -> str:
    return result[0][0].text if isinstance(result, tuple) else result[0].text


def test_ros_tools_report_unavailable_without_ros(tmp_path: Path, monkeypatch) -> None:
    # Missing ros2 must read as a degraded environment, not a broken tool —
    # the same honest contract robot_pose/camera_look already keep.
    from jenai.adapters.ros2_adapter import Ros2NotAvailableError

    async def no_ros(config):
        raise Ros2NotAvailableError("ros2 CLI not found on PATH.")

    monkeypatch.setattr("jenai.tools.ros2_core.ros_topics", no_ros)
    config, config_path = _setup(tmp_path)
    server = build_mcp_server(config, config_path)

    text = _text(asyncio.run(server.call_tool("ros_topics", {})))

    assert text.startswith("unavailable:")


def test_malformed_locations_report_gracefully(tmp_path: Path) -> None:
    config, config_path = _setup(tmp_path)
    (tmp_path / "locations.toml").write_text("not = [valid toml", encoding="utf-8")
    server = build_mcp_server(config, config_path)

    text = _text(asyncio.run(server.call_tool("list_locations", {})))

    assert "not valid TOML" in text  # graceful message, not a raw traceback


def test_navigate_to_refuses_concurrent_goals(tmp_path: Path, monkeypatch) -> None:
    from jenai.schemas import RouteOutput

    config, config_path = _setup(tmp_path)
    server = build_mcp_server(config, config_path, allow_actions=True)

    async def run() -> None:
        release = asyncio.Event()

        async def slow_nav(self, action, *, on_progress=None, on_gate=None):
            await release.wait()
            return RouteOutput(
                input_text="",
                outgoing_action=action,
                execution_status="succeeded",
                route_preview="Arrived at the goal.",
            )

        monkeypatch.setattr("jenai.mcp_server.server.NavigationGateway.execute", slow_nav)

        first = asyncio.create_task(server.call_tool("navigate_to", {"location": "Dock"}))
        await asyncio.sleep(0.05)  # let the first goal take the lock
        second = _text(await server.call_tool("navigate_to", {"location": "Dock"}))
        assert "busy" in second  # the in-flight goal was NOT preempted

        release.set()
        assert "succeeded" in _text(await first)

    asyncio.run(run())


def test_stop_tool_is_always_available(tmp_path: Path) -> None:
    # Stopping is always safe — the tool exists even on read-only servers.
    config, config_path = _setup(tmp_path)

    ro_names = {t.name for t in asyncio.run(build_mcp_server(config, config_path).list_tools())}
    act_names = {
        t.name
        for t in asyncio.run(
            build_mcp_server(config, config_path, allow_actions=True).list_tools()
        )
    }

    assert "stop" in ro_names
    assert "stop" in act_names
