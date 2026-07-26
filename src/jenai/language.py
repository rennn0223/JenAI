"""User-visible language policy for model-backed product surfaces."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal

from opencc import OpenCC

OutputLanguage = Literal["en", "zh-TW"]

_HAN_TEXT = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_PROTECTED_SPANS = re.compile(
    r"```.*?```|`[^`\n]*`|https?://[^\s<>()]+|"
    r"(?<![\w])(?:~?/|\.\.?/)[^\s，。；：！？、]+|"
    r"\b[A-Za-z_][A-Za-z0-9_.-]*=[^\s，。；：！？、]+",
    re.DOTALL,
)
_TAIWAN_PRESERVED_TERMS = (
    "機械系館",
    "批准",
    "平台",
    "游標",
    "干擾",
    "秘密",
    "文件",
    "權限",
    "吃",
)
_TAIWAN_PRESERVED_SPANS = re.compile(
    "(" + "|".join(re.escape(term) for term in _TAIWAN_PRESERVED_TERMS) + ")"
)
_TAIWAN_PRESERVED_TERM_SET = frozenset(_TAIWAN_PRESERVED_TERMS)
_TRADITIONAL_CHINESE_INSTRUCTION = (
    "When the operator writes in Chinese, every user-visible natural-language field "
    "MUST use Traditional Chinese (Taiwan), never Simplified Chinese. Keep identifiers, "
    "tool names, topic names, paths, numbers, and units unchanged."
)


def output_language_for(*hints: str) -> OutputLanguage:
    """Infer the response language from operator-provided text hints."""

    return "zh-TW" if any(_HAN_TEXT.search(hint) for hint in hints) else "en"


@lru_cache(maxsize=1)
def _traditional_chinese_converter() -> OpenCC:
    return OpenCC("s2twp")


def _normalize_prose_segment(text: str) -> str:
    if not _HAN_TEXT.search(text):
        return text
    converter = _traditional_chinese_converter()
    return "".join(
        part if part in _TAIWAN_PRESERVED_TERM_SET else str(converter.convert(part))
        for part in _TAIWAN_PRESERVED_SPANS.split(text)
    )


def normalize_user_visible_text(text: str, language: OutputLanguage) -> str:
    """Convert prose to zh-TW without mutating operator-addressable identifiers."""

    del language
    if not text or not _HAN_TEXT.search(text):
        return text

    normalized: list[str] = []
    cursor = 0
    for match in _PROTECTED_SPANS.finditer(text):
        normalized.append(_normalize_prose_segment(text[cursor : match.start()]))
        normalized.append(match.group(0))
        cursor = match.end()
    normalized.append(_normalize_prose_segment(text[cursor:]))
    return "".join(normalized)


def model_language_instruction() -> str:
    """Return the shared prompt contract for Chinese operator interactions."""

    return _TRADITIONAL_CHINESE_INSTRUCTION
