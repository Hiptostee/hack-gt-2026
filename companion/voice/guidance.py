"""ROS bridge between spoken navigation intent and the backpack direction node."""
import threading
import time

import rclpy
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Bool
try:
    from std_msgs.msg import UInt8
except ImportError:
    UInt8 = None

PATH_VALID_TIMEOUT_S = 1.5
DIRECTION_TIMEOUT_S = 0.6
DIRECTION_NAMES = {0: "forward", 1: "left", 2: "right", 3: "stop"}
TACTILE_FLAGS = {0: 0x01, 1: 0x02, 2: 0x04, 3: 0x00}
TACTILE_NAMES = {0: "front", 1: "left", 2: "right", 3: "neutral"}


class RosGuidance:
    def __init__(self):
        # RosCamera has initialized rclpy before this bridge is constructed.
        self.node = Node("voice_backpack_guidance")
        self.publisher = self.node.create_publisher(Bool, "/backpack/guidance_active", 10)
        self.lock = threading.Lock()
        self.path_valid = False
        self.valid_at = 0.0
        self.active = False
        self.hazard_permitted = False
        self.hazard_at = 0.0
        self.direction = None
        self.direction_at = 0.0
        self.node.create_subscription(Bool, "/hazard/guidance_permitted", self._hazard_gate, 1)
        self.node.create_subscription(Bool, "/backpack/path_valid", self._valid, 10)
        if UInt8 is not None:
            self.node.create_subscription(UInt8, "/backpack/direction", self._direction, 10)
        self.timer = self.node.create_timer(0.2, self._publish)
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

    def _hazard_gate(self, message):
        with self.lock:
            self.hazard_permitted = message.data
            self.hazard_at = time.monotonic()

    def _direction(self, message):
        with self.lock:
            self.direction = message.data if message.data in range(4) else None
            self.direction_at = time.monotonic()

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            valid = self.path_valid and (now - self.valid_at <= PATH_VALID_TIMEOUT_S)
            hazard = self.hazard_permitted and (now - self.hazard_at <= 0.5)
            dir_fresh = (now - self.direction_at <= DIRECTION_TIMEOUT_S) if self.direction_at else False
            raw_dir = self.direction if dir_fresh else None
            active_dir = raw_dir if (self.active and valid and hazard) else None

            flags = TACTILE_FLAGS.get(active_dir, 0) if active_dir is not None else 0
            dir_label = DIRECTION_NAMES.get(active_dir)
            tactile_label = TACTILE_NAMES.get(active_dir, "neutral")

            return {
                "active": self.active,
                "path_valid": valid,
                "hazard_permitted": hazard,
                "direction": active_dir,
                "direction_label": dir_label,
                "tactile_flags": flags,
                "tactile_label": tactile_label,
                "target_label": "backpack" if self.active else None,
                "distance_m": None,
                "bearing_deg": None,
                "age_s": round(now - self.direction_at, 3) if self.direction_at else None,
            }

    def _publish(self):
        with self.lock:
            active = self.active
        self.publisher.publish(Bool(data=active))

    def start(self):
        with self.lock:
            if not self.hazard_permitted or time.monotonic() - self.hazard_at >= 0.5:
                return "Guidance is unavailable while hazard sensing or audio is unavailable, or an urgent obstacle is present."
            if not self.path_valid or time.monotonic() - self.valid_at > PATH_VALID_TIMEOUT_S:
                return "I cannot find a current route to the backpack. Please try again."
            self.active = True
        self._publish()
        return "Guidance to the backpack started."

    def stop(self):
        with self.lock:
            self.active = False
        self._publish()

    def close(self):
        self.stop()
        self.executor.shutdown()
        self.thread.join(timeout=2)
        self.executor.remove_node(self.node)
        self.node.destroy_node()
