"""Reusable secret redaction for user-visible and exported text."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?P<prefix>[\"']?(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"password|secret|token)[\"']?\s*[:=]\s*)"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,}&]+)"
)
_BEARER = re.compile(r"(?i)([\"']?authorization[\"']?\s*:\s*[\"']?\s*bearer\s+)([^\s\"']+)")


def _redact_assignment(match: re.Match[str]) -> str:
    prefix = match.group("prefix")
    value = match.group("value")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        quote = value[0]
        return f"{prefix}{quote}[REDACTED]{quote}"
    return f"{prefix}[REDACTED]"


def known_secret_values(
    credentials: Path | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    environment_names: Iterable[str] = (),
) -> set[str]:
    """Collect configured credential values without exposing their names or locations."""
    values: set[str] = set()
    if credentials is not None:
        try:
            lines = credentials.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            _key, separator, value = stripped.removeprefix("export ").partition("=")
            if separator:
                value = value.strip().strip("\"'")
                if len(value) >= 4:
                    values.add(value)
    environ = os.environ if environment is None else environment
    explicit_names = {str(name).strip() for name in environment_names if str(name).strip()}
    for key, value in environ.items():
        if (key in explicit_names or re.search(r"(?i)(key|token|secret|password)$", key)) and len(
            value
        ) >= 4:
            values.add(value)
    return values


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
    redacted = _SECRET_ASSIGNMENT.sub(_redact_assignment, redacted)
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
