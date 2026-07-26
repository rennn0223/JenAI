"""Stable ROS node identities for JenAI sidecar processes."""

from __future__ import annotations

import os


def bridge_node_name(pid: int | None = None) -> str:
    """Return a valid, process-unique ROS node name for one bridge sidecar."""
    process_id = os.getpid() if pid is None else int(pid)
    if process_id <= 0:
        raise ValueError("bridge process id must be positive")
    return f"jenai_bridge_{process_id}"
