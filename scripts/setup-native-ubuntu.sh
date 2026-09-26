#!/usr/bin/env bash
set -euo pipefail

if [[ ! -r /etc/os-release ]]; then
  echo "This installer supports Ubuntu 24.04 only." >&2
  exit 1
fi

source /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "24.04" ]]; then
  echo "Expected Ubuntu 24.04; found ${PRETTY_NAME:-an unknown OS}." >&2
  exit 1
fi

project_dir="$(cd "$(dirname "$0")/.." && pwd)"

sudo apt-get update
sudo apt-get install -y ca-certificates curl locales software-properties-common
sudo locale-gen en_US en_US.UTF-8
sudo update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8
sudo add-apt-repository -y universe

sudo curl -fsSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
  -o /usr/share/keyrings/ros-archive-keyring.gpg
repo_arch="$(dpkg --print-architecture)"
repo_codename="$(. /etc/os-release && echo "$UBUNTU_CODENAME")"
echo "deb [arch=${repo_arch} signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu ${repo_codename} main" \
  | sudo tee /etc/apt/sources.list.d/ros2.list >/dev/null

sudo apt-get update
sudo apt-get install -y \
  build-essential \
  git \
  i2c-tools \
  libboost-all-dev \
  libceres-dev \
  libeigen3-dev \
  python3-colcon-common-extensions \
  python3-rosdep \
  ros-jazzy-imu-filter-madgwick \
  ros-jazzy-ros-base \
  ros-jazzy-realsense2-camera \
  ros-jazzy-realsense2-description \
  ros-jazzy-rtabmap-ros \
  ros-jazzy-rmw-zenoh-cpp \
  ros-jazzy-robot-localization

sudo usermod -aG i2c "${USER}"

if [[ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]]; then
  sudo rosdep init
fi
rosdep update

cd "${project_dir}/ros_ws"
if [[ ! -d src/open_vins ]]; then
  # Jazzy/Ubuntu 24.04 support is currently in this fork (upstream PR #500).
  git clone --depth 1 https://github.com/nathanshankar/open_vins.git src/open_vins
elif [[ "$(git -C src/open_vins remote get-url origin)" != *nathanshankar/open_vins* ]]; then
  echo "Existing src/open_vins is not the Jazzy-compatible fork; inspect it before building." >&2
  exit 1
fi
rosdep install --from-paths src --ignore-src --rosdistro jazzy -y
set +u
source /opt/ros/jazzy/setup.bash
set -u
colcon build --symlink-install --packages-up-to ov_msckf realsense_mapper \
  --cmake-args -DCMAKE_BUILD_TYPE=Release -DENABLE_ARUCO_TAGS=OFF

echo
echo "Native ROS 2 workspace built successfully."
echo "Run: source ${project_dir}/ros_ws/install/setup.bash"
echo "Then: ros2 launch realsense_mapper hardware.launch.py"
