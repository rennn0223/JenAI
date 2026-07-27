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


def test_allow_actions_does_not_expose_unregistered_navigation(tmp_path: Path) -> None:
    from jenai.config.models import VehicleProfile

    config, config_path = _setup(tmp_path)
    config.vehicle = VehicleProfile(type="quadruped", display_name="Nexuni prototype")

    server = build_mcp_server(config, config_path, allow_actions=True)
    tool_names = {tool.name for tool in asyncio.run(server.list_tools())}

    assert "navigate_to" not in tool_names


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


def test_navigate_to_without_active_site_reports_blocked(tmp_path: Path) -> None:
    # Saved coordinates are not valid until an operator activates a validated site.
    config, config_path = _setup(tmp_path)
    server = build_mcp_server(config, config_path, allow_actions=True)

    result = asyncio.run(server.call_tool("navigate_to", {"location": "Dock"}))

    text = result[0][0].text if isinstance(result, tuple) else result[0].text
    assert "blocked" in text
    assert "Site Profile" in text


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
                outgoing_action={**action, "capability_id": "dock_approach"},
                execution_status="succeeded",
                route_preview="Arrived at the goal.",
            )

        monkeypatch.setattr("jenai.mcp_server.server.NavigationGateway.execute", slow_nav)

        first = asyncio.create_task(server.call_tool("navigate_to", {"location": "Dock"}))
        await asyncio.sleep(0.05)  # let the first goal take the lock
        second = _text(await server.call_tool("navigate_to", {"location": "Dock"}))
        assert "busy" in second  # the in-flight goal was NOT preempted

        release.set()
        assert "outcome=arrived_unverified" in _text(await first)

    asyncio.run(run())


def test_mcp_stop_labels_unconfirmed_navigation_cancel(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    from jenai.mcp_server import server as server_module
    from jenai.state.audit import AuditStore
    from jenai.state.runs import RunStore
    from jenai.state.task_receipts import TaskReceiptStore
    from jenai.tools.safety import HaltReceipt, NavigationCancelStatus

    config, config_path = _setup(tmp_path)

    async def bridge():
        return object()

    async def unconfirmed(_config, _bridge):
        return HaltReceipt(
            navigation_cancel_status=NavigationCancelStatus.UNCONFIRMED,
            zero_velocity_delivered=True,
            message=(
                "Zero-velocity command published, but navigation cancellation was not "
                "acknowledged. Motion stop was not independently observed."
            ),
        )

    monkeypatch.setattr(server_module, "halt_robot_with_receipt", unconfirmed)
    audit = AuditStore(tmp_path / "audit.sqlite3")
    run_store = RunStore(
        audit_store=audit,
        receipt_store=TaskReceiptStore(tmp_path / "reports" / "tasks"),
    )
    resources = SimpleNamespace(
        config=config,
        config_path=config_path,
        bridge=bridge,
        run_store=run_store,
    )

    result = asyncio.run(server_module._stop_robot(resources))

    assert result.startswith("unconfirmed:")
    assert "not acknowledged" in result
    run = run_store.list_runs()[0]
    assert run.status == "completed"
    assert run.outcome == "partial"
    assert run.tool_calls[0].status == "failed"
    assert run.tool_calls[0].raw_output["zero_velocity_command_published"] is True
    assert run.tool_calls[0].raw_output["motion_stop_observed"] is None
    assert len(list((tmp_path / "reports" / "tasks").glob("task-*.json"))) == 1
    assert any(event.event_type == "run_finished" for event in audit.list_events())


def test_stop_tool_is_always_available(tmp_path: Path) -> None:
    # Stopping is always safe — the tool exists even on read-only servers.
    config, config_path = _setup(tmp_path)

    ro_names = {t.name for t in asyncio.run(build_mcp_server(config, config_path).list_tools())}
    act_names = {
        t.name
        for t in asyncio.run(build_mcp_server(config, config_path, allow_actions=True).list_tools())
    }

    assert "stop" in ro_names
    assert "stop" in act_names
