from __future__ import annotations

import asyncio
import math
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenai.adapters.locations import save_locations
from jenai.bridge import BridgeError, RosBridgeClient
from jenai.bridge._occupancy import occupancy_grid_identity
from jenai.bridge._protocol import dispatch_request
from jenai.bridge._watchdog import WatchdogState
from jenai.config.models import AppConfig, SiteProfile
from jenai.schemas import Location, Pose2D, RouteOutput
from jenai.site_assets import fingerprint_locations_file
from jenai.tools import navigation_gateway as gateway_module


def test_occupancy_grid_identity_is_stable_and_content_sensitive() -> None:
    values = {
        "data": [0, -1, 100, 0],
        "width": 2,
        "height": 2,
        "resolution": 0.05,
        "origin_x": -1.0,
        "origin_y": -2.0,
        "origin_yaw": 0.0,
        "frame_id": "map",
    }

    first = occupancy_grid_identity(**values)
    second = occupancy_grid_identity(**values)
    changed = occupancy_grid_identity(**{**values, "data": [0, -1, 99, 0]})

    assert first == second
    assert len(first) == 64
    assert changed != first


@pytest.mark.parametrize(
    "overrides",
    [
        {"data": [0]},
        {"data": [0, -2, 100, 0]},
        {"frame_id": ""},
        {"origin_yaw": math.nan},
    ],
)
def test_occupancy_grid_identity_rejects_malformed_grid(overrides: dict) -> None:
    values = {
        "data": [0, -1, 100, 0],
        "width": 2,
        "height": 2,
        "resolution": 0.05,
        "origin_x": -1.0,
        "origin_y": -2.0,
        "origin_yaw": 0.0,
        "frame_id": "map",
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        occupancy_grid_identity(**values)


class _ProtocolNode:
    def __init__(self) -> None:
        self._halt_lock = threading.Lock()
        self.timeout: float | None = None

    def map_identity(self, timeout: float, reset_subscription: bool) -> dict:
        self.timeout = timeout
        return {"op": "map_identity", "reset_subscription": reset_subscription}

    def map_source_identity(self) -> dict:
        return {"runtime_source_sha256": "a" * 64}


def test_protocol_dispatches_bounded_map_identity_read() -> None:
    node = _ProtocolNode()

    result = dispatch_request(node, "map_identity", {}, WatchdogState())

    assert result == {"op": "map_identity", "reset_subscription": False}
    assert node.timeout == 3.0


def test_protocol_dispatches_clean_map_resubscription_and_source_identity() -> None:
    node = _ProtocolNode()
    watchdog = WatchdogState()

    reset = dispatch_request(
        node,
        "map_identity",
        {"timeout": 1.5, "reset_subscription": True},
        watchdog,
    )
    source = dispatch_request(node, "map_source_identity", {}, watchdog)

    assert reset == {"op": "map_identity", "reset_subscription": True}
    assert node.timeout == 1.5
    assert source == {"runtime_source_sha256": "a" * 64}


@pytest.mark.parametrize(
    "mutation",
    [
        {"algorithm": "md5"},
        {"digest": "not-a-digest"},
        {"width": 0},
        {"resolution": 0.0},
        {"frame_id": ""},
        {"runtime_source_sha256": "not-a-digest"},
    ],
)
def test_bridge_map_identity_rejects_malformed_evidence(monkeypatch, mutation) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        payload = {
            "algorithm": "sha256-occupancy-grid-v1",
            "digest": "a" * 64,
            "width": 20,
            "height": 30,
            "resolution": 0.05,
            "origin_x": -1.0,
            "origin_y": -2.0,
            "origin_yaw": 0.0,
            "frame_id": "map",
            "source": "/map",
            "runtime_source_sha256": "b" * 64,
        }
        payload.update(mutation)

        async def request(*_args, **_kwargs):
            return payload

        monkeypatch.setattr(client, "request", request)
        with pytest.raises(BridgeError, match="invalid map_identity response"):
            await client.map_identity()

    asyncio.run(run())


def test_bridge_map_pin_reuses_one_publication_in_the_same_runtime(monkeypatch) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        map_reads = 0
        runtime = object()
        payload = {
            "algorithm": "sha256-occupancy-grid-v1",
            "digest": "a" * 64,
            "width": 20,
            "height": 30,
            "resolution": 0.05,
            "origin_x": -1.0,
            "origin_y": -2.0,
            "origin_yaw": 0.0,
            "frame_id": "map",
            "source": "/map",
            "runtime_source_sha256": "b" * 64,
        }

        async def runtime_identity():
            return runtime

        async def request(op, *_args, **_kwargs):
            nonlocal map_reads
            if op == "map_source_identity":
                return {"runtime_source_sha256": "b" * 64}
            assert op == "map_identity"
            map_reads += 1
            return payload

        monkeypatch.setattr(client, "runtime_identity", runtime_identity)
        monkeypatch.setattr(client, "request", request)

        first = await client.map_identity(binding_sha256="c" * 64)
        second = await client.map_identity(binding_sha256="c" * 64)

        assert first is second
        assert map_reads == 1

    asyncio.run(run())


def test_forced_map_reacquisition_does_not_require_a_source_probe(monkeypatch) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        operations: list[str] = []

        async def runtime_identity():
            return object()

        async def request(op, *_args, **kwargs):
            operations.append(op)
            assert op == "map_identity"
            assert kwargs["params"]["reset_subscription"] is True
            return {
                "algorithm": "sha256-occupancy-grid-v1",
                "digest": "a" * 64,
                "width": 20,
                "height": 30,
                "resolution": 0.05,
                "origin_x": -1.0,
                "origin_y": -2.0,
                "origin_yaw": 0.0,
                "frame_id": "map",
                "source": "/map",
                "runtime_source_sha256": "b" * 64,
            }

        monkeypatch.setattr(client, "runtime_identity", runtime_identity)
        monkeypatch.setattr(client, "request", request)

        identity = await client.map_identity(
            binding_sha256="c" * 64,
            reset_subscription=True,
        )

        assert identity.digest == "a" * 64
        assert operations == ["map_identity"]

    asyncio.run(run())


@pytest.mark.parametrize("changed_binding", ["bridge_runtime", "map_runtime", "site_profile"])
def test_bridge_map_pin_invalidates_when_any_runtime_binding_changes(
    monkeypatch,
    changed_binding: str,
) -> None:
    async def run() -> None:
        client = RosBridgeClient()
        runtime_state = [object()]
        source_state = ["b" * 64]
        map_reads = 0

        async def runtime_identity():
            return runtime_state[0]

        async def request(op, *_args, **_kwargs):
            nonlocal map_reads
            if op == "map_source_identity":
                return {"runtime_source_sha256": source_state[0]}
            assert op == "map_identity"
            map_reads += 1
            return {
                "algorithm": "sha256-occupancy-grid-v1",
                "digest": ("a" if map_reads == 1 else "d") * 64,
                "width": 20,
                "height": 30,
                "resolution": 0.05,
                "origin_x": -1.0,
                "origin_y": -2.0,
                "origin_yaw": 0.0,
                "frame_id": "map",
                "source": "/map",
                "runtime_source_sha256": source_state[0],
            }

        monkeypatch.setattr(client, "runtime_identity", runtime_identity)
        monkeypatch.setattr(client, "request", request)

        binding = "c" * 64
        first = await client.map_identity(binding_sha256=binding)
        if changed_binding == "bridge_runtime":
            runtime_state[0] = object()
        elif changed_binding == "map_runtime":
            source_state[0] = "e" * 64
        else:
            binding = "f" * 64
        second = await client.map_identity(binding_sha256=binding)

        assert first.digest == "a" * 64
        assert second.digest == "d" * 64
        assert map_reads == 2

    asyncio.run(run())


def test_active_site_without_validation_is_not_execution_ready() -> None:
    site = SiteProfile(
        site_id="isaac-warehouse",
        display_name="Isaac Warehouse",
        version="1",
        active=True,
        map_sha256="a" * 64,
    )

    assert site.active is True
    assert site.execution_ready is False


def test_active_site_blocks_navigation_when_observed_map_differs(monkeypatch) -> None:
    expected = "a" * 64
    observed = "b" * 64
    bridge = SimpleNamespace(running=True)

    async def get_bridge():
        return bridge

    async def identity(*_args, **_kwargs):
        return SimpleNamespace(digest=observed, frame_id="map")

    bridge.map_identity = identity

    async def fake_arm(_config, _bridge) -> None:
        return None

    async def must_not_dispatch(*_args, **_kwargs):
        raise AssertionError("navigation must not start on a map mismatch")

    monkeypatch.setattr(gateway_module, "arm_watchdog", fake_arm)
    monkeypatch.setattr(gateway_module, "navigate_with_fallback", must_not_dispatch)
    config = AppConfig(
        locations_path="locations.toml",
        site=SiteProfile(
            site_id="isaac-warehouse",
            display_name="Isaac Warehouse",
            version="1",
            active=True,
            validated=True,
            map_sha256=expected,
            locations_sha256="c" * 64,
        ),
    )
    gateway = gateway_module.NavigationGateway(config, get_bridge=get_bridge)

    output = asyncio.run(
        gateway.execute({"goal": {"frame_id": "map", "pose": {"x": 1.0, "y": 2.0, "yaw": 0.0}}})
    )

    assert output.execution_status == "blocked"
    assert "map identity mismatch" in output.route_preview.lower()
    assert expected[:12] in output.route_preview
    assert observed[:12] in output.route_preview


def test_newly_observed_different_map_blocks_later_navigation(monkeypatch) -> None:
    expected = "a" * 64
    observed = iter((expected, "d" * 64))
    dispatch_calls = 0

    class FakeBridge:
        running = True

        async def map_identity(self, **_kwargs) -> SimpleNamespace:
            return SimpleNamespace(digest=next(observed), frame_id="map")

    async def get_bridge() -> FakeBridge:
        return FakeBridge()

    async def fake_arm(_config, _bridge) -> None:
        return None

    async def dispatch(_config, _provider, _action, **_kwargs) -> RouteOutput:
        nonlocal dispatch_calls
        dispatch_calls += 1
        return RouteOutput(input_text="", execution_status="succeeded")

    monkeypatch.setattr(gateway_module, "arm_watchdog", fake_arm)
    monkeypatch.setattr(gateway_module, "navigate_with_fallback", dispatch)
    monkeypatch.setattr(
        gateway_module, "bind_navigation_action", lambda _config, _path, action: action
    )
    config = AppConfig(
        locations_path="locations.toml",
        site=SiteProfile(
            site_id="isaac-warehouse",
            display_name="Isaac Warehouse",
            version="1",
            active=True,
            validated=True,
            map_sha256=expected,
            locations_sha256="c" * 64,
        ),
    )
    config.vehicle.capabilities = ["navigate"]
    gateway = gateway_module.NavigationGateway(
        config,
        config_path=Path("/tmp/config.toml"),
        get_bridge=get_bridge,
    )
    action = {"goal": {"frame_id": "map", "pose": {"x": 1.0, "y": 2.0}}}

    first = asyncio.run(gateway.execute(action))
    second = asyncio.run(gateway.execute(action))

    assert first.execution_status == "succeeded"
    assert second.execution_status == "blocked"
    assert "map identity mismatch" in second.route_preview.lower()
    assert dispatch_calls == 1


def test_active_site_blocks_after_both_bounded_map_reads_fail(monkeypatch) -> None:
    expected = "a" * 64
    bridge = SimpleNamespace(running=True)
    calls = 0

    async def get_bridge():
        return bridge

    async def identity(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise BridgeError("No latched OccupancyGrid received on /map")

    bridge.map_identity = identity

    async def fake_arm(_config, _bridge) -> None:
        return None

    async def must_not_dispatch(*_args, **_kwargs):
        raise AssertionError("navigation must not start without verified map identity")

    monkeypatch.setattr(gateway_module, "arm_watchdog", fake_arm)
    monkeypatch.setattr(gateway_module, "navigate_with_fallback", must_not_dispatch)
    config = AppConfig(
        locations_path="locations.toml",
        site=SiteProfile(
            site_id="isaac-warehouse",
            display_name="Isaac Warehouse",
            version="1",
            active=True,
            validated=True,
            map_sha256=expected,
            locations_sha256="c" * 64,
        ),
    )
    gateway = gateway_module.NavigationGateway(config, get_bridge=get_bridge)

    output = asyncio.run(
        gateway.execute({"goal": {"frame_id": "map", "pose": {"x": 1.0, "y": 2.0, "yaw": 0.0}}})
    )

    assert output.execution_status == "blocked"
    assert "navigation was blocked" in output.route_preview.lower()
    assert calls == 2


def test_active_site_reacquires_map_once_after_a_cold_dds_miss(monkeypatch) -> None:
    expected = "a" * 64
    calls: list[bool] = []

    class FakeBridge:
        running = True

        async def map_identity(
            self,
            *,
            timeout: float,
            binding_sha256: str,
            reset_subscription: bool = False,
        ) -> SimpleNamespace:
            assert timeout == 3.0
            assert len(binding_sha256) == 64
            calls.append(reset_subscription)
            if not reset_subscription:
                raise BridgeError("No latched OccupancyGrid received on /map")
            return SimpleNamespace(
                digest=expected,
                frame_id="map",
                width=20,
                height=30,
                resolution=0.05,
                origin_x=-1.0,
                origin_y=-2.0,
                origin_yaw=0.0,
                runtime_source_sha256="b" * 64,
            )

    bridge = FakeBridge()
    dispatch_calls = 0

    async def get_bridge() -> FakeBridge:
        return bridge

    async def fake_arm(_config, _bridge) -> None:
        return None

    async def dispatch(_config, _provider, _action, **_kwargs) -> RouteOutput:
        nonlocal dispatch_calls
        dispatch_calls += 1
        return RouteOutput(input_text="", execution_status="succeeded")

    monkeypatch.setattr(gateway_module, "arm_watchdog", fake_arm)
    monkeypatch.setattr(gateway_module, "navigate_with_fallback", dispatch)
    monkeypatch.setattr(
        gateway_module, "bind_navigation_action", lambda _config, _path, action: action
    )
    config = AppConfig(
        locations_path="locations.toml",
        site=SiteProfile(
            site_id="isaac-warehouse",
            display_name="Isaac Warehouse",
            version="1",
            active=True,
            validated=True,
            map_sha256=expected,
            locations_sha256="c" * 64,
        ),
    )
    config.vehicle.capabilities = ["navigate"]
    gateway = gateway_module.NavigationGateway(
        config,
        config_path=Path("/tmp/config.toml"),
        get_bridge=get_bridge,
    )

    output = asyncio.run(
        gateway.execute({"goal": {"frame_id": "map", "pose": {"x": 1.0, "y": 2.0}}})
    )

    assert output.execution_status == "succeeded", output.route_preview
    assert calls == [False, True]
    assert dispatch_calls == 1


def test_unbound_site_blocks_navigation_before_dispatch(monkeypatch) -> None:
    bridge = SimpleNamespace(running=True)

    async def get_bridge():
        return bridge

    async def fake_arm(_config, _bridge) -> None:
        return None

    async def fake_dispatch(_config, _provider, _action, **_kwargs):
        assert not hasattr(bridge, "map_identity")
        return RouteOutput(input_text="", execution_status="succeeded")

    monkeypatch.setattr(gateway_module, "arm_watchdog", fake_arm)
    monkeypatch.setattr(gateway_module, "navigate_with_fallback", fake_dispatch)
    gateway = gateway_module.NavigationGateway(AppConfig(), get_bridge=get_bridge)

    output = asyncio.run(
        gateway.execute({"goal": {"frame_id": "map", "pose": {"x": 1.0, "y": 2.0, "yaw": 0.0}}})
    )

    assert output.execution_status == "blocked"
    assert "no validated site profile is active" in output.route_preview.lower()


def test_gateway_blocks_goal_that_differs_from_validated_location(
    monkeypatch, tmp_path: Path
) -> None:
    expected = "a" * 64
    locations_path = tmp_path / "locations.toml"
    location = Location(
        name="Dock",
        frame_id="map",
        pose=Pose2D(x=1.0, y=2.0, yaw=0.0),
    )
    save_locations([location], locations_path)
    bridge = SimpleNamespace(running=True)

    async def get_bridge():
        return bridge

    async def identity(*_args, **_kwargs):
        return SimpleNamespace(digest=expected, frame_id="map")

    bridge.map_identity = identity

    async def fake_arm(_config, _bridge) -> None:
        return None

    async def must_not_dispatch(*_args, **_kwargs):
        raise AssertionError("navigation must not start for a modified saved goal")

    monkeypatch.setattr(gateway_module, "arm_watchdog", fake_arm)
    monkeypatch.setattr(gateway_module, "navigate_with_fallback", must_not_dispatch)
    config = AppConfig(
        locations_path="locations.toml",
        site=SiteProfile(
            site_id="isaac-warehouse",
            display_name="Isaac Warehouse",
            active=True,
            validated=True,
            map_sha256=expected,
            locations_sha256=fingerprint_locations_file(locations_path),
            locations_path="locations.toml",
            validated_routes=["Dock"],
        ),
    )
    gateway = gateway_module.NavigationGateway(
        config, config_path=tmp_path / "config.toml", get_bridge=get_bridge
    )
    action = {"goal": location.model_dump(mode="json")}
    action["goal"]["pose"]["x"] = 9.0
    output = asyncio.run(gateway.execute(action))

    assert output.execution_status == "blocked"
    assert "does not match" in output.route_preview.lower()
