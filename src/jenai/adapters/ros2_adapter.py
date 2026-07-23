"""ros2 CLI subprocess wrapper (topics/echo/pub/action) with timeouts."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from jenai.subprocesses import run_process_async


class Ros2AdapterError(Exception):
    """Raised when a ros2 CLI operation cannot be completed."""


class Ros2NotAvailableError(Ros2AdapterError):
    """Raised when the `ros2` command is not on PATH."""


class Ros2CommandError(Ros2AdapterError):
    def __init__(
        self,
        message: str,
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int | None = None,
    ) -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def is_available() -> bool:
    return shutil.which("ros2") is not None


def _ros_env(domain_id: int | None) -> dict[str, str]:
    """Pin every CLI call to one canonical ROS domain.

    Explicit ``0`` and an unset ``ROS_DOMAIN_ID`` are semantically identical
    to DDS, but ROS CLI daemon discovery can otherwise treat them as separate
    cached graphs. Canonicalizing the environment keeps all probes consistent.
    """
    resolved = str(domain_id) if domain_id is not None else os.environ.get("ROS_DOMAIN_ID", "0")
    return {**os.environ, "ROS_DOMAIN_ID": resolved}


def _run(
    args: list[str], *, timeout: float, domain_id: int | None = None
) -> subprocess.CompletedProcess[str]:
    if not is_available():
        raise Ros2NotAvailableError(
            "ros2 command was not found on PATH. Install ROS2 Jazzy and source its setup script."
        )
    # A domain override talks to that domain's own ros2 daemon (the CLI keeps
    # one per ROS_DOMAIN_ID), so probing the twin never disturbs the robot's.
    env = _ros_env(domain_id)
    try:
        return subprocess.run(
            ["ros2", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Ros2CommandError(f"ros2 {' '.join(args)} could not run: {exc}") from exc


async def _run_async(
    args: list[str], *, timeout: float, domain_id: int | None = None
) -> subprocess.CompletedProcess[str]:
    """Cancellable ros2 CLI execution with process-group termination and reap."""

    if not is_available():
        raise Ros2NotAvailableError(
            "ros2 command was not found on PATH. Install ROS2 Jazzy and source its setup script."
        )
    env = _ros_env(domain_id)
    try:
        return await run_process_async(["ros2", *args], timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Ros2CommandError(f"ros2 {' '.join(args)} could not run: {exc}") from exc


def list_topics(*, timeout: float = 5.0, domain_id: int | None = None) -> list[str]:
    completed = _run(["topic", "list"], timeout=timeout, domain_id=domain_id)
    if completed.returncode != 0:
        raise Ros2CommandError(
            f"ros2 topic list exited with code {completed.returncode}: {completed.stderr.strip()}",
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def list_actions(*, timeout: float = 5.0, domain_id: int | None = None) -> list[str]:
    """Names of action servers on the graph (`ros2 action list`).

    Action topics are hidden from `ros2 topic list`, so this is the only
    honest way to detect e.g. a running Nav2 (/navigate_to_pose).
    """
    completed = _run(["action", "list"], timeout=timeout, domain_id=domain_id)
    if completed.returncode != 0:
        raise Ros2CommandError(
            f"ros2 action list exited with code {completed.returncode}: {completed.stderr.strip()}",
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def parameter_get(node: str, parameter: str, *, timeout: float = 5.0) -> str:
    """Read one ROS parameter value without its type annotation.

    The raw CLI wrapper is intentionally narrow: diagnostics only need a
    trustworthy string value and non-zero/empty responses are errors rather
    than values that callers could accidentally treat as configuration.
    """
    completed = _run(
        ["param", "get", node, parameter, "--hide-type"],
        timeout=timeout,
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value:
        detail = completed.stderr.strip() or "empty parameter value"
        raise Ros2CommandError(
            f"ros2 param get {node} {parameter} failed: {detail}",
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return value


@dataclass
class TopicInfo:
    name: str
    message_type: str = ""
    publisher_count: int = 0
    subscriber_count: int = 0
    publishers: list[str] = field(default_factory=list)
    subscribers: list[str] = field(default_factory=list)


def topic_info(topic: str, *, timeout: float = 5.0) -> TopicInfo:
    completed = _run(["topic", "info", topic, "--verbose"], timeout=timeout)
    if completed.returncode != 0:
        raise Ros2CommandError(
            f"ros2 topic info {topic} exited with code {completed.returncode}: "
            f"{completed.stderr.strip()}",
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
    return _parse_topic_info(topic, completed.stdout)


def _safe_int(value: str) -> int:
    """Parse a count field tolerantly.

    `ros2 topic info --verbose` output can vary across distros / locales; a
    non-integer count should degrade to 0 rather than crash the whole schema
    lookup with an uncaught ValueError.
    """
    try:
        return int(value.strip())
    except (ValueError, TypeError):
        return 0


def _parse_topic_info(topic: str, raw: str) -> TopicInfo:
    """Best-effort line parser for `ros2 topic info --verbose` output.

    Cannot be verified against a live ROS2 install in this environment; this
    matches the documented output shape (Type/Publisher count/Node name blocks)
    closely enough for F07's acceptance bar, not a full protocol parser.
    """
    message_type = ""
    publisher_count = 0
    subscriber_count = 0
    publishers: list[str] = []
    subscribers: list[str] = []
    section: str | None = None

    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("Type:"):
            message_type = stripped.split("Type:", 1)[1].strip()
        elif stripped.startswith("Publisher count:"):
            publisher_count = _safe_int(stripped.split(":", 1)[1])
            section = "publishers"
        elif stripped.startswith(("Subscription count:", "Subscriber count:")):
            subscriber_count = _safe_int(stripped.split(":", 1)[1])
            section = "subscribers"
        elif stripped.startswith("Node name:"):
            node_name = stripped.split(":", 1)[1].strip()
            if section == "publishers" and node_name:
                publishers.append(node_name)
            elif section == "subscribers" and node_name:
                subscribers.append(node_name)

    return TopicInfo(
        name=topic,
        message_type=message_type,
        publisher_count=publisher_count,
        subscriber_count=subscriber_count,
        publishers=publishers,
        subscribers=subscribers,
    )


def interface_show(message_type: str, *, timeout: float = 5.0) -> str:
    completed = _run(["interface", "show", message_type], timeout=timeout)
    if completed.returncode != 0:
        raise Ros2CommandError(
            f"ros2 interface show {message_type} exited with code {completed.returncode}: "
            f"{completed.stderr.strip()}",
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
    return completed.stdout


def topic_echo(
    topic: str, *, count: int = 1, timeout: float = 5.0, latched: bool = False
) -> list[str]:
    """Capture up to `count` snapshot messages from a topic.

    Uses `ros2 topic echo <topic> --once`, invoked once per requested message.
    Returns the raw per-message text blocks; an empty list means no message
    arrived (empty topic or timeout), which the caller reports gracefully.
    `latched` subscribes RELIABLE + TRANSIENT_LOCAL — required for topics like
    /amcl_pose that only re-publish on updates (a volatile subscriber would
    wait forever next to a stationary robot).
    """
    args = ["topic", "echo", topic, "--once"]
    if latched:
        args += ["--qos-durability", "transient_local", "--qos-reliability", "reliable"]
    messages: list[str] = []
    for _ in range(max(1, count)):
        completed = _run(args, timeout=timeout)
        if completed.returncode != 0:
            raise Ros2CommandError(
                f"ros2 topic echo {topic} exited with code {completed.returncode}: "
                f"{completed.stderr.strip()}",
                stdout=completed.stdout,
                stderr=completed.stderr,
                returncode=completed.returncode,
            )
        block = completed.stdout.strip().strip("-").strip()
        if not block:
            break
        messages.append(block)
    return messages


def action_available(name: str, *, timeout: float = 6.0) -> bool:
    """True if `name` is an advertised ROS2 action (e.g. Nav2's /navigate_to_pose)."""
    completed = _run(["action", "list"], timeout=timeout)
    return name in {line.strip() for line in completed.stdout.splitlines() if line.strip()}


async def action_available_async(name: str, *, timeout: float = 6.0) -> bool:
    """Cancellable async form used by the navigation fallback."""

    completed = await _run_async(["action", "list"], timeout=timeout)
    if completed.returncode != 0:
        raise Ros2CommandError(
            f"ros2 action list exited with code {completed.returncode}: {completed.stderr.strip()}",
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
    return name in {line.strip() for line in completed.stdout.splitlines() if line.strip()}


def action_send_goal(
    name: str, action_type: str, goal_yaml: str, *, timeout: float = 120.0
) -> tuple[bool, str]:
    """Send a goal to an action server and wait for the result (blocking).

    Returns (succeeded, detail). Best-effort success detection from the CLI
    output — the caller should treat a non-succeeded result honestly.
    """
    completed = _run(["action", "send_goal", name, action_type, goal_yaml], timeout=timeout)
    output = (completed.stdout or "").strip()
    succeeded = completed.returncode == 0 and "SUCCEEDED" in output.upper()
    return succeeded, output or (completed.stderr or "").strip() or "no output"


async def action_send_goal_async(
    name: str, action_type: str, goal_yaml: str, *, timeout: float = 120.0
) -> tuple[bool, str]:
    """Send a goal via a CLI process that cannot outlive task cancellation."""

    completed = await _run_async(
        ["action", "send_goal", name, action_type, goal_yaml], timeout=timeout
    )
    output = (completed.stdout or "").strip()
    succeeded = completed.returncode == 0 and "SUCCEEDED" in output.upper()
    return succeeded, output or (completed.stderr or "").strip() or "no output"


@dataclass
class PubResult:
    ok: bool
    message: str


def _topic_pub_args(
    topic: str,
    message_type: str,
    payload_yaml: str,
    *,
    once: bool,
) -> list[str]:
    args = ["topic", "pub"]
    if once:
        args.append("--once")
    args.extend([topic, message_type, payload_yaml])
    return args


def topic_pub(
    topic: str,
    message_type: str,
    payload_yaml: str,
    *,
    once: bool = True,
    timeout: float = 10.0,
) -> PubResult:
    """Synchronous compatibility wrapper for non-cancellable callers."""

    args = _topic_pub_args(topic, message_type, payload_yaml, once=once)
    completed = _run(args, timeout=timeout)
    if completed.returncode != 0:
        raise Ros2CommandError(
            f"ros2 topic pub {topic} exited with code {completed.returncode}: "
            f"{completed.stderr.strip()}",
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
    return PubResult(ok=True, message=completed.stdout.strip() or "published")


async def topic_pub_async(
    topic: str,
    message_type: str,
    payload_yaml: str,
    *,
    once: bool = True,
    timeout: float = 10.0,
    cancel_stop_yaml: str | None = None,
) -> PubResult:
    """Publish once without allowing cancellation to orphan the ROS process.

    ``run_process_async`` kills and reaps the whole normal process group before
    cancellation propagates. For a velocity message the caller also supplies an
    all-zero payload; that conservative pulse is sent only after the non-zero
    publisher is gone, so an emergency halt cannot be followed by a late publish.
    """

    if not is_available():
        raise Ros2NotAvailableError(
            "ros2 command was not found on PATH. Install ROS2 Jazzy and source its setup script."
        )
    args = _topic_pub_args(topic, message_type, payload_yaml, once=once)
    try:
        completed = await _run_async(args, timeout=timeout)
    except asyncio.CancelledError:
        if cancel_stop_yaml is not None:
            await _best_effort_stop(topic, message_type, cancel_stop_yaml)
        raise

    if completed.returncode != 0:
        raise Ros2CommandError(
            f"ros2 topic pub {topic} exited with code {completed.returncode}: "
            f"{completed.stderr.strip()}",
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
    return PubResult(ok=True, message=completed.stdout.strip() or "published")


def _bounded_publisher_args(
    topic: str,
    message_type: str,
    payload_yaml: str,
    stop_yaml: str,
    rate_hz: float,
    duration_s: float,
) -> list[str]:
    helper = Path(__file__).resolve().parents[1] / "bridge" / "ros_bounded_publisher.py"
    ros_python = os.environ.get("JENAI_ROS_PYTHON", "/usr/bin/python3")
    return [
        ros_python,
        str(helper),
        topic,
        message_type,
        payload_yaml,
        stop_yaml,
        str(rate_hz),
        str(duration_s),
        "5",
    ]


async def _best_effort_stop(topic: str, message_type: str, stop_yaml: str) -> None:
    """Try one cancellable zero publish after the motion process has been reaped."""

    try:
        await topic_pub_async(topic, message_type, stop_yaml)
    except Ros2AdapterError:
        pass


async def topic_pub_for(
    topic: str,
    message_type: str,
    payload_yaml: str,
    *,
    rate_hz: float = 10.0,
    duration_s: float = 1.0,
    stop_yaml: str | None = None,
) -> PubResult:
    """Publish for a bounded duration and stop on success, failure, or cancel."""

    if not is_available():
        raise Ros2NotAvailableError(
            "ros2 command was not found on PATH. Install ROS2 Jazzy and source its setup script."
        )
    if duration_s <= 0.0:
        if stop_yaml is not None:
            await topic_pub_async(topic, message_type, stop_yaml)
        return PubResult(ok=True, message=f"zero-duration drive on {topic}; sent stop only")

    stop_yaml = stop_yaml or "{}"
    args = _bounded_publisher_args(
        topic,
        message_type,
        payload_yaml,
        stop_yaml,
        rate_hz,
        duration_s,
    )
    try:
        completed = await run_process_async(
            args,
            timeout=8.0 + max(0.0, duration_s),
        )
    except asyncio.CancelledError:
        # run_process_async has killed and reaped the non-zero publisher before
        # this final zero command is attempted.
        await _best_effort_stop(topic, message_type, stop_yaml)
        raise
    except (OSError, subprocess.TimeoutExpired) as exc:
        await _best_effort_stop(topic, message_type, stop_yaml)
        return PubResult(
            ok=False,
            message=f"drive failed: bounded publisher could not complete on {topic}: {exc}",
        )

    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else ""
        return PubResult(
            ok=False,
            message=(
                f"drive failed: bounded publisher on {topic} exited with code "
                f"{completed.returncode}" + (f": {detail}" if detail else "")
            ),
        )

    return PubResult(
        ok=True,
        message=completed.stdout.strip() or f"drove {topic} for {duration_s:g}s, then stopped",
    )
