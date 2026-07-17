"""ros2 CLI subprocess wrapper (topics/echo/pub/action) with timeouts."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


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


def _run(
    args: list[str], *, timeout: float, domain_id: int | None = None
) -> subprocess.CompletedProcess[str]:
    if not is_available():
        raise Ros2NotAvailableError(
            "ros2 command was not found on PATH. Install ROS2 Jazzy and source its setup script."
        )
    # A domain override talks to that domain's own ros2 daemon (the CLI keeps
    # one per ROS_DOMAIN_ID), so probing the twin never disturbs the robot's.
    env = {**os.environ, "ROS_DOMAIN_ID": str(domain_id)} if domain_id is not None else None
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
            f"ros2 action list exited with code {completed.returncode}: "
            f"{completed.stderr.strip()}",
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


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
        elif stripped.startswith("Subscription count:") or stripped.startswith(
            "Subscriber count:"
        ):
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


@dataclass
class PubResult:
    ok: bool
    message: str


def topic_pub(
    topic: str,
    message_type: str,
    payload_yaml: str,
    *,
    once: bool = True,
    timeout: float = 10.0,
) -> PubResult:
    args = ["topic", "pub"]
    if once:
        args.append("--once")
    args.extend([topic, message_type, payload_yaml])

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


def topic_pub_for(
    topic: str,
    message_type: str,
    payload_yaml: str,
    *,
    rate_hz: float = 10.0,
    duration_s: float = 1.0,
    stop_yaml: str | None = None,
) -> PubResult:
    """Publish a message at rate_hz for duration_s, then send a stop.

    Robot velocity controllers usually watchdog-stop when commands stop arriving,
    so a single publish only nudges the robot. A ROS-system-Python helper owns one
    publisher for both the timed motion and stop pulses. Two separate ROS CLI
    processes are deliberately avoided: startup of the stop process can leave the
    previous non-zero command active for another second on short motions.
    """
    if not is_available():
        raise Ros2NotAvailableError(
            "ros2 command was not found on PATH. Install ROS2 Jazzy and source its setup script."
        )
    if duration_s <= 0.0:
        if stop_yaml is not None:
            topic_pub(topic, message_type, stop_yaml)
        return PubResult(ok=True, message=f"zero-duration drive on {topic}; sent stop only")

    stop_yaml = stop_yaml or "{}"
    helper = Path(__file__).resolve().parents[1] / "bridge" / "_bounded_publisher.py"
    ros_python = os.environ.get("JENAI_ROS_PYTHON", "/usr/bin/python3")
    args = [
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
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=8.0 + max(0.0, duration_s),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        # The helper normally stops in finally. If the parent had to terminate
        # it, send one last best-effort stop through the CLI.
        try:
            topic_pub(topic, message_type, stop_yaml)
        except Ros2AdapterError:
            pass
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
