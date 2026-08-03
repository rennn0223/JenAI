"""Read-only collision-geometry export executed inside a sealed Isaac import bundle."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from jenai.acceptance.motion_safety import RuntimeBinding, write_bytes_create_once
from jenai.acceptance.motion_safety_probe import (
    LiveUsdObservationBackend,
    RepositoryProbeConfig,
    _bounded_json,
    _repository_source_identity,
    _sha256_file,
)


def export_stage(config_path: Path, output_path: Path) -> str:
    if not config_path.is_absolute() or not output_path.is_absolute():
        raise ValueError("Isaac Stage export paths must be absolute")
    payload = _bounded_json(config_path)
    config = RepositoryProbeConfig.model_validate(payload)
    values = config.runtime.model_dump(mode="json")
    _repository_source_identity(str(values["git_sha"]))
    scene_path = Path(values["scene_path"])
    if _sha256_file(scene_path) != values["scene_sha256"]:
        raise RuntimeError("reviewed scene digest differs from the Stage source file")

    import omni.timeline

    timeline = omni.timeline.get_timeline_interface()
    if not timeline.is_playing():
        raise RuntimeError("Isaac timeline is not playing")
    values["capture_ros_ns"] = int(timeline.get_current_time() * 1_000_000_000)
    values["capture_host_monotonic_ns"] = time.monotonic_ns()
    runtime = RuntimeBinding.model_validate(values)
    evidence = LiveUsdObservationBackend().collision_geometry(config.usd, runtime)
    content = (evidence.model_dump_json(indent=2) + "\n").encode()
    write_bytes_create_once(output_path, content)
    return hashlib.sha256(content).hexdigest()
