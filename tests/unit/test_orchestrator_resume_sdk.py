"""Regression: resuming with approvals must EXECUTE the approved tool.

Runs the REAL openai-agents Runner (scripted model, no network). Guards the
v0.34.x bug where passing ``context=`` to ``Runner.run`` on resume made the
SDK replace the state's context wrapper — wiping the just-recorded approvals,
so the approved tool re-interrupted within milliseconds instead of executing
and the loop detector mislabelled it a "model loop".
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agents import Agent, RunContextWrapper, function_tool
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from jenai.agent import orchestrator
from jenai.agent.context import JenAIRunContext
from jenai.agent.session import JenAIFileSession
from jenai.config.store import build_minimal_config
from jenai.state.runs import RunStore
from jenai.state.session import create_session

_EXECUTED: list[str] = []


@function_tool(needs_approval=True)
async def guarded_move_tool(ctx: RunContextWrapper) -> str:
    """Move the robot (test double)."""
    _EXECUTED.append("ran")
    return "moved ok"


class _ScriptedModel(Model):
    """Turn 1: call the guarded tool. Any later turn: plain final message."""

    def __init__(self) -> None:
        self.turns = 0

    async def get_response(self, *args, **kwargs) -> ModelResponse:
        self.turns += 1
        if self.turns == 1:
            call = ResponseFunctionToolCall(
                type="function_call",
                id="fc_1",
                call_id="call_1",
                name="guarded_move_tool",
                arguments="{}",
                status="completed",
            )
            return ModelResponse(output=[call], usage=Usage(), response_id=None)
        message = ResponseOutputMessage(
            id="msg_1",
            type="message",
            role="assistant",
            status="completed",
            content=[ResponseOutputText(type="output_text", text="done", annotations=[])],
        )
        return ModelResponse(output=[message], usage=Usage(), response_id=None)

    def stream_response(self, *args, **kwargs):  # pragma: no cover - not streamed
        raise NotImplementedError


def _ctx(monkeypatch) -> JenAIRunContext:
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="gpt-test",
        api_key_env="JENAI_TEST_KEY",
    )
    monkeypatch.setenv("JENAI_TEST_KEY", "secret")
    run_store = RunStore()
    run = run_store.create_run("session-resume-sdk", "move please")
    session = create_session(config, working_directory="/tmp")
    return JenAIRunContext(
        config=config,
        config_path=Path("/tmp/config.toml"),
        session=session,
        run=run,
        run_store=run_store,
    )


def test_resume_executes_approved_tool_with_real_sdk(monkeypatch, tmp_path) -> None:
    _EXECUTED.clear()
    monkeypatch.setenv("OPENAI_AGENTS_DISABLE_TRACING", "1")
    monkeypatch.setattr(
        orchestrator,
        "JenAIFileSession",
        lambda session_id: JenAIFileSession(session_id, directory=tmp_path),
    )
    ctx = _ctx(monkeypatch)
    agent = Agent(
        name="mover",
        instructions="move when asked",
        model=_ScriptedModel(),
        tools=[guarded_move_tool],
    )

    paused = asyncio.run(orchestrator.start_run(agent, ctx, "move please"))
    assert paused.status == "awaiting_approval"
    assert _EXECUTED == []  # nothing may run before the human decides
    call_id = paused.interruptions[0].tool_call_id

    resumed = asyncio.run(orchestrator.resume_with_approvals(agent, ctx, {call_id: True}))

    assert _EXECUTED == ["ran"], "approved tool never executed — approvals were lost on resume"
    assert resumed.status == "completed"
    assert resumed.final_output == "done"


def test_resume_rejection_still_reaches_model_with_real_sdk(monkeypatch, tmp_path) -> None:
    _EXECUTED.clear()
    monkeypatch.setenv("OPENAI_AGENTS_DISABLE_TRACING", "1")
    monkeypatch.setattr(
        orchestrator,
        "JenAIFileSession",
        lambda session_id: JenAIFileSession(session_id, directory=tmp_path),
    )
    ctx = _ctx(monkeypatch)
    agent = Agent(
        name="mover",
        instructions="move when asked",
        model=_ScriptedModel(),
        tools=[guarded_move_tool],
    )

    paused = asyncio.run(orchestrator.start_run(agent, ctx, "move please"))
    call_id = paused.interruptions[0].tool_call_id

    resumed = asyncio.run(
        orchestrator.resume_with_approvals(
            agent, ctx, {call_id: False}, rejection_message="too risky"
        )
    )

    assert _EXECUTED == []  # rejected tool must never run
    assert resumed.status == "completed"  # model got the rejection and wrapped up
