from __future__ import annotations

import os
from pathlib import Path

from jenai.config import default_env_file_path, load_env_file


def test_load_env_file_sets_variables(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("JENAI_TEST_TOKEN_A", raising=False)
    monkeypatch.delenv("JENAI_TEST_TOKEN_B", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "\n"
        "JENAI_TEST_TOKEN_A=plain-value\n"
        'export JENAI_TEST_TOKEN_B="quoted value"\n'
        "not a valid line\n",
        encoding="utf-8",
    )

    result = load_env_file(env_file)

    assert result.found
    assert result.loaded == ["JENAI_TEST_TOKEN_A", "JENAI_TEST_TOKEN_B"]
    assert os.environ["JENAI_TEST_TOKEN_A"] == "plain-value"
    assert os.environ["JENAI_TEST_TOKEN_B"] == "quoted value"
    monkeypatch.delenv("JENAI_TEST_TOKEN_A")
    monkeypatch.delenv("JENAI_TEST_TOKEN_B")


def test_load_env_file_never_overrides_existing_environment(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("JENAI_TEST_TOKEN_C", "from-shell")
    env_file = tmp_path / ".env"
    env_file.write_text("JENAI_TEST_TOKEN_C=from-file\n", encoding="utf-8")

    result = load_env_file(env_file)

    assert result.skipped == ["JENAI_TEST_TOKEN_C"]
    assert os.environ["JENAI_TEST_TOKEN_C"] == "from-shell"


def test_load_env_file_missing_default_is_not_an_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("JENAI_ENV_FILE", raising=False)
    monkeypatch.setenv("JENAI_CONFIG", str(tmp_path / "config.toml"))

    result = load_env_file()

    assert not result.found
    assert not result.explicit
    assert result.path == tmp_path / ".env"


def test_load_env_file_flags_explicit_missing_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JENAI_ENV_FILE", str(tmp_path / "nope.env"))

    result = load_env_file()

    assert not result.found
    assert result.explicit
    assert result.path == tmp_path / "nope.env"


def test_default_env_file_path_sits_next_to_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("JENAI_ENV_FILE", raising=False)
    monkeypatch.setenv("JENAI_CONFIG", str(tmp_path / "custom" / "config.toml"))

    assert default_env_file_path() == tmp_path / "custom" / ".env"


def test_doctor_reports_env_file(tmp_path: Path, monkeypatch) -> None:
    from jenai.doctor import run_doctor

    monkeypatch.setenv("JENAI_ENV_FILE", str(tmp_path / "missing.env"))
    result = run_doctor(tmp_path / "config.toml")
    item = next(i for i in result.items if i.check_name == "env_file")
    assert item.status == "warn"

    (tmp_path / "missing.env").write_text("", encoding="utf-8")
    result = run_doctor(tmp_path / "config.toml")
    item = next(i for i in result.items if i.check_name == "env_file")
    assert item.status == "pass"
