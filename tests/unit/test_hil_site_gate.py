from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from jenai.acceptance import isaac_hil


def _item(section: str, name: str, status: str) -> SimpleNamespace:
    payload = {
        "section": section,
        "check_name": name,
        "status": status,
        "message": name,
        "fix_suggestion": "fix" if status != "pass" else None,
    }
    return SimpleNamespace(model_dump=lambda **_kwargs: payload)


def test_hil_requires_map_identity_when_doctor_reports_active_site(monkeypatch) -> None:
    items = [
        _item("ros2", "ros2_cli", "pass"),
        _item("nav", "map", "pass"),
        _item("nav", "localization", "pass"),
        _item("nav", "laser", "pass"),
        _item("nav", "nav2", "pass"),
        _item("nav", "cmd_vel", "pass"),
        _item("site", "map_identity", "fail"),
    ]
    monkeypatch.setattr(
        isaac_hil,
        "run_doctor",
        lambda _path: SimpleNamespace(items=items, overall="fail"),
    )

    _items, passed, evidence = isaac_hil._doctor_checks(
        Path("config.toml"),
        attempts=1,
    )

    assert passed is False
    assert evidence["required_checks"] == [
        "cmd_vel",
        "laser",
        "localization",
        "map",
        "map_identity",
        "nav2",
        "ros2_cli",
    ]
    assert evidence["non_passing_required"][0]["check_name"] == "map_identity"
