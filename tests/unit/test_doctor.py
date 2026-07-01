from __future__ import annotations

from pathlib import Path

from jenai.config import save_config
from jenai.config.store import build_minimal_config
from jenai.doctor import run_doctor


def test_doctor_reports_missing_config(tmp_path: Path) -> None:
    result = run_doctor(tmp_path / "missing.toml")

    assert result.overall == "fail"
    assert any(item.section == "config" and item.status == "fail" for item in result.items)


def test_doctor_reports_provider_and_models_from_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JENAI_TEST_API_KEY", "test-key")
    path = tmp_path / "config.toml"
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="gpt-test",
        api_key_env="JENAI_TEST_API_KEY",
    )
    save_config(config, path)

    result = run_doctor(path)

    assert any(
        item.section == "provider"
        and item.check_name == "active_provider"
        and item.status == "pass"
        for item in result.items
    )
    assert any(
        item.section == "provider"
        and item.check_name == "model_bindings"
        and item.status == "pass"
        for item in result.items
    )

