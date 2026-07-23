"""Contracts for the source-checkout one-key launcher."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts" / "jenai"


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    project = tmp_path / "project"
    bin_dir.mkdir(parents=True)
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    _write_executable(
        bin_dir / "uv",
        "#!/usr/bin/env bash\n"
        "printf 'base=%s workspace=%s args=%s\\n' "
        '"${JENAI_ROS_BASE:-}" "${JENAI_ROS_WORKSPACE:-}" "$*"\n',
    )
    env = os.environ.copy()
    env.update({"HOME": str(home), "JENAI_DIR": str(project)})
    env.pop("ROS_DISTRO", None)
    return home, project, env


def test_launcher_sources_jazzy_underlay_and_workspace(tmp_path: Path) -> None:
    home, project, env = _fixture(tmp_path)
    ros_setup = tmp_path / "ros-setup.bash"
    workspace_setup = home / "IsaacSim-ros_workspaces" / "jazzy_ws" / "install" / "setup.bash"
    workspace_setup.parent.mkdir(parents=True)
    ros_setup.write_text("export ROS_DISTRO=jazzy\nexport JENAI_ROS_BASE=ready\n", encoding="utf-8")
    workspace_setup.write_text("export JENAI_ROS_WORKSPACE=ready\n", encoding="utf-8")
    env.update({"ROS_SETUP": str(ros_setup)})

    result = subprocess.run(
        [str(LAUNCHER), "doctor"], env=env, text=True, capture_output=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert "base=ready workspace=ready" in result.stdout
    assert f"args=run --project {project} JenAI doctor" in result.stdout


def test_launcher_allows_workspace_bootstrap_to_be_disabled(tmp_path: Path) -> None:
    _, _, env = _fixture(tmp_path)
    env.update({"ROS_SETUP": str(tmp_path / "missing.bash"), "ROS_WORKSPACE_SETUP": ""})

    result = subprocess.run([str(LAUNCHER)], env=env, check=False)

    assert result.returncode == 0
