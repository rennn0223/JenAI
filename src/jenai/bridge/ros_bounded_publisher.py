"""ROS-system-Python sidecar for one precisely timed, bounded publication.

The main JenAI environment intentionally does not import ``rclpy``. This
small sidecar runs with the ROS system interpreter so the motion command and
the final zero command share one publisher. Using two separate ``ros2 topic
pub`` processes leaves the last non-zero velocity active during startup of the
second process, which can nearly double short test motions.
"""

from __future__ import annotations

import argparse
import json
import signal
import time
from types import FrameType
from typing import Any

import rclpy
from rclpy.node import Node
from rosidl_runtime_py.set_message import set_message_fields
from rosidl_runtime_py.utilities import get_message


def _message(message_class: Any, payload: str) -> Any:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("message payload must be a JSON object")
    message = message_class()
    set_message_fields(message, value)
    return message


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("topic")
    parser.add_argument("message_type")
    parser.add_argument("payload")
    parser.add_argument("stop_payload")
    parser.add_argument("rate_hz", type=float)
    parser.add_argument("duration_s", type=float)
    parser.add_argument("match_timeout_s", type=float)
    args = parser.parse_args()

    stop_requested = False

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        nonlocal stop_requested
        stop_requested = True

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, request_stop)

    if args.rate_hz <= 0 or args.duration_s <= 0 or args.match_timeout_s <= 0:
        raise SystemExit("rate, duration, and match timeout must be positive")

    rclpy.init()
    node = Node("jenai_bounded_publisher")
    publisher = None
    stop = None
    try:
        message_class = get_message(args.message_type)
        motion = _message(message_class, args.payload)
        stop = _message(message_class, args.stop_payload)
        publisher = node.create_publisher(message_class, args.topic, 10)

        match_deadline = time.monotonic() + args.match_timeout_s
        while publisher.get_subscription_count() < 1:
            if stop_requested:
                return
            if time.monotonic() >= match_deadline:
                raise TimeoutError(
                    f"no matching subscription on {args.topic} within {args.match_timeout_s:g}s"
                )
            rclpy.spin_once(node, timeout_sec=0.05)

        period_s = 1.0 / args.rate_hz
        started = time.monotonic()
        deadline = started + args.duration_s
        next_publish = started
        count = 0
        while True:
            now = time.monotonic()
            if stop_requested or now >= deadline:
                break
            publisher.publish(motion)
            count += 1
            next_publish += period_s
            rclpy.spin_once(
                node,
                timeout_sec=max(0.0, min(next_publish - time.monotonic(), period_s)),
            )

        # Repeating the stop briefly on this publisher removes the startup
        # delay of a second ROS CLI process and keeps short motions bounded.
        for _ in range(3):
            publisher.publish(stop)
            rclpy.spin_once(node, timeout_sec=0.02)

        elapsed = time.monotonic() - started
        print(
            f"drove {args.topic} for {elapsed:.3f}s with {count} messages at "
            f"{args.rate_hz:g} Hz, then sent 3 stop pulses",
            flush=True,
        )
    finally:
        if publisher is not None and stop is not None:
            publisher.publish(stop)
            rclpy.spin_once(node, timeout_sec=0.02)
        node.destroy_node()
        rclpy.shutdown()
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    main()
