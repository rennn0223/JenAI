from __future__ import annotations

import subprocess

import pytest

from jenai.adapters import ros2_adapter

TOPIC_INFO_VERBOSE = """\
Type: geometry_msgs/msg/Twist
Publisher count: 1

Node name: teleop
Node namespace: /
Topic type: geometry_msgs/msg/Twist
Endpoint type: PUBLISHER
GID: 01.02.03
QoS profile:
  Reliability: RELIABLE

Subscription count: 2

Node name: controller
Node namespace: /

Node name: logger
Node namespace: /
"""


def _completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["ros2"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_is_available_reflects_shutil_which(monkeypatch) -> None:
    monkeypatch.setattr(ros2_adapter.shutil, "which", lambda name: None)
    assert ros2_adapter.is_available() is False

    monkeypatch.setattr(ros2_adapter.shutil, "which", lambda name: "/usr/bin/ros2")
    assert ros2_adapter.is_available() is True


def test_list_topics_not_available_raises(monkeypatch) -> None:
    monkeypatch.setattr(ros2_adapter.shutil, "which", lambda name: None)
    with pytest.raises(ros2_adapter.Ros2NotAvailableError):
        ros2_adapter.list_topics()


def test_list_topics_parses_lines(monkeypatch) -> None:
    monkeypatch.setattr(ros2_adapter.shutil, "which", lambda name: "/usr/bin/ros2")
    monkeypatch.setattr(
        ros2_adapter.subprocess,
        "run",
        lambda *a, **kw: _completed(stdout="/cmd_vel\n/scan\n/rosout\n"),
    )
    assert ros2_adapter.list_topics() == ["/cmd_vel", "/scan", "/rosout"]


def test_list_topics_nonzero_exit_raises_command_error(monkeypatch) -> None:
    monkeypatch.setattr(ros2_adapter.shutil, "which", lambda name: "/usr/bin/ros2")
    monkeypatch.setattr(
        ros2_adapter.subprocess,
        "run",
        lambda *a, **kw: _completed(returncode=1, stderr="boom"),
    )
    with pytest.raises(ros2_adapter.Ros2CommandError):
        ros2_adapter.list_topics()


def test_run_wraps_timeout(monkeypatch) -> None:
    monkeypatch.setattr(ros2_adapter.shutil, "which", lambda name: "/usr/bin/ros2")

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["ros2"], timeout=5)

    monkeypatch.setattr(ros2_adapter.subprocess, "run", _raise_timeout)
    with pytest.raises(ros2_adapter.Ros2CommandError):
        ros2_adapter.list_topics()


def test_topic_info_parses_verbose_output(monkeypatch) -> None:
    monkeypatch.setattr(ros2_adapter.shutil, "which", lambda name: "/usr/bin/ros2")
    monkeypatch.setattr(
        ros2_adapter.subprocess,
        "run",
        lambda *a, **kw: _completed(stdout=TOPIC_INFO_VERBOSE),
    )

    info = ros2_adapter.topic_info("/cmd_vel")

    assert info.message_type == "geometry_msgs/msg/Twist"
    assert info.publisher_count == 1
    assert info.subscriber_count == 2
    assert info.publishers == ["teleop"]
    assert info.subscribers == ["controller", "logger"]


def test_parameter_get_returns_unquoted_value(monkeypatch) -> None:
    monkeypatch.setattr(ros2_adapter.shutil, "which", lambda name: "/usr/bin/ros2")
    monkeypatch.setattr(
        ros2_adapter.subprocess,
        "run",
        lambda *a, **kw: _completed(stdout="'/chassis/odom'\n"),
    )

    assert ros2_adapter.parameter_get("/controller_server", "odom_topic") == "/chassis/odom"


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr"),
    [(1, "", "parameter not set"), (0, "\n", "")],
)
def test_parameter_get_rejects_failed_or_empty_response(
    monkeypatch, returncode: int, stdout: str, stderr: str
) -> None:
    monkeypatch.setattr(ros2_adapter.shutil, "which", lambda name: "/usr/bin/ros2")
    monkeypatch.setattr(
        ros2_adapter.subprocess,
        "run",
        lambda *a, **kw: _completed(returncode=returncode, stdout=stdout, stderr=stderr),
    )

    with pytest.raises(ros2_adapter.Ros2CommandError):
        ros2_adapter.parameter_get("/controller_server", "odom_topic")


def test_interface_show_returns_raw_text(monkeypatch) -> None:
    monkeypatch.setattr(ros2_adapter.shutil, "which", lambda name: "/usr/bin/ros2")
    monkeypatch.setattr(
        ros2_adapter.subprocess,
        "run",
        lambda *a, **kw: _completed(stdout="float64 x\nfloat64 y\n"),
    )
    assert ros2_adapter.interface_show("geometry_msgs/msg/Point") == "float64 x\nfloat64 y\n"


def test_topic_pub_success(monkeypatch) -> None:
    monkeypatch.setattr(ros2_adapter.shutil, "which", lambda name: "/usr/bin/ros2")
    monkeypatch.setattr(
        ros2_adapter.subprocess,
        "run",
        lambda *a, **kw: _completed(stdout="publisher: beginning loop\n"),
    )
    result = ros2_adapter.topic_pub("/cmd_vel", "geometry_msgs/msg/Twist", "{linear: {x: 0.5}}")
    assert result.ok is True


def test_topic_pub_failure_raises(monkeypatch) -> None:
    monkeypatch.setattr(ros2_adapter.shutil, "which", lambda name: "/usr/bin/ros2")
    monkeypatch.setattr(
        ros2_adapter.subprocess,
        "run",
        lambda *a, **kw: _completed(returncode=2, stderr="invalid message"),
    )
    with pytest.raises(ros2_adapter.Ros2CommandError):
        ros2_adapter.topic_pub("/cmd_vel", "geometry_msgs/msg/Twist", "bad")
