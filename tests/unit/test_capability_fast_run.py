from __future__ import annotations

import asyncio
from pathlib import Path

from jenai.agent.context import JenAIRunContext
from jenai.agent.fast_paths import start_capability_card_run
from jenai.config.store import build_minimal_config
from jenai.schemas import SessionState
from jenai.state.runs import RunStore


def test_capability_fast_run_is_recorded_without_model_or_tool_calls() -> None:
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="model",
        api_key_env="",
    )
    assert config.model_bindings is not None
    session = SessionState(
        provider_profile="test",
        model_bindings=config.model_bindings,
        working_directory=".",
    )
    store = RunStore()
    run = store.create_run(session.session_id, "請介紹你的能力與限制")
    ctx = JenAIRunContext(
        config=config,
        config_path=Path("config.toml"),
        session=session,
        run=run,
        run_store=store,
    )

    result = asyncio.run(start_capability_card_run(ctx, config))

    assert result.status == "completed"
    assert result.tool_calls == []
    assert result.final_output is not None
    assert "能力" in result.final_output
    assert "本次即時可用" in result.final_output
