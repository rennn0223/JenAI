from __future__ import annotations

import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).parents[2] / "scripts" / "isaac_nav2.sh"


def _validate(
    plugin_output: str,
    min_theta_output: str,
    stateful_output: str,
    min_vel_x_output: str = "Double value is: 0.0",
    vtheta_samples_output: str = "Integer value is: 15",
) -> bool:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; controller_parameters_are_expected "$2" "$3" "$4" "$5" "$6"',
            "bash",
            str(_SCRIPT),
            plugin_output,
            min_theta_output,
            stateful_output,
            min_vel_x_output,
            vtheta_samples_output,
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
    assert not _validate(
        expected_plugin,
        "Double value is: 0.1",
        "Boolean value is: False",
        "Double value is: -0.1",
    )
    assert not _validate(
        expected_plugin,
        "Double value is: 0.1",
        "Boolean value is: False",
        "Double value is: 0.0",
        "Integer value is: 20",
    )


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
    assert _numeric_matches("Double value is: 0.0", "0.0")
    assert _numeric_matches("Double value is: -0.1", "-0.1")
    assert not _numeric_matches("Double value is: -0.11", "-0.1")
    assert not _numeric_matches("Double value is: 0.051", "0.05")
    assert not _numeric_matches("Double value is: 0.03", "0.05")
    assert not _numeric_matches("0.05", "0.05")


def test_default_goal_tolerances_match_the_verified_endpoint_contract() -> None:
    script = _SCRIPT.read_text()
    assert "JENAI_NAV2_XY_GOAL_TOLERANCE:-0.05" in script
    assert "JENAI_NAV2_YAW_GOAL_TOLERANCE:-0.15" in script
    assert "JENAI_NAV2_MIN_VEL_X:-0.0" in script
    assert "JENAI_NAV2_VTHETA_SAMPLES:-15" in script
    assert "JENAI_NAV2_AMCL_ALPHA:-0.01" in script
    assert "JENAI_NAV2_AMCL_UPDATE_MIN_A:-0.02" in script
    assert "JENAI_NAV2_AMCL_UPDATE_MIN_D:-0.02" in script


def test_parameter_override_is_rendered_before_nav2_launch(tmp_path: Path) -> None:
    source = tmp_path / "source.yaml"
    target = tmp_path / "rendered.yaml"
    source.write_text(
        """
amcl:
  ros__parameters:
    alpha1: 0.2
    alpha2: 0.2
    alpha3: 0.2
    alpha4: 0.2
    alpha5: 0.2
    update_min_a: 0.2
    update_min_d: 0.25
controller_server:
  ros__parameters:
    progress_checker:
      plugin: "nav2_controller::PoseProgressChecker"
    general_goal_checker:
      stateful: true
      xy_goal_tolerance: 0.25
      yaw_goal_tolerance: 0.25
    FollowPath:
      min_vel_x: 0.0
      min_speed_theta: 0.1
      vtheta_samples: 20
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
    assert "vtheta_samples: 15" in rendered
    assert "min_vel_x: 0.0" in rendered
    assert rendered.count("alpha1: 0.01") == 1
    assert rendered.count("alpha2: 0.01") == 1
    assert rendered.count("alpha3: 0.01") == 1
    assert rendered.count("alpha4: 0.01") == 1
    assert rendered.count("alpha5: 0.01") == 1
    assert "update_min_a: 0.02" in rendered
    assert "update_min_d: 0.02" in rendered


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
amcl:
  ros__parameters:
    alpha1: 0.2
    alpha2: 0.2
    alpha3: 0.2
    alpha4: 0.2
    alpha5: 0.2
    update_min_a: 0.2
    update_min_d: 0.25
controller_server:
  ros__parameters:
    progress_checker:
      plugin: "nav2_controller::PoseProgressChecker"
    general_goal_checker:
      stateful: true
      xy_goal_tolerance: 0.25
      yaw_goal_tolerance: 0.25
    FollowPath:
      min_vel_x: 0.0
      min_speed_theta: 0.1
      vtheta_samples: 20
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
            FollowPath.vtheta_samples) printf 'Integer value is: 15\n' ;;
            FollowPath.min_vel_x) printf 'Double value is: -0.1\n' ;;
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
    assert "unexpected navigation precision parameters" in result.stderr
    assert "kill-session" in log_file.read_text(encoding="utf-8")


def test_start_refuses_existing_session_without_killing_it(tmp_path: Path) -> None:
    map_file = tmp_path / "map.yaml"
    params_file = tmp_path / "params.yaml"
    override_file = tmp_path / "override.yaml"
    log_file = tmp_path / "tmux.log"
    map_file.write_text("image: map.pgm\n", encoding="utf-8")
    params_file.write_text("controller_server: {}\n", encoding="utf-8")

    shell = r"""
source "$1"
source_environment() { :; }
session_exists() { return 0; }
tmux() {
    printf '%s\n' "$*" >> "$TEST_TMUX_LOG"
    return 0
}
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
    assert "already running" in result.stderr
    assert not log_file.exists() or "kill-session" not in log_file.read_text(encoding="utf-8")
    assert not override_file.exists()


def test_losing_concurrent_start_never_kills_the_winning_session(tmp_path: Path) -> None:
    """A failed tmux create does not grant ownership of another process session."""
    map_file = tmp_path / "map.yaml"
    params_file = tmp_path / "params.yaml"
    override_file = tmp_path / "override.yaml"
    log_file = tmp_path / "tmux.log"
    render_log = tmp_path / "render.log"
    map_file.write_text("image: map.pgm\n", encoding="utf-8")
    params_file.write_text("controller_server: {}\n", encoding="utf-8")
    override_file.write_text("winner-owned\n", encoding="utf-8")

    shell = r"""
source "$1"
source_environment() { :; }
render_nav2_params_override() {
    printf "%s\n" "$2" > "$TEST_RENDER_LOG"
    OVERRIDE_PARAMS_FILE="$2"
    : > "$2"
}
SESSION_CHECKS=0
session_exists() {
    SESSION_CHECKS=$((SESSION_CHECKS + 1))
    [ "$SESSION_CHECKS" -gt 1 ]
}
tmux() {
    printf "%s\n" "$*" >> "$TEST_TMUX_LOG"
    case "$1" in
        new-session) return 1 ;;
        kill-session) return 0 ;;
    esac
    return 0
}
start_stack
"""
    result = subprocess.run(
        ["bash", "-c", shell, "bash", str(_SCRIPT), str(params_file), str(override_file)],
        check=False,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "JENAI_NAV2_MAP": str(map_file),
            "JENAI_NAV2_PARAMS": str(params_file),
            "JENAI_NAV2_OVERRIDE_PARAMS": str(override_file),
            "TEST_TMUX_LOG": str(log_file),
            "TEST_RENDER_LOG": str(render_log),
        },
    )

    assert result.returncode != 0
    commands = log_file.read_text(encoding="utf-8")
    assert "new-session" in commands
    assert "kill-session" not in commands
    assert override_file.read_text(encoding="utf-8") == "winner-owned\n"
    rendered_path = Path(render_log.read_text(encoding="utf-8").strip())
    assert rendered_path != override_file
    assert not rendered_path.exists()
