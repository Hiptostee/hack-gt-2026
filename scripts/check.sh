#!/usr/bin/env bash
set -euo pipefail

container="realsense-rtabmap-poc"
docker exec "${container}" bash -lc '
  source /opt/ros/jazzy/setup.bash
  source /workspace/ros_ws/install/setup.bash
  echo "=== Camera ==="
  ros2 topic hz /camera/color/image_raw --window 10 & pid=$!
  sleep 4; kill $pid 2>/dev/null || true
  echo "=== RTAB-Map outputs ==="
  ros2 topic info /rtabmap/cloud_map
  ros2 topic info /rtabmap/map
  echo "=== Odometry stabilization ==="
  ros2 topic hz /visual_odom --window 10 & pid=$!
  sleep 3; kill $pid 2>/dev/null || true
  ros2 topic hz /odometry/filtered --window 10 & pid=$!
  sleep 3; kill $pid 2>/dev/null || true
  echo "=== Backpack planner ==="
  ros2 topic info /backpack/path
  ros2 topic info /backpack/planner_image
  ros2 topic echo /backpack/path_valid --once
  echo "=== TF ==="
  ros2 run tf2_ros tf2_echo map camera_link & pid=$!
  sleep 3; kill $pid 2>/dev/null || true
'
