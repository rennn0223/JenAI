from __future__ import annotations

from jenai.config.models import AppConfig
from jenai.schemas import SessionState


class SessionSetupError(Exception):
    """Raised when a session cannot be created from the current config."""


def create_session(config: AppConfig, *, working_directory: str) -> SessionState:
    if config.active_provider is None:
        raise SessionSetupError("Cannot start a session without an active provider.")
    if config.model_bindings is None:
        raise SessionSetupError("Cannot start a session without model bindings configured.")

    return SessionState(
        provider_profile=config.active_provider,
        model_bindings=config.model_bindings,
        working_directory=working_directory,
    )
