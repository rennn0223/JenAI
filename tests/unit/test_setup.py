from __future__ import annotations

import stat

from jenai.config.setup import run_setup_wizard
from jenai.config.store import load_config


def _drive(monkeypatch, answers: list[str]) -> None:
    it = iter(answers)
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(it))


def test_run_setup_wizard_writes_config_and_starter_locations_file(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"

    # choice=3 (OpenAI preset), then name/model/base_url/api_key_env/locations
    _drive(monkeypatch, ["3", "my-provider", "gpt-test", "", "OPENAI_API_KEY", "locations.toml"])

    written = run_setup_wizard(config_path)

    assert written == config_path
    loaded = load_config(config_path)
    assert loaded.active_provider == "my-provider"
    assert loaded.provider_profiles["my-provider"].provider == "openai"
    assert loaded.locations_path == "locations.toml"
    assert (tmp_path / "locations.toml").exists()


def test_run_setup_wizard_respects_custom_locations_path(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"

    _drive(
        monkeypatch, ["3", "my-provider", "gpt-test", "", "OPENAI_API_KEY", "custom/locations.toml"]
    )

    run_setup_wizard(config_path)

    assert (tmp_path / "custom" / "locations.toml").exists()


def test_run_setup_wizard_local_preset_and_bad_choice_retry(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"

    # "9" is out of range → wizard re-asks instead of crashing; "1" = local Ollama
    _drive(
        monkeypatch,
        ["9", "1", "local", "qwen3:8b", "http://localhost:11434/v1", "", "locations.toml"],
    )

    run_setup_wizard(config_path)

    loaded = load_config(config_path)
    assert loaded.provider_profiles["local"].provider == "ollama"
    assert loaded.provider_profiles["local"].base_url == "http://localhost:11434/v1"
    # TOML round-trips a cleared key env as "" — either way means "no key needed"
    assert not loaded.provider_profiles["local"].api_key_env


def test_setup_moves_accidentally_pasted_nvidia_key_to_env(tmp_path, monkeypatch, capsys) -> None:
    config_path = tmp_path / "config.toml"
    secret = "nvapi-example-secret"
    (tmp_path / ".env").write_text("export NVIDIA_API_KEY=old\nKEEP_ME=yes\n", encoding="utf-8")
    _drive(
        monkeypatch,
        [
            "2",
            "nvidia-cloud",
            "meta/llama-3.3-70b-instruct",
            "https://integrate.api.nvidia.com/v1",
            secret,
            "locations.toml",
        ],
    )

    run_setup_wizard(config_path)

    loaded = load_config(config_path)
    assert loaded.provider_profiles["nvidia-cloud"].api_key_env == "NVIDIA_API_KEY"
    env_path = tmp_path / ".env"
    assert env_path.read_text(encoding="utf-8") == f"KEEP_ME=yes\nNVIDIA_API_KEY={secret}\n"
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert secret not in config_path.read_text(encoding="utf-8")
    assert secret not in capsys.readouterr().out
