#!/usr/bin/env python3
"""PROTOTYPE: answer whether JenAI no-motion acceptance works in one fresh Headless process."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--usd", type=Path, required=True)
    parser.add_argument("--usd-sha256", required=True)
    parser.add_argument("--config-template", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout-s", type=float, default=120.0)
    return parser


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write_once(path: Path, value: object) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["/usr/bin/git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=10.0,
        env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/nonexistent")},
    ).stdout.strip()


def _run_while_updating(
    command: list[str],
    *,
    simulation_app: Any,
    environment: dict[str, str],
    timeout_s: float,
) -> tuple[int, str, str]:
    process = subprocess.Popen(
        command,
        cwd=environment["JENAI_HEADLESS_REPOSITORY"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout_s
    try:
        while process.poll() is None:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"bounded child timed out: {command[0]}")
            simulation_app.update()
        stdout, stderr = process.communicate(timeout=5.0)
        return process.returncode, stdout, stderr
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5.0)


def _rebound_config(
    *,
    template_path: Path,
    repository: Path,
    usd_path: Path,
    usd_sha256: str,
    output_dir: Path,
    nav2_params_path: Path,
    simulation_epoch: str,
    runtime_boot_id: str,
) -> dict[str, object]:
    probe_module = importlib.import_module("jenai.acceptance.motion_safety_probe")
    safety_module = importlib.import_module("jenai.acceptance.motion_safety")
    template = probe_module.RepositoryProbeConfig.model_validate_json(template_path.read_text())
    head = _git(repository, "rev-parse", "HEAD")
    nav2_params_sha256 = _sha256(nav2_params_path)
    runtime_fingerprint = _canonical_sha256(
        {
            "git_sha": head,
            "scene_sha256": usd_sha256,
            "nav2_params_sha256": nav2_params_sha256,
            "simulation_epoch": simulation_epoch,
            "runtime_boot_id": runtime_boot_id,
            "ros_domain_id": os.environ["ROS_DOMAIN_ID"],
        }
    )
    runtime = template.runtime.model_copy(
        update={
            "git_sha": head,
            "scene_path": str(usd_path),
            "scene_sha256": usd_sha256,
            "nav2_params_sha256": nav2_params_sha256,
            "planner_config_sha256": nav2_params_sha256,
            "runtime_fingerprint": runtime_fingerprint,
            "simulation_epoch": simulation_epoch,
            "runtime_boot_id": runtime_boot_id,
            "capture_ros_ns": 0,
            "capture_host_monotonic_ns": 0,
        }
    )
    sources = []
    for source in template.clearance_sources:
        values = source.model_dump(mode="json", exclude={"content_sha256"})
        values.update(
            {
                "simulation_epoch": simulation_epoch,
                "runtime_boot_id": runtime_boot_id,
                "runtime_fingerprint": runtime_fingerprint,
            }
        )
        sources.append(safety_module.ClearanceSourceEvidence.create(**values))
    usd = template.usd.model_copy(
        update={
            "scene_path": str(usd_path),
            "stage_export_path": str(output_dir / "stage-evidence.json"),
            "stage_export_sha256": None,
        }
    )
    request = template.motion_request.model_copy(
        update={"authorization_nonce": f"headless-no-motion-{uuid.uuid4().hex}"}
    )
    config = template.model_copy(
        update={
            "runtime": runtime,
            "motion_request": request,
            "usd": usd,
            "clearance_sources": tuple(sources),
        }
    )
    return config.model_dump(mode="json")


def main() -> int:  # noqa: C901 - throwaway spike deliberately surfaces one lifecycle
    args = _parser().parse_args()
    repository = Path(__file__).resolve().parents[1]
    if not args.usd.is_absolute() or not args.config_template.is_absolute():
        raise ValueError("USD and config template paths must be absolute")
    if not args.output_dir.is_absolute() or args.output_dir.exists():
        raise ValueError("output directory must be a new absolute path")
    if not math.isfinite(args.timeout_s) or not 30.0 <= args.timeout_s <= 300.0:
        raise ValueError("timeout must be finite and within [30, 300] seconds")
    if _git(repository, "status", "--porcelain"):
        raise RuntimeError("Headless prototype requires a clean reviewed checkout")
    if _sha256(args.usd) != args.usd_sha256:
        raise RuntimeError("exact USD digest mismatch")
    args.output_dir.mkdir(mode=0o700)

    result: dict[str, object] = {
        "prototype": "isaaclab_headless_no_motion_v1",
        "git_sha": _git(repository, "rev-parse", "HEAD"),
        "usd_path": str(args.usd),
        "usd_sha256": args.usd_sha256,
        "ros_domain_id": os.environ.get("ROS_DOMAIN_ID"),
        "motion_attempted": False,
        "automatic_retry": False,
        "status": "started",
    }
    simulation_app = None
    environment = dict(os.environ)
    environment["JENAI_HEADLESS_REPOSITORY"] = str(repository)
    session = f"jenai-headless-{os.getpid()}"
    state_dir = args.output_dir / "nav2-state"
    environment.update(
        {
            "JENAI_NAV2_TMUX_SESSION": session,
            "JENAI_NAV2_STATE_DIR": str(state_dir),
        }
    )
    nav2_cleanup_required = False
    try:
        from isaaclab.app import AppLauncher

        launcher = AppLauncher(headless=True)
        simulation_app = launcher.app

        import omni.timeline
        import omni.usd
        from isaacsim.core.utils.extensions import enable_extension
        from isaacsim.core.utils.stage import open_stage

        enable_extension("isaacsim.ros2.bridge")
        for _ in range(10):
            simulation_app.update()
        if not open_stage(str(args.usd)):
            raise RuntimeError("Isaac failed to open exact USD")
        loading_deadline = time.monotonic() + min(args.timeout_s, 60.0)
        while omni.usd.get_context().get_stage_loading_status()[2] > 0:
            if time.monotonic() >= loading_deadline:
                raise TimeoutError("USD stage loading timed out")
            simulation_app.update()
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            raise RuntimeError("Isaac has no active Stage after load")
        root_identifier = str(stage.GetRootLayer().realPath or stage.GetRootLayer().identifier)
        if Path(root_identifier).resolve() != args.usd.resolve():
            raise RuntimeError("active Stage root differs from exact USD")
        timeline = omni.timeline.get_timeline_interface()
        timeline.play()
        for _ in range(30):
            simulation_app.update()
        first_time = float(timeline.get_current_time())
        for _ in range(30):
            simulation_app.update()
        second_time = float(timeline.get_current_time())
        if second_time <= first_time:
            raise RuntimeError("Headless simulation clock did not advance")
        result["stage"] = {
            "root_identifier": root_identifier,
            "start_time_s": first_time,
            "observed_time_s": second_time,
        }

        nav2_cleanup_required = True
        start_code, start_stdout, start_stderr = _run_while_updating(
            [str(repository / "scripts" / "isaac_nav2.sh"), "start"],
            simulation_app=simulation_app,
            environment=environment,
            timeout_s=min(args.timeout_s, 90.0),
        )
        result["nav2_start"] = {
            "returncode": start_code,
            "stdout": start_stdout[-4000:],
            "stderr": start_stderr[-4000:],
        }
        if start_code != 0:
            raise RuntimeError("isolated Nav2 failed to start")
        override_record = state_dir / f"{session}-override-path"
        nav2_params_path = Path(override_record.read_text().strip())
        if not nav2_params_path.is_absolute() or not nav2_params_path.is_file():
            raise RuntimeError("isolated Nav2 params identity is unavailable")

        simulation_epoch = f"headless-{uuid.uuid4().hex}"
        runtime_boot_id = f"{session}-{int(time.monotonic_ns())}"
        config_payload = _rebound_config(
            template_path=args.config_template,
            repository=repository,
            usd_path=args.usd,
            usd_sha256=args.usd_sha256,
            output_dir=args.output_dir,
            nav2_params_path=nav2_params_path,
            simulation_epoch=simulation_epoch,
            runtime_boot_id=runtime_boot_id,
        )
        pre_export_path = args.output_dir / "motion-readiness-pre-export.json"
        _write_once(pre_export_path, config_payload)

        exporter = importlib.import_module("jenai.acceptance.motion_safety_stage_export")
        stage_digest = exporter.export_stage(
            pre_export_path,
            args.output_dir / "stage-evidence.json",
        )
        config_payload["usd"]["stage_export_sha256"] = stage_digest  # type: ignore[index]
        config_path = args.output_dir / "motion-readiness.json"
        _write_once(config_path, config_payload)

        capture_path = args.output_dir / "captured-evidence.json"
        capture_code, capture_stdout, capture_stderr = _run_while_updating(
            [
                str(repository / ".venv-ros" / "bin" / "python"),
                str(repository / "scripts" / "isaac_motion_readiness.py"),
                "capture",
                "--config",
                str(config_path),
                "--output",
                str(capture_path),
                "--timeout-s",
                "8",
            ],
            simulation_app=simulation_app,
            environment=environment,
            timeout_s=args.timeout_s,
        )
        artifact_path = args.output_dir / "motion-readiness-artifact.json"
        assemble_code, assemble_stdout, assemble_stderr = _run_while_updating(
            [
                str(repository / ".venv-ros" / "bin" / "python"),
                str(repository / "scripts" / "isaac_motion_readiness.py"),
                "assemble",
                "--evidence",
                str(capture_path),
                "--output",
                str(artifact_path),
            ],
            simulation_app=simulation_app,
            environment=environment,
            timeout_s=30.0,
        )
        validate_code, validate_stdout, validate_stderr = _run_while_updating(
            [
                str(repository / ".venv-ros" / "bin" / "python"),
                str(repository / "scripts" / "isaac_motion_readiness.py"),
                "validate",
                "--artifact",
                str(artifact_path),
            ],
            simulation_app=simulation_app,
            environment=environment,
            timeout_s=30.0,
        )
        result["motion_readiness"] = {
            "capture_returncode": capture_code,
            "capture_stdout": capture_stdout[-4000:],
            "capture_stderr": capture_stderr[-4000:],
            "assemble_returncode": assemble_code,
            "assemble_stdout": assemble_stdout[-4000:],
            "assemble_stderr": assemble_stderr[-4000:],
            "validate_returncode": validate_code,
            "validate_stdout": validate_stdout[-4000:],
            "validate_stderr": validate_stderr[-4000:],
            "artifact": str(artifact_path),
        }
        if validate_code not in {0, 3}:
            raise RuntimeError("offline Motion Readiness artifact is invalid")
        result["status"] = "answered"
        result["answer"] = "valid_pass" if validate_code == 0 else "valid_block"
        return 0
    except BaseException as exc:
        result["status"] = "failed"
        result["failure_type"] = type(exc).__name__
        result["failure"] = str(exc)
        return 1
    finally:
        if simulation_app is not None and nav2_cleanup_required:
            stop_code, stop_stdout, stop_stderr = _run_while_updating(
                [str(repository / "scripts" / "isaac_nav2.sh"), "stop"],
                simulation_app=simulation_app,
                environment=environment,
                timeout_s=30.0,
            )
            result["nav2_stop"] = {
                "returncode": stop_code,
                "stdout": stop_stdout[-4000:],
                "stderr": stop_stderr[-4000:],
            }
        if simulation_app is not None:
            result["simulation_app_close_requested"] = True
        _write_once(args.output_dir / "headless-spike-report.json", result)
        if simulation_app is not None:
            simulation_app.close()


if __name__ == "__main__":
    raise SystemExit(main())
