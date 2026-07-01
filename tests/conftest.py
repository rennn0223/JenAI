from __future__ import annotations

import pytest

from jenai.tools.registry import TOOL_RISK_REGISTRY


@pytest.fixture(autouse=True)
def _restore_tool_risk_registry():
    """`TOOL_RISK_REGISTRY` is populated once at import time by the real tool
    modules and is otherwise treated as read-only shared state. Snapshot and
    restore it around every test so a test that mutates it (even via a
    `finally` block) can never leak state into unrelated tests.
    """
    snapshot = dict(TOOL_RISK_REGISTRY)
    yield
    TOOL_RISK_REGISTRY.clear()
    TOOL_RISK_REGISTRY.update(snapshot)
