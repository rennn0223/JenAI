from __future__ import annotations

from jenai.config.setup import run_setup_wizard
from jenai.config.store import load_config


def test_run_setup_wizard_writes_config_and_starter_locations_file(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"

    answers = iter(
        [
            "my-provider",
            "openai",
            "gpt-test",
            "",
            "OPENAI_API_KEY",
            "locations.toml",
        ]
    )
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(answers))

    written = run_setup_wizard(config_path)

    assert written == config_path
    loaded = load_config(config_path)
    assert loaded.active_provider == "my-provider"
    assert loaded.locations_path == "locations.toml"

    locations_file = tmp_path / "locations.toml"
    assert locations_file.exists()


def test_run_setup_wizard_respects_custom_locations_path(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"

    answers = iter(
        [
            "my-provider",
            "openai",
            "gpt-test",
            "",
            "OPENAI_API_KEY",
            "custom/locations.toml",
        ]
    )
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(answers))

    run_setup_wizard(config_path)

    assert (tmp_path / "custom" / "locations.toml").exists()
