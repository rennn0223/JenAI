"""Shell command execution + risk assessment (approval-card material)."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from jenai.schemas import EffectScope, RiskLevel, ShellOutput

# Tokens that indicate a write / delete / install / privilege action. Their
# presence bumps the command to P2 so the approval card warns more loudly.
_HIGH_RISK_TOKENS = (
    "rm ",
    "rmdir",
    "mkfs",
    "dd ",
    "shutdown",
    "reboot",
    "sudo",
    "apt",
    "yum",
    "dnf",
    "pip install",
    "npm install",
    "chmod",
    "chown",
    "kill",
    "git push",
    "> ",
    ">>",
    "mv ",
)

_OUTPUT_LIMIT = 2000


def _run_process(
    command: str | list[str],
    *,
    shell: bool = False,
    cwd: str | Path | None = None,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run a process with a timeout that also terminates its descendants."""
    popen_kwargs = {
        "shell": shell,
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "posix":
        # Put the shell and every normal descendant in one group so a timeout
        # cannot leave a background command running after JenAI reports exit 124.
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **popen_kwargs)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:  # pragma: no cover - ROS deployment and CI are Linux
            process.terminate()
        try:
            process.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:  # pragma: no cover - ROS deployment and CI are Linux
                process.kill()
            process.communicate()
        raise exc
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


@dataclass(frozen=True)
class CommandRisk:
    risk_level: RiskLevel
    effect_scope: EffectScope
    risk_summary: str


def assess_command(command: str) -> CommandRisk:
    lowered = command.lower()
    hits = [token.strip() for token in _HIGH_RISK_TOKENS if token in lowered]
    if hits:
        return CommandRisk(
            risk_level=RiskLevel.P2,
            effect_scope=EffectScope.HOST_COMMAND,
            risk_summary=(
                "High risk: command may write, delete, install, or elevate "
                f"privileges (matched: {', '.join(sorted(set(hits)))})."
            ),
        )
    return CommandRisk(
        risk_level=RiskLevel.P1,
        effect_scope=EffectScope.HOST_COMMAND,
        risk_summary="Runs a host shell command; review before approving.",
    )


def _resolve_cwd(cwd: str | None) -> str:
    return str(Path(cwd).expanduser()) if cwd else str(Path.cwd())


def _summarize(text: str) -> str:
    text = text.strip()
    if len(text) <= _OUTPUT_LIMIT:
        return text
    return text[:_OUTPUT_LIMIT] + f"\n… (truncated, {len(text)} chars total)"


def preview_command(command: str, *, cwd: str | None = None) -> ShellOutput:
    """Build the pre-approval preview (no execution)."""
    risk = assess_command(command)
    return ShellOutput(
        command=command,
        working_directory=_resolve_cwd(cwd),
        risk_summary=risk.risk_summary,
        approval_status="pending",
    )


async def run_shell(command: str, *, cwd: str | None = None, timeout: float = 30.0) -> ShellOutput:
    """Execute an approved shell command and summarize its output."""
    workdir = _resolve_cwd(cwd)
    risk = assess_command(command)
    try:
        completed = await asyncio.to_thread(
            _run_process,
            command,
            shell=True,
            cwd=workdir,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ShellOutput(
            command=command,
            working_directory=workdir,
            risk_summary=risk.risk_summary,
            approval_status="approved",
            exit_code=124,
            stderr_summary=f"Command timed out after {timeout:.0f}s.",
        )

    return ShellOutput(
        command=command,
        working_directory=workdir,
        risk_summary=risk.risk_summary,
        approval_status="approved",
        exit_code=completed.returncode,
        stdout_summary=_summarize(completed.stdout),
        stderr_summary=_summarize(completed.stderr),
    )
