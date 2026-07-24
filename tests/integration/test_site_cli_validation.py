"""Regression tests for the Site Profile validation command contract."""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from jenai.cli.main import app
from jenai.config import save_config
from jenai.config.models import SiteProfile
from jenai.config.store import build_minimal_config
from jenai.schemas import DoctorCheckItem, DoctorStatus

runner = CliRunner()


def _config_with_site(tmp_path, *, active: bool):
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="model",
        api_key_env="",
    )
    config.site = SiteProfile(
        site_id="warehouse",
        display_name="Warehouse",
        active=active,
        validated=True,
        map_sha256="a" * 64,
        locations_path="locations.toml",
    )
    config_path = tmp_path / "config.toml"
    save_config(config, config_path)
    return config_path


def test_site_validate_repair_exits_success_after_pass_checks(tmp_path, monkeypatch) -> None:
    config_path = _config_with_site(tmp_path, active=True)

    monkeypatch.setattr(
        "jenai.cli.site.read_live_map_identity",
        lambda: SimpleNamespace(digest="a" * 64, frame_id="map"),
    )

    def fake_revalidate(config, *_args, **_kwargs):
        config.site.locations_sha256 = "b" * 64
        return config

    monkeypatch.setattr(
        "jenai.cli.site.revalidate_active_site_profile",
        fake_revalidate,
    )
    monkeypatch.setattr(
        "jenai.cli.site.check_site",
        lambda *_args: [
            DoctorCheckItem(
                section="site",
                check_name="map_identity",
                status=DoctorStatus.PASS,
                message="Live map matches the Site Profile.",
            )
        ],
    )

    result = runner.invoke(
        app,
        [
            "site",
            "validate",
            "--config",
            str(config_path),
            "--repair",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert "Revalidated Site Profile" in result.stdout
    assert "pass map_identity" in result.stdout


def test_site_validate_without_active_profile_fails_before_checks(tmp_path, monkeypatch) -> None:
    config_path = _config_with_site(tmp_path, active=False)
    check_called = False

    def unexpected_check(*_args):
        nonlocal check_called
        check_called = True
        raise AssertionError("inactive profiles must not be validated")

    monkeypatch.setattr("jenai.cli.site.check_site", unexpected_check)

    result = runner.invoke(
        app,
        ["site", "validate", "--config", str(config_path)],
    )

    assert result.exit_code == 1
    assert "No active Site Profile" in result.stderr
    assert check_called is False
