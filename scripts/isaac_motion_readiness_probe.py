#!/usr/bin/env python3
"""Repository-owned no-motion Isaac/ROS observation companion."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _sealed_import_roots(argv: list[str]) -> list[str]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--dependency-root", type=Path, action="append", default=[])
    namespace, remaining = parser.parse_known_args(argv)
    bundle = namespace.source_bundle
    if not bundle.is_absolute() or not bundle.is_file():
        raise ValueError("reviewed source bundle is invalid")
    allowed_prefixes = (Path("/opt/ros"), Path("/home/nvidia/IsaacSim-ros_workspaces"))
    dependencies = []
    for raw in namespace.dependency_root:
        path = raw.resolve()
        if not path.is_dir() or not any(path.is_relative_to(root) for root in allowed_prefixes):
            raise ValueError("ROS dependency root is outside the closed allowlist")
        dependencies.append(str(path))
    sys.path[:0] = [str(bundle), *dependencies]
    return remaining


def main(argv: list[str] | None = None) -> int:
    remaining = _sealed_import_roots(list(sys.argv[1:] if argv is None else argv))
    from jenai.acceptance.motion_safety_probe import main as probe_main

    return probe_main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
