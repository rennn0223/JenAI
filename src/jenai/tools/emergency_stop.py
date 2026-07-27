"""Deterministic emergency-stop service and conservative intent recognition.

This module deliberately has no dependency on an LLM provider or Agent SDK.
The safety reflex remains available even when model infrastructure is absent.
"""

from __future__ import annotations

import contextlib
import re

from jenai.bridge import RosBridgeClient
from jenai.config.models import AppConfig
from jenai.schemas import EffectScope
from jenai.tools.safety import HaltReceipt, arm_watchdog, halt_robot_with_receipt

_STOP_NEGATIONS = (
    "do not stop",
    "don.t stop",
    "don't stop",
    "without stopping",
    "do not halt",
    "don.t halt",
    "don't halt",
    "without halting",
    "do not cancel",
    "don.t cancel",
    "don't cancel",
    "without canceling",
    "不要停止",
    "不要停車",
    "不要停下",
    "不要急停",
    "不要取消",
    "別停止",
    "別停車",
    "別停下",
    "別急停",
    "別取消",
)
_STOP_INFORMATIONAL_PREFIXES = (
    "how does",
    "how do",
    "what is",
    "where is",
    "where ",
    "tell me where",
    "explain",
    "documentation",
    "document ",
    "docs ",
    "如何",
    "怎麼",
    "什麼是",
    "在哪裡",
    "在哪",
    "說明",
    "介紹",
)
_STOP_QUESTION_MARKERS = (
    "is it safe",
    "should i",
    "whether",
    "是否",
    "安全嗎",
    "安全？",
    "安全?",
)
_ENGLISH_TARGET = re.compile(
    r"\b(robot|vehicle|navigation|nav2|goal|mission|moving|movement|driving|patrol)\b"
)
_ENGLISH_STOP = re.compile(r"\b(stop|halt)\b")
_ENGLISH_CANCEL = re.compile(r"\bcancel\b")
_CHINESE_TARGET = re.compile(r"機器人|車輛|載具|導航|nav2|任務|目標|移動|行駛|巡邏", re.IGNORECASE)
_CHINESE_CANCEL_TARGET = re.compile(r"取消.{0,4}(導航|任務|目標)")
_CHINESE_EXPLICIT_STOP = re.compile(r"急停|停下|停車(?!場|位|格)")
_CHINESE_NON_MOTION_STOP = re.compile(r"停下(?:來)?(?:思考|想想|想|討論|閱讀|檢視|檢查|回答|說明)")
_CHINESE_GENERIC_STOP = re.compile(r"停止")


def emergency_stop_effect_scope(config: AppConfig) -> EffectScope:
    """Describe whether the stop can affect a simulator or a physical robot."""

    if config.deployment_mode == "physical":
        return EffectScope.ROBOT_CONTROL
    return EffectScope.SIM_CONTROL


def is_emergency_stop_request(text: str) -> bool:
    """Recognize explicit stop commands while rejecting discussion and nouns."""

    lowered = " ".join(text.lower().strip().split())
    if not lowered or any(term in lowered for term in _STOP_NEGATIONS):
        return False
    if any(lowered.startswith(term) for term in _STOP_INFORMATIONAL_PREFIXES):
        return False
    if any(term in lowered for term in _STOP_QUESTION_MARKERS):
        return False

    stripped = lowered.strip(" .!！。")
    if _CHINESE_CANCEL_TARGET.search(lowered):
        return True
    motion_text = _CHINESE_NON_MOTION_STOP.sub("", lowered)
    if not motion_text.strip(" ，,、。"):
        return False
    if _CHINESE_EXPLICIT_STOP.search(motion_text):
        return True
    if _CHINESE_GENERIC_STOP.search(motion_text):
        return bool(_CHINESE_TARGET.search(motion_text)) or stripped == "停止"
    if _ENGLISH_CANCEL.search(lowered):
        return bool(_ENGLISH_TARGET.search(lowered)) or stripped == "cancel"
    if _ENGLISH_STOP.search(lowered):
        return bool(_ENGLISH_TARGET.search(lowered)) or stripped in {"stop", "halt"}
    return False


async def execute_emergency_stop(config: AppConfig) -> HaltReceipt:
    """Execute the provider-free cancel-and-zero workflow and return evidence."""

    bridge = RosBridgeClient()
    try:
        await arm_watchdog(config, bridge)
        await bridge.start()
        return await halt_robot_with_receipt(config, bridge)
    finally:
        with contextlib.suppress(Exception):
            await bridge.stop()
