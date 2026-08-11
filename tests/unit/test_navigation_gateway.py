from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenai.config.models import AppConfig
from jenai.schemas import GateCriterion, GateReport, RouteOutput
from jenai.state.audit import AuditStore
from jenai.tools import navigation_gateway as gateway_module
from jenai.tools.safety import HaltReceipt, NavigationCancelStatus

ACTION = {"goal": {"frame_id": "map", "pose": {"x": 1.0, "y": 2.0, "yaw": 0.0}}}


@pytest.fixture(autouse=True)
def _site_identity_already_verified(monkeypatch) -> None:
    """These tests isolate downstream gateway ownership and gate-report behavior."""

    async def verified(_self, _action, *, run_id, session_id):
        return None

    monkeypatch.setattr(
        gateway_module, "bind_navigation_action", lambda _config, _path, action: action
    )

    monkeypatch.setattr(gateway_module.NavigationGateway, "_verify_active_site", verified)


def test_owned_gateway_arms_watchdog_before_start_and_closes(monkeypatch) -> None:
    events: list[str] = []

    class FakeBridge:
        running = False

        async def configure_safety(self, **_kwargs) -> None:
            events.append("arm")

        async def start(self) -> None:
            events.append("start")
            self.running = True

        async def stop(self) -> None:
            events.append("stop")
            self.running = False

    bridge = FakeBridge()
    monkeypatch.setattr(gateway_module, "RosBridgeClient", lambda: bridge)

    async def fake_dispatch(config, get_bridge, action, **_kwargs):
        assert config.route_adapter == "nav2"
        assert action == ACTION
        assert await get_bridge() is bridge
        events.append("execute")
        return RouteOutput(input_text="", execution_status="succeeded")

    monkeypatch.setattr(gateway_module, "navigate_with_fallback", fake_dispatch)
    config = AppConfig(route_adapter="nav2")

    output = asyncio.run(
        gateway_module.execute_navigation(config, ACTION, config_path=Path("/tmp/config.toml"))
    )

    assert output.execution_status == "succeeded"
    assert events == ["arm", "start", "execute", "stop"]


def test_external_gateway_reuses_bridge_without_taking_ownership(monkeypatch) -> None:
    events: list[str] = []
    bridge = SimpleNamespace(running=True)

    async def get_bridge():
        return bridge

    async def fake_arm(_config, seen_bridge) -> None:
        assert seen_bridge is bridge
        events.append("arm")

    async def fake_dispatch(_config, provider, _action, **_kwargs):
        assert await provider() is bridge
        return RouteOutput(input_text="", execution_status="succeeded")

    monkeypatch.setattr(gateway_module, "arm_watchdog", fake_arm)
    monkeypatch.setattr(gateway_module, "navigate_with_fallback", fake_dispatch)
    gateway = gateway_module.NavigationGateway(
        AppConfig(), config_path=Path("/tmp/config.toml"), get_bridge=get_bridge
    )

    asyncio.run(gateway.execute(ACTION))
    asyncio.run(gateway.close())

    assert events == ["arm"]


def test_gateway_stop_uses_provider_free_halt_on_the_active_bridge(monkeypatch) -> None:
    bridge = SimpleNamespace(running=True)
    config = AppConfig()
    receipt = HaltReceipt(
        navigation_cancel_status=NavigationCancelStatus.ACKNOWLEDGED,
        zero_velocity_delivered=True,
        message="Cancellation acknowledged.",
    )
    observed: list[object] = []

    async def get_bridge():
        return bridge

    async def fake_arm(_config, seen_bridge) -> None:
        assert seen_bridge is bridge

    async def fake_halt(seen_config, seen_bridge) -> HaltReceipt:
        observed.extend((seen_config, seen_bridge))
        return receipt

    monkeypatch.setattr(gateway_module, "arm_watchdog", fake_arm)
    monkeypatch.setattr(gateway_module, "halt_robot_with_receipt", fake_halt)
    gateway = gateway_module.NavigationGateway(config, get_bridge=get_bridge)

    result = asyncio.run(gateway.stop())

    assert result is receipt
    assert observed == [config, bridge]


def test_gateway_persists_structured_gate_verdict(monkeypatch, tmp_path) -> None:
    audit = AuditStore(tmp_path / "audit.sqlite3")
    report = GateReport(
        verdict="refer",
        reason="endpoint unavailable",
        twin_elapsed_s=1.25,
        criteria=[
            GateCriterion(
                criterion_id="G4",
                name="endpoint deviation",
                status="fail",
                detail="no pose",
            )
        ],
    )

    async def fake_dispatch(_config, _provider, _action, **kwargs):
        kwargs["on_gate_report"](report)
        return RouteOutput(input_text="", execution_status="failed")

    monkeypatch.setattr(gateway_module, "navigate_with_fallback", fake_dispatch)
    gateway = gateway_module.NavigationGateway(
        AppConfig(), config_path=Path("/tmp/config.toml"), audit_store=audit
    )

    asyncio.run(gateway.execute(ACTION, run_id="run-1", session_id="session-1"))

    event = next(
        item for item in audit.list_events(run_id="run-1") if item.event_type == "gate_verdict"
    )
    assert event.event_type == "gate_verdict"
    assert event.status == "refer"
    assert event.summary == "endpoint unavailable"
    assert event.details["criteria"] == [{"id": "G4", "status": "fail"}]


def test_gateway_exposes_structured_gate_verdict_without_audit_store(monkeypatch) -> None:
    report = GateReport(verdict="block", reason="forbidden zone")
    observed: list[GateReport] = []

    async def fake_dispatch(_config, _provider, _action, **kwargs):
        kwargs["on_gate_report"](report)
        return RouteOutput(input_text="", execution_status="blocked")

    monkeypatch.setattr(gateway_module, "navigate_with_fallback", fake_dispatch)
    gateway = gateway_module.NavigationGateway(AppConfig(), config_path=Path("/tmp/config.toml"))

    asyncio.run(gateway.execute(ACTION, on_gate_report=observed.append))

    assert observed == [report]


def test_gate_observer_failure_does_not_change_navigation_result(monkeypatch) -> None:
    report = GateReport(verdict="pass", reason="clear")

    async def fake_dispatch(_config, _provider, _action, **kwargs):
        kwargs["on_gate_report"](report)
        return RouteOutput(input_text="", execution_status="succeeded")

    def broken_observer(_report: GateReport) -> None:
        raise RuntimeError("evidence sink unavailable")

    monkeypatch.setattr(gateway_module, "navigate_with_fallback", fake_dispatch)
    gateway = gateway_module.NavigationGateway(AppConfig(), config_path=Path("/tmp/config.toml"))

    output = asyncio.run(gateway.execute(ACTION, on_gate_report=broken_observer))

    assert output.execution_status == "succeeded"


def test_gateway_blocks_an_unregistered_navigation_capability(monkeypatch) -> None:
    async def must_not_dispatch(*_args, **_kwargs):
        raise AssertionError("an unregistered capability reached Nav2")

    monkeypatch.setattr(gateway_module, "navigate_with_fallback", must_not_dispatch)
    config = AppConfig()
    config.vehicle.capabilities = ["inspect_state"]
    gateway = gateway_module.NavigationGateway(
        config,
        config_path=Path("/tmp/config.toml"),
    )

    output = asyncio.run(gateway.execute(ACTION))

    assert output.execution_status == "blocked"
    assert "navigate" in output.route_preview
    assert "not registered" in output.route_preview


def test_gateway_cancellation_after_site_preflight_prevents_dispatch(monkeypatch) -> None:
    site_started = asyncio.Event()
    release_site = asyncio.Event()
    cancelled = False
    dispatch_calls = 0

    async def paused_site(_self, _action, *, run_id, session_id):
        site_started.set()
        await release_site.wait()
        return None

    async def must_not_dispatch(*_args, **_kwargs):
        nonlocal dispatch_calls
        dispatch_calls += 1
        raise AssertionError("dispatch ran after cancellation")

    async def get_bridge():
        raise AssertionError("bridge acquisition ran after cancellation")

    monkeypatch.setattr(gateway_module.NavigationGateway, "_verify_active_site", paused_site)
    monkeypatch.setattr(gateway_module, "navigate_with_fallback", must_not_dispatch)
    gateway = gateway_module.NavigationGateway(
        AppConfig(),
        config_path=Path("/tmp/config.toml"),
        get_bridge=get_bridge,
    )

    async def run() -> None:
        nonlocal cancelled
        task = asyncio.create_task(gateway.execute(ACTION, is_cancelled=lambda: cancelled))
        await site_started.wait()
        cancelled = True
        release_site.set()

        output = await task

        assert output.execution_status == "cancelled"
        assert dispatch_calls == 0

    asyncio.run(run())
