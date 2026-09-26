"""ROS is optional: import this module only for --ros deployments."""
import threading
import time

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image

from companion.errors import AppError


class RosCamera:
    def __init__(self, topic):
        rclpy.init(args=[])
        self.node = Node("scene_companion_camera")
        self.lock = threading.Lock()
        self.frame = None
        self.received = 0
        self.age_at_receipt = float("inf")
        self.subscription = self.node.create_subscription(Image, topic, self.receive,
                                                         qos_profile_sensor_data)
        self.thread = threading.Thread(target=self.spin, daemon=True)
        self.thread.start()

    def spin(self):
        try:
            rclpy.spin(self.node)
        except ExternalShutdownException:
            pass

    def receive(self, message):
        stamp = message.header.stamp.sec + message.header.stamp.nanosec / 1e9
        now_ros = self.node.get_clock().now().nanoseconds / 1e9
        age = now_ros - stamp if stamp > 0 else float("inf")
        with self.lock:
            self.frame = message  # Retain only the most recent frame; no continuous encoding.
            self.received = time.monotonic()
            self.age_at_receipt = age

    def fresh(self):
        return (self.frame is not None and self.age_at_receipt >= -0.1 and
                self.age_at_receipt + time.monotonic() - self.received <= 2)

    def status(self):
        with self.lock:
            return "Ready" if self.fresh() else "No recent camera frame"

    def capture(self):
        with self.lock:
            if not self.fresh():
                raise AppError("No recent camera frame. Check the camera connection.", 503)
            message = self.frame
            age = max(0, self.age_at_receipt + time.monotonic() - self.received)
        if message.encoding not in ("rgb8", "bgr8"):
            raise AppError("Camera must publish RGB8 or BGR8 images.", 503)
        pixels = np.ndarray((message.height, message.width, 3), dtype=np.uint8,
                            buffer=message.data, strides=(message.step, 3, 1))
        if message.encoding == "rgb8":
            pixels = cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR)
        if max(message.width, message.height) > 1600:
            scale = 1600 / max(message.width, message.height)
            pixels = cv2.resize(pixels, None, fx=scale, fy=scale)
        success, encoded = cv2.imencode(".jpg", pixels, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not success:
            raise AppError("Could not encode camera image.", 503)
        return encoded.tobytes(), time.time() - age

    def close(self):
        rclpy.shutdown()
        self.thread.join(timeout=2)
        self.node.destroy_node()
