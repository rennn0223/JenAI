from __future__ import annotations

from types import SimpleNamespace

from jenai.config.models import AppConfig, SiteProfile

_DIGEST = "a" * 64


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
