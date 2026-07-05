"""Scaffold a ROS2 (ament_python) package from a natural-language spec.

The leap from *control* agent (drives an existing robot) to *development*
copilot (writes robot code). The honest split that makes this trustworthy:

- **Boilerplate is deterministic** — package.xml / setup.py / setup.cfg /
  resource marker are rendered by templates here, so the package always builds
  with `colcon build` regardless of what the model does. New students get the
  ament_python wiring (the part everyone gets wrong) correct for free.
- **Node logic is LLM-written and user-reviewed** — the model fills the node
  body; we never pretend it is correct control code. It is generated, shown,
  and only written after the user confirms; then they read it before building.

`render_package` is pure (dict of relpath → content) so it is unit-testable
without ROS or a provider; `generate_package_plan` is the one LLM call.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

from jenai.config.models import AppConfig
from jenai.providers.chat import ask_json

# ROS2 package names: lowercase, digits, underscores; must start with a letter.
_PKG_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
# Only these rosdep keys are allowed into package.xml unreviewed — an
# LLM-hallucinated dependency would make `rosdep`/`colcon` fail confusingly.
_KNOWN_DEPS = {
    "rclpy", "std_msgs", "sensor_msgs", "geometry_msgs", "nav_msgs",
    "nav2_msgs", "action_msgs", "tf2_ros", "tf2_geometry_msgs",
    "visualization_msgs", "std_srvs", "builtin_interfaces",
}


class PackagePlan(BaseModel):
    """A reviewed, buildable package spec produced from natural language."""

    model_config = ConfigDict(extra="ignore")  # tolerate extra LLM keys

    package_name: str
    description: str
    node_name: str
    node_code: str
    dependencies: list[str] = ["rclpy"]

    @field_validator("package_name", "node_name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        v = v.strip().lower().replace("-", "_").replace(" ", "_")
        if not _PKG_NAME.match(v):
            raise ValueError(f"invalid ROS2 name '{v}' (need [a-z][a-z0-9_]*)")
        return v

    @field_validator("dependencies")
    @classmethod
    def _known_deps(cls, deps: list[str]) -> list[str]:
        # Drop unknown/hallucinated deps but always keep rclpy — a Python node
        # cannot run without it, and an unknown dep breaks the whole build.
        kept = [d for d in dict.fromkeys(deps) if d in _KNOWN_DEPS]
        return kept or ["rclpy"]


_PLAN_PROMPT = (
    "You are scaffolding a ROS2 Humble/Jazzy ament_python package from a "
    "user request. Respond with ONLY JSON:\n"
    '{"package_name": "snake_case", "description": "one line", '
    '"node_name": "snake_case", "dependencies": ["rclpy", "sensor_msgs", ...], '
    '"node_code": "COMPLETE python source for the node"}\n'
    "Rules: dependencies only from this set: "
    + ", ".join(sorted(_KNOWN_DEPS))
    + ". node_code must be a runnable rclpy Node with a main() that does "
    "rclpy.init(), spins the node, and shuts down cleanly; wire the subscribers/"
    "publishers the request implies; keep control logic simple and commented. "
    "Do NOT include markdown fences.\n\nRequest: "
)


async def generate_package_plan(config: AppConfig, spec: str) -> PackagePlan | None:
    """Ask the model for a package plan; None on any failure (honest: the
    caller reports 'could not generate' rather than writing a broken package)."""
    parsed = await ask_json(config, _PLAN_PROMPT + spec, binding="plan")
    if not isinstance(parsed, dict):
        return None
    try:
        return PackagePlan.model_validate(parsed)
    except Exception:
        return None


def _skeleton_node(node_name: str) -> str:
    """Fallback node body when the model returned nothing usable — still a
    valid, runnable node so the package builds and the user can fill it in."""
    return (
        "import rclpy\n"
        "from rclpy.node import Node\n\n\n"
        f"class {_camel(node_name)}(Node):\n"
        "    def __init__(self) -> None:\n"
        f'        super().__init__("{node_name}")\n'
        '        self.get_logger().info("node up — add your logic here.")\n\n\n'
        "def main() -> None:\n"
        "    rclpy.init()\n"
        f"    node = {_camel(node_name)}()\n"
        "    try:\n"
        "        rclpy.spin(node)\n"
        "    except KeyboardInterrupt:\n"
        "        pass\n"
        "    finally:\n"
        "        node.destroy_node()\n"
        "        rclpy.shutdown()\n\n\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )


def _camel(snake: str) -> str:
    return "".join(part.capitalize() for part in snake.split("_")) or "Node"


def render_package(plan: PackagePlan) -> dict[str, str]:
    """Render a plan into {relative_path: file_contents}. Pure — no I/O, no ROS.

    Boilerplate (package.xml/setup.py/setup.cfg/resource/__init__) is fully
    deterministic so the result always builds; only the node body comes from
    the model (falling back to a runnable skeleton)."""
    pkg = plan.package_name
    node = plan.node_name
    node_code = plan.node_code.strip() or _skeleton_node(node)
    exec_depends = "\n".join(f"  <exec_depend>{d}</exec_depend>" for d in plan.dependencies)
    return {
        "package.xml": (
            '<?xml version="1.0"?>\n'
            '<?xml-model href="http://download.ros.org/schema/package_format3.xsd" '
            'schematypens="http://www.w3.org/2001/XMLSchema"?>\n'
            '<package format="3">\n'
            f"  <name>{pkg}</name>\n"
            "  <version>0.0.0</version>\n"
            f"  <description>{plan.description}</description>\n"
            "  <maintainer email=\"you@example.com\">you</maintainer>\n"
            "  <license>Apache-2.0</license>\n\n"
            "  <buildtool_depend>ament_python</buildtool_depend>\n"
            f"{exec_depends}\n\n"
            "  <test_depend>ament_copyright</test_depend>\n"
            "  <test_depend>ament_flake8</test_depend>\n"
            "  <test_depend>ament_pep257</test_depend>\n"
            "  <test_depend>python3-pytest</test_depend>\n\n"
            "  <export>\n"
            "    <build_type>ament_python</build_type>\n"
            "  </export>\n"
            "</package>\n"
        ),
        "setup.py": (
            "from setuptools import find_packages, setup\n\n"
            f'package_name = "{pkg}"\n\n'
            "setup(\n"
            "    name=package_name,\n"
            '    version="0.0.0",\n'
            '    packages=find_packages(exclude=["test"]),\n'
            "    data_files=[\n"
            '        ("share/ament_index/resource_index/packages", '
            '["resource/" + package_name]),\n'
            '        ("share/" + package_name, ["package.xml"]),\n'
            "    ],\n"
            '    install_requires=["setuptools"],\n'
            "    zip_safe=True,\n"
            '    maintainer="you",\n'
            '    maintainer_email="you@example.com",\n'
            f'    description="{plan.description}",\n'
            '    license="Apache-2.0",\n'
            '    tests_require=["pytest"],\n'
            "    entry_points={\n"
            '        "console_scripts": [\n'
            f'            "{node} = {pkg}.{node}:main",\n'
            "        ],\n"
            "    },\n"
            ")\n"
        ),
        "setup.cfg": (
            "[develop]\n"
            f"script_dir=$base/lib/{pkg}\n"
            "[install]\n"
            f"install_scripts=$base/lib/{pkg}\n"
        ),
        f"resource/{pkg}": "",
        f"{pkg}/__init__.py": "",
        f"{pkg}/{node}.py": node_code if node_code.endswith("\n") else node_code + "\n",
        "README.md": (
            f"# {pkg}\n\n{plan.description}\n\n"
            "Generated by JenAI `scaffold`. **Review `"
            f"{pkg}/{node}.py` before building** — the node body is "
            "LLM-written; the package boilerplate is deterministic.\n\n"
            "```bash\n"
            "cd <your_ros2_ws>\n"
            f"colcon build --packages-select {pkg}\n"
            "source install/setup.bash\n"
            f"ros2 run {pkg} {node}\n"
            "```\n"
        ),
    }


def default_ws(config: AppConfig) -> Path:
    """Where packages are created: config.ros2_ws/src, else ~/ros2_ws/src."""
    root = config.ros2_ws or "~/ros2_ws"
    return Path(root).expanduser() / "src"


def build_package(ws_root: Path, package_name: str, *, timeout: float = 300.0) -> tuple[bool, str]:
    """Run `colcon build --packages-select <pkg>` in the workspace root.

    Sources the ROS setup first (same ROS_SETUP contract as the bridge) so it
    works from a non-sourced shell. Returns (ok, log_tail) — the tail is what
    the repair round feeds back to the model. Honest failure when colcon/ROS
    are absent: (False, reason), never a fake success.
    """
    import os
    import subprocess

    ros_setup = os.environ.get("ROS_SETUP", "/opt/ros/jazzy/setup.bash")
    command = (
        f'source "{ros_setup}" 2>/dev/null; '
        f"colcon build --packages-select {package_name}"
    )
    try:
        proc = subprocess.run(
            ["bash", "-c", command],
            cwd=ws_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return False, "bash/colcon not found — build skipped."
    except subprocess.TimeoutExpired:
        return False, f"colcon build timed out after {timeout:.0f}s."
    log = (proc.stdout + "\n" + proc.stderr).strip()
    # Keep only the tail: colcon logs are long and the model needs the error.
    tail = "\n".join(log.splitlines()[-40:])
    return proc.returncode == 0, tail


_REPAIR_PROMPT = (
    "The following ROS2 node failed to build. Fix the node source. Respond "
    'with ONLY JSON: {{"node_code": "COMPLETE corrected python source"}}. '
    "Do not change the package layout; no markdown fences.\n\n"
    "--- build errors (tail) ---\n{errors}\n\n--- current node source ---\n{code}\n"
)


async def repair_node(config: AppConfig, plan: PackagePlan, build_log: str) -> PackagePlan | None:
    """One LLM repair round: feed the build errors + node source back, get a
    corrected node body. None when the model can't produce one (the caller
    reports the build failure honestly instead of looping forever)."""
    parsed = await ask_json(
        config,
        _REPAIR_PROMPT.format(errors=build_log[-3000:], code=plan.node_code[-6000:]),
        binding="plan",
    )
    if not isinstance(parsed, dict) or not str(parsed.get("node_code", "")).strip():
        return None
    return plan.model_copy(update={"node_code": str(parsed["node_code"])})


def rewrite_node(plan: PackagePlan, ws_src: Path) -> Path:
    """Overwrite ONLY the node source of an already-written package (the
    repair round must never touch the deterministic boilerplate)."""
    node_path = ws_src / plan.package_name / plan.package_name / f"{plan.node_name}.py"
    code = plan.node_code.strip() + "\n"
    node_path.write_text(code, encoding="utf-8")
    return node_path


def write_package(plan: PackagePlan, ws_src: Path) -> Path:
    """Write a rendered package under ws_src/<pkg>/. Refuses to overwrite an
    existing package (never clobber the user's code). Returns the package dir."""
    pkg_dir = ws_src / plan.package_name
    if pkg_dir.exists():
        raise FileExistsError(f"package already exists: {pkg_dir}")
    for relpath, content in render_package(plan).items():
        target = pkg_dir / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return pkg_dir
