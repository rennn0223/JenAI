from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

import jenai.acceptance.nav_differential_runner as runner
from jenai.acceptance.nav_differential import PairClassification
from jenai.config.models import AppConfig

ArtifactFactory = Callable[..., dict[str, object]]

_DEFAULT_DDS_CONFIG_SHA256 = "7" * 64


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _middleware_identity(
    *,
    requested_rmw: str | None = None,
    effective_rmw: str = "rmw_fastrtps_cpp",
    dds_config_sha256: str = _DEFAULT_DDS_CONFIG_SHA256,
) -> dict[str, object]:
    descriptor: dict[str, object] = {
        "rmw_implementation_requested": requested_rmw,
        "rmw_implementation_effective": effective_rmw,
        "rmw_discovery_source": "bridge_python_probe",
        "bridge_python_executable": "/usr/bin/python3.12",
        "bridge_python_version": "3.12.3",
        "dds_config_mode": "middleware_default",
        "dds_bindings": {},
        "dds_config_sha256": dds_config_sha256,
    }
    return {
        **descriptor,
        "descriptor_sha256": _canonical_sha256(descriptor),
    }


def _runtime_identity(factory: ArtifactFactory) -> dict[str, Any]:
    artifact = factory(mode="R1_bridge_nav2")
    identity = cast(dict[str, Any], artifact["runtime_identity"])
    identity.update(
        {
            "python_executable": "/usr/bin/python3.12",
            "python_version": "3.12.3",
            "ros_middleware": _middleware_identity(),
        }
    )
    runner._apply_runtime_fingerprint(identity)
    return identity


def _target_binding(
    goal: dict[str, Any],
    *,
    requested_query: str = "Dock",
    resolved_name: str = "Dock",
    resolved_id: str = "loc-dock",
    pose: dict[str, float] | None = None,
) -> dict[str, object]:
    bound_pose = pose or {
        "x": float(goal["x"]),
        "y": float(goal["y"]),
        "yaw": float(goal["yaw"]),
    }
    record = {
        "capability_id": "navigate",
        "frame_id": str(goal["frame_id"]).lstrip("/"),
        "locations_sha256": "c" * 64,
        "pose": bound_pose,
        "resolved_name": resolved_name,
    }
    canonical_record_sha256 = _canonical_sha256(record)
    canonical_goal_sha256 = _canonical_sha256(goal)
    stable_binding = {
        "canonical_goal_sha256": canonical_goal_sha256,
        "canonical_record_sha256": canonical_record_sha256,
        "capability_id": "navigate",
        "locations_sha256": "c" * 64,
    }
    return {
        "requested_query": requested_query,
        "resolved_name": resolved_name,
        "resolved_id": resolved_id,
        "frame_id": record["frame_id"],
        "pose": deepcopy(bound_pose),
        "capability_id": "navigate",
        "locations_sha256": "c" * 64,
        "canonical_record_sha256": canonical_record_sha256,
        "canonical_goal_sha256": canonical_goal_sha256,
        "binding_sha256": _canonical_sha256(stable_binding),
    }


def _bind_artifact_target(
    artifact: dict[str, object],
    *,
    requested_query: str = "Dock",
    resolved_name: str = "Dock",
    resolved_id: str = "loc-dock",
    pose: dict[str, float] | None = None,
) -> None:
    goal = cast(dict[str, Any], artifact["canonical_goal"])
    artifact["target_binding"] = _target_binding(
        goal,
        requested_query=requested_query,
        resolved_name=resolved_name,
        resolved_id=resolved_id,
        pose=pose,
    )


def _comparison(
    factory: ArtifactFactory,
    *,
    left: dict[str, object],
    right: dict[str, object],
) -> dict[str, Any]:
    left["runtime_identity"] = _runtime_identity(factory)
    right["runtime_identity"] = deepcopy(left["runtime_identity"])
    return runner.compare_differential_artifacts(
        cast(dict[str, Any], left),
        cast(dict[str, Any], right),
    )


def test_runtime_fingerprint_binds_python_executable(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    identity = _runtime_identity(differential_artifact_factory)
    original = identity["fingerprint"]

    identity["python_executable"] = "/opt/jenai/python/bin/python3"
    runner._apply_runtime_fingerprint(identity)

    assert identity["fingerprint"] != original


def test_runtime_fingerprint_binds_python_version(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    identity = _runtime_identity(differential_artifact_factory)
    original = identity["fingerprint"]

    identity["python_version"] = "3.13.1"
    runner._apply_runtime_fingerprint(identity)

    assert identity["fingerprint"] != original


@pytest.mark.parametrize(
    ("field", "invalid_value", "expected_failure"),
    [
        ("python_executable", "python3", "python_executable_unavailable"),
        ("python_version", "three.twelve", "python_version_invalid"),
    ],
)
def test_source_identity_rejects_unbound_python_runtime(
    differential_artifact_factory: ArtifactFactory,
    field: str,
    invalid_value: str,
    expected_failure: str,
) -> None:
    identity = _runtime_identity(differential_artifact_factory)
    identity[field] = invalid_value
    runner._apply_runtime_fingerprint(identity)

    assert expected_failure in runner._source_identity_failures(identity)


def test_target_binding_must_match_the_artifact_canonical_goal(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = differential_artifact_factory(mode="R1_bridge_nav2")
    right = differential_artifact_factory(mode="R2_jenai_no_retry")
    _bind_artifact_target(
        left,
        pose={"x": 9.0, "y": 2.0, "yaw": 0.0},
    )
    _bind_artifact_target(right)

    report = _comparison(differential_artifact_factory, left=left, right=right)

    assert report["included"] is False
    assert report["classifications"] == [PairClassification.INSUFFICIENT_EVIDENCE]


def test_pairing_gate_rejects_different_resolved_target_bindings(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = differential_artifact_factory(mode="R1_bridge_nav2")
    right = differential_artifact_factory(mode="R2_jenai_no_retry")
    _bind_artifact_target(left)
    _bind_artifact_target(
        right,
        requested_query="Lab",
        resolved_name="Lab",
        resolved_id="loc-lab",
    )

    report = _comparison(differential_artifact_factory, left=left, right=right)

    assert report["included"] is False
    assert PairClassification.PAIRING_GATE_FAILED in report["classifications"]


def test_alias_queries_for_the_same_resolved_target_remain_pairable(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    left = differential_artifact_factory(mode="R1_bridge_nav2")
    right = differential_artifact_factory(mode="R2_jenai_no_retry")
    _bind_artifact_target(left, requested_query="Dock")
    _bind_artifact_target(right, requested_query="charging station")

    report = _comparison(differential_artifact_factory, left=left, right=right)

    assert report["included"] is True
    assert PairClassification.PAIRING_GATE_FAILED not in report["classifications"]


def test_runtime_identity_uses_effective_rmw_probe_with_middleware_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _middleware_identity()
    probe_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def probe(*args: object, **kwargs: object) -> dict[str, object]:
        probe_calls.append((args, kwargs))
        return deepcopy(descriptor)

    monkeypatch.setattr(runner, "_probe_ros_middleware_identity", probe, raising=False)
    monkeypatch.setattr(runner, "_command_output", lambda *args, **kwargs: "")
    monkeypatch.setattr(runner, "_controller_odom_topic", lambda **kwargs: "/chassis/odom")
    monkeypatch.setattr(runner, "_nav2_process_generation", lambda session: None)
    monkeypatch.delenv("RMW_IMPLEMENTATION", raising=False)
    monkeypatch.delenv("FASTRTPS_DEFAULT_PROFILES_FILE", raising=False)
    monkeypatch.delenv("FASTDDS_DEFAULT_PROFILES_FILE", raising=False)
    monkeypatch.delenv("CYCLONEDDS_URI", raising=False)
    config_path = tmp_path / "config.toml"
    config_path.write_text('version = "0.1.0"\n', encoding="utf-8")

    identity = runner._runtime_identity(
        AppConfig(deployment_mode="simulation"),
        config_path,
        reviewed_git_sha=None,
        expected_source_root=None,
        scene_path=None,
        live_scene_sha256=None,
        simulation_epoch="epoch-01",
    )

    assert len(probe_calls) == 1
    assert identity["ros_middleware"] == descriptor
    assert descriptor["rmw_implementation_requested"] is None
    assert descriptor["dds_config_mode"] == "middleware_default"


def test_source_identity_rejects_requested_and_effective_rmw_mismatch(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    identity = _runtime_identity(differential_artifact_factory)
    identity["ros_middleware"] = _middleware_identity(
        requested_rmw="rmw_cyclonedds_cpp",
        effective_rmw="rmw_fastrtps_cpp",
    )
    runner._apply_runtime_fingerprint(identity)

    assert "rmw_implementation_mismatch" in runner._source_identity_failures(identity)


def test_runtime_fingerprint_binds_middleware_descriptor_digest(
    differential_artifact_factory: ArtifactFactory,
) -> None:
    identity = _runtime_identity(differential_artifact_factory)
    original = identity["fingerprint"]

    identity["ros_middleware"] = _middleware_identity(dds_config_sha256="8" * 64)
    runner._apply_runtime_fingerprint(identity)

    assert identity["fingerprint"] != original
