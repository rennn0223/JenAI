from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from jenai.bridge import BridgeError, RosBridgeClient
from jenai.bridge import client as client_module
from jenai.schemas import GateReport, Location, Pose2D, RouteOutput
from jenai.tools.mission_core import parse_mission, run_mission
from jenai.tools.nav_live import NavigationCancelled, navigate_live

FAKE_BRIDGE = Path(__file__).parent / "fake_bridge.py"


@pytest.fixture
def fake_bridge(monkeypatch):
    monkeypatch.setattr(client_module, "_BRIDGE_SCRIPT", FAKE_BRIDGE)
    monkeypatch.setenv("JENAI_BRIDGE_PYTHON", sys.executable)
    monkeypatch.setattr(RosBridgeClient, "available", staticmethod(lambda: True))


ACTION = {"goal": {"name": "Kitchen", "frame_id": "map", "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0}}}


def test_navigate_live_success_with_progress(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        progress = []
        output = await navigate_live(client, ACTION, on_progress=progress.append)
        assert output.execution_status == "succeeded"
        assert "Arrived" in output.route_preview
        assert progress and progress[0].distance_remaining == 3.2
        await client.stop()

    asyncio.run(run())


def test_navigate_live_reports_unavailable_when_bridge_fails(fake_bridge, monkeypatch) -> None:
    async def run() -> None:
        client = RosBridgeClient()

        async def failing_send(**_kw) -> None:
            raise BridgeError("Nav2 (/navigate_to_pose) action server is not running.")

        monkeypatch.setattr(client, "nav_send", failing_send)
        output = await navigate_live(client, ACTION)
        assert output.execution_status == "unavailable"
        assert "NOT sent" in output.route_preview

    asyncio.run(run())


def test_navigate_live_detaches_event_handlers(fake_bridge) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        await navigate_live(client, ACTION)
        assert client._event_handlers.get("nav_feedback", []) == []
        assert client._event_handlers.get("nav_result", []) == []
        await client.stop()

    asyncio.run(run())


@pytest.mark.parametrize("acknowledged", [True, False])
def test_navigate_live_cancellation_preserves_nav2_acknowledgement(acknowledged: bool) -> None:
    class CancelBridge:
        def __init__(self) -> None:
            self.handlers: dict[str, list] = {}
            self.sent = asyncio.Event()

        def on_event(self, event: str, handler) -> None:
            self.handlers.setdefault(event, []).append(handler)

        def off_event(self, event: str, handler) -> None:
            self.handlers[event].remove(handler)

        async def nav_send(self, **_kwargs) -> None:
            self.sent.set()

        async def nav_cancel(self) -> bool:
            return acknowledged

        async def ping(self) -> bool:
            return True

    async def run() -> None:
        bridge = CancelBridge()
        task = asyncio.create_task(navigate_live(bridge, ACTION))
        await bridge.sent.wait()
        task.cancel()

        with pytest.raises(NavigationCancelled) as raised:
            await task

        assert raised.value.nav_cancel_acknowledged is acknowledged
        assert task.cancelled()
        assert bridge.handlers["nav_feedback"] == []
        assert bridge.handlers["nav_result"] == []

    asyncio.run(run())


@pytest.mark.parametrize("acknowledged", [True, False])
def test_navigate_live_timeout_reports_cancel_acknowledgement(acknowledged: bool) -> None:
    class TimeoutBridge:
        def __init__(self) -> None:
            self.handlers: dict[str, list] = {}

        def on_event(self, event: str, handler) -> None:
            self.handlers.setdefault(event, []).append(handler)

        def off_event(self, event: str, handler) -> None:
            self.handlers[event].remove(handler)

        async def nav_send(self, **_kwargs) -> None:
            return None

        async def nav_cancel(self) -> bool:
            return acknowledged

        async def ping(self) -> bool:
            return True

    async def run() -> None:
        output = await navigate_live(TimeoutBridge(), ACTION, timeout=0.001)

        assert output.execution_status == "failed"
        expected = (
            "Nav2 cancellation acknowledged."
            if acknowledged
            else "Nav2 cancellation was not acknowledged."
        )
        assert expected in output.route_preview

    asyncio.run(run())


def test_run_mission_uses_injected_navigator() -> None:
    async def run() -> None:
        locations = [
            Location(name="Kitchen", frame_id="map", pose=Pose2D(x=2, y=1, yaw=0)),
            Location(name="Lobby", frame_id="map", pose=Pose2D(x=0, y=0, yaw=0)),
        ]
        navigated: list[str] = []

        async def fake_navigate(action: dict) -> RouteOutput:
            navigated.append(action["goal"]["name"])
            return RouteOutput(
                input_text="",
                outgoing_action=action,
                execution_status="succeeded",
                route_preview="Arrived at the goal.",
            )

        report = await run_mission(
            None,  # config unused when navigate is injected for goto-only missions
            locations,
            parse_mission("kitchen, lobby"),
            navigate=fake_navigate,
        )
        assert navigated == ["Kitchen", "Lobby"]
        assert all(r.status == "succeeded" for r in report.results)

    asyncio.run(run())


def test_navigate_live_direct_mode_uses_drive_to_pose(fake_bridge) -> None:
    """route_adapter='odom': navigate_live drives via drive_to_pose (odom→cmd_vel),
    consuming the same nav_feedback/nav_result events."""

    class _Vehicle:
        cmd_vel_topic = "/cmd_vel"
        cmd_vel_stamped = False
        max_linear = 1.5
        max_angular = 0.53

    async def run() -> None:
        client = RosBridgeClient()
        progress: list = []
        output = await navigate_live(
            client, ACTION, on_progress=progress.append, direct=True, vehicle=_Vehicle()
        )
        assert output.execution_status == "succeeded"
        assert progress and progress[0].distance_remaining == 1.5
        await client.stop()

    asyncio.run(run())


def test_navigate_with_fallback_odom_dispatches_direct(fake_bridge, monkeypatch) -> None:
    from jenai.config.store import build_minimal_config
    from jenai.tools.nav_live import navigate_with_fallback

    config = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )
    config.route_adapter = "odom"

    seen: dict = {}

    async def fake_drive(**kwargs):
        seen.update(kwargs)

    async def run() -> None:
        client = RosBridgeClient()
        monkeypatch.setattr(client, "drive_to_pose", fake_drive)

        async def get_bridge():
            return client

        # Emit the terminal result so navigate_live completes.
        import threading

        def _late_result():
            import time

            time.sleep(0.2)
            client._dispatch_event({"event": "nav_result", "tag": "", "status": "succeeded"})

        threading.Thread(target=_late_result, daemon=True).start()
        out = await navigate_with_fallback(config, get_bridge, ACTION)
        assert out.execution_status == "succeeded"
        assert seen["x"] == 2.0 and seen["y"] == 1.5  # goal reached drive_to_pose
        await client.stop()

    asyncio.run(run())


def test_navigate_with_fallback_surfaces_structured_gate_report(monkeypatch) -> None:
    from jenai.config.store import build_minimal_config
    from jenai.tools.nav_live import navigate_with_fallback

    config = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )
    config.twin.enabled = True
    report = GateReport(verdict="block", reason="collision")

    async def fake_rehearse(*_args, **_kwargs):
        return report

    monkeypatch.setattr("jenai.twin.rehearse_goal", fake_rehearse)
    seen: list[GateReport] = []

    async def unused_bridge():
        raise AssertionError("a blocked gate must not request the real bridge")

    output = asyncio.run(
        navigate_with_fallback(
            config,
            unused_bridge,
            ACTION,
            on_gate_report=seen.append,
        )
    )

    assert seen == [report]
    assert output.execution_status == "blocked"


def test_navigate_with_fallback_rejects_malformed_goal_fail_closed() -> None:
    # An LLM-fabricated action without a proper goal.pose must be refused at
    # the single navigation exit — never defaulted to the map origin (0, 0).

    from jenai.config.store import build_minimal_config
    from jenai.tools.nav_live import navigate_with_fallback

    config = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )
    config.route_adapter = "nav2"

    async def get_bridge():  # pragma: no cover - must never be reached
        raise AssertionError("a malformed action must not open a bridge")

    bad_actions = [
        {"outgoing_action": {"goal": "map_right_down"}},  # model quoted the wrapper
        {"goal": "map_right_down"},  # goal is a string
        {"goal": {"name": "x"}},  # no pose
        {"goal": {"pose": {"x": float("nan"), "y": 0.0}}},  # non-finite
        {"goal": {"pose": {"x": True, "y": 0.0}}},  # bool is not a coordinate
        {},  # empty
    ]
    for action in bad_actions:
        out = asyncio.run(navigate_with_fallback(config, get_bridge, action))
        assert out.execution_status == "failed", action
        assert "nothing was sent" in out.route_preview, action


def test_unwrap_outgoing_action_tolerates_quoted_preview() -> None:
    import jenai.agent.specialists  # noqa: F401  (module init order: agent → tools)
    from jenai.tools.route_agent_tools import _unwrap_outgoing_action

    inner = {"goal": {"pose": {"x": 1.0, "y": 2.0}}}
    assert _unwrap_outgoing_action({"outgoing_action": inner}) == inner
    assert _unwrap_outgoing_action(inner) == inner  # already unwrapped: untouched
    both = {"goal": inner["goal"], "outgoing_action": {"other": 1}}
    assert _unwrap_outgoing_action(both) == both  # a real goal wins over the wrapper


def test_parse_outgoing_action_accepts_object_and_bounded_json_strings() -> None:
    import json

    import jenai.agent.specialists  # noqa: F401  (module init order: agent → tools)
    from jenai.tools.route_agent_tools import _parse_outgoing_action

    action = {"goal": {"pose": {"x": 1.0, "y": 2.0, "yaw": 0.0}}}
    assert _parse_outgoing_action(action) == action
    assert _parse_outgoing_action(json.dumps(action)) == action
    assert _parse_outgoing_action(json.dumps(json.dumps(action))) == action
    assert _parse_outgoing_action({"outgoing_action": action}) == action
    assert _parse_outgoing_action(json.dumps({"outgoing_action": action})) == action
    assert _parse_outgoing_action(json.dumps(json.dumps({"outgoing_action": action}))) == action


@pytest.mark.parametrize(
    "value",
    [
        "not json",
        "[]",
        "null",
        "true",
        "1",
        '"plain string"',
        '""{\\"goal\\": {}}""',
        [],
        None,
        False,
        1,
    ],
)
def test_parse_outgoing_action_rejects_non_object_or_excess_nesting(value: object) -> None:
    import jenai.agent.specialists  # noqa: F401  (module init order: agent → tools)
    from jenai.tools.route_agent_tools import _parse_outgoing_action

    with pytest.raises(ValueError):
        _parse_outgoing_action(value)


def test_parse_outgoing_action_rejects_excess_string_or_wrapper_nesting() -> None:
    import json

    import jenai.agent.specialists  # noqa: F401  (module init order: agent → tools)
    from jenai.tools.route_agent_tools import _parse_outgoing_action

    action = {"goal": {"pose": {"x": 1.0, "y": 2.0}}}
    invalid = [
        json.dumps(json.dumps(json.dumps(action))),
        {"outgoing_action": {"outgoing_action": action}},
        {"outgoing_action": [action]},
    ]

    for value in invalid:
        with pytest.raises(ValueError):
            _parse_outgoing_action(value)


@pytest.mark.parametrize(
    "terminal_status",
    [
        "localization_jump",
        "localization_jump_halt_unconfirmed",
        "localization_jump_halt_failed",
    ],
)
def test_navigate_live_surfaces_localization_jump_reason(terminal_status: str) -> None:
    class JumpBridge:
        def __init__(self) -> None:
            self.handlers: dict[str, list] = {}

        def on_event(self, event: str, handler) -> None:
            self.handlers.setdefault(event, []).append(handler)

        def off_event(self, event: str, handler) -> None:
            self.handlers[event].remove(handler)

        async def nav_send(self, **_kwargs) -> None:
            for handler in self.handlers["nav_result"]:
                handler(
                    {
                        "event": "nav_result",
                        "status": terminal_status,
                        "reason": (
                            "Localization safety stop: /amcl_pose jumped 24.00 m "
                            "in 0.10 s. Navigation canceled and zero velocity sent."
                        ),
                    }
                )

        async def ping(self) -> bool:
            return True

    async def run() -> None:
        output = await navigate_live(JumpBridge(), ACTION)
        assert output.execution_status == "failed"
        assert "/amcl_pose jumped 24.00 m" in output.route_preview
        assert "zero velocity sent" in output.route_preview

    asyncio.run(run())
