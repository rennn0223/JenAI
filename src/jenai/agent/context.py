from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jenai.config.models import AppConfig
from jenai.schemas import RunRecord, SessionState
from jenai.state.runs import RunStore


@dataclass
class JenAIRunContext:
    """Passed as `context=` to every `Runner.run` call; tools receive it via
    `RunContextWrapper[JenAIRunContext]` and use it to look up config/state and
    record `ToolCallRecord`s on the active run.
    """

    config: AppConfig
    config_path: Path
    session: SessionState
    run: RunRecord
    run_store: RunStore
