from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from jenai.config import AppConfig, ConfigError, load_config
from jenai.schemas import DoctorCheckItem, DoctorResult, DoctorStatus


def run_doctor(config_path: Path | None = None) -> DoctorResult:
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

    items.extend(_check_ros2())
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
                fix_suggestion=f"Set {profile.api_key_env} before using provider-backed features.",
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


def _check_locations(config: AppConfig | None, config_path: Path | None) -> list[DoctorCheckItem]:
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

    base_path = config_path or Path.cwd() / "config.toml"
    locations_path = config.resolved_locations_path(base_path)
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
    return [
        DoctorCheckItem(
            section="webui",
            check_name="assets",
            status=DoctorStatus.WARN,
            message="WebUI skeleton is not implemented yet.",
            fix_suggestion="Implement WebUI assets in a later PR-sized task.",
        )
    ]
