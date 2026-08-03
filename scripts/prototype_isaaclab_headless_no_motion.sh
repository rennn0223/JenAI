#!/usr/bin/env bash
# PROTOTYPE: one-shot, observation-only Isaac Lab Headless acceptance runner.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ISAACLAB_ROOT="${ISAACLAB_ROOT:-/home/nvidia/IsaacLab}"
ROS_SETUP="${ROS_SETUP:-/opt/ros/jazzy/setup.bash}"
ROS_WORKSPACE_SETUP="${ROS_WORKSPACE_SETUP:-/home/nvidia/IsaacSim-ros_workspaces/jazzy_ws/install/setup.bash}"

[[ -x "${ISAACLAB_ROOT}/isaaclab-spark.sh" ]] || {
    echo "Headless prototype: Isaac Lab Spark launcher is unavailable." >&2
    exit 1
}
[[ -f "${ROS_SETUP}" && -f "${ROS_WORKSPACE_SETUP}" ]] || {
    echo "Headless prototype: ROS 2 environment is unavailable." >&2
    exit 1
}

# shellcheck disable=SC1090
source "${ROS_SETUP}"
# shellcheck disable=SC1090
source "${ROS_WORKSPACE_SETUP}"

export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-73}"
