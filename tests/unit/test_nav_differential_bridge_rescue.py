from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from typing import Any

import pytest

from jenai.acceptance import nav_differential_runner as runner
from jenai.bridge import BridgeRuntimeIdentity, HaltEvidence
from jenai.config.models import AppConfig

ArtifactFactory = Callable[..., dict[str, object]]


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _identity(*, pid: int, nonce: str, start_ticks: int) -> dict[str, Any]:
    descriptor: dict[str, Any] = {
        "schema_version": 1,
        "pid": pid,
        "launch_nonce": nonce,
        "boot_id": "12345678-1234-5678-1234-567812345678",
        "process_start_ticks": start_ticks,
        "python_executable": "/usr/bin/python3.12",
        "python_version": "3.12.3",
        "rmw_implementation_requested": "rmw_fastrtps_cpp",
        "rmw_implementation_effective": "rmw_fastrtps_cpp",
        "ros_domain_id": 7,
        "dds_config_mode": "middleware_default",
        "dds_bindings": {},
        "dds_config_sha256": _digest({}),
        "ros_environment_bindings": {},
        "ros_environment_sha256": _digest({}),
    }
    return {**descriptor, "descriptor_sha256": _digest(descriptor)}


@pytest.mark.parametrize("cancel_acknowledged", [True, False])
def test_dead_primary_uses_one_safety_replacement_bridge(
    monkeypatch: pytest.MonkeyPatch,
    cancel_acknowledged: bool,
) -> None:
    primary_payload = _identity(pid=101, nonce="a" * 32, start_ticks=1001)
    replacement_payload = _identity(pid=202, nonce="b" * 32, start_ticks=2002)
    events: list[str] = []

    class Primary:
        running = False

        async def unwatch(self, _watch_id: int) -> None:
            raise AssertionError("dead primary must not be restarted by unwatch")

        async def stop(self) -> None:
            events.append("primary_stop")

    class Replacement:
        running = False

        def __init__(self, *, domain_id: int | None = None) -> None:
            assert domain_id == 7
            events.append("replacement_created")

        async def configure_safety(self, **_: object) -> None:
            events.append("replacement_configured")

        async def start(self, timeout: float = 10.0) -> None:
            assert timeout > 0
            self.running = True
            events.append("replacement_started")

        async def runtime_identity(self, *, pin: bool = False) -> BridgeRuntimeIdentity:
            assert pin is True
            return BridgeRuntimeIdentity.from_payload(replacement_payload)

        async def halt_with_evidence(self, *_: object) -> HaltEvidence:
            events.append("replacement_halt")
            return HaltEvidence(True, True, cancel_acknowledged)

        async def stop(self) -> None:
            self.running = False
            events.append("replacement_stopped")

    monkeypatch.setattr(runner, "RosBridgeClient", Replacement)
    cleanup = asyncio.run(
        runner._cleanup_live_capture(
            Primary(),  # type: ignore[arg-type]
            AppConfig(deployment_mode="simulation"),
            [1],
            None,
            motion_attempted=True,
            primary_runtime_identity=primary_payload,
        )
    )

    assert events.count("replacement_created") == 1
    assert events[-1] == "primary_stop"
    assert cleanup["rescue_bridge"]["bridge_shutdown"]["status"] == "PASS"
    if cancel_acknowledged:
        assert cleanup["status"] == "PASS"
        assert cleanup["final_halt"]["status"] == "PASS"
    else:
        assert cleanup["status"] == "FAIL"
        assert cleanup["final_halt"]["status"] == "FAIL"


def test_offline_validator_rejects_tampered_rescue_bridge_identity(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    artifact = differential_artifact_factory(mode="R1_bridge_nav2")
    runtime = artifact["runtime_identity"]
    assert isinstance(runtime, dict)
    primary = runtime["ros_middleware"]
    assert isinstance(primary, dict)
    replacement_descriptor = {
        **{key: value for key, value in primary.items() if key != "descriptor_sha256"},
        "pid": 202,
        "launch_nonce": "b" * 32,
        "process_start_ticks": 2002,
    }
    replacement = {
        **replacement_descriptor,
        "descriptor_sha256": _digest(replacement_descriptor),
    }
    final_halt = {
        "status": "PASS",
        "zero_velocity_command_published": True,
        "navigation_cancel_requested": True,
        "navigation_cancel_acknowledged": True,
        "motion_stop_observed": False,
    }
    artifact["cleanup"] = {
        "status": "PASS",
        "failures": [],
        "primary_halt": {"status": "FAIL", "detail": "bridge_not_running"},
        "final_halt": final_halt,
        "rescue_bridge": {
            "status": "PASS",
            "failures": [],
            "runtime_identity": replacement,
            "identity_compatible": True,
            "final_halt": final_halt,
            "bridge_shutdown": {"status": "PASS"},
        },
        "unwatch": {"status": "PASS", "failures": []},
        "bridge_shutdown": {"status": "PASS"},
    }

    assert runner._comparison_eligibility_failure(artifact, "r1") is None

    replacement_descriptor["boot_id"] = "87654321-4321-8765-4321-876543218765"
    replacement.update(
        replacement_descriptor,
        descriptor_sha256=_digest(replacement_descriptor),
    )
    detail = runner._comparison_eligibility_failure(artifact, "r1")

    assert detail is not None
    assert "cleanup_rescue_identity" in detail
