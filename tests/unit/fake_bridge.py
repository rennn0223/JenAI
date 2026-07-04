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
            emit(
                {
                    "id": req_id,
                    "ok": True,
                    "result": {
                        "x": 1.5,
                        "y": -2.0,
                        "yaw": 0.5,
                        "frame_id": "map",
                        "source": "/amcl_pose",
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
            # A stale event from a "previous goal" first — clients must ignore it.
            emit({"event": "nav_result", "tag": "stale-goal", "status": "canceled"})
            emit({"event": "nav_feedback", "tag": tag, "distance_remaining": 3.2, "elapsed": 1.0})
            emit({"id": req_id, "ok": True, "result": {"sent": True}})
            emit({"event": "nav_result", "tag": tag, "status": "succeeded"})
        elif op == "drive_to_pose":
            tag = req.get("tag", "")
            emit({"event": "nav_feedback", "tag": tag, "distance_remaining": 1.5, "elapsed": 0.5})
            emit({"id": req_id, "ok": True, "result": {"sent": True}})
            emit({"event": "nav_result", "tag": tag, "status": "succeeded"})
        else:
            emit({"id": req_id, "ok": False, "error": f"unknown op '{op}'"})


if __name__ == "__main__":
    main()
