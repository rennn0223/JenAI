from __future__ import annotations

import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).parents[2] / "scripts" / "isaac_nav2.sh"


def _validate(plugin_output: str, min_theta_output: str) -> bool:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; controller_parameters_are_expected "$2" "$3"',
            "bash",
            str(_SCRIPT),
            plugin_output,
            min_theta_output,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def test_controller_parameter_validation_accepts_exact_expected_values() -> None:
    assert _validate(
        "String value is: nav2_controller::PoseProgressChecker",
        "Double value is: 0.1",
    )


def test_controller_parameter_validation_rejects_similar_but_wrong_values() -> None:
    expected_plugin = "String value is: nav2_controller::PoseProgressChecker"
    assert not _validate(expected_plugin, "Double value is: 0.11")
    assert not _validate(expected_plugin, "Double value is: 10.1")
    assert not _validate(
        "String value is: nav2_controller::SimpleProgressChecker",
        "Double value is: 0.1",
    )
    assert not _validate("nav2_controller::PoseProgressChecker", "0.1")


def _numeric_matches(output: str, expected: str) -> bool:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; numeric_parameter_equals "$2" "$3"',
            "bash",
            str(_SCRIPT),
            output,
            expected,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def test_numeric_parameter_validation_requires_exact_formatted_value() -> None:
    assert _numeric_matches("Double value is: 0.05", "0.05")
    assert _numeric_matches("Double value is: 0.15", "0.15")
    assert not _numeric_matches("Double value is: 0.051", "0.05")
    assert not _numeric_matches("Double value is: 0.03", "0.05")
    assert not _numeric_matches("0.05", "0.05")


def test_default_goal_tolerances_match_the_verified_endpoint_contract() -> None:
    script = _SCRIPT.read_text()
    assert "JENAI_NAV2_XY_GOAL_TOLERANCE:-0.05" in script
    assert "JENAI_NAV2_YAW_GOAL_TOLERANCE:-0.15" in script
