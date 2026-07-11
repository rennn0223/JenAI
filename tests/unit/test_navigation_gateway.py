from __future__ import annotations

import asyncio
from types import SimpleNamespace

from jenai.config.models import AppConfig
from jenai.schemas import GateCriterion, GateReport, RouteOutput
from jenai.state.audit import AuditStore
from jenai.tools import navigation_gateway as gateway_module

ACTION = {"goal": {"frame_id": "map", "pose": {"x": 1.0, "y": 2.0, "yaw": 0.0}}}


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

    output = asyncio.run(gateway_module.execute_navigation(config, ACTION))

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
    gateway = gateway_module.NavigationGateway(AppConfig(), get_bridge=get_bridge)

    asyncio.run(gateway.execute(ACTION))
    asyncio.run(gateway.close())

    assert events == ["arm"]


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
    gateway = gateway_module.NavigationGateway(AppConfig(), audit_store=audit)

    asyncio.run(gateway.execute(ACTION, run_id="run-1", session_id="session-1"))

    event = audit.list_events(run_id="run-1")[0]
    assert event.event_type == "gate_verdict"
    assert event.status == "refer"
    assert event.summary == "endpoint unavailable"
    assert event.details["criteria"] == [{"id": "G4", "status": "fail"}]
