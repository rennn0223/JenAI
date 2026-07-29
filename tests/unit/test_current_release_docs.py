"""Current public guidance must follow the package's published version."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_public_installation_guides_name_the_current_release() -> None:
    version = _version()
    release_truth = f"目前 repository 是 public；v{version} Release 公開提供"
    for path in (
        "README.md",
        "docs/QUICKSTART.md",
        "docs/operations/ROLLBACK.md",
        ".github/workflows/README.md",
        "docs/product/PRODUCT_READINESS.md",
    ):
        assert release_truth in _read(path), path

    assert f"目前穩定版請使用 `v{version}`" in _read("README.md")
    assert f"目前穩定版為 `v{version}`" in _read("docs/QUICKSTART.md")


def test_living_release_status_no_longer_calls_current_version_a_candidate() -> None:
    version = _version()
    readiness = _read("docs/product/PRODUCT_READINESS.md")
    handoff_title = _read("docs/product/HANDOFF.md").splitlines()[0]
    test_manual = _read("docs/validation/TEST.md")

    assert f"JenAI v{version} 正式版" in readiness
    assert f"目前 v{version} 仍是未提交／未發布候選" not in readiness
    assert f"最近發布 v{version}" in handoff_title
    assert f"最近發布的 v{version} Release workflow 已通過" in test_manual
