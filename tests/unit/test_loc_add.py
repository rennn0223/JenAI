from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jenai.adapters.locations import LocationsFileError, append_location, load_locations
from jenai.bridge import BridgeError, PoseInfo
from jenai.config.store import build_minimal_config
from jenai.schemas import Location, Pose2D
from jenai.tui import JenAITuiApp


def test_append_location_creates_and_appends(tmp_path: Path) -> None:
    path = tmp_path / "locations.toml"
    append_location(Location(name="Dock", pose=Pose2D(x=1, y=2, yaw=0)), path)
    append_location(Location(name="Kitchen", pose=Pose2D(x=3, y=4, yaw=1)), path)

    names = [loc.name for loc in load_locations(path)]
    assert names == ["Dock", "Kitchen"]


def test_append_location_rejects_duplicate_names_and_aliases(tmp_path: Path) -> None:
    path = tmp_path / "locations.toml"
    append_location(
        Location(name="Dock", aliases=["充電站"], pose=Pose2D(x=1, y=2, yaw=0)), path
    )
    with pytest.raises(LocationsFileError, match="already exists"):
        append_location(Location(name="dock", pose=Pose2D(x=0, y=0, yaw=0)), path)
    with pytest.raises(LocationsFileError, match="already exists"):
        append_location(Location(name="充電站", pose=Pose2D(x=0, y=0, yaw=0)), path)


def _app(tmp_path: Path) -> JenAITuiApp:
    from jenai.config import save_config

    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="gpt-test",
        api_key_env="",
    )
    config_path = tmp_path / "config.toml"
    save_config(config, config_path)
    return JenAITuiApp(config=config, config_path=config_path)


class _FakeBridge:
    def __init__(self, pose: PoseInfo | None) -> None:
        self._pose = pose

    async def get_pose(self, timeout: float = 3.0) -> PoseInfo:
        if self._pose is None:
            raise BridgeError("No pose received on /amcl_pose or /odom (are they publishing?)")
        return self._pose


def test_loc_add_saves_current_pose(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        app = _app(tmp_path)
        fake = _FakeBridge(
            PoseInfo(x=2.5, y=-1.25, yaw=0.7854, frame_id="map", source="/amcl_pose")
        )

        async def fake_get_bridge():
            return fake

        monkeypatch.setattr(app, "_get_bridge", fake_get_bridge)
        async with app.run_test():
            await app.handle_user_text("/loc add here Charging Dock")

        saved = load_locations(tmp_path / "locations.toml")
        assert saved[0].name == "Charging Dock"
        assert (saved[0].pose.x, saved[0].pose.y) == (2.5, -1.25)
        assert saved[0].frame_id == "map"

    asyncio.run(run())


def test_loc_add_without_pose_reports_warning_and_saves_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    async def run() -> None:
        app = _app(tmp_path)
        fake = _FakeBridge(None)

        async def fake_get_bridge():
            return fake

        monkeypatch.setattr(app, "_get_bridge", fake_get_bridge)
        async with app.run_test():
            await app.handle_user_text("/loc add here Nowhere")

        assert not (tmp_path / "locations.toml").exists()

    asyncio.run(run())


def test_loc_add_placeholder_and_empty_rejected(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        app = _app(tmp_path)
        async with app.run_test():
            await app.handle_user_text("/loc add here <name>")
            await app.handle_user_text("/loc add here")

        assert not (tmp_path / "locations.toml").exists()

    asyncio.run(run())
