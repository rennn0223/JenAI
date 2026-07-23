"""Shell command execution + risk assessment (approval-card material)."""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from jenai.schemas import EffectScope, RiskLevel, ShellOutput
from jenai.subprocesses import run_process_async

# Tokens that make the explanation more specific.  Every arbitrary host-shell
# command is P2 regardless: shell syntax, interpreters, command substitution,
# aliases and downloaded scripts make a reliable "read-only" classifier
# impossible. Dedicated slash commands remain the bounded fast path.
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


def _signal_group(process: subprocess.Popen[str], *, force: bool) -> None:
    # signal.SIGKILL only exists on POSIX, so it must not be named outside
    # this branch; non-POSIX falls back to signalling the direct child only.
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass
    elif force:  # pragma: no cover - ROS deployment and CI are Linux
        process.kill()
    else:  # pragma: no cover - ROS deployment and CI are Linux
        process.terminate()


def _terminate_tree(process: subprocess.Popen[str]) -> None:
    """Stop the process group, escalating to SIGKILL, without ever blocking forever."""
    _signal_group(process, force=False)
    try:
        process.communicate(timeout=1.0)
        return
    except subprocess.TimeoutExpired:
        pass
    _signal_group(process, force=True)
    try:
        process.communicate(timeout=1.0)
    except subprocess.TimeoutExpired:
        # A descendant that escaped the session (setsid/daemon) survived the
        # group SIGKILL and still holds the pipes; communicate() would wait for
        # EOF indefinitely. Stop reading and reap only the direct child, which
        # the SIGKILL cannot have missed. Callers discard output on this path.
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        process.wait()


def run_process(
    command: str | list[str],
    *,
    shell: bool = False,
    cwd: str | Path | None = None,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run a process with a timeout that also terminates its descendants."""
    # Put the shell and every normal descendant in one group on POSIX so a
    # timeout cannot leave a background command running after exit 124.
    process = subprocess.Popen(
        command,
        shell=shell,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        # Any exit path — timeout, Ctrl-C, cancellation — must not leave the
        # child running (subprocess.run kills on every exception too).
        _terminate_tree(process)
        raise
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
        risk_level=RiskLevel.P2,
        effect_scope=EffectScope.HOST_COMMAND,
        risk_summary=(
            "High risk: arbitrary host shell commands can read, write, delete, "
            "or launch other programs; review the complete command before approving."
        ),
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
        completed = await run_process_async(
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
