"""Living release documents must agree with the package version."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from jenai import __version__

ROOT = Path(__file__).resolve().parents[2]
VERSIONED_DOCS = (
    ROOT / "docs" / "TEST.md",
    ROOT / "docs" / "COMMANDS.md",
    ROOT / "docs" / "ROADMAP.md",
)


def test_release_version_is_consistent_across_package_lock_and_living_docs() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        version = tomllib.load(handle)["project"]["version"]

    with (ROOT / "uv.lock").open("rb") as handle:
        packages = tomllib.load(handle)["package"]
    locked = next(package for package in packages if package["name"] == "jenai")

    assert __version__ == version
    assert locked["version"] == version
    assert (ROOT / "docs" / "releases" / f"v{version}.md").is_file()

    pattern = re.compile(r"對應版本:(?:\*\*)?v(\d+\.\d+\.\d+)")
    for path in VERSIONED_DOCS:
        text = path.read_text(encoding="utf-8")
        match = pattern.search(text)
        assert match is not None, f"{path.name} has no machine-checkable 對應版本"
        assert match.group(1) == version, f"{path.name} targets v{match.group(1)}, not v{version}"

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    handoff = (ROOT / "docs" / "HANDOFF.md").read_text(encoding="utf-8")
    assert f"## 狀態（v{version}" in readme
    assert f",v{version})" in handoff.splitlines()[0]

    test_manual = (ROOT / "docs" / "TEST.md").read_text(encoding="utf-8")
    assert f"`JenAI {version}`" in test_manual


def test_ci_covers_supported_python_versions() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    match = re.search(r"python-version: \[([^\]]+)\]", workflow)

    assert match is not None, "CI must declare an explicit Python matrix"
    versions = json.loads(f"[{match.group(1)}]")
    assert versions == ["3.12", "3.13", "3.14"]
    assert "python-version: ${{ matrix.python-version }}" in workflow
