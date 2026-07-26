"""capture_and_analyze: camera frame → VLM (the single vision entry point)."""

from __future__ import annotations

import base64
import mimetypes
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from jenai.config.models import AppConfig
from jenai.language import (
    model_language_instruction,
    normalize_user_visible_text,
    output_language_for,
)
from jenai.providers.chat import ask_vision_json
from jenai.schemas import VisionOutput
from jenai.secure_files import atomic_write_bytes

if TYPE_CHECKING:
    from jenai.bridge import RosBridgeClient

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_NEGATIVE_ANOMALY_PREFIXES = (
    "no anomaly",
    "no anomalies",
    "no significant anomaly",
    "no significant anomalies",
    "no issue",
    "no issues",
    "nothing unusual",
    "未發現異常",
    "無明顯異常",
    "沒有異常",
    "無異常",
)
_NON_ANOMALY_PHRASES = (
    "no human",
    "no person",
    "no people",
    "no operator",
    "simulated appearance",
    "rendered appearance",
)
_QUALIFIERS = (" but ", " however ", " except ", "；但", "，但", "但是", "然而")


class VisionError(Exception):
    """Raised when an image cannot be analyzed (bad path or non-image file)."""


def _as_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value in (None, ""):
        return []
    return [str(value)]


def _normalize_anomalies(value: object) -> list[str]:
    """Keep only concrete anomaly candidates, preserving model order.

    A VLM sometimes places normal-state prose (for example, "no anomalies")
    inside the anomaly array. Such prose must not escalate a patrol. This is a
    conservative output contract guard, not a replacement for perception.
    """

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in _as_str_list(value):
        item = raw.strip()
        folded = " ".join(item.casefold().split())
        negative_prefix = folded.startswith(_NEGATIVE_ANOMALY_PREFIXES)
        qualified = any(marker in folded for marker in _QUALIFIERS)
        if not item or (negative_prefix and not qualified):
            continue
        if any(phrase in folded for phrase in _NON_ANOMALY_PHRASES) and not qualified:
            continue
        if folded not in seen:
            normalized.append(item)
            seen.add(folded)
    return normalized


def _to_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _build_prompt(task_context: str, language_hint: str = "") -> str:
    context_line = f"Current task context: {task_context}\n" if task_context.strip() else ""
    language_line = (
        f"{model_language_instruction()}\n"
        if output_language_for(language_hint, task_context) == "zh-TW"
        else ""
    )
    return (
        "You are JenAI's vision analyst for a ROS2 robot. Analyze the image and respond "
        "with ONLY JSON matching: "
        '{"summary": "...", "objects": ["..."], "anomalies": ["..."], '
        '"relevance_to_task": "...", "next_action_suggestions": ["..."]}.\n'
        "Set anomalies to [] when no concrete anomaly is visible. An anomaly entry must "
        "describe a specific, visible, actionable deviation supported by this image. "
        "Do not put normal-state statements, absence of people, an orderly or safe scene, "
        "or the image's simulated/rendered appearance in anomalies. Do not call an object "
        "unexpected without a supplied baseline. Put uncertainty in summary and suggest "
        "human review instead of inventing evidence.\n"
        f"{language_line}"
        f"{context_line}"
    )


async def analyze_image(
    config: AppConfig,
    source: str,
    *,
    task_context: str = "",
    language_hint: str = "",
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
            f"'{path.name}' is not a supported image ({', '.join(sorted(_IMAGE_SUFFIXES))})."
        )

    language = output_language_for(language_hint, task_context)

    def normalized(text: str) -> str:
        return normalize_user_visible_text(text, language)

    def normalized_list(value: object) -> list[str]:
        return [normalized(item) for item in _as_str_list(value)]

    def unavailable_output() -> VisionOutput:
        return VisionOutput(
            analysis_status="unavailable",
            source=str(path),
            summary=(
                "視覺模型無法使用或未回傳結構化結果。"
                if language == "zh-TW"
                else "Vision model is unavailable or returned no structured result."
            ),
            relevance_to_task=task_context,
        )

    parsed = await ask_vision_json(
        config, _build_prompt(task_context, language_hint), _to_data_url(path)
    )
    if not isinstance(parsed, dict):
        return unavailable_output()
    summary = parsed.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return unavailable_output()

    return VisionOutput(
        source=str(path),
        summary=normalized(summary),
        objects=normalized_list(parsed.get("objects")),
        anomalies=[normalized(item) for item in _normalize_anomalies(parsed.get("anomalies"))],
        relevance_to_task=normalized(str(parsed.get("relevance_to_task", task_context))),
        next_action_suggestions=normalized_list(parsed.get("next_action_suggestions")),
    )


async def capture_and_analyze(
    config: AppConfig,
    bridge: RosBridgeClient,
    topic: str,
    *,
    timeout: float = 5.0,
    on_captured: Callable[[], None] | None = None,
    preserve_to: Path | None = None,
    task_context: str = "",
) -> VisionOutput:
    """One-shot camera capture → VLM analysis, with optional durable evidence.

    The shared flow behind `/vision camera` and the MCP camera_look tool.
    Raises BridgeError when no frame can be captured and VisionError when the
    file can't be analyzed; `on_captured` fires between the two phases (the
    TUI uses it to flip its spinner label). When ``preserve_to`` is provided,
    the captured bytes are atomically copied to that private path and the VLM
    analyzes the durable artifact. The bridge's temporary frame is always
    removed.
    """
    frame_path = await bridge.capture_frame(topic, timeout=timeout)
    if on_captured is not None:
        on_captured()
    try:
        analysis_path = frame_path
        if preserve_to is not None:
            analysis_path = atomic_write_bytes(preserve_to, frame_path.read_bytes())
        return await analyze_image(
            config,
            str(analysis_path),
            task_context=task_context,
        )
    finally:
        frame_path.unlink(missing_ok=True)  # one-shot capture; don't litter /tmp
