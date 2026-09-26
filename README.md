# RealSense D415 + ROS 2 RTAB-Map

This repository is a standard ROS 2 Jazzy workspace plus a reproducible Ubuntu
24.04 test container for Docker Desktop on macOS. Hardware deployment runs
natively on Ubuntu 24.04 or a Raspberry Pi.

The stack includes:

- Intel RealSense D415 driver
- RTAB-Map RGB-D visual odometry and SLAM (C++)
- accumulated colored point cloud on `/rtabmap/cloud_map`
- 2D occupancy grid on `/rtabmap/map`
- YOLOX black-backpack detection on `/yolo/annotated_image`
- heading-aware A* route to a detected backpack on `/backpack/path`
- 60 Hz EKF stabilization between visual odometry updates
- RViz inside an XFCE desktop served by TigerVNC and noVNC
- a small C++ status node that verifies both map outputs are arriving

## Why RTAB-Map

RTAB-Map is the standard ROS 2 RGB-D SLAM choice for this sensor. It performs
visual odometry, loop closure, graph optimization, 3D cloud assembly, and 2D
occupancy-grid generation in one maintained ROS package. The wearable camera
uses 6-DoF visual odometry. The D415 has no IMU, so the camera should still
begin approximately level for a useful 2D occupancy grid.

## Quick start

Clone the repository, copy the example settings, and build once:

```bash
git clone <repository-url> realsense_rtabmap_poc
cd realsense_rtabmap_poc
cp .env.example .env
make build
```

The image uses the official `ros:jazzy-ros-base-noble` base. That is Ubuntu
24.04 with ROS 2 already installed and is simpler and more repeatable than
installing ROS by hand in the Dockerfile.

### macOS test environment (Docker Desktop)

Docker Desktop cannot directly pass this UVC depth camera into its Linux VM.
The included macOS launcher therefore captures aligned RGB-D frames with native
Librealsense and sends them to a C++ ROS 2 receiver inside the container.

Install the native dependency once:

```bash
brew install cmake librealsense
```

Create the Compose application once:

```bash
docker compose up -d --build
```

After that, `realsense_rtabmap_poc` appears in Docker Desktop and can be started
and stopped there without a project script. The container automatically runs
the ROS `mac_test.launch.py` entry point and waits for camera frames.

Docker Desktop cannot claim the D415 directly. In a separate terminal, run only
the native camera bridge:

```bash
./scripts/start-camera-macos.sh
```

macOS requests administrator access for Librealsense. Keep this camera terminal
open; `Ctrl-C` stops only the bridge and does not affect Docker.

Open `http://localhost:6080/vnc.html` and press **Connect**. No VNC password is
configured; both UI ports bind only to localhost.

The macOS bridge uses TCP port `50051`; no camera data leaves the laptop.

### Raspberry Pi running Ubuntu natively

Use a 64-bit Raspberry Pi 4 or 5 with Ubuntu 24.04. Docker is not required.
From a clone of this repository, run:

```bash
./scripts/setup-native-ubuntu.sh
source ros_ws/install/setup.bash
ros2 launch realsense_mapper hardware.launch.py
```

To isolate camera, odometry, and SLAM performance on the Pi without running
YOLO detection or backpack path planning, launch with:

```bash
ros2 launch realsense_mapper hardware.launch.py enable_backpack_stack:=false
```

On compute-constrained Pi deployments, request synchronized 15 FPS streams so
the camera does not produce frames faster than the mapping stack can consume:

```bash
ros2 launch realsense_mapper hardware.launch.py \
  camera_profile:=640x480x15 \
  odom_image_decimation:=2 \
  enable_backpack_stack:=false
```

The hardware launch file starts the D415 directly over USB. The installer adds
ROS 2 Jazzy, installs package dependencies with `rosdep`,
and builds the workspace with `colcon`. RViz/noVNC are intentionally not
installed on the Pi; inspect the Pi's ROS topics from a laptop on the same
network and with the same `ROS_DOMAIN_ID`.

### RViz in Docker with TigerVNC on a Mac

Docker Desktop 4.34 or newer can run the repository image as an RViz-only
viewer using host networking. TigerVNC Viewer runs natively on the Mac and
connects to RViz in the container. In Docker Desktop, open **Settings >
Resources > Network**, enable **Host networking**, and apply the restart.

DDS multicast discovery may not cross Docker Desktop reliably, so run a Fast
DDS discovery server on the Pi. Find the Pi's LAN address with `hostname -I`,
then start the server using that address (replace `PI_LAN_IP` below):

```bash
unset ROS_DISCOVERY_SERVER
fast-discovery-server -i 0 -l PI_LAN_IP -p 11811
```

Keep that terminal open. In the Pi terminal used for mapping, restart the stack
as a discovery-server client:

```bash
export ROS_DOMAIN_ID=42
export ROS_DISCOVERY_SERVER=PI_LAN_IP:11811
ros2 launch realsense_mapper hardware.launch.py \
  camera_profile:=640x480x15 \
  odom_image_decimation:=2 \
  enable_backpack_stack:=false
```

Keep the Mac and Pi on the same LAN. Stop any existing Compose application,
then run the RViz-only service from the repository on the Mac:

```bash
docker compose down
ROS_DOMAIN_ID=42 ROS_DISCOVERY_SERVER=PI_LAN_IP:11811 \
  docker compose --profile viewer up rviz-viewer --build
```

Connect the native TigerVNC Viewer on the Mac to `localhost:5901`. No VNC
password is configured, and the VNC server is provided by the local Docker
container. The browser fallback remains `http://localhost:6080/vnc.html`.
This service starts RViz and the VNC desktop only; the camera, odometry, and
mapper continue to run exclusively on the Pi. Stop the viewer with `Ctrl-C` or:

```bash
docker compose --profile viewer down
```

## Using the mapper

1. Start with the D415 level and pointed into a textured room.
2. Wait until the live image and TF appear in RViz.
3. Walk slowly. Motion blur, low texture, and quick turns can break visual
   odometry.
4. Revisit part of the room to let RTAB-Map detect a loop closure.

The D415 runs synchronized color and aligned depth at 640x480x30 on USB 3.
This doubles the original 15 FPS rate. Although some D400 stream tables list
lower and faster individual modes, this D415 does not advertise 640x360 as a
matching RGB8 and Z16 pair; forcing it makes Librealsense reject the pipeline.
Color exposure starts at 24 ms and depth exposure at 4 ms, with auto-exposure
disabled to reduce motion blur and brightness lag. These are starting values,
not universal calibration: use `realsense-viewer` in the actual operating
environment and increase exposure only when the image is too dark to retain
features. The macOS bridge applies the same profile and exposure settings.

Visual odometry is published on `/visual_odom`. `robot_localization` consumes it
and maintains `/odometry/filtered` plus `odom -> camera_link` using its
constant-velocity prediction model. RTAB-Map consumes the filtered odometry.
The filter smooths very short dropouts; it is not a substitute for an IMU and
its prediction will drift.

RViz is preconfigured for:

- `Accumulated 3D Map`: `/rtabmap/cloud_map`
- `2D Occupancy Map`: `/rtabmap/map`
- backpack route over the occupancy map
- YOLO-annotated camera image with the projected route and TF

When a backpack has valid aligned depth, the planner transforms its center into
the map, selects a goal 0.4 m short of the object, and plans through
known-free cells. A configurable turn penalty favors long straight segments
while retaining A* obstacle avoidance. The map route is published as
`/backpack/path`; the camera overlay is `/backpack/planner_image`, and the
detected 3D target is `/backpack/goal`.

The A* cost also prefers 0.55 m of obstacle clearance, and its raw grid result
is reduced to collision-checked line-of-sight segments. Backpack positions are
low-pass filtered to prevent route flicker. For early mapping tests, unknown
cells are currently traversable; this is unsafe for real guidance and must be
disabled (`allow_unknown: false`) before any field use.

For guidance safety, the planner accepts EKF prediction for at most 0.35 s
after the last real visual-odometry message. It then clears the route, publishes
`false` on `/backpack/path_valid`, and overlays `TRACKING LOST - STOP` on the
camera. Any future haptic controller must require `/backpack/path_valid == true`
and fail silent when that topic becomes stale. This prototype is not a
certified mobility aid and must not be the user's only navigation safeguard.

## Black-backpack detector

The C++ detector runs the OpenCV Zoo YOLOX COCO model at a capped 4 FPS. It
first selects COCO class 24 (`backpack`), then accepts detections whose inner
crop is sufficiently dark. Accepted boxes are drawn in green and published on:

- annotated RGB: `/yolo/annotated_image`
- detection JSON: `/yolo/black_backpack`

The darkness filter and detector thresholds are parameters in
`mapping.launch.py`. The darkness check is intentionally basic; changing bag
color, lighting, or adding reliable bag identity will require a small custom
training set rather than only changing the color threshold.

Each run starts with a clean RTAB-Map database. To see logs or verify topics:

```bash
docker exec realsense-rtabmap-poc tail -f /tmp/mapping.log
./scripts/check.sh
```

Stop everything with `Ctrl-C`, or from another shell:

```bash
make stop
```

Useful developer commands are `make logs`, `make shell`, and `make check`.

## Will an IMU improve this?

Yes, if it is fused correctly and rigidly mounted to the camera. An IMU is most
helpful during fast rotations, brief motion blur, and low-texture views, and it
provides a reliable gravity direction so pitch and roll do not warp the 2D
occupancy grid. It will not fix poor depth data, blank walls, bad time stamps,
or a loose camera/IMU mount, and a low-cost IMU does not provide drift-free
position by itself.

Use an IMU with a hardware timestamp or a well-synchronized ROS timestamp,
publish `sensor_msgs/msg/Imu`, and provide an accurate static transform between
the IMU and `camera_link`. The integration should be configured for the exact
IMU chosen; axis conventions, calibration, noise covariance, and time offset
matter more than merely adding an `/imu` topic.

## Limits of this proof of concept

- This is mapping/localization infrastructure, not safe mobility guidance.
- The D415 has no IMU. Chest pitch and roll can distort the map; a later wearable
  should fuse an IMU or use a camera with one.
- Blank walls, darkness, motion blur, and fast rotation can make RGB-D visual
  odometry lose tracking.
- The occupancy grid depends on the camera starting level and is intended only
  for early experimentation.
