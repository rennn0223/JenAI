#!/usr/bin/env python3
"""Render a Nav2 parameter copy with JenAI's verified simulation profile.

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
    ("amcl", "ros__parameters", "alpha1"): "amcl_alpha",
    ("amcl", "ros__parameters", "alpha2"): "amcl_alpha",
    ("amcl", "ros__parameters", "alpha3"): "amcl_alpha",
    ("amcl", "ros__parameters", "alpha4"): "amcl_alpha",
    ("amcl", "ros__parameters", "alpha5"): "amcl_alpha",
    ("amcl", "ros__parameters", "update_min_a"): "amcl_update_min_a",
    ("amcl", "ros__parameters", "update_min_d"): "amcl_update_min_d",
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
    (
        "controller_server",
        "ros__parameters",
        "FollowPath",
        "min_vel_x",
    ): "min_vel_x",
    (
        "controller_server",
        "ros__parameters",
        "FollowPath",
        "vtheta_samples",
    ): "vtheta_samples",
}


def _finite(value: str, *, name: str) -> str:
    try:
        numeric = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite")
    return value


def _positive_finite(value: str, *, name: str) -> str:
    numeric = float(_finite(value, name=name))
    if numeric <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _odd_positive_integer(value: str, *, name: str) -> str:
    try:
        numeric = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if numeric < 3 or numeric % 2 == 0:
        raise ValueError(f"{name} must be an odd integer of at least 3")
    return value


def render(
    source: Path,
    target: Path,
    *,
    xy: str,
    yaw: str,
    min_vel_x: str,
    vtheta_samples: str,
    amcl_alpha: str,
    amcl_update_min_a: str,
    amcl_update_min_d: str,
) -> None:
    """Write an atomic copy with every required navigation-profile value replaced."""

    values = {
        "xy": _positive_finite(xy, name="xy tolerance"),
        "yaw": _positive_finite(yaw, name="yaw tolerance"),
        "min_vel_x": _finite(min_vel_x, name="minimum x velocity"),
        "vtheta_samples": _odd_positive_integer(vtheta_samples, name="angular velocity samples"),
        "stateful": "false",
        "amcl_alpha": _positive_finite(amcl_alpha, name="AMCL odometry noise"),
        "amcl_update_min_a": _positive_finite(
            amcl_update_min_a,
            name="AMCL angular update threshold",
        ),
        "amcl_update_min_d": _positive_finite(
            amcl_update_min_d,
            name="AMCL translation update threshold",
        ),
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
    parser.add_argument("--min-vel-x", required=True)
    parser.add_argument("--vtheta-samples", required=True)
    parser.add_argument("--amcl-alpha", required=True)
    parser.add_argument("--amcl-update-min-a", required=True)
    parser.add_argument("--amcl-update-min-d", required=True)
    args = parser.parse_args()
    try:
        render(
            args.source,
            args.target,
            xy=args.xy,
            yaw=args.yaw,
            min_vel_x=args.min_vel_x,
            vtheta_samples=args.vtheta_samples,
            amcl_alpha=args.amcl_alpha,
            amcl_update_min_a=args.amcl_update_min_a,
            amcl_update_min_d=args.amcl_update_min_d,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
