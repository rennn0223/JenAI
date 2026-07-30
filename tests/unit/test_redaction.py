from __future__ import annotations

import pytest

from jenai.redaction import redact_sensitive_bytes, redact_sensitive_text


@pytest.mark.parametrize(
    "payload",
    (
        '{"password": "correct horse battery staple"}',
        "password='correct horse battery staple'",
        'api_key="token with spaces"',
        "https://robot.local/?token=browser-secret",
        "JENAI_WEB_TOKEN=browser-secret",
    ),
)
def test_redaction_removes_complete_secret(payload: str) -> None:
    redacted = redact_sensitive_text(payload)

    assert "horse battery staple" not in redacted
    assert "token with spaces" not in redacted
    assert "browser-secret" not in redacted
    assert "[REDACTED]" in redacted


def test_byte_redaction_removes_complete_quoted_secret() -> None:
    redacted = redact_sensitive_bytes(b'{"password": "correct horse battery staple"}')

    assert b"horse battery staple" not in redacted
    assert b"[REDACTED]" in redacted


def test_known_secret_value_redaction_still_handles_unstructured_text() -> None:
    assert redact_sensitive_text("value is opaque-secret", secret_values=("opaque-secret",)) == (
        "value is [REDACTED]"
    )
