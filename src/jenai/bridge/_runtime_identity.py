"""Secret-safe identity evidence for the actual ROS bridge sidecar process.

This module deliberately uses only the Python standard library so it can be
imported by ``ros_bridge.py`` under the system interpreter that owns rclpy.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

_DDS_ENVIRONMENT_BINDINGS = (
    "FASTRTPS_DEFAULT_PROFILES_FILE",
    "FASTDDS_DEFAULT_PROFILES_FILE",
    "CYCLONEDDS_URI",
    "ROS_DISCOVERY_SERVER",
    "ROS_AUTOMATIC_DISCOVERY_RANGE",
    "ROS_STATIC_PEERS",
)
_DDS_FILE_BINDINGS = frozenset({"FASTRTPS_DEFAULT_PROFILES_FILE", "FASTDDS_DEFAULT_PROFILES_FILE"})


class RuntimeIdentityUnavailable(ValueError):
    """The sidecar could not bind its effective middleware configuration."""


def _canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path, *, binding_name: str) -> str:
    try:
        if not path.is_file():
            raise RuntimeIdentityUnavailable(f"{binding_name} is not a readable regular file")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise RuntimeIdentityUnavailable(f"{binding_name} could not be read") from exc


def _cyclonedds_file(value: str) -> Path | None:
    parsed = urlparse(value)
    if parsed.scheme == "file":
        if parsed.netloc not in {"", "localhost"}:
            raise RuntimeIdentityUnavailable("remote DDS profile URI is unsupported")
        return Path(unquote(parsed.path)).expanduser()
    candidate = Path(value).expanduser()
    if candidate.is_absolute() or value.startswith(("./", "../")):
        return candidate
    return None


def _dds_bindings(environment: dict[str, str]) -> dict[str, dict[str, str]]:
    bindings: dict[str, dict[str, str]] = {}
    for name in _DDS_ENVIRONMENT_BINDINGS:
        value = environment.get(name)
        if not value:
            continue
        path = Path(value).expanduser() if name in _DDS_FILE_BINDINGS else None
        if name == "CYCLONEDDS_URI":
            path = _cyclonedds_file(value)
        if path is not None:
            bindings[name] = {
                "kind": "file_content",
                "sha256": _file_sha256(path, binding_name=name),
            }
        else:
            bindings[name] = {
                "kind": "environment_value",
                "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            }
    return bindings


def build_runtime_identity_payload(
    *,
    effective_rmw: str,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Describe the currently running sidecar without exposing DDS secrets."""

    if not isinstance(effective_rmw, str) or not effective_rmw.strip():
        raise RuntimeIdentityUnavailable("effective RMW implementation is unavailable")
    env = dict(os.environ if environment is None else environment)
    raw_domain = env.get("ROS_DOMAIN_ID", "0")
    try:
        ros_domain_id = int(raw_domain)
    except (TypeError, ValueError) as exc:
        raise RuntimeIdentityUnavailable("ROS_DOMAIN_ID is invalid") from exc
    if not 0 <= ros_domain_id <= 232:
        raise RuntimeIdentityUnavailable("ROS_DOMAIN_ID is outside the DDS range")

    bindings = _dds_bindings(env)
    descriptor: dict[str, Any] = {
        "schema_version": 1,
        "pid": os.getpid(),
        "python_executable": os.path.normpath(sys.executable),
        "python_version": platform.python_version(),
        "rmw_implementation_requested": env.get("RMW_IMPLEMENTATION") or None,
        "rmw_implementation_effective": effective_rmw.strip(),
        "ros_domain_id": ros_domain_id,
        "dds_config_mode": "environment_binding" if bindings else "middleware_default",
        "dds_bindings": bindings,
        "dds_config_sha256": _canonical_json_sha256(bindings),
    }
    return {**descriptor, "descriptor_sha256": _canonical_json_sha256(descriptor)}


__all__ = [
    "RuntimeIdentityUnavailable",
    "build_runtime_identity_payload",
]
