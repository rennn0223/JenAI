"""Command-contract tests for ROS 2 health-check discovery."""

from __future__ import annotations

import subprocess

import pytest

from jenai.adapters import ros2_adapter


def test_parameter_get_fresh_bypasses_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], float, int | None]] = []

    def fake_run(
        args: list[str],
        *,
        timeout: float,
        domain_id: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((args, timeout, domain_id))
        return subprocess.CompletedProcess(args, 0, stdout="/chassis/odom\n", stderr="")

    monkeypatch.setattr(ros2_adapter, "_run", fake_run)

    value = ros2_adapter.parameter_get(
        "/controller_server",
        "odom_topic",
        timeout=10.0,
        fresh=True,
    )

    assert value == "/chassis/odom"
    assert calls == [
        (
            [
                "param",
                "get",
                "/controller_server",
                "odom_topic",
                "--no-daemon",
                "--spin-time",
                "3.0",
                "--hide-type",
            ],
            10.0,
            None,
        )
    ]


def test_topic_info_fresh_bypasses_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], float, int | None]] = []

    def fake_run(
        args: list[str],
        *,
        timeout: float,
        domain_id: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((args, timeout, domain_id))
        return subprocess.CompletedProcess(args, 0, stdout="Publisher count: 0\n", stderr="")

    monkeypatch.setattr(ros2_adapter, "_run", fake_run)

    info = ros2_adapter.topic_info("/cmd_vel", timeout=10.0, fresh=True)

    assert info.name == "/cmd_vel"
    assert calls == [
        (
            [
                "topic",
                "info",
                "/cmd_vel",
                "--verbose",
                "--no-daemon",
                "--spin-time",
                "3.0",
            ],
            10.0,
            None,
        )
    ]
