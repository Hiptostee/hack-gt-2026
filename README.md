# RealSense D415 + ROS 2 RTAB-Map

This repository is a standard ROS 2 Jazzy workspace plus a reproducible Ubuntu
24.04 test container for Docker Desktop on macOS. Hardware deployment runs
natively on Ubuntu 24.04 or a Raspberry Pi.

Current planning: [plan.md](plan.md) and the
[hazard warning spec](ros_ws/src/hazard_warnings/specs.md) describe unfinished
integration work. Audio hardware options are being explored: likely an open
speaker for the hackathon demo, bone conduction for the product after it.
The hazard-feedback target includes audio and tactile output; local audio comes
first and tactile patterns remain brainstorming. Idle single tap repeats the
last answer; thinking/speaking tap is planned to cancel without automatic repeat.
Hold to talk/release to send and double tap for local spoken help/status are
also decided. Help needs no internet and contacts no one; cloud-worker scheduling
still needs integration work. Locator sound is removed from scope; legacy code
still needs removal. The approved button mapping is in [companion/specs.md §2](companion/specs.md#2-interaction-model).
The worn-camera coverage survey is a required task before capability claims.

The stack includes:

- Intel RealSense D415 driver
- RTAB-Map RGB-D visual odometry and SLAM (C++)
- accumulated colored point cloud on `/rtabmap/cloud_map`
- 2D occupancy grid on `/rtabmap/map`
- YOLOX black-backpack detection on `/yolo/annotated_image`
- heading-aware A* route to a detected backpack on `/backpack/path`
- laptop voice companion that uses the Pi camera and can activate backpack guidance
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

An MPU6050 at I2C address `0x68` can stabilize short-term rotation. The
configured mounting transform assumes IMU X points up, Y points left, Z points
backward, and the board is centered 2 m behind `camera_link`. Install the
filter and grant the login user access to I2C once, then log out and back in:

```bash
sudo apt install -y i2c-tools ros-jazzy-imu-filter-madgwick
sudo usermod -aG i2c "$USER"
```

For the current RGB-D localization path on the Pi, launch with:

```bash
source /opt/ros/jazzy/setup.bash
source ~/hack-gt-2026/ros_ws/install/setup.bash
export ROS_DOMAIN_ID=42
export RMW_IMPLEMENTATION=rmw_zenoh_cpp
export ZENOH_ROUTER_CHECK_ATTEMPTS=30
ros2 launch realsense_mapper hardware.launch.py \
  camera_profile:=640x480x15 \
  odom_image_decimation:=2 \
  enable_backpack_stack:=false \
  enable_imu:=false \
  enable_icp:=false \
  enable_loop_closure:=true
```

Keep one Zenoh router running in another Pi terminal (`ros2 run rmw_zenoh_cpp
rmw_zenohd`). To include YOLO and backpack routing in the same launch, set
`enable_backpack_stack:=true`.

The RGB-D tracker sometimes emits an invalid zero pose when it loses a frame.
The `valid_visual_odom` node removes those messages and limits sudden position
and orientation changes. `stable_odometry` then publishes a speed-limited pose
and TF at 60 Hz for mapping. The raw `/visual_odom` topic remains available for
diagnosis, and the planner watches `/visual_odom_valid` for tracking freshness.
The external MPU6050 is optional; the current non-ICP path does not fuse it.

ICP is optional. Enable it only when the depth view contains enough varied
geometry to initialize scan matching. On the Pi test, a mostly flat depth scene
repeatedly gave `Scan complexity too low`, so RGB-D tracking was more reliable.
Mapping limits image/odometry timestamp separation to 40 ms.

The Pi hardware launch enables RTAB-Map loop closures. Graph corrections may
move the map frame when a loop is recognized; the local `odom -> camera_link`
pose remains speed-limited. Use `enable_loop_closure:=false` for a short local
mapping run if map-frame corrections are distracting. Disabling closures
allows long-term map drift.

ICP uses 5 cm voxels, a bounded 8,000-point local map, and a 20% minimum
correspondence ratio. These are starting settings, not hardware-verified accuracy
guarantees. If ICP processing consistently exceeds the 67 ms frame interval at
15 FPS or delay keeps growing, add `icp_voxel_size:=0.08` to reduce load. Use
`odom_image_decimation:=1` when the Pi can process enough visual features at
that resolution.

Native RealSense cloud generation is disabled, including `pointcloud__neon_`
on ARM, because enabling it caused a camera-process segmentation fault on the
Pi. `rtabmap_util/point_cloud_xyz` projects aligned depth with color intrinsics,
decimating by four in each dimension before publishing XYZ to `/icp/points`.
Install `ros-jazzy-rtabmap-util` if upgrading an existing installation.
Verify `ros2 topic info /icp/points` reports one publisher and
`ros2 topic hz /icp_odom` receives messages before evaluating mapping quality.
A topic listed only because ICP subscribes to it is not proof of cloud output.

ICP predicts motion from its last successful registration and resets its local
scan map after five consecutive failures. A reset is recovery from lost tracking,
not a guarantee that the trajectory stayed accurate; repeated resets mean the
depth geometry or registration still needs attention. An EKF cannot recover
translation from the IMU alone while both odometry sources are lost. Start level
and keep the rig still during gyro calibration so the relative IMU reference
matches the initial odometry reference.

For a repeatable check, hold still for 10 seconds after calibration, move slowly
one metre and back, then turn slowly while viewing furniture or a room corner.
Save the launch output with `2>&1 | tee /tmp/slam-quality.log` and inspect
`ros2 topic hz /icp_odom` in a terminal with the same ROS/Zenoh environment.
Compare drift, repeated surfaces, ICP correspondence ratios and processing
delays against the previous run; a screenshot alone cannot validate accuracy.

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

For the complete Pi and laptop voice-navigation demo, use the two launchers in
[companion/README.md](companion/README.md#current-demo-click-and-speak-on-the-laptop-camera-and-navigation-on-the-pi).
They start the Zenoh router and bridge as part of the Pi launch and the Docker
viewer, SSH tunnel, and Gemini page as part of the laptop launch. Do not also
start the individual services below while using those launchers.
On the tactile branch, the laptop launcher also sends wireless direction packets
to the two ESP32 hands. See [tactileESP32/BUILD.md](tactileESP32/BUILD.md) for
firmware and shared Wi-Fi setup.

DDS cannot advertise a routable return address through Docker Desktop's VM.
Use ROS 2's Zenoh middleware instead; it carries discovery and topic data over
a TCP connection. Install it on the Pi once:

```bash
sudo apt update
sudo apt install -y ros-jazzy-rmw-zenoh-cpp
```

Start the Zenoh router on the Pi and keep this terminal open:

```bash
source /opt/ros/jazzy/setup.bash
export RMW_IMPLEMENTATION=rmw_zenoh_cpp
export ZENOH_CONFIG_OVERRIDE='listen/endpoints=["tcp/0.0.0.0:7447"]'
ros2 run rmw_zenoh_cpp rmw_zenohd
```

In the Pi terminal used for mapping, restart the stack as a local Zenoh client:

```bash
unset ROS_DISCOVERY_SERVER
export ROS_DOMAIN_ID=42
export RMW_IMPLEMENTATION=rmw_zenoh_cpp
export ZENOH_ROUTER_CHECK_ATTEMPTS=30
unset ZENOH_CONFIG_OVERRIDE
ros2 launch realsense_mapper hardware.launch.py \
  camera_profile:=640x480x15 \
  odom_image_decimation:=1 \
  enable_backpack_stack:=false \
  enable_imu:=true \
  enable_icp:=true
```

Find the Pi's numeric LAN address with `hostname -I`. Keep the Mac and Pi on
the same LAN, stop any existing Compose application, then run the RViz-only
service from the repository on the Mac (replace `PI_LAN_IP`):

```bash
docker compose down
export PI_LAN_IP=100.73.168.115
ROS_DOMAIN_ID=42 docker compose --profile viewer up rviz-viewer --build
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

Raw visual odometry is published on `/visual_odom`; valid visual poses are
published on `/visual_odom_valid`. In non-ICP mode, `stable_odometry` publishes
`/odometry/filtered` and `odom -> camera_link` at 60 Hz, moving toward the most
recent valid pose at bounded speed. RTAB-Map consumes that stabilized odometry.
When visual tracking is lost, the pose holds until valid frames return.

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

With the backpack stack enabled, `/backpack/direction` publishes a single
`std_msgs/UInt8` code at 5 Hz: `0` forward, `1` left, `2` right, `3` rotate
left, `4` rotate right. Rotation commands start when the route heading differs
from the wearer's yaw by about 60 degrees; smaller corrections use `1` or `2`.
Direction codes are sent only after the voice companion activates
`/backpack/guidance_active`. That topic must be refreshed at least twice a second;
its publisher goes inactive when the user says to stop or the companion exits.
It compares the wearer's heading with a point 0.55 m along the A* route, with
hysteresis to reduce flicker. It publishes no code when the path or tracking
status is stale, the path is invalid, or the wearer has reached the route endpoint.
For a demo, view the codes with `ros2 topic echo /backpack/direction`. An ESP32
receiver should stop acting when codes stop arriving.

The A* cost also prefers 0.55 m of obstacle clearance, and its raw grid result
is reduced to collision-checked line-of-sight segments. Backpack positions are
remembered and smoothed in the local odometry frame, then projected into the
current map on each replan so loop closures update the route. For early mapping
tests, unknown cells are currently traversable; this is unsafe for real
guidance and must be disabled (`allow_unknown: false`) before any field use.

For guidance safety, the planner accepts a brief tracking gap of at most 0.75 s
after the last real visual-odometry message. It then clears the route, publishes
`false` on `/backpack/path_valid`, and overlays `TRACKING LOST - STOP` on the
camera. Any future haptic controller must require `/backpack/path_valid == true`
and fail silent when that topic becomes stale. This prototype is not a
certified mobility aid and must not be the user's only navigation safeguard.

## Black-backpack detector

The C++ detector runs the OpenCV Zoo YOLOX COCO model at most once every two
seconds after each inference finishes, using two OpenCV CPU threads. It
first selects COCO class 24 (`backpack`), then accepts detections whose inner
crop is sufficiently dark. Accepted boxes are drawn in green and published on:

- annotated RGB: `/yolo/annotated_image`
- detection JSON: `/yolo/black_backpack`

The darkness filter and detector thresholds are parameters in
`mapping.launch.py`. The planner matches each detection to the depth frame
from the same moment before computing the 3D goal and its A* path. The darkness
check is intentionally basic; changing bag color, lighting, or adding reliable
bag identity will require a small custom training set rather than only changing
the color threshold.

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
