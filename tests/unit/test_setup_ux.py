from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

import pytest

from jenai.config.setup import PRESETS, _secure_api_key_input, run_setup_wizard
from jenai.config.store import load_config


def _drive(monkeypatch: pytest.MonkeyPatch, answers: Sequence[str]) -> None:
    iterator = iter(answers)
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(iterator))


def test_setup_explains_invalid_fields_and_recovers_in_place(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.toml"
    _drive(
        monkeypatch,
        [
            "3",  # OpenAI
            "",
            "my-provider",
            "",
            "gpt-test",
            "not-a-url",
            "",
            "OPENAI_API_KEY",
            "",
            "locations.toml",
        ],
    )

    run_setup_wizard(config_path)

    output = capsys.readouterr().out
    loaded = load_config(config_path)
    assert "Profile 名稱不可留白" in output
    assert "預設模型不可留白" in output
    assert "Base URL 必須是完整的 http:// 或 https:// 網址" in output
    assert "Locations 檔路徑不可留白" in output
    assert loaded.active_provider == "my-provider"
    assert loaded.model_bindings is not None
    assert loaded.model_bindings.default == "gpt-test"


def test_setup_completion_summary_contains_the_real_next_journey(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.toml"
    _drive(
        monkeypatch,
        ["1", "local", "qwen3:8b", "http://localhost:11434/v1", "", "locations.toml"],
    )

    run_setup_wizard(config_path)

    output = capsys.readouterr().out
    assert "Terminal-first AI agent for ROS 2 robots" in output
    assert "Locations" in output
    assert str(tmp_path / "locations.toml") in output
    assert "1. JenAI doctor" in output
    assert "2. JenAI" in output


def test_setup_hides_a_pasted_secret_and_reports_the_actual_env_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "custom" / "config.toml"
    secret = "Abcdef1234567890"
    prompts: list[dict[str, object]] = []
    answers = iter(
        [
            "2",
            "nvidia-cloud",
            "meta/llama-3.3-70b-instruct",
            "https://integrate.api.nvidia.com/v1",
            secret,
            "locations.toml",
        ]
    )

    def prompt(*_args: object, **kwargs: object) -> str:
        prompts.append(kwargs)
        return next(answers)

    monkeypatch.setattr("typer.prompt", prompt)

    run_setup_wizard(config_path)

    assert prompts[4]["hide_input"] is True
    assert (config_path.parent / ".env").read_text(encoding="utf-8") == (
        f"NVIDIA_API_KEY={secret}\n"
    )
    assert secret not in capsys.readouterr().out


def test_setup_reports_the_env_path_next_to_a_custom_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "custom" / "config.toml"
    _drive(
        monkeypatch,
        [
            "2",
            "nvidia-cloud",
            "meta/llama-3.3-70b-instruct",
            "https://integrate.api.nvidia.com/v1",
            "NVIDIA_API_KEY",
            "locations.toml",
        ],
    )

    run_setup_wizard(config_path)

    output = capsys.readouterr().out
    assert str(config_path.parent / ".env") in output
    assert "~/.config/jenai/.env" not in output


def test_setup_secret_write_does_not_follow_predictable_temp_symlink(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    env_path = tmp_path / ".env"
    victim = tmp_path / "victim.txt"
    victim.write_text("preserve me\n", encoding="utf-8")
    predictable_temp = env_path.with_name(f".{env_path.name}.{os.getpid()}.tmp")
    predictable_temp.symlink_to(victim)

    env_name, written = _secure_api_key_input(
        "nvapi-super-secret",
        PRESETS[1],
        config_path,
    )

    assert env_name == "NVIDIA_API_KEY"
    assert written == env_path
    assert victim.read_text(encoding="utf-8") == "preserve me\n"
    assert env_path.read_text(encoding="utf-8") == "NVIDIA_API_KEY=nvapi-super-secret\n"


def test_setup_relocates_identifier_shaped_pasted_secret(tmp_path: Path) -> None:
    config_path = tmp_path / "custom" / "config.toml"
    secret = "Abcdef1234567890"

    env_name, written = _secure_api_key_input(secret, PRESETS[1], config_path)

    assert env_name == "NVIDIA_API_KEY"
    assert written == config_path.parent / ".env"
    assert written.read_text(encoding="utf-8") == f"NVIDIA_API_KEY={secret}\n"


def test_setup_accepts_explicit_environment_variable_reference(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"

    env_name, written = _secure_api_key_input(
        "env:MY_NVIDIA_KEY",
        PRESETS[1],
        config_path,
    )

    assert env_name == "MY_NVIDIA_KEY"
    assert written is None
    assert not (tmp_path / ".env").exists()
