"""Session creation and persisted conversation state."""

from __future__ import annotations

import hashlib

from jenai.config.models import AppConfig
from jenai.schemas import SessionState


class SessionSetupError(Exception):
    """Raised when a session cannot be created from the current config."""


def _stable_session_id(working_directory: str) -> str:
    """A session id that is stable for a given project directory.

    Using a deterministic id (rather than a fresh random one each launch) lets
    the file-backed conversation memory persist across restarts for the same
    project, while keeping different projects' histories separate.
    """
    digest = hashlib.sha256(working_directory.encode("utf-8")).hexdigest()[:12]
    return f"session_{digest}"


def create_session(config: AppConfig, *, working_directory: str) -> SessionState:
    if config.active_provider is None:
        raise SessionSetupError("Cannot start a session without an active provider.")
    if config.model_bindings is None:
        raise SessionSetupError("Cannot start a session without model bindings configured.")

    return SessionState(
        session_id=_stable_session_id(working_directory),
        provider_profile=config.active_provider,
        model_bindings=config.model_bindings,
        working_directory=working_directory,
    )
