"""Cancellable subprocess primitives with process-group cleanup.

Asyncio task cancellation does not stop work delegated through to_thread.
Robot publishers and approved shell commands therefore use these native async
subprocess helpers so Esc and emergency stop cannot leave an action running.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path


def _signal_process_group(
    process: asyncio.subprocess.Process,
    *,
    force: bool,
) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass
    elif process.returncode is None:  # pragma: no cover - ROS targets are Linux
        if force:
            process.kill()
        else:
            process.terminate()


async def terminate_process_tree(
    process: asyncio.subprocess.Process,
    *,
    grace_s: float = 1.0,
) -> None:
    """Terminate, reap, then kill any normal descendants still in the group."""

    _signal_process_group(process, force=False)
    if process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), timeout=grace_s)
        except TimeoutError:
            _signal_process_group(process, force=True)
            try:
                await asyncio.wait_for(process.wait(), timeout=grace_s)
            except TimeoutError:  # pragma: no cover - SIGKILL should reap
                if process.returncode is None:
                    process.kill()
                    await process.wait()

    # The direct shell can exit before a child that traps SIGTERM. Its session
    # group still has the original pid as pgid, so a final kill closes that gap.
    if os.name == "posix":
        _signal_process_group(process, force=True)


def _captured_text(stream) -> str:
    stream.flush()
    stream.seek(0)
    return stream.read().decode("utf-8", errors="replace")


async def run_process_async(
    command: str | Sequence[str],
    *,
    shell: bool = False,
    cwd: str | Path | None = None,
    timeout: float,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess that is killed and reaped on timeout or cancellation."""

    kwargs = {
        "cwd": cwd,
        "env": dict(env) if env is not None else None,
        "start_new_session": os.name == "posix",
    }
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        if shell:
            if not isinstance(command, str):
                raise TypeError("shell commands must be strings")
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=stdout_file,
                stderr=stderr_file,
                **kwargs,
            )
        else:
            if isinstance(command, str):
                raise TypeError("exec commands must be a sequence of arguments")
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=stdout_file,
                stderr=stderr_file,
                **kwargs,
            )

        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except TimeoutError as exc:
            await asyncio.shield(terminate_process_tree(process))
            raise subprocess.TimeoutExpired(command, timeout) from exc
        except asyncio.CancelledError:
            await asyncio.shield(terminate_process_tree(process))
            raise

        return subprocess.CompletedProcess(
            command,
            process.returncode,
            _captured_text(stdout_file),
            _captured_text(stderr_file),
        )
