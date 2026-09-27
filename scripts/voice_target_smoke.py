#!/usr/bin/env python3
"""Planner smoke test for spoken targets (companion/navigate/specs.md), no camera.

Feeds backpack_path_planner_node a free map, flat 3 m depth, camera intrinsics,
TF and visual odometry, then sends /target/detection requests and checks the
/target/status replies. Run inside the ROS container with the planner running:

    ros2 run realsense_mapper backpack_path_planner_node &
    python3 scripts/voice_target_smoke.py
"""
import json
import sys
import threading
import time

import rclpy
from geometry_msgs.msg import PointStamped, TransformStamped
from nav_msgs.msg import OccupancyGrid, Odometry
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image
from std_msgs.msg import Empty, String
from tf2_ros import StaticTransformBroadcaster, TransformBroadcaster

WIDTH, HEIGHT, FX, CX, CY, DEPTH_MM = 640, 480, 500.0, 320.0, 240.0, 3000
OPTICAL = "camera_color_optical_frame"


class Feeder(Node):
    def __init__(self):
        super().__init__("voice_target_smoke")
        self.camera_xy = (0.0, 0.0)
        self.odometry = True
        self.stamps = []
        self.statuses = []
        self.goal = None
        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                             reliability=ReliabilityPolicy.RELIABLE)
        self.map_pub = self.create_publisher(OccupancyGrid, "/rtabmap/map", latched)
        self.info_pub = self.create_publisher(CameraInfo, "/camera/color/camera_info",
                                              qos_profile_sensor_data)
        self.depth_pub = self.create_publisher(Image, "/camera/aligned_depth_to_color/image_raw",
                                               qos_profile_sensor_data)
        self.odom_pub = self.create_publisher(Odometry, "/visual_odom_valid", qos_profile_sensor_data)
        self.target_pub = self.create_publisher(String, "/target/detection", 10)
        self.clear_pub = self.create_publisher(Empty, "/target/clear", 10)
        self.yolo_pub = self.create_publisher(String, "/yolo/black_backpack", 10)
        self.create_subscription(String, "/target/status",
                                 lambda m: self.statuses.append(json.loads(m.data)), 10)
        self.create_subscription(PointStamped, "/backpack/goal",
                                 lambda m: setattr(self, "goal", (m.point.x, m.point.y)), 10)
        self.tf = TransformBroadcaster(self)
        self.static_tf = StaticTransformBroadcaster(self)
        self.static_tf.sendTransform([
            self.transform("map", "odom", 0.0, 0.0, (0.0, 0.0, 0.0, 1.0)),
            self.transform("camera_link", OPTICAL, 0.0, 0.0, (-0.5, 0.5, -0.5, 0.5))])
        self.publish_map()
        self.create_timer(0.05, self.tick)

    def transform(self, parent, child, x, y, rotation):
        t = TransformStamped()
        t.header.stamp = self.get_clock().now().to_msg()
        t.header.frame_id, t.child_frame_id = parent, child
        t.transform.translation.x, t.transform.translation.y = x, y
        (t.transform.rotation.x, t.transform.rotation.y,
         t.transform.rotation.z, t.transform.rotation.w) = rotation
        return t

    def publish_map(self):
        grid = OccupancyGrid()
        grid.header.frame_id = "map"
        grid.info.resolution, grid.info.width, grid.info.height = 0.05, 200, 200
        grid.info.origin.position.x = grid.info.origin.position.y = -5.0
        grid.info.origin.orientation.w = 1.0
        grid.data = [0] * (200 * 200)
        self.map_pub.publish(grid)

    def tick(self):
        now = self.get_clock().now().to_msg()
        self.tf.sendTransform(self.transform("odom", "camera_link", *self.camera_xy,
                                             (0.0, 0.0, 0.0, 1.0)))
        if self.odometry:
            odom = Odometry()
            odom.header.stamp = now
            self.odom_pub.publish(odom)
        info = CameraInfo()
        info.header.stamp, info.header.frame_id = now, OPTICAL
        info.width, info.height = WIDTH, HEIGHT
        info.k = [FX, 0.0, CX, 0.0, FX, CY, 0.0, 0.0, 1.0]
        self.info_pub.publish(info)
        depth = Image()
        depth.header.stamp, depth.header.frame_id = now, OPTICAL
        depth.width, depth.height, depth.encoding = WIDTH, HEIGHT, "16UC1"
        depth.step = WIDTH * 2
        depth.data = DEPTH_MM.to_bytes(2, "little") * (WIDTH * HEIGHT)
        self.depth_pub.publish(depth)
        self.stamps.append((time.monotonic(), now))

    def stamp_from(self, seconds_ago):
        cutoff = time.monotonic() - seconds_ago
        return min(self.stamps, key=lambda s: abs(s[0] - cutoff))[1]

    def request(self, request_id, stamp, u=520, v=240):
        box = {"label": "water fountain", "confidence": 1, "x": u - 20, "y": v - 20,
               "width": 40, "height": 40}
        message = (f'{{"stamp":{stamp.sec}.{stamp.nanosec:09d},"request_id":"{request_id}",'
                   f'"detections":[{json.dumps(box, separators=(",", ":"))}]}}')
        self.target_pub.publish(String(data=message))

    def wait_for(self, request_id, state=None, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for status in self.statuses:
                if status["request_id"] == request_id and (state is None or status["state"] == state):
                    return status
            time.sleep(0.05)
        return None


def main():
    rclpy.init()
    node = Feeder()
    threading.Thread(target=rclpy.spin, args=(node,), daemon=True).start()
    failures = []

    def check(name, condition, detail=""):
        print(("PASS " if condition else "FAIL ") + name + (f"  {detail}" if detail else ""))
        if not condition:
            failures.append(name)

    time.sleep(2.0)

    # Camera at the origin captures the frame, then walks 1 m forward before the
    # box arrives. The goal must come from the capture-time pose.
    captured = node.stamp_from(0.0)
    node.camera_xy = (1.0, 0.0)
    time.sleep(1.0)
    node.request("walk", captured)
    status = node.wait_for("walk")
    check("ok after walking during the request", status and status["state"] == "ok", str(status))
    if status and status["state"] == "ok":
        check("distance from the current pose", abs(status["distance_m"] - 2.33) < 0.1,
              str(status["distance_m"]))
        check("bearing right is negative", abs(status["bearing_deg"] + 31.0) < 2.0,
              str(status["bearing_deg"]))
    time.sleep(0.3)
    check("goal at the capture-time world position",
          node.goal and abs(node.goal[0] - 3.0) < 0.1 and abs(node.goal[1] + 1.2) < 0.1,
          str(node.goal))

    node.yolo_pub.publish(String(data=(
        f'{{"stamp":{captured.sec}.{captured.nanosec:09d},"detections":[{{"label":"black backpack",'
        '"confidence":0.9,"x":100,"y":200,"width":40,"height":40,"dark_ratio":0.9}]}')))
    time.sleep(0.8)
    check("backpack detection cannot steal the target",
          node.goal and abs(node.goal[1] + 1.2) < 0.1, str(node.goal))

    node.odometry = False
    check("tracking loss is reported", node.wait_for("walk", "tracking_lost") is not None)
    node.odometry = True
    check("tracking recovery is reported", node.wait_for("walk", "tracking_restored") is not None)

    node.camera_xy = (2.8, -0.8)
    check("arrival is reported", node.wait_for("walk", "arrived") is not None)

    node.request("stale", (node.get_clock().now() - Duration(seconds=9)).to_msg())
    status = node.wait_for("stale")
    check("stale frame is refused", status and status["state"] == "stale", str(status))

    # Straight ahead at 3 m, but the wearer is already 0.5 m from it on arrival.
    node.camera_xy = (0.0, 0.0)
    time.sleep(0.3)
    captured = node.stamp_from(0.0)
    node.camera_xy = (2.5, 0.0)
    time.sleep(0.3)
    node.request("near", captured, u=320)
    status = node.wait_for("near")
    check("target inside the standoff is near, not guided",
          status and status["state"] == "near", str(status))

    node.camera_xy = (0.0, 0.0)
    time.sleep(0.3)
    node.request("ahead", node.stamp_from(0.1), u=320)
    status = node.wait_for("ahead")
    check("straight ahead", status and status["state"] == "ok"
          and abs(status["bearing_deg"]) < 2.0, str(status))

    node.clear_pub.publish(Empty())
    check("clear is acknowledged", node.wait_for("ahead", "cleared") is not None)

    rclpy.shutdown()
    print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
