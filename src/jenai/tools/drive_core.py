from __future__ import annotations

import re
from dataclasses import dataclass

from jenai.config.models import AppConfig
from jenai.providers.chat import ask_json

# Default magnitudes for regex-parsed commands (m/s and rad/s). The LLM path may
# return its own values for nuanced instructions.
_SPEED = 0.5
_TURN = 0.6
_DURATION = 2.0
_MAX_DURATION = 30.0

_FORWARD = ("前進", "前进", "forward", "ahead", "straight", "go forward")
_BACK = ("後退", "后退", "backward", "backwards", "reverse", "back up")
_LEFT = ("左轉", "左转", "turn left", "left")
_RIGHT = ("右轉", "右转", "turn right", "right")
_STOP = ("停", "stop", "halt", "煞車", "刹车", "brake")
# Negations that flip a bare "stop" into "keep going" ("don't stop" / "別停");
# too ambiguous for the regex to turn into a direction, so we defer to the LLM.
_NEGATIONS = ("don't", "do not", "dont", "never", "不要", "別", "别", "勿", "甭")


@dataclass(frozen=True)
class DriveIntent:
    """A parsed velocity command: linear (m/s) + angular (rad/s) for a duration."""

    linear_x: float
    angular_z: float
    duration_s: float
    description: str

    def to_payload(self) -> dict:
        return {
            "linear": {"x": self.linear_x, "y": 0.0, "z": 0.0},
            "angular": {"x": 0.0, "y": 0.0, "z": self.angular_z},
        }


def _parse_duration(text: str) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:秒|s\b|sec|secs|seconds?|second)", text.lower())
    if match:
        return min(max(float(match.group(1)), 0.0), _MAX_DURATION)
    return _DURATION


def _describe(linear_x: float, angular_z: float, duration_s: float) -> str:
    parts: list[str] = []
    if linear_x > 0:
        parts.append(f"forward {linear_x:g} m/s")
    elif linear_x < 0:
        parts.append(f"reverse {abs(linear_x):g} m/s")
    if angular_z > 0:
        parts.append(f"turn left {angular_z:g} rad/s")
    elif angular_z < 0:
        parts.append(f"turn right {abs(angular_z):g} rad/s")
    if not parts:
        return "stop"
    return ", ".join(parts) + f" for {duration_s:g}s"


def _extract_via_regex(text: str) -> DriveIntent | None:
    lowered = text.lower()

    linear_x = 0.0
    angular_z = 0.0
    matched = False
    if any(k in lowered for k in _FORWARD):
        linear_x = _SPEED
        matched = True
    elif any(k in lowered for k in _BACK):
        linear_x = -_SPEED
        matched = True
    if any(k in lowered for k in _LEFT):
        angular_z = _TURN
        matched = True
    elif any(k in lowered for k in _RIGHT):
        angular_z = -_TURN
        matched = True

    if matched:
        # A movement direction was given; a stray "stop" in the sentence (e.g.
        # "go forward and don't stop") must not override the requested motion.
        duration = _parse_duration(text)
        return DriveIntent(linear_x, angular_z, duration, _describe(linear_x, angular_z, duration))

    if any(k in lowered for k in _STOP):
        # Bare stop — but not a negated one ("don't stop"), which has no direction
        # we can infer here, so let the LLM interpret it.
        if any(neg in lowered for neg in _NEGATIONS):
            return None
        return DriveIntent(0.0, 0.0, 0.5, "stop")

    return None


async def _extract_via_llm(config: AppConfig, text: str) -> DriveIntent | None:
    prompt = (
        "Convert this robot driving instruction into a velocity command for a "
        "geometry_msgs/msg/Twist on /cmd_vel. Respond with ONLY JSON: "
        '{"linear_x": <m/s, + forward / - reverse>, '
        '"angular_z": <rad/s, + left / - right>, '
        '"duration_s": <seconds>, "description": "<short summary>"}. '
        "Default speed 0.5, turn 0.6, duration 2 when unspecified; use 0 for stop.\n\n"
        f"Instruction: {text}"
    )
    parsed = await ask_json(config, prompt, binding="chat")
    if not isinstance(parsed, dict):
        return None
    try:
        linear_x = float(parsed.get("linear_x", 0.0))
        angular_z = float(parsed.get("angular_z", 0.0))
        duration = min(max(float(parsed.get("duration_s", _DURATION)), 0.0), _MAX_DURATION)
    except (TypeError, ValueError):
        return None
    description = str(parsed.get("description") or "").strip() or _describe(
        linear_x, angular_z, duration
    )
    return DriveIntent(linear_x, angular_z, duration, description)


async def extract_drive_command(config: AppConfig, text: str) -> DriveIntent | None:
    """Parse a natural-language driving instruction into a DriveIntent.

    Tries a fast offline regex for common commands (前進/後退/左轉/右轉/停 +
    duration) first, then falls back to the LLM for nuanced instructions.
    """
    return _extract_via_regex(text) or await _extract_via_llm(config, text)
