from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from jenai.cli.main import app
from jenai.config import save_config
from jenai.config.store import build_minimal_config

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert "JenAI 0.1.0" in result.stdout


def test_doctor_json_command(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    save_config(
        build_minimal_config(
            provider_name="test",
            provider="openai",
            default_model="gpt-test",
            api_key_env="",
        ),
        config_path,
    )

    result = runner.invoke(app, ["doctor", "--config", str(config_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["overall"] in {"pass", "warn", "fail"}
    assert isinstance(payload["items"], list)

