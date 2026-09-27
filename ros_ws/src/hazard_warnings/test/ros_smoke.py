#!/usr/bin/env python3
"""Synthetic ROS pipeline test. No camera, speaker or wearable validation.

Usage: python3 ros_smoke.py HAZARD_EXECUTABLE DIRECTION_EXECUTABLE
Run after sourcing ROS and the built workspace; repository must be on PYTHONPATH.
"""
import json
import struct
import subprocess
import sys
import time

import rclpy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Path
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image, Imu
from std_msgs.msg import Bool, String, UInt8
from companion.voice.hazard_state import HazardState


def main():
    hazard_exe, direction_exe = sys.argv[1:]
    rclpy.init()
    node = Node("hazard_smoke")
    state = HazardState()
    snapshots, commands = [], []
    def snapshot(message):
        value = json.loads(message.data)
        snapshots.append(value)
        if len(snapshots) > 100:
            del snapshots[:-100]
        state.receive(message.data, node.get_clock().now().nanoseconds, time.monotonic())
    node.create_subscription(String, "/hazard_warning", snapshot, 1)
    node.create_subscription(UInt8, "/backpack/direction", lambda m: commands.append(m.data), 10)
    depth_pub = node.create_publisher(Image, "/camera/aligned_depth_to_color/image_raw", qos_profile_sensor_data)
    info_pub = node.create_publisher(CameraInfo, "/camera/color/camera_info", qos_profile_sensor_data)
    imu_pub = node.create_publisher(Imu, "/imu/data", qos_profile_sensor_data)
    gate = node.create_publisher(Bool, "/hazard/guidance_permitted", 1)
    active = node.create_publisher(Bool, "/backpack/guidance_active", 10)
    valid = node.create_publisher(Bool, "/backpack/path_valid", 10)
    paths = node.create_publisher(Path, "/backpack/path", 10)
    path = Path()
    for x in (0.0, 3.0):
        p = PoseStamped(); p.pose.position.x = x; p.pose.orientation.w = 1.0
        path.poses.append(p)

    def drive(seconds=1.0, distance=3.0, encoding="16UC1", invalid=False,
              send_depth=True, tilt=False, gate_alive=True, stale=False):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            stamp = node.get_clock().now().to_msg()
            if stale:
                stamp.sec -= 2
            info = CameraInfo(); info.header.stamp = stamp
            info.header.frame_id = "camera_color_optical_frame"
            info.width = 80; info.height = 60
            info.k = [60.0, 0.0, 40.0, 0.0, 60.0, 30.0, 0.0, 0.0, 1.0]
            info.distortion_model = "plumb_bob"; info.d = [0.0] * 5
            imu = Imu(); imu.header.stamp = stamp; imu.header.frame_id = "imu_link"
            imu.linear_acceleration.z = 0.0 if tilt else 9.81
            imu.linear_acceleration.x = 9.81 if tilt else 0.0
            image = Image(); image.header = info.header
            image.width = 80; image.height = 60; image.encoding = encoding
            image.step = 80 * (2 if encoding == "16UC1" else 4)
            values = [3.0] * 4800
            for y in range(8, 28):
                for x in range(37, 44):
                    values[y * 80 + x] = distance
            if invalid:
                values = [0.0] * 4800
            image.data = (struct.pack("<4800H", *(int(v * 1000) for v in values))
                          if encoding == "16UC1" else struct.pack("<4800f", *values))
            info_pub.publish(info); imu_pub.publish(imu)
            if send_depth:
                depth_pub.publish(image)
            active.publish(Bool(data=True)); valid.publish(Bool(data=True)); paths.publish(path)
            rclpy.spin_once(node, timeout_sec=0.01)
            for _ in range(8):
                rclpy.spin_once(node, timeout_sec=0)
            permitted, _ = state.poll(node.get_clock().now().nanoseconds, time.monotonic())
            if gate_alive:
                gate.publish(Bool(data=permitted))
            time.sleep(0.04)

    def check(condition, description):
        if not condition:
            raise AssertionError(description + " latest=" + repr(snapshots[-1:]))
        print("PASS", description, flush=True)

    processes = []
    try:
        # Defaults must stay unavailable even with synthetically perfect inputs.
        uncalibrated = subprocess.Popen([hazard_exe]); processes.append(uncalibrated)
        drive(2.0)
        check(snapshots and snapshots[-1]["health"]["body_pose"] == "unavailable", "uncalibrated mount fails closed")
        uncalibrated.terminate(); uncalibrated.wait(timeout=5)
        params = {"calibrated": "true", "coverage_verified": "true", "body_half_width_m": "0.25",
                  "torso_min_m": "0.8", "head_min_m": "1.4", "head_max_m": "1.9",
                  "camera_to_body_xyz": "[0.0, 0.0, 1.3]",
                  "camera_to_body_rpy": "[-1.5707963267948966, 0.0, -1.5707963267948966]"}
        args = [hazard_exe, "--ros-args"]
        for key, value in params.items():
            args += ["-p", f"{key}:={value}"]
        producer = subprocess.Popen(args); processes.append(producer)
        direction = subprocess.Popen([direction_exe]); processes.append(direction)
        drive(2.0)
        check(snapshots[-1]["health"]["depth"] == "ok" and not snapshots[-1]["events"], "observed background, no supported obstacle")
        check(bool(commands), "fresh healthy audio/sensing lease permits direction output")
        drive(0.8, distance=1.2)
        check(any(e["severity"] == "caution" for e in snapshots[-1]["events"]), "two-frame caution geometry")
        drive(0.8, distance=0.6, encoding="32FC1")
        check(any(e["severity"] == "urgent" for e in snapshots[-1]["events"]), "32FC1 urgent geometry")
        commands.clear(); drive(0.7, distance=0.6)
        check(not commands, "urgent obstacle inhibits movement output")
        drive(0.8, invalid=True)
        check(snapshots[-1]["health"]["depth"] == "degraded" and not snapshots[-1]["events"], "depth holes are degraded sensing, not clearance")
        drive(0.8, tilt=True)
        check(snapshots[-1]["health"]["body_pose"] == "unavailable", "mount tilt disables body-height inference")
        drive(0.8, stale=True)
        check(snapshots[-1]["health"]["depth"] == "unavailable", "old capture stamps cannot refresh sensing")
        drive(0.8); drive(0.8, send_depth=False)
        check(snapshots[-1]["health"]["depth"] == "unavailable", "camera stops but fault heartbeat continues")
        drive(0.8)
        producer.terminate(); producer.wait(timeout=5)
        commands.clear(); drive(1.0)
        commands.clear(); drive(0.4)
        check(not commands and not state.poll(node.get_clock().now().nanoseconds, time.monotonic())[0], "producer death expires consumer health")
        gate.publish(Bool(data=True)); drive(0.8, gate_alive=False)
        commands.clear(); drive(0.4, gate_alive=False)
        check(not commands, "audio/companion lease loss inhibits direction independently")
        print("Synthetic ROS hazard pipeline passed; no hardware claims.")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate(); process.wait(timeout=5)
        node.destroy_node(); rclpy.shutdown()


if __name__ == "__main__":
    main()
