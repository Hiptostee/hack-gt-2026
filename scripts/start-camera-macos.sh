#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
host_build_dir="${project_dir}/host_streamer/build-local"
streamer="${host_build_dir}/realsense_host_streamer"

cd "${project_dir}"
cmake -S host_streamer -B "${host_build_dir}" -DCMAKE_BUILD_TYPE=Release
cmake --build "${host_build_dir}" --parallel

echo "macOS requires administrator access to claim the D415 video interfaces."
sudo -v

while IFS= read -r listener_pid; do
  [[ -n "${listener_pid}" ]] || continue
  echo "Stopping stale camera bridge on TCP 50051 (PID ${listener_pid})."
  sudo kill "${listener_pid}" 2>/dev/null || true
  sleep 1
  sudo kill -9 "${listener_pid}" 2>/dev/null || true
done < <(sudo lsof -t -nP -iTCP:50051 -sTCP:LISTEN 2>/dev/null || true)

sleep 2
echo "Starting the macOS D415 bridge. Leave this terminal open; Ctrl-C stops it."
echo "The Docker Desktop stack may be started before or after this command."
exec sudo "${streamer}" 50051
