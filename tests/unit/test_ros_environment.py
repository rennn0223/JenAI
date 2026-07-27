"""ROS environment bootstrap contracts for every packaged JenAI entry point."""

from __future__ import annotations

import os
from pathlib import Path

from jenai.ros_environment import bootstrap_ros_environment


def _setup(path: Path, *exports: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"export {value}" for value in exports) + "\n", encoding="utf-8")


def test_bootstrap_sources_jazzy_and_workspace_when_ros2_is_unsourced(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ros_bin = tmp_path / "ros-bin"
    ros_bin.mkdir()
    ros2 = ros_bin / "ros2"
    ros2.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    ros2.chmod(0o755)
    base = tmp_path / "opt" / "ros" / "jazzy" / "setup.bash"
    workspace = tmp_path / "jazzy_ws" / "install" / "setup.bash"
    _setup(
        base,
        "ROS_DISTRO=jazzy",
        f"PATH={ros_bin}:${{PATH}}",
        "JENAI_TEST_ROS_BASE=ready",
    )
    _setup(workspace, "JENAI_TEST_ROS_WORKSPACE=ready")
    monkeypatch.delenv("ROS_DISTRO", raising=False)
    monkeypatch.delenv("JENAI_TEST_ROS_BASE", raising=False)
    monkeypatch.delenv("JENAI_TEST_ROS_WORKSPACE", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("ROS_SETUP", str(base))
    monkeypatch.setenv("ROS_WORKSPACE_SETUP", str(workspace))

    result = bootstrap_ros_environment()

    assert result.available is True
    assert result.loaded == (base, workspace)
    assert result.error is None
    assert os.environ["ROS_DISTRO"] == "jazzy"
    assert os.environ["JENAI_TEST_ROS_BASE"] == "ready"
    assert os.environ["JENAI_TEST_ROS_WORKSPACE"] == "ready"


def test_bootstrap_does_not_layer_default_jazzy_workspace_over_active_humble(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    workspace = home / "IsaacSim-ros_workspaces" / "jazzy_ws" / "install" / "setup.bash"
    _setup(workspace, "JENAI_TEST_WRONG_OVERLAY=loaded")
    ros_bin = tmp_path / "humble-bin"
    ros_bin.mkdir()
    ros2 = ros_bin / "ros2"
    ros2.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    ros2.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ROS_DISTRO", "humble")
    monkeypatch.setenv("PATH", f"{ros_bin}:/usr/bin:/bin")
    monkeypatch.delenv("ROS_SETUP", raising=False)
    monkeypatch.delenv("ROS_WORKSPACE_SETUP", raising=False)
    monkeypatch.delenv("JENAI_TEST_WRONG_OVERLAY", raising=False)

    result = bootstrap_ros_environment()

    assert result.available is True
    assert result.loaded == ()
    assert "JENAI_TEST_WRONG_OVERLAY" not in os.environ


def test_bootstrap_allows_workspace_autoload_to_be_disabled(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace" / "setup.bash"
    _setup(workspace, "JENAI_TEST_DISABLED_OVERLAY=loaded")
    monkeypatch.delenv("ROS_DISTRO", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("ROS_SETUP", str(tmp_path / "missing.bash"))
    monkeypatch.setenv("ROS_WORKSPACE_SETUP", "")
    monkeypatch.delenv("JENAI_TEST_DISABLED_OVERLAY", raising=False)

    result = bootstrap_ros_environment()

    assert result.available is False
    assert result.loaded == ()
    assert result.error is None
    assert "JENAI_TEST_DISABLED_OVERLAY" not in os.environ
