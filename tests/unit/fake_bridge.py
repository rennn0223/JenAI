#!/usr/bin/env python3
"""Protocol-faithful fake of ros_bridge.py for tests — stdlib only, no ROS."""

from __future__ import annotations

import json
import sys
import time


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main() -> None:
    emit({"event": "ready"})
    pose = {
        "x": 1.5,
        "y": -2.0,
        "yaw": 0.5,
        "frame_id": "map",
        "source": "/amcl_pose",
    }
    for line in sys.stdin:
        req = json.loads(line)
        req_id, op = req.get("id"), req.get("op")
        if op == "shutdown":
            emit({"id": req_id, "ok": True, "result": {}})
            break
        if op == "ping":
            emit({"id": req_id, "ok": True, "result": {"pong": True}})
        elif op == "halt":
            emit({"id": req_id, "ok": True, "result": {"halted": True, "nav_canceled": False}})
        elif op == "watchdog":
            emit({"id": req_id, "ok": True, "result": {"watchdog_s": req.get("timeout", 0.0)}})
        elif op == "pose":
            emit({"id": req_id, "ok": True, "result": dict(pose)})
        elif op == "map_identity":
            emit(
                {
                    "id": req_id,
                    "ok": True,
                    "result": {
                        "algorithm": "sha256-occupancy-grid-v1",
                        "digest": "a" * 64,
                        "width": 20,
                        "height": 30,
                        "resolution": 0.05,
                        "origin_x": -1.0,
                        "origin_y": -2.0,
                        "origin_yaw": 0.0,
                        "frame_id": "map",
                        "source": "/map",
                    },
                }
            )
        elif op == "map_cell":
            emit(
                {
                    "id": req_id,
                    "ok": True,
                    "result": {
                        "in_bounds": True,
                        "free": True,
                        "value": 0,
                        "cell_x": 4,
                        "cell_y": 6,
                        "width": 20,
                        "height": 30,
                        "resolution": 0.05,
                        "origin_x": -1.0,
                        "origin_y": -2.0,
                        "frame_id": "map",
                        "source": "/map",
                    },
                }
            )
        elif op == "nav_plan":
            emit(
                {
                    "id": req_id,
                    "ok": True,
                    "result": {
                        "feasible": True,
                        "pose_count": 12,
                        "path_length_m": 4.25,
                        "planning_time_s": 0.02,
                        "error_code": 0,
                        "error_name": "NONE",
                        "error_message": "",
                    },
                }
            )
        elif op == "boom":
            emit({"id": req_id, "ok": False, "error": "synthetic failure"})
        elif op == "slow":
            time.sleep(2.0)
            emit({"id": req_id, "ok": True, "result": {}})
        elif op == "watch":
            emit(
                {
                    "event": "watch",
                    "watch_id": req["watch_id"],
                    "topic": req["topic"],
                    "data": {"percentage": 0.42},
                }
            )
            emit({"id": req_id, "ok": True, "result": {"watch_id": req["watch_id"]}})
        elif op == "nav_send":
            tag = req.get("tag", "")
            pose.update(
                x=req["x"],
                y=req["y"],
                yaw=req.get("yaw", 0.0),
                frame_id=req.get("frame_id", "map"),
                source="nav2_feedback",
            )
            # A stale event from a "previous goal" first — clients must ignore it.
            emit({"event": "nav_result", "tag": "stale-goal", "status": "canceled"})
            emit({"event": "nav_feedback", "tag": tag, "distance_remaining": 3.2, "elapsed": 1.0})
            emit({"id": req_id, "ok": True, "result": {"sent": True}})
            emit(
                {
                    "event": "nav_result",
                    "tag": tag,
                    "status": "succeeded",
                    "final_pose": dict(pose),
                }
            )
        elif op == "drive_to_pose":
            tag = req.get("tag", "")
            pose.update(
                x=req["x"],
                y=req["y"],
                yaw=req.get("yaw", 0.0),
                frame_id="odom",
                source="/odom",
            )
            emit({"event": "nav_feedback", "tag": tag, "distance_remaining": 1.5, "elapsed": 0.5})
            emit({"id": req_id, "ok": True, "result": {"sent": True}})
            emit({"event": "nav_result", "tag": tag, "status": "succeeded"})
        else:
            emit({"id": req_id, "ok": False, "error": f"unknown op '{op}'"})


if __name__ == "__main__":
    main()
