from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field


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


def _run(args: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    if not is_available():
        raise Ros2NotAvailableError(
            "ros2 command was not found on PATH. Install ROS2 Jazzy and source its setup script."
        )
    try:
        return subprocess.run(
            ["ros2", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Ros2CommandError(f"ros2 {' '.join(args)} could not run: {exc}") from exc


def list_topics(*, timeout: float = 5.0) -> list[str]:
    completed = _run(["topic", "list"], timeout=timeout)
    if completed.returncode != 0:
        raise Ros2CommandError(
            f"ros2 topic list exited with code {completed.returncode}: {completed.stderr.strip()}",
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
            publisher_count = int(stripped.split(":", 1)[1].strip())
            section = "publishers"
        elif stripped.startswith("Subscription count:") or stripped.startswith(
            "Subscriber count:"
        ):
            subscriber_count = int(stripped.split(":", 1)[1].strip())
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
