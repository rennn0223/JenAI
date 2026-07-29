"""Typed HTTP response models for the Robot Runtime transport."""

from pydantic import Field

from jenai.runtime.contracts import RUNTIME_SCHEMA_VERSION, RuntimeState
from jenai.schemas.models import JenAIModel


class RuntimeHealth(JenAIModel):
    """Cheap liveness/readiness response for clients and supervisors."""

    schema_version: int = RUNTIME_SCHEMA_VERSION
    status: str
    runtime_id: str
    state: RuntimeState
    safety_epoch: int = Field(ge=0)
    last_sequence: int = Field(ge=0)
