from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from jenai.config import (
    AppConfig,
    ConfigError,
    default_config_path,
    default_env_file_path,
    load_config,
)
from jenai.schemas import DoctorCheckItem, DoctorResult, DoctorStatus


def run_doctor(config_path: Path | None = None, *, include_nav: bool = True) -> DoctorResult:
    """Full health check. `include_nav=False` skips the navigation-stack
    probes (a few seconds of ros2 CLI calls) — used on the TUI startup path,
    where doctor must stay fast; `jenai doctor` always runs everything."""
    # Resolve the default location once so every check (config load AND the
    # locations-file check) agrees on the real config dir, instead of some
    # falling back to the current working directory.
    config_path = config_path or default_config_path()
    items: list[DoctorCheckItem] = []
    items.extend(_check_python())
    items.extend(_check_uv())
    items.extend(_check_virtual_env())

    config: AppConfig | None = None
    try:
        config = load_config(config_path)
        items.append(
            DoctorCheckItem(
                section="config",
                check_name="config_file",
                status=DoctorStatus.PASS if config.is_complete() else DoctorStatus.WARN,
                message="Config file loaded."
                if config.is_complete()
                else "Config file exists but is incomplete.",
                fix_suggestion=None
                if config.is_complete()
                else "Run JenAI and complete the setup wizard.",
            )
        )
    except ConfigError as exc:
        items.append(
            DoctorCheckItem(
                section="config",
                check_name="config_file",
                status=DoctorStatus.FAIL,
                message=str(exc),
                fix_suggestion="Run JenAI to create a fresh config file.",
            )
        )

    items.extend(_check_env_file())
    items.extend(_check_ros2())
    if include_nav:
        items.extend(_check_nav_stack(config))
    items.extend(_check_provider(config))
    items.extend(_check_locations(config, config_path))
    items.extend(_check_webui_assets())
    return DoctorResult.from_items(items)


def _check_python() -> list[DoctorCheckItem]:
    version = sys.version_info
    ok = version >= (3, 12)
    return [
        DoctorCheckItem(
            section="environment",
            check_name="python",
            status=DoctorStatus.PASS if ok else DoctorStatus.FAIL,
            message=f"Python {platform.python_version()} detected.",
            fix_suggestion=None if ok else "Install Python 3.12 or newer.",
        )
    ]


def _check_uv() -> list[DoctorCheckItem]:
    uv_path = shutil.which("uv")
    return [
        DoctorCheckItem(
            section="environment",
            check_name="uv",
            status=DoctorStatus.PASS if uv_path else DoctorStatus.WARN,
            message=f"uv found at {uv_path}." if uv_path else "uv was not found on PATH.",
            fix_suggestion=None if uv_path else "Install uv and run uv sync.",
        )
    ]


def _check_virtual_env() -> list[DoctorCheckItem]:
    venv = os.environ.get("VIRTUAL_ENV")
    return [
        DoctorCheckItem(
            section="environment",
            check_name="virtual_env",
            status=DoctorStatus.PASS if venv else DoctorStatus.WARN,
            message=f"Virtual environment active: {venv}."
            if venv
            else "No active virtual environment detected.",
            fix_suggestion=None if venv else "Run uv venv and activate .venv before development.",
        )
    ]


def _check_env_file() -> list[DoctorCheckItem]:
    env_path = default_env_file_path()
    explicit = "JENAI_ENV_FILE" in os.environ
    if env_path.is_file():
        return [
            DoctorCheckItem(
                section="config",
                check_name="env_file",
                status=DoctorStatus.PASS,
                message=f"Env file found: {env_path}",
            )
        ]
    if explicit:
        return [
            DoctorCheckItem(
                section="config",
                check_name="env_file",
                status=DoctorStatus.WARN,
                message=f"JENAI_ENV_FILE points to a missing file: {env_path}",
                fix_suggestion="Fix the JENAI_ENV_FILE path or unset it to use the default.",
            )
        ]
    return [
        DoctorCheckItem(
            section="config",
            check_name="env_file",
            status=DoctorStatus.PASS,
            message=f"No env file at {env_path} (optional; shell environment is used).",
        )
    ]


def _check_ros2() -> list[DoctorCheckItem]:
    ros2_path = shutil.which("ros2")
    if not ros2_path:
        return [
            DoctorCheckItem(
                section="ros2",
                check_name="ros2_cli",
                status=DoctorStatus.FAIL,
                message="ros2 command was not found on PATH.",
                fix_suggestion=(
                    "Install ROS2 Jazzy and source its setup script before running JenAI."
                ),
            )
        ]

    try:
        completed = subprocess.run(
            ["ros2", "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [
            DoctorCheckItem(
                section="ros2",
                check_name="ros2_cli",
                status=DoctorStatus.FAIL,
                message=f"ros2 command exists but could not run: {exc}",
                fix_suggestion="Verify the ROS2 environment is sourced in this terminal.",
            )
        ]

    ok = completed.returncode == 0
    return [
        DoctorCheckItem(
            section="ros2",
            check_name="ros2_cli",
            status=DoctorStatus.PASS if ok else DoctorStatus.FAIL,
            message="ros2 command is runnable."
            if ok
            else f"ros2 --help exited with code {completed.returncode}.",
            fix_suggestion=None
            if ok
            else "Verify the ROS2 environment is sourced in this terminal.",
        )
    ]


def _check_nav_stack(config: AppConfig | None) -> list[DoctorCheckItem]:
    """Navigation-readiness checks: is there a map, localization, a laser,
    a listening velocity controller, and Nav2 itself?

    Everything here is WARN-level — a machine without a running robot is a
    normal state, and these checks exist to walk a newcomer through
    docs/ONBOARDING.md, not to block startup.
    """
    if shutil.which("ros2") is None:
        return []  # ros2_cli already reports the root cause

    def _run(args: list[str], timeout: float = 15.0) -> str | None:
        # First call may also spawn the ros2 daemon — give it headroom.
        try:
            completed = subprocess.run(
                args, check=False, capture_output=True, text=True, timeout=timeout
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return completed.stdout if completed.returncode == 0 else None

    topics_out = _run(["ros2", "topic", "list"])
    if topics_out is None:
        return [
            DoctorCheckItem(
                section="nav",
                check_name="ros_graph",
                status=DoctorStatus.WARN,
                message="Could not list ROS2 topics (daemon slow or graph unreachable).",
                fix_suggestion="Try `ros2 topic list` manually; is the robot/simulator up?",
            )
        ]
    topics = set(topics_out.split())

    def _item(name: str, ok: bool, ok_msg: str, warn_msg: str, fix: str) -> DoctorCheckItem:
        return DoctorCheckItem(
            section="nav",
            check_name=name,
            status=DoctorStatus.PASS if ok else DoctorStatus.WARN,
            message=ok_msg if ok else warn_msg,
            fix_suggestion=None if ok else fix,
        )

    items = [
        _item(
            "map",
            "/map" in topics,
            "A map is being published (/map).",
            "No /map topic — no map server or SLAM running.",
            "Build a map first: docs/ONBOARDING.md §3 (slam_toolbox), then serve it.",
        ),
        _item(
            "localization",
            "/amcl_pose" in topics,
            "AMCL localization is up (/amcl_pose).",
            "No /amcl_pose — robot pose will fall back to raw odometry.",
            "Start Nav2 localization with your saved map: docs/ONBOARDING.md §4.",
        ),
        _item(
            "laser",
            "/scan" in topics,
            "Laser scan is publishing (/scan).",
            "No /scan topic — SLAM/AMCL and obstacle avoidance need a laser.",
            "Check the LiDAR driver (docs/ONBOARDING.md §2).",
        ),
        _item(
            "nav2",
            any(t.startswith("/navigate_to_pose") for t in topics),
            "Nav2 NavigateToPose action is available.",
            "Nav2 is not running — /route will honestly report unavailable.",
            "Launch Nav2 (docs/ONBOARDING.md §5).",
        ),
    ]

    # Someone must be listening on cmd_vel, or every motion command is a no-op.
    cmd_vel = config.vehicle.cmd_vel_topic if config is not None else "/cmd_vel"
    info_out = _run(["ros2", "topic", "info", cmd_vel]) if cmd_vel in topics else None
    subscribed = bool(info_out) and "Subscription count: 0" not in info_out
    items.append(
        _item(
            "cmd_vel",
            subscribed,
            f"A controller subscribes to {cmd_vel}.",
            f"Nothing subscribes to {cmd_vel} — the robot would ignore velocity commands.",
            "Start the base/motor controller (docs/ONBOARDING.md §2).",
        )
    )
    return items


def _check_provider(config: AppConfig | None) -> list[DoctorCheckItem]:
    if config is None or config.active_provider is None:
        return [
            DoctorCheckItem(
                section="provider",
                check_name="active_provider",
                status=DoctorStatus.FAIL,
                message="No active provider is configured.",
                fix_suggestion="Run JenAI and complete provider setup.",
            )
        ]

    profile = config.active_profile()
    if profile is None:
        return [
            DoctorCheckItem(
                section="provider",
                check_name="active_provider",
                status=DoctorStatus.FAIL,
                message=(
                    f"Active provider '{config.active_provider}' is missing from "
                    "provider_profiles."
                ),
                fix_suggestion="Update the config or rerun setup.",
            )
        ]

    items = [
        DoctorCheckItem(
            section="provider",
            check_name="active_provider",
            status=DoctorStatus.PASS,
            message=f"Active provider is {profile.name} ({profile.provider}).",
        )
    ]

    if profile.api_key_env and not os.environ.get(profile.api_key_env):
        items.append(
            DoctorCheckItem(
                section="provider",
                check_name="api_key",
                status=DoctorStatus.WARN,
                message=f"Environment variable {profile.api_key_env} is not set.",
                fix_suggestion=(
                    f"Add it to the env file, e.g.: printf '{profile.api_key_env}=…\\n' "
                    f">> {default_env_file_path()} && chmod 600 {default_env_file_path()}"
                ),
            )
        )
    else:
        items.append(
            DoctorCheckItem(
                section="provider",
                check_name="api_key",
                status=DoctorStatus.PASS,
                message="Provider credential environment looks configured.",
            )
        )

    if config.model_bindings is None:
        items.append(
            DoctorCheckItem(
                section="provider",
                check_name="model_bindings",
                status=DoctorStatus.FAIL,
                message="Model bindings are missing.",
                fix_suggestion="Configure chat, plan, vision, route, and default model bindings.",
            )
        )
    else:
        items.append(
            DoctorCheckItem(
                section="provider",
                check_name="model_bindings",
                status=DoctorStatus.PASS,
                message="Model bindings are present.",
            )
        )

    return items


def _check_locations(config: AppConfig | None, config_path: Path) -> list[DoctorCheckItem]:
    if config is None:
        return [
            DoctorCheckItem(
                section="locations",
                check_name="locations_file",
                status=DoctorStatus.WARN,
                message="Locations cannot be checked without a valid config.",
                fix_suggestion="Create a config file, then add locations.",
            )
        ]

    locations_path = config.resolved_locations_path(config_path)
    if locations_path is None:
        return [
            DoctorCheckItem(
                section="locations",
                check_name="locations_file",
                status=DoctorStatus.WARN,
                message="No locations_path is configured.",
                fix_suggestion="Add locations_path to the JenAI config.",
            )
        ]

    if not locations_path.exists():
        return [
            DoctorCheckItem(
                section="locations",
                check_name="locations_file",
                status=DoctorStatus.WARN,
                message=f"Locations file does not exist: {locations_path}",
                fix_suggestion="Create the locations file before using /route or /loc commands.",
            )
        ]

    return [
        DoctorCheckItem(
            section="locations",
            check_name="locations_file",
            status=DoctorStatus.PASS,
            message=f"Locations file exists: {locations_path}",
        )
    ]


def _check_webui_assets() -> list[DoctorCheckItem]:
    try:
        from jenai.webui import render_dashboard_html

        render_dashboard_html({"provider": None})
    except Exception as exc:  # pragma: no cover - defensive
        return [
            DoctorCheckItem(
                section="webui",
                check_name="assets",
                status=DoctorStatus.FAIL,
                message=f"WebUI renderer could not load: {exc}",
                fix_suggestion="Check the jenai.webui package for import errors.",
            )
        ]
    return [
        DoctorCheckItem(
            section="webui",
            check_name="assets",
            status=DoctorStatus.PASS,
            message="WebUI dashboard renderer is available (JenAI web).",
        )
    ]
