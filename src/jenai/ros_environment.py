"""Load a usable ROS 2 environment for every packaged JenAI entry point.

Shell setup files cannot modify their parent Python process. JenAI therefore
sources the configured underlay and workspace in a short-lived Bash process,
captures the resulting environment, and applies it before any command probes
ROS. Missing setup files are intentionally non-fatal because ROS is optional.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_ROS_SETUP = Path("/opt/ros/jazzy/setup.bash")
_DEFAULT_WORKSPACE_RELATIVE = Path("IsaacSim-ros_workspaces/jazzy_ws/install/setup.bash")
_SOURCE_ENV_SCRIPT = """
for setup_file in "$@"; do
    if ! source "$setup_file" >/dev/null; then
        exit 1
    fi
done
env -0
"""


@dataclass(frozen=True, slots=True)
class RosEnvironmentBootstrapResult:
    """Observable result of a best-effort ROS environment bootstrap."""

    available: bool
    loaded: tuple[Path, ...]
    error: str | None = None


def _configured_paths() -> tuple[Path, Path | None, bool]:
    base = Path(os.environ.get("ROS_SETUP", str(_DEFAULT_ROS_SETUP))).expanduser()
    workspace_value = os.environ.get("ROS_WORKSPACE_SETUP")
    workspace_is_default = workspace_value is None
    if workspace_value == "":
        workspace = None
    elif workspace_value is None:
        workspace = Path.home() / _DEFAULT_WORKSPACE_RELATIVE
    else:
        workspace = Path(workspace_value).expanduser()
    return base, workspace, workspace_is_default


def _environment_from_output(output: bytes) -> dict[str, str]:
    environment: dict[str, str] = {}
    for entry in output.split(b"\0"):
        key, separator, value = entry.partition(b"=")
        if separator and key:
            environment[os.fsdecode(key)] = os.fsdecode(value)
    return environment


def bootstrap_ros_environment(*, timeout_s: float = 5.0) -> RosEnvironmentBootstrapResult:
    """Source the configured ROS underlay/overlay when the process needs them.

    The default Jazzy workspace is never layered over an already usable,
    explicitly active non-Jazzy distribution. An explicit
    ``ROS_WORKSPACE_SETUP`` remains an operator-controlled override. Failures
    are returned for diagnostics rather than aborting startup, preserving
    JenAI's provider-only features on machines without ROS.
    """

    base, workspace, workspace_is_default = _configured_paths()
    ros2_available = shutil.which("ros2") is not None
    active_distro = os.environ.get("ROS_DISTRO")
    if ros2_available and active_distro and active_distro != "jazzy" and workspace_is_default:
        return RosEnvironmentBootstrapResult(available=True, loaded=())

    paths: list[Path] = []
    if (not ros2_available or not active_distro) and base.is_file():
        paths.append(base)
    if workspace is not None and workspace.is_file():
        paths.append(workspace)

    if not paths:
        return RosEnvironmentBootstrapResult(available=ros2_available, loaded=())

    try:
        completed = subprocess.run(
            [
                "bash",
                "--noprofile",
                "--norc",
                "-c",
                _SOURCE_ENV_SCRIPT,
                "jenai-ros-environment",
                *(str(path) for path in paths),
            ],
            check=False,
            capture_output=True,
            timeout=timeout_s,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return RosEnvironmentBootstrapResult(
            available=ros2_available,
            loaded=(),
            error=f"could not load ROS environment: {exc}",
        )

    if completed.returncode != 0:
        detail = completed.stderr.decode(errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        return RosEnvironmentBootstrapResult(
            available=ros2_available,
            loaded=(),
            error=f"ROS setup exited with code {completed.returncode}{suffix}",
        )

    resolved = _environment_from_output(completed.stdout)
    os.environ.update(resolved)
    return RosEnvironmentBootstrapResult(
        available=shutil.which("ros2") is not None,
        loaded=tuple(paths),
    )
