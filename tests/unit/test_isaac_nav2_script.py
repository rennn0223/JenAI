from __future__ import annotations

import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).parents[2] / "scripts" / "isaac_nav2.sh"


def _validate(plugin_output: str, min_theta_output: str, stateful_output: str) -> bool:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; controller_parameters_are_expected "$2" "$3" "$4"',
            "bash",
            str(_SCRIPT),
            plugin_output,
            min_theta_output,
            stateful_output,
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
        "Boolean value is: False",
    )


def test_controller_parameter_validation_rejects_similar_but_wrong_values() -> None:
    expected_plugin = "String value is: nav2_controller::PoseProgressChecker"
    assert not _validate(expected_plugin, "Double value is: 0.11", "Boolean value is: False")
    assert not _validate(expected_plugin, "Double value is: 10.1", "Boolean value is: False")
    assert not _validate(
        "String value is: nav2_controller::SimpleProgressChecker",
        "Double value is: 0.1",
        "Boolean value is: False",
    )
    assert not _validate("nav2_controller::PoseProgressChecker", "0.1", "Boolean value is: False")
    assert not _validate(expected_plugin, "Double value is: 0.1", "Boolean value is: True")
    assert not _validate(expected_plugin, "Double value is: 0.1", "False")


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


def test_parameter_override_is_rendered_before_nav2_launch(tmp_path: Path) -> None:
    source = tmp_path / "source.yaml"
    target = tmp_path / "rendered.yaml"
    source.write_text(
        """
controller_server:
  ros__parameters:
    progress_checker:
      plugin: "nav2_controller::PoseProgressChecker"
    general_goal_checker:
      stateful: true
      xy_goal_tolerance: 0.25
      yaw_goal_tolerance: 0.25
    FollowPath:
      min_speed_theta: 0.1
      xy_goal_tolerance: 0.25
""".lstrip(),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                'source "$1"; '
                'render_nav2_params_override "$2" "$3"; '
                'printf "%s" "$OVERRIDE_PARAMS_FILE"'
            ),
            "bash",
            str(_SCRIPT),
            str(source),
            str(target),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "JENAI_NAV2_XY_GOAL_TOLERANCE": "0.05",
            "JENAI_NAV2_YAW_GOAL_TOLERANCE": "0.15",
            "JENAI_NAV2_OVERRIDE_PARAMS": str(target),
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == str(target)
    rendered = target.read_text(encoding="utf-8")
    assert "xy_goal_tolerance: 0.05" in rendered
    assert "yaw_goal_tolerance: 0.15" in rendered
    assert "stateful: false" in rendered
    assert rendered.count("xy_goal_tolerance: 0.05") == 2
    assert "min_speed_theta: 0.1" in rendered


def test_script_never_mutates_controller_plugin_parameters_at_runtime() -> None:
    script = _SCRIPT.read_text(encoding="utf-8")
    assert "ros2 param set" not in script
    assert "params_file:=%q" in script
    assert '"$OVERRIDE_PARAMS_FILE"' in script


def test_start_failure_cleans_the_owned_tmux_session(tmp_path: Path) -> None:
    map_file = tmp_path / "map.yaml"
    params_file = tmp_path / "params.yaml"
    override_file = tmp_path / "override.yaml"
    log_file = tmp_path / "tmux.log"
    map_file.write_text("image: map.pgm\n", encoding="utf-8")
    params_file.write_text(
        """
controller_server:
  ros__parameters:
    progress_checker:
      plugin: "nav2_controller::PoseProgressChecker"
    general_goal_checker:
      stateful: true
      xy_goal_tolerance: 0.25
      yaw_goal_tolerance: 0.25
    FollowPath:
      min_speed_theta: 0.1
      xy_goal_tolerance: 0.25
""".lstrip(),
        encoding="utf-8",
    )

    shell = r"""
source "$1"
source_environment() { :; }
STARTED=0
session_exists() { [ "$STARTED" -eq 1 ]; }
tmux() {
    printf '%s\n' "$*" >> "$TEST_TMUX_LOG"
    case "$1" in
        new-session) STARTED=1 ;;
        kill-session) STARTED=0 ;;
    esac
    return 0
}
ros2() {
    if [ "$1 $2 $3" = "lifecycle get /controller_server" ]; then
        printf 'active [3]\n'
    elif [ "$1 $2 $3" = "param get /controller_server" ]; then
        case "$4" in
            progress_checker.plugin)
                printf 'String value is: nav2_controller::SimpleProgressChecker\n'
                ;;
            FollowPath.min_speed_theta) printf 'Double value is: 0.1\n' ;;
            general_goal_checker.stateful) printf 'Boolean value is: False\n' ;;
            general_goal_checker.xy_goal_tolerance) printf 'Double value is: 0.05\n' ;;
            general_goal_checker.yaw_goal_tolerance) printf 'Double value is: 0.15\n' ;;
            FollowPath.xy_goal_tolerance) printf 'Double value is: 0.05\n' ;;
        esac
    fi
}
sleep() { :; }
start_stack
"""
    result = subprocess.run(
        ["bash", "-c", shell, "bash", str(_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "JENAI_NAV2_MAP": str(map_file),
            "JENAI_NAV2_PARAMS": str(params_file),
            "JENAI_NAV2_OVERRIDE_PARAMS": str(override_file),
            "TEST_TMUX_LOG": str(log_file),
        },
    )

    assert result.returncode != 0
    assert "unexpected precision parameters" in result.stderr
    assert "kill-session" in log_file.read_text(encoding="utf-8")
