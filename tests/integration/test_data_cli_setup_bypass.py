from typer.testing import CliRunner

from jenai.cli.main import app


def test_bare_data_help_does_not_require_config_or_start_setup(monkeypatch) -> None:
    def unexpected_setup(_path):
        raise AssertionError("data help must not start setup")

    monkeypatch.setattr("jenai.cli.main.run_setup_wizard", unexpected_setup)

    result = CliRunner().invoke(app, ["data"])

    assert result.exit_code == 0
    assert "status" in result.stdout
    assert "harden" in result.stdout
    assert "export" in result.stdout
    assert "purge" in result.stdout
    assert "prune" in result.stdout
