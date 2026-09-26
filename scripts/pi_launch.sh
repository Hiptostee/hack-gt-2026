#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set +u
source /opt/ros/jazzy/setup.bash
source "${repo_root}/ros_ws/install/setup.bash"
set -u

export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-42}"
export RMW_IMPLEMENTATION=rmw_zenoh_cpp
unset ROS_DISCOVERY_SERVER ZENOH_CONFIG_OVERRIDE

if python3 -c 'import socket; s = socket.socket(); result = s.connect_ex(("127.0.0.1", 8081)); s.close(); raise SystemExit(result != 0)'; then
  echo "Port 8081 is already in use by a Pi bridge. Stop that old bridge before starting this launch." >&2
  echo "Find it with: ss -ltnp '( sport = :8081 )'" >&2
  exit 1
fi

router_pid=""
mapping_pid=""
cleanup() {
  trap - EXIT HUP INT TERM
  if [[ -n "$mapping_pid" ]]; then
    kill -TERM "$mapping_pid" 2>/dev/null || true
    wait "$mapping_pid" 2>/dev/null || true
  fi
  if [[ -n "$router_pid" ]]; then
    kill -TERM "$router_pid" 2>/dev/null || true
    wait "$router_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT HUP INT TERM

echo "Starting Zenoh router on port 7447..."
ZENOH_CONFIG_OVERRIDE='listen/endpoints=["tcp/0.0.0.0:7447"]' \
  ros2 run rmw_zenoh_cpp rmw_zenohd &
router_pid=$!
sleep 2
if ! kill -0 "$router_pid" 2>/dev/null; then
  echo "Zenoh router exited. Stop any existing router on port 7447 and retry." >&2
  exit 1
fi

echo "Starting mapping, backpack guidance, and Pi bridge..."
ros2 launch realsense_mapper hardware.launch.py \
  camera_profile:=640x480x15 \
  enable_backpack_stack:=true \
  enable_imu:=true \
  enable_icp:=true \
  "$@" &
mapping_pid=$!
wait -n "$router_pid" "$mapping_pid" || true
echo "A Pi service exited; stopping the remaining services." >&2
