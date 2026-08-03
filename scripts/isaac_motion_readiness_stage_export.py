#!/usr/bin/env python3
"""Run the exact reviewed Stage exporter inside the active Isaac process."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import stat
import subprocess
import sys
import zipfile
import zipimport
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Keep this safety dependency visible to the architecture import guard.
    import jenai.acceptance.motion_safety_stage_export  # noqa: F401

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


def _sealed_fd_bundle_path(value: object) -> str | None:
    prefix = "/proc/self/fd/"
    if not isinstance(value, str) or not value.startswith(prefix):
        return None
    descriptor_text = value.removeprefix(prefix).partition("/")[0]
    if not descriptor_text.isdecimal():
        return None
    return prefix + descriptor_text


def _module_origins(module: object) -> tuple[str, ...]:
    spec = getattr(module, "__spec__", None)
    values = (getattr(module, "__file__", None), getattr(spec, "origin", None))
    return tuple(dict.fromkeys(value for value in values if isinstance(value, str)))


def _loaded_jenai_bundle_state() -> tuple[tuple[str, ...], frozenset[str]]:
    names: list[str] = []
    roots: set[str] = set()
    for name, module in tuple(sys.modules.items()):
        if name != "jenai" and not name.startswith("jenai."):
            continue
        origins = _module_origins(module)
        module_roots = {_sealed_fd_bundle_path(origin) for origin in origins}
        if not origins or None in module_roots or len(module_roots) != 1:
            raise RuntimeError(f"non-sealed JenAI module is already loaded: {name}")
        names.append(name)
        roots.update(root for root in module_roots if root is not None)
    return tuple(names), frozenset(roots)


def _remove_path_occurrences(bundle_roots: frozenset[str]) -> None:
    sys.path[:] = [entry for entry in sys.path if entry not in bundle_roots]


def _clear_bundle_importers(bundle_roots: frozenset[str]) -> None:
    for cached_path in tuple(sys.path_importer_cache):
        if _sealed_fd_bundle_path(cached_path) not in bundle_roots:
            continue
        sys.path_importer_cache.pop(cached_path, None)
    directory_cache = getattr(zipimport, "_zip_directory_cache", None)
    if isinstance(directory_cache, dict):
        for cached_path in tuple(directory_cache):
            if _sealed_fd_bundle_path(cached_path) in bundle_roots:
                directory_cache.pop(cached_path, None)
    importlib.invalidate_caches()


def _deactivate_reviewed_bundle(bundle_path: str) -> None:
    """Remove every import reference to one soon-to-be-closed bundle descriptor."""

    for name, module in tuple(sys.modules.items()):
        if name != "jenai" and not name.startswith("jenai."):
            continue
        roots = {
            root
            for origin in _module_origins(module)
            if (root := _sealed_fd_bundle_path(origin)) is not None
        }
        if bundle_path in roots:
            sys.modules.pop(name, None)
    bundle_roots = frozenset({bundle_path})
    _remove_path_occurrences(bundle_roots)
    _clear_bundle_importers(bundle_roots)


def _require_current_bundle_origin(module: object, name: str, bundle_path: str) -> None:
    origins = _module_origins(module)
    if not origins or any(_sealed_fd_bundle_path(origin) != bundle_path for origin in origins):
        raise RuntimeError(f"{name} was not loaded from the current reviewed source bundle")


def _activate_reviewed_bundle(descriptor: int) -> str:
    """Mount one verified bundle as the only JenAI source in long-lived Kit."""

    bundle_path = f"/proc/self/fd/{descriptor}"
    stale_names, stale_roots = _loaded_jenai_bundle_state()
    for name in stale_names:
        sys.modules.pop(name, None)
    bundle_roots = frozenset({*stale_roots, bundle_path})
    _remove_path_occurrences(bundle_roots)
    _clear_bundle_importers(bundle_roots)
    sys.path.insert(0, bundle_path)
    return bundle_path


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository = Path(__file__).resolve().parents[1]
    descriptor = _open_reviewed_bundle(args.source_bundle, repository)
    bundle_path: str | None = None
    try:
        bundle_path = _activate_reviewed_bundle(descriptor)
        jenai_module = importlib.import_module("jenai")
        acceptance_module = importlib.import_module("jenai.acceptance")
        exporter_module = importlib.import_module("jenai.acceptance.motion_safety_stage_export")
        _require_current_bundle_origin(jenai_module, "jenai", bundle_path)
        _require_current_bundle_origin(acceptance_module, "jenai.acceptance", bundle_path)
        _require_current_bundle_origin(exporter_module, "Stage exporter", bundle_path)
        export_stage = exporter_module.export_stage

        digest = export_stage(args.config, args.output)
        print(digest)
        return 0
    finally:
        try:
            if bundle_path is not None:
                _deactivate_reviewed_bundle(bundle_path)
        finally:
            os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
