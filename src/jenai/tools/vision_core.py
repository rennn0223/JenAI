from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from jenai.config.models import AppConfig
from jenai.providers.chat import ask_vision_json
from jenai.schemas import VisionOutput

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


class VisionError(Exception):
    """Raised when an image cannot be analyzed (bad path or non-image file)."""


def _as_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value in (None, ""):
        return []
    return [str(value)]


def _to_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _build_prompt(task_context: str) -> str:
    context_line = (
        f"Current task context: {task_context}\n" if task_context.strip() else ""
    )
    return (
        "You are JenAI's vision analyst for a ROS2 robot. Analyze the image and respond "
        "with ONLY JSON matching: "
        '{"summary": "...", "objects": ["..."], "anomalies": ["..."], '
        '"relevance_to_task": "...", "next_action_suggestions": ["..."]}.\n'
        f"{context_line}"
    )


async def analyze_image(
    config: AppConfig, source: str, *, task_context: str = ""
) -> VisionOutput:
    """Analyze a local image with the configured vision model.

    Raises VisionError for a missing path or a non-image file. Degrades to a
    summary-only result when the vision model is unavailable.
    """
    path = Path(source).expanduser()
    if not path.exists() or not path.is_file():
        raise VisionError(f"Image file not found: {source}")
    if path.suffix.lower() not in _IMAGE_SUFFIXES:
        raise VisionError(
            f"'{path.name}' is not a supported image "
            f"({', '.join(sorted(_IMAGE_SUFFIXES))})."
        )

    parsed = await ask_vision_json(config, _build_prompt(task_context), _to_data_url(path))
    if not isinstance(parsed, dict):
        return VisionOutput(
            source=str(path),
            summary="Vision model is unavailable or returned no structured result.",
            relevance_to_task=task_context,
        )

    return VisionOutput(
        source=str(path),
        summary=str(parsed.get("summary", "")),
        objects=_as_str_list(parsed.get("objects")),
        anomalies=_as_str_list(parsed.get("anomalies")),
        relevance_to_task=str(parsed.get("relevance_to_task", task_context)),
        next_action_suggestions=_as_str_list(parsed.get("next_action_suggestions")),
    )
