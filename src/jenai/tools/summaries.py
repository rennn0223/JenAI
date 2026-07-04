"""Tool output summarization for the timeline."""

from __future__ import annotations

from jenai.config.models import AppConfig
from jenai.providers.chat import ask_json
from jenai.schemas import FieldSummary


async def summarize_ros_schema(
    config: AppConfig,
    message_type: str,
    raw_interface: str,
) -> list[FieldSummary]:
    """Summarize a `ros2 interface show` output into plain-language field descriptions.

    Falls back to a naive line-based summary if the model call fails or returns
    something unparseable, so `/ros schema` degrades gracefully instead of raising.
    """
    prompt = (
        f"Summarize the fields of this ROS2 message type '{message_type}'. "
        "Respond with ONLY a JSON array of objects, each with keys "
        '"field_name", "field_type", "description". No prose, no markdown fences.\n\n'
        f"{raw_interface}"
    )

    parsed = await ask_json(config, prompt)
    if parsed is None:
        return _naive_field_summary(raw_interface)

    try:
        return [FieldSummary.model_validate(item) for item in parsed]
    except (TypeError, ValueError):
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
