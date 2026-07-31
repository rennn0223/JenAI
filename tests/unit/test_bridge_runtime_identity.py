from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import platform
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from jenai.acceptance import nav_differential_runner as runner
from jenai.acceptance.nav_differential import CanonicalGoal
from jenai.acceptance.nav_differential_runner import (
    DIFFERENTIAL_EXECUTION_CONFIRMATION,
    DifferentialCaptureOptions,
    DifferentialMode,
    ResetPolicy,
)
from jenai.bridge import BridgeError
from jenai.bridge import client as client_module
from jenai.config.models import AppConfig


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _runtime_identity_payload(
    *,
    requested_rmw: str | None = None,
    effective_rmw: str = "rmw_fastrtps_cpp",
    ros_domain_id: int = 7,
) -> dict[str, object]:
    descriptor: dict[str, object] = {
        "schema_version": 1,
        "pid": 4242,
        "python_executable": "/usr/bin/python3.12",
        "python_version": "3.12.3",
        "rmw_implementation_requested": requested_rmw,
        "rmw_implementation_effective": effective_rmw,
        "ros_domain_id": ros_domain_id,
        "dds_config_mode": "middleware_default",
        "dds_bindings": {},
        "dds_config_sha256": ("44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"),
    }
    return {**descriptor, "descriptor_sha256": _canonical_sha256(descriptor)}


class _IdentityBridge:
    def __init__(self, failure_mode: str, calls: list[str]) -> None:
        self.failure_mode = failure_mode
        self.calls = calls

    async def configure_safety(self, **_kwargs: object) -> None:
        self.calls.append("configure_safety")

    async def start(self) -> None:
        self.calls.append("start")

    async def runtime_identity(self, *, pin: bool = False) -> object:
        self.calls.append("runtime_identity")
        assert pin is True
        if self.failure_mode == "missing":
            raise BridgeError("ROS bridge ready event has no runtime identity")
        payload = _runtime_identity_payload(
            requested_rmw=("rmw_cyclonedds_cpp" if self.failure_mode == "rmw_mismatch" else None),
            effective_rmw="rmw_fastrtps_cpp",
            ros_domain_id=8 if self.failure_mode == "domain_mismatch" else 7,
        )
        return client_module.BridgeRuntimeIdentity.from_payload(payload)

    async def nav_send(self, *_args: object, **_kwargs: object) -> None:
        self.calls.append("nav_send")
        raise AssertionError("identity gate must run before navigation")


def test_stdlib_runtime_identity_hashes_file_and_value_bindings_without_disclosure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_identity = importlib.import_module("jenai.bridge._runtime_identity")
    profile = tmp_path / "private-fastdds-profile.xml"
    profile.write_text("<dds>alpha</dds>", encoding="utf-8")
    discovery_secret = "10.42.0.7:11811;token=do-not-disclose"
    monkeypatch.setenv("ROS_DOMAIN_ID", "7")
    monkeypatch.setenv("RMW_IMPLEMENTATION", "rmw_fastrtps_cpp")
    monkeypatch.setenv("FASTRTPS_DEFAULT_PROFILES_FILE", str(profile))
    monkeypatch.setenv("ROS_DISCOVERY_SERVER", discovery_secret)
    for name in (
        "FASTDDS_DEFAULT_PROFILES_FILE",
        "CYCLONEDDS_URI",
        "ROS_AUTOMATIC_DISCOVERY_RANGE",
        "ROS_STATIC_PEERS",
    ):
        monkeypatch.delenv(name, raising=False)

    first = runtime_identity.build_runtime_identity_payload(effective_rmw="rmw_fastrtps_cpp")

    assert set(first) == {
        "schema_version",
        "pid",
        "python_executable",
        "python_version",
        "rmw_implementation_requested",
        "rmw_implementation_effective",
        "ros_domain_id",
        "dds_config_mode",
        "dds_bindings",
        "dds_config_sha256",
        "descriptor_sha256",
    }
    assert first["pid"] == os.getpid()
    assert first["python_executable"] == sys.executable
    assert first["python_version"] == platform.python_version()
    assert first["rmw_implementation_requested"] == "rmw_fastrtps_cpp"
    assert first["rmw_implementation_effective"] == "rmw_fastrtps_cpp"
    assert first["ros_domain_id"] == 7
    assert first["dds_config_mode"] == "environment_binding"
    bindings = cast(dict[str, dict[str, str]], first["dds_bindings"])
    assert bindings == {
        "FASTRTPS_DEFAULT_PROFILES_FILE": {
            "kind": "file_content",
            "sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
        },
        "ROS_DISCOVERY_SERVER": {
            "kind": "environment_value",
            "sha256": hashlib.sha256(discovery_secret.encode("utf-8")).hexdigest(),
        },
    }
    serialized = json.dumps(first, sort_keys=True)
    assert str(profile) not in serialized
    assert discovery_secret not in serialized
    assert first["dds_config_sha256"] == _canonical_sha256(bindings)
    descriptor = {key: value for key, value in first.items() if key != "descriptor_sha256"}
    assert first["descriptor_sha256"] == _canonical_sha256(descriptor)

    profile.write_text("<dds>beta</dds>", encoding="utf-8")
    second = runtime_identity.build_runtime_identity_payload(effective_rmw="rmw_fastrtps_cpp")

    assert second["dds_bindings"] != first["dds_bindings"]
    assert second["dds_config_sha256"] != first["dds_config_sha256"]
    assert second["descriptor_sha256"] != first["descriptor_sha256"]


def test_stdlib_runtime_identity_fails_closed_for_unreadable_file_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_identity = importlib.import_module("jenai.bridge._runtime_identity")
    missing_profile = tmp_path / "missing-fastdds-profile.xml"
    monkeypatch.setenv("FASTRTPS_DEFAULT_PROFILES_FILE", str(missing_profile))

    with pytest.raises(ValueError, match="FASTRTPS_DEFAULT_PROFILES_FILE"):
        runtime_identity.build_runtime_identity_payload(effective_rmw="rmw_fastrtps_cpp")


def test_differential_static_identity_does_not_run_a_second_middleware_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('version = "0.1.0"\n', encoding="utf-8")

    def forbidden_probe(**_kwargs: object) -> dict[str, object]:
        raise AssertionError("runner must use the identity emitted by its actual sidecar")

    monkeypatch.setattr(
        runner,
        "_probe_ros_middleware_identity",
        forbidden_probe,
        raising=False,
    )
    monkeypatch.setattr(runner, "_command_output", lambda *args, **kwargs: "")
    monkeypatch.setattr(runner, "_controller_odom_topic", lambda **kwargs: "/chassis/odom")
    monkeypatch.setattr(runner, "_nav2_process_generation", lambda _session: None)

    identity = runner._runtime_identity(
        AppConfig(deployment_mode="simulation"),
        config_path,
        reviewed_git_sha=None,
        expected_source_root=None,
        scene_path=None,
        live_scene_sha256=None,
        simulation_epoch="epoch-01",
    )

    assert identity.get("ros_middleware") is None


@pytest.mark.parametrize(
    ("failure_mode", "expected_failure"),
    [
        ("missing", None),
        ("domain_mismatch", "bridge_runtime_domain_mismatch"),
        ("rmw_mismatch", "rmw_implementation_mismatch"),
    ],
)
def test_differential_ready_identity_blocks_before_watch_or_navigation(
    failure_mode: str,
    expected_failure: str | None,
    differential_artifact_factory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene = tmp_path / "warehouse.usd"
    scene.write_text("#usda 1.0", encoding="utf-8")
    options = DifferentialCaptureOptions(
        output=tmp_path / f"{failure_mode}.json",
        location="Dock",
        pair_id="pair-01",
        mode=DifferentialMode.R1_BRIDGE_NAV2,
        simulation_epoch="epoch-01",
        reset_policy=ResetPolicy.NAV2_RESTART,
        expected_source_root=tmp_path,
        expected_git_sha="1" * 40,
        execute=True,
        confirmation=DIFFERENTIAL_EXECUTION_CONFIRMATION,
        scene_path=scene,
        live_scene_sha256=hashlib.sha256(scene.read_bytes()).hexdigest(),
    )
    artifact = runner._base_artifact(options)
    fixture_artifact = differential_artifact_factory(mode="R1_bridge_nav2")
    identity = deepcopy(cast(dict[str, Any], fixture_artifact["runtime_identity"]))
    artifact["runtime_identity"] = identity
    config = AppConfig(deployment_mode="simulation", vehicle={"domain_id": 7})
    goal = CanonicalGoal.from_yaw(
        frame_id="map",
        x=1.0,
        y=2.0,
        yaw=0.0,
        simulation_epoch=options.simulation_epoch,
    )
    calls: list[str] = []

    async def no_op_enrich(_bridge: object, _identity: dict[str, Any]) -> None:
        calls.append("live_map_identity")

    async def forbidden_watch(*_args: object, **_kwargs: object) -> list[int]:
        calls.append("watch")
        raise AssertionError("identity gate must run before topic watches")

    bridge = _IdentityBridge(failure_mode, calls)
    monkeypatch.setattr(runner, "RosBridgeClient", lambda **_kwargs: bridge)
    monkeypatch.setattr(runner, "_enrich_live_identity", no_op_enrich)
    monkeypatch.setattr(runner, "_watch_topics", forbidden_watch)
    monkeypatch.setattr(
        runner,
        "_probe_ros_middleware_identity",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("runner must not launch a detached middleware probe")
        ),
        raising=False,
    )
    resources = runner._CaptureResources()

    async def run() -> None:
        invocation = runner._capture_live_path(
            artifact,
            options,
            config_path=tmp_path / "config.toml",
            config=config,
            bound_action={"goal": {"id": "loc-dock", "name": "Dock"}},
            goal=goal,
            identity=identity,
            pose_observations=runner._PoseObservationRecorder(),
            resources=resources,
        )
        if failure_mode == "missing":
            with pytest.raises(BridgeError, match="runtime identity"):
                await invocation
            return
        await invocation

    asyncio.run(run())

    assert calls[:3] == ["configure_safety", "start", "runtime_identity"]
    assert "watch" not in calls
    assert "nav_send" not in calls
    assert resources.motion_attempted is False
    if expected_failure is not None:
        assert artifact["overall"] == "blocked"
        runtime_gate = next(
            check for check in artifact["checks"] if check["id"] == "runtime_identity_gate"
        )
        assert expected_failure in runtime_gate["failures"]
