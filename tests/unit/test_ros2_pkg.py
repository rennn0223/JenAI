from __future__ import annotations

import ast
import asyncio
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from jenai.config.store import build_minimal_config
from jenai.tools.ros2_pkg_core import (
    PackagePlan,
    default_ws,
    generate_package_plan,
    render_package,
    write_package,
)


def _plan(**over) -> PackagePlan:
    base = dict(
        package_name="obstacle_stop",
        description="Stop on close obstacle",
        node_name="stopper",
        node_code="import rclpy\n\n\ndef main():\n    rclpy.init()\n    rclpy.shutdown()\n",
        dependencies=["rclpy", "sensor_msgs"],
    )
    base.update(over)
    return PackagePlan(**base)


def test_name_validation_normalizes_and_rejects() -> None:
    assert _plan(package_name="My-Pkg Name").package_name == "my_pkg_name"
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        _plan(package_name="9bad")  # cannot start with a digit


def test_unknown_dependencies_dropped_rclpy_kept() -> None:
    p = _plan(dependencies=["rclpy", "hallucinated_msgs", "sensor_msgs"])
    assert p.dependencies == ["rclpy", "sensor_msgs"]
    assert _plan(dependencies=["nonsense"]).dependencies == ["rclpy"]  # never empty


def test_render_produces_buildable_layout() -> None:
    files = render_package(_plan())
    assert set(files) >= {
        "package.xml", "setup.py", "setup.cfg",
        "resource/obstacle_stop", "obstacle_stop/__init__.py",
        "obstacle_stop/stopper.py", "README.md",
    }
    assert "<build_type>ament_python</build_type>" in files["package.xml"]
    assert "<exec_depend>sensor_msgs</exec_depend>" in files["package.xml"]
    # entry point wires the console script to the node's main().
    assert '"stopper = obstacle_stop.stopper:main"' in files["setup.py"]
    assert files["resource/obstacle_stop"] == ""  # ament marker is empty


def test_render_escapes_description_for_python_and_xml() -> None:
    description = 'Say "hello" & move <slowly>\nthen stop'
    files = render_package(_plan(description=description))

    ast.parse(files["setup.py"])
    root = ET.fromstring(files["package.xml"])

    assert root.findtext("description") == description


def test_empty_node_code_falls_back_to_runnable_skeleton() -> None:
    files = render_package(_plan(node_code="  "))
    node = files["obstacle_stop/stopper.py"]
    assert "class Stopper(Node)" in node and "def main()" in node
    assert "rclpy.init()" in node and "rclpy.shutdown()" in node


def test_write_package_creates_tree_and_refuses_overwrite(tmp_path: Path) -> None:
    ws_src = tmp_path / "src"
    pkg_dir = write_package(_plan(), ws_src)
    assert (pkg_dir / "package.xml").exists()
    assert (pkg_dir / "obstacle_stop" / "stopper.py").exists()
    with pytest.raises(FileExistsError):
        write_package(_plan(), ws_src)  # never clobber existing code


def test_default_ws_uses_config_then_home() -> None:
    cfg = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )
    assert default_ws(cfg).name == "src" and "ros2_ws" in str(default_ws(cfg))
    cfg.ros2_ws = "/tmp/my_ws"
    assert default_ws(cfg) == Path("/tmp/my_ws/src")


def test_generate_plan_parses_llm_and_degrades(monkeypatch) -> None:
    import jenai.tools.ros2_pkg_core as mod

    cfg = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )

    async def ok(config, prompt, *, binding="chat"):
        return {
            "package_name": "demo", "description": "d", "node_name": "n",
            "dependencies": ["rclpy"], "node_code": "def main():\n    pass\n",
        }

    async def junk(config, prompt, *, binding="chat"):
        return "not a dict"

    monkeypatch.setattr(mod, "ask_json", ok)
    plan = asyncio.run(generate_package_plan(cfg, "make a demo node"))
    assert plan is not None and plan.package_name == "demo"

    monkeypatch.setattr(mod, "ask_json", junk)
    assert asyncio.run(generate_package_plan(cfg, "x")) is None  # honest None


# --- generate-and-verify (--build) -------------------------------------------


class _Proc:
    def __init__(self, rc: int, out: str = "", err: str = ""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_build_package_reports_ok_and_tail(monkeypatch, tmp_path: Path) -> None:
    import subprocess

    from jenai.tools.ros2_pkg_core import build_package

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, "Summary: 1 package finished"))
    ok, tail = build_package(tmp_path, "demo")
    assert ok is True and "finished" in tail

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(1, "", "SyntaxError: bad"))
    ok, tail = build_package(tmp_path, "demo")
    assert ok is False and "SyntaxError" in tail


def test_repair_node_returns_patched_plan_or_none(monkeypatch) -> None:
    import jenai.tools.ros2_pkg_core as mod

    cfg = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )

    async def fixed(config, prompt, *, binding="chat"):
        assert "SyntaxError" in prompt  # errors actually reach the model
        return {"node_code": "def main():\n    pass\n"}

    async def hopeless(config, prompt, *, binding="chat"):
        return {"node_code": "   "}

    monkeypatch.setattr(mod, "ask_json", fixed)
    plan = asyncio.run(mod.repair_node(cfg, _plan(), "SyntaxError: bad"))
    assert plan is not None and plan.node_code.startswith("def main")
    assert plan.package_name == "obstacle_stop"  # layout untouched

    monkeypatch.setattr(mod, "ask_json", hopeless)
    assert asyncio.run(mod.repair_node(cfg, _plan(), "err")) is None


def test_rewrite_node_touches_only_the_node_file(tmp_path: Path) -> None:
    from jenai.tools.ros2_pkg_core import rewrite_node

    ws_src = tmp_path / "src"
    write_package(_plan(), ws_src)
    before_xml = (ws_src / "obstacle_stop" / "package.xml").read_text()
    fixed = _plan(node_code="# fixed\n")
    rewrite_node(fixed, ws_src)
    assert (ws_src / "obstacle_stop" / "obstacle_stop" / "stopper.py").read_text() == "# fixed\n"
    assert (ws_src / "obstacle_stop" / "package.xml").read_text() == before_xml
