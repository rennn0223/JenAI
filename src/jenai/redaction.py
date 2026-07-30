"""Reusable secret redaction for user-visible and exported text."""

from __future__ import annotations

import re
from collections.abc import Iterable

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)"
    r"[\"']?\s*[:=]\s*[\"']?)([^\"'\s,}]+)"
)
_BEARER = re.compile(r"(?i)([\"']?authorization[\"']?\s*:\s*[\"']?\s*bearer\s+)([^\s\"']+)")


def redact_sensitive_text(
    text: str,
    *,
    secret_values: Iterable[str] = (),
) -> str:
    """Redact known values and common credential assignments from text."""
    redacted = str(text)
    for secret in sorted(secret_values, key=len, reverse=True):
        if len(secret) >= 4:
            redacted = redacted.replace(secret, "[REDACTED]")
    redacted = _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", redacted)
    redacted = _BEARER.sub(r"\1[REDACTED]", redacted)
    return redacted


def redact_sensitive_bytes(
    payload: bytes,
    *,
    secret_values: Iterable[bytes] = (),
) -> bytes:
    """Redact text-shaped byte payloads while preserving non-UTF-8 bytes."""
    redacted = payload
    for secret in sorted(secret_values, key=len, reverse=True):
        if len(secret) >= 4:
            redacted = redacted.replace(secret, b"[REDACTED]")
    try:
        text = redacted.decode("utf-8")
    except UnicodeDecodeError:
        return redacted
    return redact_sensitive_text(text).encode()
