#!/usr/bin/env bash
set -euo pipefail

# ROS-generated environment hooks may read unset variables while initializing.
# Keep strict mode for this script, but temporarily disable nounset while sourcing.
set +u
source /opt/ros/jazzy/setup.bash
source /workspace/ros_ws/install/setup.bash
set -u

mkdir -p /root/.vnc /root/.config/xfce4 /data
rm -f /tmp/.X1-lock /tmp/.X11-unix/X1

Xvnc :1 \
  -geometry "${VNC_RESOLUTION:-1600x1000}" \
  -depth 24 \
  -localhost no \
  -SecurityTypes None \
  -rfbport 5901 \
  > /tmp/xvnc.log 2>&1 &
VNC_PID=$!

for _ in $(seq 1 30); do
  [[ -S /tmp/.X11-unix/X1 ]] && break
  sleep 0.2
done

dbus-launch --exit-with-session startxfce4 > /tmp/xfce.log 2>&1 &
websockify --web=/usr/share/novnc 6080 localhost:5901 > /tmp/novnc.log 2>&1 &

MAPPING_PID=""
if [[ "${VIEWER_ONLY:-0}" != "1" ]]; then
  ros2 launch realsense_mapper "${ROS_LAUNCH_FILE:-mac_test.launch.py}" \
    > /tmp/mapping.log 2>&1 &
  MAPPING_PID=$!
fi

sleep 4
rviz2 -d /workspace/ros_ws/install/realsense_mapper/share/realsense_mapper/config/mapper.rviz \
  > /tmp/rviz.log 2>&1 &
RVIZ_PID=$!

cleanup() {
  kill "${RVIZ_PID}" "${VNC_PID}" 2>/dev/null || true
  if [[ -n "${MAPPING_PID}" ]]; then
    kill "${MAPPING_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if [[ "${VIEWER_ONLY:-0}" == "1" ]]; then
  echo "RViz viewer started. noVNC: http://localhost:6080/vnc.html"
  echo "Logs: docker exec realsense-rviz-viewer tail -f /tmp/rviz.log"
  wait -n "${RVIZ_PID}" "${VNC_PID}"
else
  echo "Mapping started. noVNC: http://localhost:6080/vnc.html"
  echo "Logs: docker exec realsense-rtabmap-poc tail -f /tmp/mapping.log"
  wait -n "${MAPPING_PID}" "${RVIZ_PID}" "${VNC_PID}"
fi

echo "A required process exited. Recent logs:" >&2
tail -n 100 /tmp/mapping.log /tmp/rviz.log /tmp/xvnc.log >&2 2>/dev/null || true
exit 1
