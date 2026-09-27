#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set +u
source /opt/ros/jazzy/setup.bash
source "${repo_root}/ros_ws/install/setup.bash"
set -u

mapper_prefix="$(ros2 pkg prefix realsense_mapper)"
source_launch="${repo_root}/ros_ws/src/realsense_mapper/launch/mapping.launch.py"
installed_launch="${mapper_prefix}/share/realsense_mapper/launch/mapping.launch.py"
if [[ ! -f "$installed_launch" ]] || ! cmp -s "$source_launch" "$installed_launch"; then
  echo "The installed realsense_mapper launch is older than this checkout." >&2
  echo "Run: source /opt/ros/jazzy/setup.bash && cd ${repo_root}/ros_ws && colcon build --packages-select realsense_mapper" >&2
  exit 1
fi

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

launch_args=(camera_profile:=640x480x15)
mapping_arg_present=false
backpack_arg_present=false
backpack_enabled=true
model_arg_present=false
model_path=""
for arg in "$@"; do
  case "$arg" in
    enable_mapping:=*) mapping_arg_present=true ;;
    enable_backpack_stack:=*)
      backpack_arg_present=true
      backpack_enabled="${arg#enable_backpack_stack:=}"
      ;;
    backpack_model_path:=*)
      model_arg_present=true
      model_path="${arg#backpack_model_path:=}"
      ;;
  esac
done
if [[ "$mapping_arg_present" == false ]]; then
  launch_args+=(enable_mapping:=true)
fi
if [[ "$backpack_arg_present" == false ]]; then
  launch_args+=(enable_backpack_stack:=true)
fi
if [[ "$backpack_enabled" == true || "$backpack_enabled" == 1 ]]; then
  if [[ "$model_arg_present" == false ]]; then
    if [[ -s /opt/models/yolox.onnx ]]; then
      model_path=/opt/models/yolox.onnx
    else
      model_path="${repo_root}/data/models/yolox.onnx"
      if [[ ! -s "$model_path" ]]; then
        mkdir -p "${repo_root}/data/models"
        model_tmp="${model_path}.part.$$"
        echo "Downloading YOLOX model to ${model_path} (one-time setup)..."
        if ! curl -fL --retry 3 \
          'https://huggingface.co/opencv/object_detection_yolox/resolve/main/object_detection_yolox_2022nov.onnx?download=true' \
          -o "$model_tmp"; then
          rm -f "$model_tmp"
          echo "YOLOX download failed. Retry when the Pi has internet, or pass backpack_model_path:=PATH." >&2
          exit 1
        fi
        mv "$model_tmp" "$model_path"
      fi
    fi
    launch_args+=("backpack_model_path:=${model_path}")
  elif [[ ! -s "$model_path" ]]; then
    echo "YOLOX model not found at ${model_path}." >&2
    exit 1
  fi
fi
launch_args+=("$@")

echo "Starting camera, RGB-D odometry, Pi bridge, and requested mapping/backpack services (IMU and ICP opt-in)..."
ros2 launch realsense_mapper hardware.launch.py "${launch_args[@]}" &
mapping_pid=$!
wait -n "$router_pid" "$mapping_pid" || true
echo "A Pi service exited; stopping the remaining services." >&2
