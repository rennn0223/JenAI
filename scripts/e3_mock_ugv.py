"""Isolated ROS2 UGV fixture for the E3 bounded-agent benchmark.

Run in a non-production ROS domain. The node integrates /cmd_vel into /odom,
and exposes reset/feedback switches so success and missing-feedback cases are
repeatable without touching Isaac Sim or physical hardware.
"""

from __future__ import annotations

import math
import os
import time

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from std_srvs.srv import SetBool, Trigger

ISOLATED_DOMAIN_ID = "42"


class MockUGV(Node):
    def __init__(self) -> None:
        super().__init__("e3_mock_ugv")
        self.x = 0.0
        self.y = 0.0
        self.yaw = 0.0
        self.linear = 0.0
        self.angular = 0.0
        self.feedback_enabled = True
        self.drop_feedback_on_motion = False
        self.last_update = time.monotonic()
        self.cmd_count = 0
        self.create_subscription(Twist, "/cmd_vel", self.on_cmd, 10)
        self.odom_pub = self.create_publisher(Odometry, "/odom", 10)
        self.create_service(SetBool, "/bench/set_feedback", self.on_feedback)
        self.create_service(
            SetBool,
            "/bench/drop_feedback_on_motion",
            self.on_drop_feedback_on_motion,
        )
        self.create_service(Trigger, "/bench/reset", self.on_reset)
        self.create_timer(0.05, self.tick)

    def on_cmd(self, msg: Twist) -> None:
        self.linear = float(msg.linear.x)
        self.angular = float(msg.angular.z)
        self.cmd_count += 1
        if self.drop_feedback_on_motion and (self.linear or self.angular):
            self.feedback_enabled = False

    def on_feedback(self, request: SetBool.Request, response: SetBool.Response):
        self.feedback_enabled = bool(request.data)
        response.success = True
        response.message = f"feedback_enabled={self.feedback_enabled}"
        return response

    def on_drop_feedback_on_motion(
        self, request: SetBool.Request, response: SetBool.Response
    ):
        self.drop_feedback_on_motion = bool(request.data)
        response.success = True
        response.message = (
            f"drop_feedback_on_motion={self.drop_feedback_on_motion}"
        )
        return response

    def on_reset(self, _request: Trigger.Request, response: Trigger.Response):
        self.x = self.y = self.yaw = self.linear = self.angular = 0.0
        self.cmd_count = 0
        self.feedback_enabled = True
        self.drop_feedback_on_motion = False
        self.last_update = time.monotonic()
        response.success = True
        response.message = "pose and command counter reset"
        return response

    def tick(self) -> None:
        now = time.monotonic()
        dt = min(now - self.last_update, 0.2)
        self.last_update = now
        self.yaw += self.angular * dt
        self.x += self.linear * math.cos(self.yaw) * dt
        self.y += self.linear * math.sin(self.yaw) * dt
        if not self.feedback_enabled:
            return
        msg = Odometry()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "odom"
        msg.child_frame_id = "base_link"
        msg.pose.pose.position.x = self.x
        msg.pose.pose.position.y = self.y
        msg.pose.pose.orientation.z = math.sin(self.yaw / 2.0)
        msg.pose.pose.orientation.w = math.cos(self.yaw / 2.0)
        msg.twist.twist.linear.x = self.linear
        msg.twist.twist.angular.z = self.angular
        self.odom_pub.publish(msg)


def main() -> None:
    actual = os.environ.get("ROS_DOMAIN_ID")
    if actual != ISOLATED_DOMAIN_ID:
        raise SystemExit(
            "E3 mock UGV refuses to run outside its isolated ROS domain: "
            f"set ROS_DOMAIN_ID={ISOLATED_DOMAIN_ID} (got {actual!r})."
        )
    rclpy.init()
    node = MockUGV()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
