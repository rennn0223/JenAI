#!/usr/bin/env python3
"""Render a Nav2 parameter copy with JenAI's endpoint tolerances.

This helper deliberately uses only the Python standard library. It performs a
small, path-aware edit of the existing Nav2 YAML instead of replacing the
vendor file or relying on lifecycle-unsafe runtime parameter mutation.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import tempfile
from pathlib import Path

_MAPPING_LINE = re.compile(r"^(?P<indent> *)(?P<key>[^#:\s][^:]*):(?P<rest>.*)$")
_TARGETS = {
    (
        "controller_server",
        "ros__parameters",
        "general_goal_checker",
        "xy_goal_tolerance",
    ): "xy",
    (
        "controller_server",
        "ros__parameters",
        "general_goal_checker",
        "yaw_goal_tolerance",
    ): "yaw",
    (
        "controller_server",
        "ros__parameters",
        "general_goal_checker",
        "stateful",
    ): "stateful",
    (
        "controller_server",
        "ros__parameters",
        "FollowPath",
        "xy_goal_tolerance",
    ): "xy",
}


def _positive_finite(value: str, *, name: str) -> str:
    try:
        numeric = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(numeric) or numeric <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return value


def render(source: Path, target: Path, *, xy: str, yaw: str) -> None:
    """Write an atomic copy of *source* with all required tolerances replaced."""

    values = {
        "xy": _positive_finite(xy, name="xy tolerance"),
        "yaw": _positive_finite(yaw, name="yaw tolerance"),
        "stateful": "false",
    }
    lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    stack: list[tuple[int, str]] = []
    counts = {path: 0 for path in _TARGETS}
    rendered: list[str] = []

    for line in lines:
        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise ValueError("Nav2 parameter indentation must use spaces")
        match = _MAPPING_LINE.match(line.rstrip("\r\n"))
        if match is None:
            rendered.append(line)
            continue

        indent = len(match.group("indent"))
        key = match.group("key").strip().strip("\"'")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = tuple(item[1] for item in stack) + (key,)
        rest = match.group("rest")
        newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""

        if path in _TARGETS:
            comment = ""
            if "#" in rest:
                comment = "  #" + rest.split("#", 1)[1]
            rendered.append(
                f"{match.group('indent')}{match.group('key')}: "
                f"{values[_TARGETS[path]]}{comment}{newline}"
            )
            counts[path] += 1
            continue

        rendered.append(line)
        value_without_comment = rest.split("#", 1)[0].strip()
        if not value_without_comment:
            stack.append((indent, key))

    missing = [".".join(path) for path, count in counts.items() if count != 1]
    if missing:
        raise ValueError("expected exactly one Nav2 parameter at: " + ", ".join(missing))

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.writelines(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--xy", required=True)
    parser.add_argument("--yaw", required=True)
    args = parser.parse_args()
    try:
        render(args.source, args.target, xy=args.xy, yaw=args.yaw)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
