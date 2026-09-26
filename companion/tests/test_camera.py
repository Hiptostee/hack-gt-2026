"""Exercise bridge freshness without ROS, NumPy, a camera, or cloud credentials."""
import importlib
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from companion.errors import AppError


class CameraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        modules = {
            "cv2": Mock(), "numpy": Mock(), "rclpy": Mock(),
            "rclpy.node": SimpleNamespace(Node=Mock()),
            "rclpy.executors": SimpleNamespace(ExternalShutdownException=RuntimeError),
            "rclpy.qos": SimpleNamespace(qos_profile_sensor_data=object()),
            "sensor_msgs": Mock(), "sensor_msgs.msg": SimpleNamespace(Image=Mock())}
        with patch.dict(sys.modules, modules):
            cls.bridge = importlib.import_module("companion.ros_camera")

    def setUp(self):
        self.camera = self.bridge.RosCamera.__new__(self.bridge.RosCamera)
        self.camera.lock = threading.Lock()
        self.camera.frame = SimpleNamespace(encoding="rgb8", width=2, height=2,
                                            step=6, data=b"0" * 12)
        self.camera.received = time.monotonic()
        self.camera.age_at_receipt = 0

    def test_stale_missing_and_future_frames_are_rejected(self):
        for age in (3, float("inf"), -1):
            self.camera.age_at_receipt = age
            self.assertEqual(self.camera.status(), "No recent camera frame")
            with self.assertRaises(AppError) as caught:
                self.camera.capture()
            self.assertEqual(caught.exception.status, 503)

    def test_recent_receipt_cannot_make_old_capture_fresh(self):
        self.camera.node = Mock()
        self.camera.node.get_clock().now().nanoseconds = 100 * 1_000_000_000
        message = SimpleNamespace(header=SimpleNamespace(stamp=SimpleNamespace(sec=90, nanosec=0)))
        self.camera.receive(message)
        self.assertFalse(self.camera.fresh())

    def test_capture_encodes_only_on_request(self):
        self.bridge.cv2.imencode.reset_mock()
        self.bridge.cv2.imencode.return_value = (True, SimpleNamespace(tobytes=lambda: b"jpeg"))
        self.assertEqual(self.camera.status(), "Ready")
        self.bridge.cv2.imencode.assert_not_called()
        data, stamp = self.camera.capture()
        self.assertEqual(data, b"jpeg")
        self.assertLess(abs(stamp - time.time()), 1)
        self.bridge.cv2.imencode.assert_called_once()

    def test_unsupported_encoding_is_reported(self):
        self.camera.frame.encoding = "16UC1"
        with self.assertRaises(AppError):
            self.camera.capture()
