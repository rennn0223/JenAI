"""Recorded deterministic paths for unambiguous informational requests."""

from __future__ import annotations

from jenai.agent.context import JenAIRunContext
from jenai.capability_reporting import capability_card_report
from jenai.config.models import AppConfig
from jenai.schemas import RunRecord, RunStatus, TaskOutcome


async def start_capability_card_run(
    ctx: JenAIRunContext,
    config: AppConfig,
) -> RunRecord:
    """Complete a capability query without model inference or live-state claims."""
    run, run_store = ctx.run, ctx.run_store
    run_store.set_status(run, RunStatus.UNDERSTANDING)
    run_store.set_status(run, RunStatus.RUNNING)
    run_store.finish(
        run,
        status=RunStatus.COMPLETED,
        outcome=TaskOutcome.SUCCEEDED,
        final_output=capability_card_report(config, language_hint=run.user_input),
    )
    return run
