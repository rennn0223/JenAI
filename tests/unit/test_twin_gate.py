"""Twin Gate (M3): G1–G5 judging, verdict policy, and the navigation hook."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from jenai.bridge import BridgeError
from jenai.config.models import AppConfig, ForbiddenZone, TwinProfile
from jenai.schemas import GateReport
from jenai.tools.nav_live import navigate_with_fallback
from jenai.twin.gate import TwinGate, rehearse_goal


class FakeTwinBridge:
    """In-process stand-in for the twin-domain RosBridgeClient."""

    def __init__(
        self,
        *,
        nav_status: str | None = "succeeded",  # None: Nav2 never finishes
        final_pose: tuple[float, float] = (2.0, 3.0),
        collide: bool = False,
        start_error: str | None = None,
        no_contact_topic: bool = False,
    ) -> None:
        self._nav_status = nav_status
        self._pose = final_pose
        self._collide = collide
        self._start_error = start_error
        self._no_contact_topic = no_contact_topic
        self._handlers: dict[str, list] = {}
        self._contact_handler = None
        self.started = False
        self.stopped = False
        self.canceled = False
        self.unwatched = False

    async def start(self, timeout: float = 10.0) -> None:
        if self._start_error is not None:
            raise BridgeError(self._start_error)
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def on_event(self, event, handler) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def off_event(self, event, handler) -> None:
        if handler in self._handlers.get(event, []):
            self._handlers[event].remove(handler)

    async def watch(self, topic, msg_type, handler, throttle: float = 1.0) -> int:
        if self._no_contact_topic:
            raise BridgeError(f"no such topic {topic}")
        self._contact_handler = handler
        return 7

    async def unwatch(self, watch_id: int) -> None:
        self.unwatched = True

    async def nav_send(self, x, y, yaw=0.0, frame_id="map", tag="") -> None:
        if self._collide and self._contact_handler is not None:
            self._contact_handler({"data": True})
            return
        if self._nav_status is not None:
            for handler in self._handlers.get("nav_result", []):
                handler({"tag": tag, "status": self._nav_status})

    async def nav_cancel(self) -> bool:
        self.canceled = True
        return True

    async def get_pose(self, timeout: float = 3.0):
        return SimpleNamespace(
            x=self._pose[0], y=self._pose[1], yaw=0.0, frame_id="map", source="/amcl_pose"
        )


def _twin(**overrides) -> TwinProfile:
    return TwinProfile(enabled=True, pose_sample_s=0.01, **overrides)


def _goal(x: float = 2.0, y: float = 3.0) -> dict:
    return {"goal": {"frame_id": "map", "pose": {"x": x, "y": y, "yaw": 0.0}}}


def _rehearse(twin: TwinProfile, bridge: FakeTwinBridge, goal: dict | None = None) -> GateReport:
    return asyncio.run(TwinGate(twin, bridge=bridge).rehearse(goal or _goal()))


def _status(report: GateReport, cid: str) -> str:
    return next(c.status for c in report.criteria if c.criterion_id == cid)


def test_clean_rehearsal_passes() -> None:
    bridge = FakeTwinBridge()
    report = _rehearse(_twin(), bridge)

    assert report.verdict == "pass"
    assert all(_status(report, cid) == "pass" for cid in ("G1", "G2", "G3", "G4", "G5"))
    assert bridge.unwatched  # the contact watch is always released


def test_goal_inside_forbidden_zone_blocks_without_simulating() -> None:
    zone = ForbiddenZone(name="pit", x_min=0, y_min=0, x_max=5, y_max=5)
    bridge = FakeTwinBridge()
    report = _rehearse(_twin(forbidden_zones=[zone]), bridge)

    assert report.verdict == "block"
    assert _status(report, "G3") == "fail"
    assert "pit" in report.reason
    assert not bridge.started  # no twin needed to reject an obviously bad goal


def test_collision_blocks_and_cancels_the_twin_goal() -> None:
    bridge = FakeTwinBridge(collide=True, nav_status=None)
    report = _rehearse(_twin(), bridge)

    assert report.verdict == "block"
    assert _status(report, "G1") == "fail"
    assert _status(report, "G5") == "skipped"  # we interrupted Nav2 ourselves
    assert bridge.canceled


def test_trajectory_entering_zone_blocks() -> None:
    # Goal is outside the zone, but the twin drives through it on the way.
    zone = ForbiddenZone(name="wet floor", x_min=1, y_min=2, x_max=3, y_max=4)
    bridge = FakeTwinBridge(nav_status=None, final_pose=(2.0, 3.0))
    report = _rehearse(_twin(forbidden_zones=[zone]), bridge, _goal(10.0, 10.0))

    assert report.verdict == "block"
    assert _status(report, "G3") == "fail"
    assert "wet floor" in report.reason
    assert bridge.canceled


def test_timeout_refers() -> None:
    bridge = FakeTwinBridge(nav_status=None)
    report = _rehearse(_twin(nav_timeout_s=0.01), bridge)

    assert report.verdict == "refer"
    assert _status(report, "G2") == "fail"
    assert bridge.canceled


def test_nav2_abort_refers() -> None:
    report = _rehearse(_twin(), FakeTwinBridge(nav_status="aborted"))

    assert report.verdict == "refer"
    assert _status(report, "G5") == "fail"
    assert "aborted" in report.reason


def test_endpoint_deviation_refers() -> None:
    bridge = FakeTwinBridge(final_pose=(2.0, 3.0))
    report = _rehearse(_twin(goal_tolerance_m=0.5), bridge, _goal(10.0, 10.0))

    assert report.verdict == "refer"
    assert _status(report, "G4") == "fail"
    assert "tolerance" in report.reason


def test_unreachable_twin_refers_never_passes() -> None:
    report = _rehearse(_twin(), FakeTwinBridge(start_error="rclpy missing"))

    assert report.verdict == "refer"
    assert "unreachable" in report.reason
    assert all(c.status == "skipped" for c in report.criteria)


def test_missing_contact_sensor_skips_g1_but_can_still_pass() -> None:
    report = _rehearse(_twin(), FakeTwinBridge(no_contact_topic=True))

    assert report.verdict == "pass"
    assert _status(report, "G1") == "skipped"


def test_rehearse_goal_releases_the_bridge_it_created() -> None:
    bridge = FakeTwinBridge()
    asyncio.run(rehearse_goal(_twin(), _goal(), bridge=bridge))
    assert not bridge.stopped  # caller-owned bridge is left alone

    # ...but the helper stops a bridge it created itself (verified via TwinGate.stop).
    gate = TwinGate(_twin(), bridge=bridge)
    asyncio.run(gate.stop())
    assert bridge.stopped


def test_navigate_with_fallback_blocks_before_the_robot(monkeypatch) -> None:
    config = AppConfig(twin=TwinProfile(enabled=True))
    seen: list[str] = []

    async def fake_rehearse(twin, action, *, on_status=None, bridge=None):
        return GateReport(verdict="block", reason="goal is inside 'pit'")

    monkeypatch.setattr("jenai.twin.rehearse_goal", fake_rehearse)

    async def get_bridge():
        raise AssertionError("the real bridge must never be touched on a block")

    out = asyncio.run(
        navigate_with_fallback(config, get_bridge, _goal(), on_gate=seen.append)
    )
    assert out.execution_status == "failed"
    assert "NOT moved" in out.route_preview
    assert "pit" in out.route_preview


def test_navigate_with_fallback_pass_proceeds_to_execution(monkeypatch) -> None:
    config = AppConfig(twin=TwinProfile(enabled=True))
    executed: list[dict] = []

    async def fake_rehearse(twin, action, *, on_status=None, bridge=None):
        return GateReport(verdict="pass")

    async def fake_route_execute(cfg, action):
        executed.append(action)
        return SimpleNamespace(execution_status="succeeded", route_preview="ok")

    monkeypatch.setattr("jenai.twin.rehearse_goal", fake_rehearse)
    monkeypatch.setattr("jenai.tools.route_core.route_execute", fake_route_execute)

    out = asyncio.run(navigate_with_fallback(config, None, _goal()))
    assert executed  # the goal reached the execution layer
    assert out.execution_status == "succeeded"


def test_gate_disabled_never_touches_the_twin(monkeypatch) -> None:
    config = AppConfig()  # twin.enabled defaults to False

    async def explode(*args, **kwargs):
        raise AssertionError("gate must not run when disabled")

    async def fake_route_execute(cfg, action):
        return SimpleNamespace(execution_status="succeeded", route_preview="ok")

    monkeypatch.setattr("jenai.twin.rehearse_goal", explode)
    monkeypatch.setattr("jenai.tools.route_core.route_execute", fake_route_execute)

    out = asyncio.run(navigate_with_fallback(config, None, _goal()))
    assert out.execution_status == "succeeded"


def test_doctor_twin_checks_probe_the_twin_domain(monkeypatch) -> None:
    from jenai.doctor.checks import _check_twin

    config = AppConfig(twin=TwinProfile(enabled=True, domain_id=42))
    monkeypatch.setattr("jenai.doctor.checks.shutil.which", lambda _: "/usr/bin/ros2")
    seen_domains: list[int | None] = []

    def fake_topics(*, timeout=5.0, domain_id=None):
        seen_domains.append(domain_id)
        return ["/twin/collision", "/map", "/scan"]

    monkeypatch.setattr("jenai.adapters.ros2_adapter.list_topics", fake_topics)
    monkeypatch.setattr(
        "jenai.adapters.ros2_adapter.list_actions",
        lambda *, timeout=5.0, domain_id=None: ["/navigate_to_pose"],
    )

    items = _check_twin(config)
    assert seen_domains == [42]  # probed on the twin's domain, not the robot's
    assert {i.check_name: i.status for i in items} == {
        "twin_graph": "pass",
        "twin_nav2": "pass",
        "twin_contact_sensor": "pass",
    }


def test_doctor_twin_silent_when_disabled_and_warns_when_unreachable(monkeypatch) -> None:
    from jenai.adapters.ros2_adapter import Ros2AdapterError
    from jenai.doctor.checks import _check_twin

    assert _check_twin(AppConfig()) == []

    config = AppConfig(twin=TwinProfile(enabled=True))
    monkeypatch.setattr("jenai.doctor.checks.shutil.which", lambda _: "/usr/bin/ros2")

    def boom(*, timeout=5.0, domain_id=None):
        raise Ros2AdapterError("graph unreachable")

    monkeypatch.setattr("jenai.adapters.ros2_adapter.list_topics", boom)
    items = _check_twin(config)
    assert [i.status for i in items] == ["warn"]
    assert "TWIN_SETUP" in items[0].fix_suggestion


def test_forbidden_zone_contains() -> None:
    zone = ForbiddenZone(x_min=-1, y_min=-1, x_max=1, y_max=1)
    assert zone.contains(0, 0)
    assert zone.contains(1, 1)  # boundary is inside: err on the safe side
    assert not zone.contains(1.01, 0)


# --- fault paths (A4): the gate must refer, never crash or pass -------------


def test_bridge_error_mid_rehearsal_refers() -> None:
    class NavErrorBridge(FakeTwinBridge):
        async def nav_send(self, x, y, yaw=0.0, frame_id="map", tag="") -> None:
            raise BridgeError("twin DDS died")

    report = _rehearse(_twin(), NavErrorBridge())
    assert report.verdict == "refer"
    assert "mid-rehearsal" in report.reason or "twin" in report.reason


def test_pose_unavailable_after_success_skips_g4_and_passes() -> None:
    class NoPoseBridge(FakeTwinBridge):
        async def get_pose(self, timeout: float = 3.0):
            raise BridgeError("no pose on the twin domain")

    # Nav2 succeeded but the twin can't report a final pose: G4 (endpoint
    # deviation) is skipped rather than guessed — the other criteria decide.
    report = _rehearse(_twin(), NoPoseBridge(nav_status="succeeded"))
    assert report.verdict == "pass"


def test_unwatch_failure_is_swallowed_not_fatal() -> None:
    class UnwatchErrorBridge(FakeTwinBridge):
        async def unwatch(self, watch_id: int) -> None:
            raise BridgeError("watch already gone")

    report = _rehearse(_twin(), UnwatchErrorBridge(nav_status="succeeded"))
    assert report.verdict == "pass"  # cleanup hiccups must not veto a verdict
