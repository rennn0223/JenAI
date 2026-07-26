from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest

from jenai.bridge import HaltEvidence
from jenai.config.store import build_minimal_config
from jenai.tools.safety import NavigationCancelStatus, halt_robot_with_receipt


def test_halt_receipt_preserves_confirmed_zero_and_cancel_evidence() -> None:
    class Bridge:
        async def halt_with_evidence(self, cmd_vel_topic="/cmd_vel", stamped=False) -> HaltEvidence:
            return HaltEvidence(True, True, True)

    receipt = asyncio.run(
        halt_robot_with_receipt(
            build_minimal_config(provider_name="test", provider="openai", default_model="test"),
            Bridge(),
        )
    )

    assert receipt.zero_velocity_delivered is True
    assert receipt.navigation_goal_canceled is True
    assert receipt.navigation_cancel_status is NavigationCancelStatus.ACKNOWLEDGED
    assert "navigation goal canceled" in receipt.message


def test_halt_receipt_is_not_created_when_delivery_is_unconfirmed() -> None:
    class Bridge:
        async def halt_with_evidence(self, cmd_vel_topic="/cmd_vel", stamped=False) -> HaltEvidence:
            raise RuntimeError("sidecar did not confirm zero velocity")

    with pytest.raises(RuntimeError, match="did not confirm"):
        asyncio.run(
            halt_robot_with_receipt(
                build_minimal_config(provider_name="test", provider="openai", default_model="test"),
                Bridge(),
            )
        )


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        (HaltEvidence(True, False, False), NavigationCancelStatus.NOT_ACTIVE),
        (HaltEvidence(True, True, False), NavigationCancelStatus.UNCONFIRMED),
    ],
)
def test_halt_receipt_preserves_non_acknowledged_cancel_states(
    evidence: HaltEvidence, expected: NavigationCancelStatus
) -> None:
    class Bridge:
        async def halt_with_evidence(self, cmd_vel_topic="/cmd_vel", stamped=False) -> HaltEvidence:
            return evidence

    receipt = asyncio.run(
        halt_robot_with_receipt(
            build_minimal_config(provider_name="test", provider="openai", default_model="test"),
            Bridge(),
        )
    )

    assert receipt.navigation_cancel_status is expected
    assert receipt.zero_velocity_delivered is True


def test_emergency_stop_service_imports_without_agent_sdk() -> None:
    script = """
import builtins
original_import = builtins.__import__
def rejecting_import(name, *args, **kwargs):
    if name == 'agents' or name.startswith('agents.'):
        raise ImportError('agent SDK intentionally unavailable')
    return original_import(name, *args, **kwargs)
builtins.__import__ = rejecting_import
import jenai.tools.emergency_stop
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
