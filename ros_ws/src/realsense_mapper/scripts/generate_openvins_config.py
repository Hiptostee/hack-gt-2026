#!/usr/bin/env python3
"""Capture this D415's CameraInfo and create a device-specific OpenVINS config."""

import os
from pathlib import Path
import shutil

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo


class ConfigGenerator(Node):
    def __init__(self):
        super().__init__("generate_openvins_config")
        default_dir = str(Path.home() / ".ros" / "openvins_d415")
        self.output_dir = Path(self.declare_parameter("output_dir", default_dir).value)
        self.subscription = self.create_subscription(
            CameraInfo, "/camera/color/camera_info", self.on_camera_info,
            qos_profile_sensor_data,
        )
        self.done = False
        self.get_logger().info("Waiting for /camera/color/camera_info...")

    def on_camera_info(self, msg):
        if self.done:
            return
        if msg.width <= 0 or msg.height <= 0 or msg.k[0] <= 0 or msg.k[4] <= 0:
            self.get_logger().error("CameraInfo has invalid resolution or focal length")
            return
        if msg.distortion_model != "plumb_bob" or len(msg.d) < 4:
            self.get_logger().error(
                f"Expected plumb_bob distortion with >=4 coefficients; got {msg.distortion_model}"
            )
            return
        if len(msg.d) > 4 and abs(msg.d[4]) > 0.01:
            self.get_logger().warn(
                f"CameraInfo k3={msg.d[4]:.5f}; OpenVINS radtan uses only k1,k2,p1,p2. "
                "Use a proper Kalibr calibration if accuracy is poor."
            )

        self.output_dir.mkdir(parents=True, exist_ok=True)
        template = Path(get_package_share_directory("realsense_mapper")) / "config" / "openvins"
        for name in ("estimator_config.yaml", "kalibr_imu_chain.yaml"):
            shutil.copyfile(template / name, self.output_dir / name)

        # T_cam_imu maps IMU coordinates to the color optical frame. The rig's
        # IMU X is up, Y left, Z backward; color optical X is right, Y down,
        # Z forward. The IMU is approximately 4 cm left of the color camera.
        config = f"""%YAML:1.0
# Initial measured mounting estimate, NOT a camera-IMU Kalibr calibration.
cam0:
  T_cam_imu:
    - [0.0, -1.0, 0.0, -0.04]
    - [-1.0, 0.0, 0.0, 0.0]
    - [0.0, 0.0, -1.0, 0.0]
    - [0.0, 0.0, 0.0, 1.0]
  cam_overlaps: []
  camera_model: pinhole
  distortion_coeffs: [{msg.d[0]:.12g}, {msg.d[1]:.12g}, {msg.d[2]:.12g}, {msg.d[3]:.12g}]
  distortion_model: radtan
  intrinsics: [{msg.k[0]:.12g}, {msg.k[4]:.12g}, {msg.k[2]:.12g}, {msg.k[5]:.12g}]
  resolution: [{msg.width}, {msg.height}]
  rostopic: /camera/color/image_raw
  timeshift_cam_imu: 0.0
"""
        target = self.output_dir / "kalibr_imucam_chain.yaml"
        temporary = target.with_suffix(".yaml.tmp")
        temporary.write_text(config)
        os.replace(temporary, target)
        self.done = True
        self.get_logger().info(f"Wrote OpenVINS configuration to {self.output_dir}")
        self.get_logger().warn(
            "Extrinsics, time offset and MPU noise are provisional. Calibrate for accurate SLAM."
        )


def main():
    rclpy.init()
    node = ConfigGenerator()
    try:
        while rclpy.ok() and not node.done:
            rclpy.spin_once(node, timeout_sec=1.0)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
