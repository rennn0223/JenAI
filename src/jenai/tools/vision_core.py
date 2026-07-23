"""capture_and_analyze: camera frame → VLM (the single vision entry point)."""

from __future__ import annotations

import base64
import mimetypes
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from jenai.config.models import AppConfig
from jenai.providers.chat import ask_vision_json
from jenai.schemas import VisionOutput

if TYPE_CHECKING:
    from jenai.bridge import RosBridgeClient

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
    context_line = f"Current task context: {task_context}\n" if task_context.strip() else ""
    return (
        "You are JenAI's vision analyst for a ROS2 robot. Analyze the image and respond "
        "with ONLY JSON matching: "
        '{"summary": "...", "objects": ["..."], "anomalies": ["..."], '
        '"relevance_to_task": "...", "next_action_suggestions": ["..."]}.\n'
        f"{context_line}"
    )


async def analyze_image(config: AppConfig, source: str, *, task_context: str = "") -> VisionOutput:
    """Analyze a local image with the configured vision model.

    Raises VisionError for a missing path or a non-image file. Degrades to a
    summary-only result when the vision model is unavailable.
    """
    path = Path(source).expanduser()
    if not path.exists() or not path.is_file():
        raise VisionError(f"Image file not found: {source}")
    if path.suffix.lower() not in _IMAGE_SUFFIXES:
        raise VisionError(
            f"'{path.name}' is not a supported image ({', '.join(sorted(_IMAGE_SUFFIXES))})."
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


async def capture_and_analyze(
    config: AppConfig,
    bridge: RosBridgeClient,
    topic: str,
    *,
    timeout: float = 5.0,
    on_captured: Callable[[], None] | None = None,
) -> VisionOutput:
    """One-shot camera capture → VLM analysis, with guaranteed frame cleanup.

    The shared flow behind `/vision camera` and the MCP camera_look tool.
    Raises BridgeError when no frame can be captured and VisionError when the
    file can't be analyzed; `on_captured` fires between the two phases (the
    TUI uses it to flip its spinner label).
    """
    frame_path = await bridge.capture_frame(topic, timeout=timeout)
    if on_captured is not None:
        on_captured()
    try:
        return await analyze_image(config, str(frame_path))
    finally:
        frame_path.unlink(missing_ok=True)  # one-shot capture; don't litter /tmp
