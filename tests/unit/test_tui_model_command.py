from __future__ import annotations

import asyncio
from pathlib import Path

from jenai.config import load_config
from jenai.config.store import build_minimal_config
from jenai.providers import ProviderChatError
from jenai.tui import JenAITuiApp
from jenai.tui.widgets import ModelPicker


def _app(tmp_path: Path) -> JenAITuiApp:
    config = build_minimal_config(
        provider_name="ollama",
        provider="openai",
        default_model="gpt-test",
        base_url="http://localhost:11434/v1",
        api_key_env="",
    )
    return JenAITuiApp(config=config, config_path=tmp_path / "config.toml")


def _fake_listing(models: list[str]):
    async def fake(_config) -> list[str]:
        return models

    return fake


def test_model_command_opens_picker(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        app = _app(tmp_path)
        monkeypatch.setattr(
            "jenai.tui.info_commands.list_provider_models", _fake_listing(["llama3.2", "qwen2.5"])
        )
        async with app.run_test():
            await app.handle_user_text("/model")

            assert app._available_models == ["llama3.2", "qwen2.5"]
            assert len(list(app.query(ModelPicker))) == 1  # bare /model = interactive picker

    asyncio.run(run())


class _Key:
    """Minimal stand-in for a Textual key event (on_key only reads .key/.stop)."""

    def __init__(self, key: str) -> None:
        self.key = key

    def stop(self) -> None:
        pass


def test_model_picker_starts_on_current_and_wraps() -> None:
    picker = ModelPicker(["a", "b", "c"], current="b")
    assert picker._selected == 1  # cursor starts on the active model
    picker.on_key(_Key("up"))
    assert picker._selected == 0
    picker.on_key(_Key("up"))
    assert picker._selected == 2  # wraps past the top


def test_model_picker_arrow_select_switches_binding(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "jenai.tui.info_commands.list_provider_models",
        _fake_listing(["model-one", "model-two", "model-three"]),
    )

    async def run() -> None:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await app.handle_user_text("/model")  # bare → opens the picker
            assert len(list(app.query(ModelPicker))) == 1

            await pilot.press("down")  # move off model-one → model-two
            await pilot.press("enter")
            await pilot.pause()

            assert list(app.query(ModelPicker)) == []  # picker closed
            assert app.config.model_bindings.chat == "model-two"
            assert app.config.model_bindings.default == "model-two"

    asyncio.run(run())


def test_model_picker_escape_keeps_current(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "jenai.tui.info_commands.list_provider_models", _fake_listing(["model-one", "model-two"])
    )

    async def run() -> None:
        app = _app(tmp_path)
        before = app.config.model_bindings.chat
        async with app.run_test() as pilot:
            await app.handle_user_text("/model")
            await pilot.press("down")  # move the cursor…
            await pilot.press("escape")  # …but cancel
            await pilot.pause()

            assert list(app.query(ModelPicker)) == []
            assert app.config.model_bindings.chat == before  # unchanged

    asyncio.run(run())


def test_model_command_switches_chat_and_default(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        app = _app(tmp_path)
        async with app.run_test():
            await app.handle_user_text("/model llama3.2")

        bindings = app.config.model_bindings
        assert bindings.chat == "llama3.2"
        assert bindings.default == "llama3.2"
        assert bindings.vision == "gpt-test"  # untouched

        persisted = load_config(tmp_path / "config.toml")
        assert persisted.model_bindings.chat == "llama3.2"

    asyncio.run(run())


def test_model_command_switches_by_number(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        app = _app(tmp_path)
        monkeypatch.setattr(
            "jenai.tui.info_commands.list_provider_models", _fake_listing(["llama3.2", "qwen2.5"])
        )
        async with app.run_test():
            await app.handle_user_text("/model 2")

        assert app.config.model_bindings.chat == "qwen2.5"

    asyncio.run(run())


def test_model_command_switches_single_binding(tmp_path: Path) -> None:
    async def run() -> None:
        app = _app(tmp_path)
        async with app.run_test():
            await app.handle_user_text("/model vision llava")

        bindings = app.config.model_bindings
        assert bindings.vision == "llava"
        assert bindings.chat == "gpt-test"  # untouched

    asyncio.run(run())


def test_model_command_switches_all_bindings(tmp_path: Path) -> None:
    async def run() -> None:
        app = _app(tmp_path)
        async with app.run_test():
            await app.handle_user_text("/model all qwen2.5")

        dumped = app.config.model_bindings.model_dump()
        assert set(dumped.values()) == {"qwen2.5"}

    asyncio.run(run())


def test_model_command_reports_unreachable_provider(tmp_path: Path, monkeypatch) -> None:
    async def failing(_config) -> list[str]:
        raise ProviderChatError("Could not reach provider endpoint")

    async def run() -> None:
        app = _app(tmp_path)
        monkeypatch.setattr("jenai.tui.info_commands.list_provider_models", failing)
        async with app.run_test():
            await app.handle_user_text("/model")
            await app.handle_user_text("/model 3")

        # No switch happened, no crash; bindings stay put.
        assert app.config.model_bindings.chat == "gpt-test"

    asyncio.run(run())


def test_model_command_rejects_out_of_range_number(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        app = _app(tmp_path)
        monkeypatch.setattr(
            "jenai.tui.info_commands.list_provider_models", _fake_listing(["only-one"])
        )
        async with app.run_test():
            await app.handle_user_text("/model 5")

        assert app.config.model_bindings.chat == "gpt-test"

    asyncio.run(run())


def test_provider_command_switches_profile(tmp_path: Path) -> None:
    async def run() -> None:
        app = _app(tmp_path)
        app.config.provider_profiles["cloud"] = app.config.provider_profiles["ollama"].model_copy(
            update={"name": "cloud", "base_url": "https://example.invalid/v1"}
        )
        async with app.run_test():
            await app.handle_user_text("/provider cloud")

        assert app.config.active_provider == "cloud"
        persisted = load_config(tmp_path / "config.toml")
        assert persisted.active_provider == "cloud"

    asyncio.run(run())


def test_provider_command_switches_by_number_and_clears_model_cache(tmp_path: Path) -> None:
    async def run() -> None:
        app = _app(tmp_path)
        app.config.provider_profiles["cloud"] = app.config.provider_profiles["ollama"].model_copy(
            update={"name": "cloud"}
        )
        app._available_models = ["stale-model"]
        async with app.run_test():
            await app.handle_user_text("/provider 2")

        assert app.config.active_provider == "cloud"
        assert app._available_models == []  # endpoint changed; numbers must not leak

    asyncio.run(run())


def test_provider_command_rejects_unknown_profile(tmp_path: Path) -> None:
    async def run() -> None:
        app = _app(tmp_path)
        async with app.run_test():
            await app.handle_user_text("/provider nope")

        assert app.config.active_provider == "ollama"

    asyncio.run(run())


def test_submitted_palette_placeholder_never_reaches_handlers(tmp_path: Path) -> None:
    async def run() -> None:
        app = _app(tmp_path)
        async with app.run_test():
            await app.handle_user_text("/model <name|number>")
            await app.handle_user_text("/provider <name>")
            await app.handle_user_text("/model <partially-edited>")

        bindings = app.config.model_bindings
        assert bindings.chat == "gpt-test"  # placeholder was never saved
        assert app.config.active_provider == "ollama"

    asyncio.run(run())
