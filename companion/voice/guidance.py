"""ROS bridge between spoken navigation intent and the backpack direction node."""
import threading
import time

import rclpy
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Bool
from std_msgs.msg import UInt8

PATH_VALID_TIMEOUT_S = 1.5
DIRECTION_TIMEOUT_S = 0.6


class RosGuidance:
    def __init__(self, *, read_only=False):
        # RosCamera has initialized rclpy before this bridge is constructed.
        self.node = Node("voice_backpack_guidance")
        self.read_only = read_only
        self.publisher = None if read_only else self.node.create_publisher(
            Bool, "/backpack/guidance_active", 10)
        self.lock = threading.Lock()
        self.path_valid = False
        self.valid_at = 0.0
        self.active = False
        self.active_at = 0.0
        self.direction = None
        self.direction_at = 0.0
        self.node.create_subscription(Bool, "/backpack/path_valid", self._valid, 10)
        self.node.create_subscription(UInt8, "/backpack/direction", self._direction, 10)
        if read_only:
            self.node.create_subscription(Bool, "/backpack/guidance_active", self._active, 10)
        self.timer = None if read_only else self.node.create_timer(0.2, self._publish)
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()

    def _spin(self):
        try:
            self.executor.spin()
        except ExternalShutdownException:
            pass

    def _valid(self, message):
        with self.lock:
            self.path_valid = message.data
            self.valid_at = time.monotonic()

    def _direction(self, message):
        with self.lock:
            self.direction = message.data if message.data in range(5) else None
            self.direction_at = time.monotonic()

    def _active(self, message):
        with self.lock:
            self.active = message.data is True
            self.active_at = time.monotonic()

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            valid = self.path_valid and now - self.valid_at <= PATH_VALID_TIMEOUT_S
            direction = self.direction if now - self.direction_at <= DIRECTION_TIMEOUT_S else None
            active = self.active and (not self.read_only or
                                      now - self.active_at <= DIRECTION_TIMEOUT_S)
            return {"active": active, "path_valid": valid,
                    "direction": direction if active and valid else None}

    def _publish(self):
        if self.read_only:
            return
        with self.lock:
            active = self.active
        self.publisher.publish(Bool(data=active))

    def start(self):
        if self.read_only:
            return "Guidance is controlled by the on-device voice companion."
        with self.lock:
            if not self.path_valid or time.monotonic() - self.valid_at > PATH_VALID_TIMEOUT_S:
                return "I cannot find a current route to the backpack. Please try again."
            self.active = True
        self._publish()
        return "Guidance to the backpack started."

    def stop(self):
        if self.read_only:
            return
        with self.lock:
            self.active = False
        self._publish()

    def close(self):
        self.stop()
        self.executor.shutdown()
        self.thread.join(timeout=2)
        self.executor.remove_node(self.node)
        self.node.destroy_node()
