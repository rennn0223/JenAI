"""Deterministic tool-output summaries for the timeline."""

from __future__ import annotations

from jenai.config.models import AppConfig
from jenai.schemas import FieldSummary


async def summarize_ros_schema(
    _config: AppConfig,
    _message_type: str,
    raw_interface: str,
) -> list[FieldSummary]:
    """Parse ``ros2 interface show`` without a second, probabilistic LLM call.

    Schema lookup is a reflex-like developer command: its latency and result
    must not depend on provider availability.  The caller already exposes the
    authoritative type and raw interface, so deterministic field extraction is
    both faster and more honest than model-generated descriptions.
    """
    return _naive_field_summary(raw_interface)


def _naive_field_summary(raw_interface: str) -> list[FieldSummary]:
    summaries: list[FieldSummary] = []
    for line in raw_interface.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            continue
        field_type, field_name = parts
        field_name = field_name.split("#", 1)[0].strip()
        if not field_name:
            continue
        summaries.append(FieldSummary(field_name=field_name, field_type=field_type, description=""))
    return summaries
