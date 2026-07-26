#!/usr/bin/env bash
# Start the Isaac Warehouse Nav2 stack without the RViz process embedded in
# NVIDIA's carter_navigation.launch.py. Intended for repeatable HIL resets.

set -e

ROS_SETUP="${ROS_SETUP:-/opt/ros/jazzy/setup.bash}"
WORKSPACE_SETUP="${ROS_WORKSPACE_SETUP:-$HOME/IsaacSim-ros_workspaces/jazzy_ws/install/setup.bash}"
SESSION="${JENAI_NAV2_TMUX_SESSION:-nav2}"
CARTER_SHARE="${CARTER_NAVIGATION_SHARE:-$HOME/IsaacSim-ros_workspaces/jazzy_ws/install/carter_navigation/share/carter_navigation}"
MAP_FILE="${JENAI_NAV2_MAP:-$CARTER_SHARE/maps/carter_warehouse_navigation.yaml}"
PARAMS_FILE="${JENAI_NAV2_PARAMS:-$CARTER_SHARE/params/carter_navigation_params.yaml}"
START_TIMEOUT_S="${JENAI_NAV2_START_TIMEOUT_S:-45}"
XY_GOAL_TOLERANCE="${JENAI_NAV2_XY_GOAL_TOLERANCE:-0.05}"
YAW_GOAL_TOLERANCE="${JENAI_NAV2_YAW_GOAL_TOLERANCE:-0.15}"

die() {
    printf 'isaac-nav2: %s\n' "$*" >&2
    exit 1
}

source_environment() {
    [ -f "$ROS_SETUP" ] || die "ROS setup not found: $ROS_SETUP"
    [ -f "$WORKSPACE_SETUP" ] || die "workspace setup not found: $WORKSPACE_SETUP"
    # shellcheck disable=SC1090
    source "$ROS_SETUP"
    # shellcheck disable=SC1090
    source "$WORKSPACE_SETUP"
    command -v ros2 >/dev/null 2>&1 || die "ros2 is unavailable after sourcing the environment"
    command -v tmux >/dev/null 2>&1 || die "tmux is required"
}

session_exists() {
    tmux has-session -t "$SESSION" 2>/dev/null
}

numeric_parameter_equals() {
    local output="$1"
    local expected="$2"
    local value="${output#*: }"

    [ "$output" != "$value" ] \
        && awk -v value="$value" -v expected="$expected" 'BEGIN {
            exit !(value ~ /^[0-9]+([.][0-9]+)?$/ && value + 0 == expected + 0)
        }'
}

controller_parameters_are_expected() {
    local plugin_output="$1"
    local min_theta_output="$2"
    local plugin="${plugin_output#*: }"

    [ "$plugin_output" != "$plugin" ] \
        && [ "$plugin" = "nav2_controller::PoseProgressChecker" ] \
        && numeric_parameter_equals "$min_theta_output" "0.1"
}

stop_stack() {
    if ! session_exists; then
        printf 'isaac-nav2: session %s is not running.\n' "$SESSION"
        return
    fi

    # Give ROS launch and the converter a chance to run their signal handlers,
    # then reap the fixed, tool-owned tmux session. Repeated stop is safe.
    tmux send-keys -t "$SESSION:navigation" C-c 2>/dev/null || true
    tmux send-keys -t "$SESSION:scan" C-c 2>/dev/null || true
    for _ in 1 2 3 4 5; do
        session_exists || break
        sleep 1
    done
    if session_exists; then
        tmux kill-session -t "$SESSION"
    fi
    printf 'isaac-nav2: stopped session %s.\n' "$SESSION"
}

start_stack() {
    source_environment
    [ -f "$MAP_FILE" ] || die "map not found: $MAP_FILE"
    [ -f "$PARAMS_FILE" ] || die "params not found: $PARAMS_FILE"
    session_exists && die "session $SESSION is already running; use restart"

    # tmux accepts one shell-command string. Each window sources ROS itself
    # because a long-lived tmux server retains an older PATH than its caller.
    printf -v nav_inner \
        'source %q && source %q && exec ros2 launch nav2_bringup bringup_launch.py map:=%q params_file:=%q use_sim_time:=True' \
        "$ROS_SETUP" "$WORKSPACE_SETUP" "$MAP_FILE" "$PARAMS_FILE"
    printf -v scan_inner \
        'source %q && source %q && exec ros2 run pointcloud_to_laserscan pointcloud_to_laserscan_node --ros-args -r cloud_in:=/front_3d_lidar/lidar_points -r scan:=/scan -p target_frame:=front_3d_lidar -p transform_tolerance:=0.01 -p min_height:=-0.4 -p max_height:=1.5 -p angle_min:=-1.5708 -p angle_max:=1.5708 -p angle_increment:=0.0087 -p scan_time:=0.1 -p range_min:=0.05 -p range_max:=100.0 -p use_inf:=True -p inf_epsilon:=1.0 -p use_sim_time:=True' \
        "$ROS_SETUP" "$WORKSPACE_SETUP"
    printf -v nav_command 'exec /bin/bash -lc %q' "$nav_inner"
    printf -v scan_command 'exec /bin/bash -lc %q' "$scan_inner"

    tmux new-session -d -s "$SESSION" -n navigation "$nav_command"
    if ! tmux new-window -d -t "$SESSION" -n scan "$scan_command"; then
        tmux kill-session -t "$SESSION" 2>/dev/null || true
        die "failed to start the LaserScan converter"
    fi

    elapsed=0
    while [ "$elapsed" -lt "$START_TIMEOUT_S" ]; do
        if ros2 lifecycle get /controller_server 2>/dev/null | grep -q '^active '; then
            ros2 param set /controller_server general_goal_checker.xy_goal_tolerance "$XY_GOAL_TOLERANCE" >/dev/null || die "failed to set xy goal tolerance"
            ros2 param set /controller_server general_goal_checker.yaw_goal_tolerance "$YAW_GOAL_TOLERANCE" >/dev/null || die "failed to set yaw goal tolerance"
            ros2 param set /controller_server FollowPath.xy_goal_tolerance "$XY_GOAL_TOLERANCE" >/dev/null || die "failed to set DWB xy goal tolerance"

            plugin_output="$(ros2 param get /controller_server progress_checker.plugin 2>/dev/null || true)"
            min_theta_output="$(ros2 param get /controller_server FollowPath.min_speed_theta 2>/dev/null || true)"
            xy_output="$(ros2 param get /controller_server general_goal_checker.xy_goal_tolerance 2>/dev/null || true)"
            yaw_output="$(ros2 param get /controller_server general_goal_checker.yaw_goal_tolerance 2>/dev/null || true)"
            dwb_xy_output="$(ros2 param get /controller_server FollowPath.xy_goal_tolerance 2>/dev/null || true)"
            if ! controller_parameters_are_expected "$plugin_output" "$min_theta_output" \
                || ! numeric_parameter_equals "$xy_output" "$XY_GOAL_TOLERANCE" \
                || ! numeric_parameter_equals "$yaw_output" "$YAW_GOAL_TOLERANCE" \
                || ! numeric_parameter_equals "$dwb_xy_output" "$XY_GOAL_TOLERANCE"; then
                stop_stack
                die "unexpected precision parameters after startup"
            fi
            printf 'isaac-nav2: active in tmux session %s (xy=%s m, yaw=%s rad).\n' "$SESSION" "$XY_GOAL_TOLERANCE" "$YAW_GOAL_TOLERANCE"
            return
        fi
        session_exists || die "Nav2 exited during startup; inspect the ROS log"
        sleep 1
        elapsed=$((elapsed + 1))
    done
    stop_stack
    die "controller_server did not become active within ${START_TIMEOUT_S}s"
}

status_stack() {
    source_environment
    session_exists || die "session $SESSION is not running"
    ros2 lifecycle get /controller_server
    ros2 param get /controller_server progress_checker.plugin
    ros2 param get /controller_server FollowPath.min_speed_theta
    ros2 action info /navigate_to_pose
}
main() {
    case "${1:-restart}" in
        start) start_stack ;;
        stop) source_environment; stop_stack ;;
        restart) source_environment; stop_stack; start_stack ;;
        status) status_stack ;;
        *) die "usage: $0 {start|stop|restart|status}" ;;
    esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
