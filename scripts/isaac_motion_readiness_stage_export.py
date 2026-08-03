#!/usr/bin/env python3
"""Run the exact reviewed Stage exporter inside the active Isaac process."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

_MAX_BUNDLE_BYTES = 64 * 1024 * 1024
_TRUSTED_GIT = Path("/usr/bin/git")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _git(repository: Path, *args: str) -> bytes:
    return subprocess.run(
        [str(_TRUSTED_GIT), *args],
        cwd=repository,
        check=True,
        capture_output=True,
        timeout=10.0,
        env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/nonexistent")},
    ).stdout


def _open_reviewed_bundle(path: Path, repository: Path) -> int:
    if not path.is_absolute():
        raise ValueError("reviewed source bundle path must be absolute")
    head = _git(repository, "rev-parse", "HEAD").decode().strip()
    if _git(repository, "status", "--porcelain"):
        raise RuntimeError("Stage exporter repository is not clean")
    tracked = tuple(
        sorted(
            name
            for name in _git(repository, "ls-tree", "-r", "--name-only", head, "--", "src/jenai")
            .decode()
            .splitlines()
            if name.endswith(".py")
        )
    )
    expected_names = {name.removeprefix("src/") for name in tracked}
    expected_names.add("jenai/_motion_safety_source_manifest.json")
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= _MAX_BUNDLE_BYTES:
            raise ValueError("reviewed source bundle is invalid")
        with zipfile.ZipFile(f"/proc/self/fd/{descriptor}") as archive:
            if set(archive.namelist()) != expected_names:
                raise RuntimeError("source bundle inventory differs from reviewed Git")
            for repository_path in tracked:
                bundled = archive.read(repository_path.removeprefix("src/"))
                reviewed = _git(repository, "show", f"{head}:{repository_path}")
                if bundled != reviewed:
                    raise RuntimeError("source bundle content differs from reviewed Git")
            manifest = json.loads(archive.read("jenai/_motion_safety_source_manifest.json"))
            if manifest != {"source_git_sha": head}:
                raise RuntimeError("source bundle manifest differs from reviewed Git")
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository = Path(__file__).resolve().parents[1]
    descriptor = _open_reviewed_bundle(args.source_bundle, repository)
    try:
        sys.path.insert(0, f"/proc/self/fd/{descriptor}")
        from jenai.acceptance.motion_safety_stage_export import export_stage

        digest = export_stage(args.config, args.output)
        print(digest)
        return 0
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
