from __future__ import annotations

import asyncio
import math
import threading
from types import SimpleNamespace

import pytest

from jenai.bridge import BridgeError, RosBridgeClient
from jenai.bridge._occupancy import occupancy_grid_identity
from jenai.bridge._protocol import dispatch_request
from jenai.bridge._watchdog import WatchdogState
from jenai.config.models import AppConfig, SiteProfile
from jenai.schemas import RouteOutput
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

    def map_identity(self, timeout: float) -> dict:
        self.timeout = timeout
        return {"op": "map_identity"}


def test_protocol_dispatches_bounded_map_identity_read() -> None:
    node = _ProtocolNode()

    result = dispatch_request(node, "map_identity", {}, WatchdogState())

    assert result == {"op": "map_identity"}
    assert node.timeout == 3.0


@pytest.mark.parametrize(
    "mutation",
    [
        {"algorithm": "md5"},
        {"digest": "not-a-digest"},
        {"width": 0},
        {"resolution": 0.0},
        {"frame_id": ""},
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
        }
        payload.update(mutation)

        async def request(*_args, **_kwargs):
            return payload

        monkeypatch.setattr(client, "request", request)
        with pytest.raises(BridgeError, match="invalid map_identity response"):
            await client.map_identity()

    asyncio.run(run())


def test_active_site_requires_validated_map_identity() -> None:
    with pytest.raises(ValueError, match="validated"):
        SiteProfile(
            site_id="isaac-warehouse",
            display_name="Isaac Warehouse",
            version="1",
            active=True,
            map_sha256="a" * 64,
        )


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
