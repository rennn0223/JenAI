from pathlib import Path

from jenai.state.data_lifecycle import purge_data


def test_purge_preserves_non_opted_in_secret_nested_in_data_directory(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    generated = sessions / "session.json"
    generated.write_text("generated", encoding="utf-8")
    credentials = sessions / ".env"
    credentials.write_text("API_KEY=keep", encoding="utf-8")

    purge_data(
        [("sessions", sessions)],
        protected_paths=(credentials,),
    )

    assert not generated.exists()
    assert credentials.read_text(encoding="utf-8") == "API_KEY=keep"


def test_file_category_never_recursively_deletes_unexpected_directory(
    tmp_path: Path,
) -> None:
    unexpected_config_directory = tmp_path / "config.toml"
    unexpected_config_directory.mkdir()
    child = unexpected_config_directory / "keep.txt"
    child.write_text("keep", encoding="utf-8")

    purge_data([("config", unexpected_config_directory)])

    assert child.exists()
