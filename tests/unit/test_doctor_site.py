from __future__ import annotations

import asyncio
from types import SimpleNamespace

from jenai.config.models import AppConfig, SiteProfile

_DIGEST = "a" * 64


def test_map_identity_probe_retries_one_cold_dds_miss(monkeypatch) -> None:
    from jenai.bridge import BridgeError
    from jenai.doctor import site

    calls = 0
    bridge = SimpleNamespace()

    async def map_identity(*, timeout: float):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise BridgeError("No latched OccupancyGrid received on /map")
        return SimpleNamespace(digest=_DIGEST, frame_id="map")

    stopped = False

    async def stop() -> None:
        nonlocal stopped
        stopped = True

    bridge.map_identity = map_identity
    bridge.stop = stop
    monkeypatch.setattr(site, "RosBridgeClient", lambda: bridge)

    observed = asyncio.run(site._read_active_map_identity_async())

    assert observed.digest == _DIGEST
    assert calls == 2
    assert stopped is True


def _active_site() -> AppConfig:
    return AppConfig(
        locations_path="locations.toml",
        site=SiteProfile(
            site_id="isaac-warehouse",
            display_name="Isaac Warehouse",
            active=True,
            validated=True,
            map_sha256=_DIGEST,
            map_frame="map",
            locations_sha256="b" * 64,
        ),
    )


def test_doctor_skips_map_probe_when_site_profile_is_inactive(monkeypatch) -> None:
    from jenai.doctor import site

    monkeypatch.setattr(
        site,
        "_read_active_map_identity",
        lambda: (_ for _ in ()).throw(AssertionError("inactive site must not probe ROS")),
    )

    assert site.check_site(AppConfig()) == []


def test_doctor_passes_only_when_active_site_matches_live_map(monkeypatch) -> None:
    from jenai.doctor import site

    monkeypatch.setattr(
        site,
        "_read_active_map_identity",
        lambda: SimpleNamespace(digest=_DIGEST, frame_id="map"),
    )

    item = site.check_site(_active_site())[0]

    assert item.check_name == "map_identity"
    assert item.status == "pass"
    assert "Isaac Warehouse" in item.message


def test_doctor_fails_closed_when_active_site_map_differs(monkeypatch) -> None:
    from jenai.doctor import site

    monkeypatch.setattr(
        site,
        "_read_active_map_identity",
        lambda: SimpleNamespace(digest="b" * 64, frame_id="map"),
    )

    item = site.check_site(_active_site())[0]

    assert item.status == "fail"
    assert "mismatch" in item.message.lower()
    assert item.fix_suggestion


def test_doctor_fails_closed_when_active_site_cannot_be_verified(monkeypatch) -> None:
    from jenai.bridge import BridgeError
    from jenai.doctor import site

    monkeypatch.setattr(
        site,
        "_read_active_map_identity",
        lambda: (_ for _ in ()).throw(BridgeError("no map")),
    )

    item = site.check_site(_active_site())[0]

    assert item.status == "fail"
    assert "could not verify" in item.message.lower()
    assert item.fix_suggestion


def test_doctor_fails_when_locations_content_differs(monkeypatch, tmp_path) -> None:
    from jenai.doctor import site

    monkeypatch.setattr(
        site,
        "_read_active_map_identity",
        lambda: SimpleNamespace(digest=_DIGEST, frame_id="map"),
    )
    (tmp_path / "locations.toml").write_text("# changed locations\n", encoding="utf-8")

    items = site.check_site(_active_site(), tmp_path / "config.toml")
    asset = next(item for item in items if item.check_name == "locations_identity")

    assert asset.status == "fail"
