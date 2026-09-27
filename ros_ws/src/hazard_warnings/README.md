# Stage A hazard warnings

Local C++ depth geometry produces versioned JSON snapshots on `/hazard_warning`.
The on-device companion validates their evidence age, speaks resident offline
phrases, and publishes `/hazard/guidance_permitted`. The direction node requires
this permission to be true and received within 500 ms. Missing audio/companion,
urgent obstacles, missing calibration or required sensing faults stop direction
output. Consumers of direction must still expire commands (the tactile branch
already does); this change does not flash or merge the hands.

No LLM, SLAM pose, second camera owner or object classifier is in this path.
This supplements a white cane. No floor, door-state or named-object warning
is implemented, and no wearable performance has been established.

## Pi setup

From the repository root, in a ROS 2 Jazzy shell:

```bash
cd ros_ws
colcon build --packages-select hazard_warnings realsense_mapper
source install/setup.bash
cd ..
PI_VOICE=1 scripts/pi_launch.sh
```

Install the normal companion audio/GPIO dependencies in `companion/README.md`.
An offline speech engine (`espeak-ng` on the Pi) renders the phrase bank once
at startup. A missing phrase inhibits guidance; a lost output stream stops the
supervised companion run. Audio-device failure has no independent tactile signal.

**Unconfigured startup intentionally says sensing is unavailable.** Complete
[coverage.md](coverage.md), then create a measured configuration based on
`config/hazards.yaml`. Set the actual frame names, camera and IMU rotations,
camera height, body dimensions and depth units; only then set `calibrated` and
`coverage_verified` to true. Pass the absolute path:

```bash
PI_VOICE=1 scripts/pi_launch.sh hazard_config:=/absolute/path/to/measured-hazards.yaml
```

The detector accepts `16UC1` with the configured metres-per-unit (provisional
0.001) and `32FC1` in metres, including padded rows and either byte order. It
requires matching aligned depth/CameraInfo dimensions and optical frame, finite
pinhole intrinsics, and either zero distortion or five-coefficient `plumb_bob`.
Unsupported calibration/distortion, stale images and invalid depth fail closed.
CameraInfo must be refreshed within one second; capture age is capped at 150 ms.

The measured static optical-to-body transform is guarded by fresh gravity and
angular-rate observations: default limits are 0.2 rad tilt, 0.7 rad/s rotation,
and acceleration magnitude within 2 m/s² of gravity. Outside that provisional
posture envelope, body-height inference is unavailable. This does not compensate
for torso pitch and does not depend on the suspect SLAM IMU transform.

All six torso/head × left/center/right image regions need at least four samples
and 50% valid depth at the configured caution-distance projection. This is an
online degradation check, not a replacement for the coverage survey or proof
that every hazard is visible. Valid observed clusters can still warn during
partial depth degradation; navigation remains inhibited.

## Behavior and diagnosis

- Full-resolution connected depth returns preserve thin foreground structures.
  Ordinary clusters need two fresh frames; strongly supported urgent clusters
  can warn immediately. Five observed frames beyond the caution band + 0.2 m
  clear a tracked event. Missing evidence expires without claiming clearance.
- Observations expire after 250 ms. Snapshot heartbeat loss expires after 500 ms.
  Duplicate/out-of-order sequences, previous source sessions, malformed data,
  unsupported evidence and future timestamps do not refresh the consumer.
- Urgent, sensing-fault and caution speech preempt help/answers. Repetition is
  limited per event to 2 s urgent / 3 s caution; escalation bypasses cooldown.
  Playback rechecks expiry before the first audio block. No automatic answer resume.
- Three valid healthy snapshots allow recovery. Floor/semantic health stays
  unavailable without disabling the supported upper-body detector. Quiet health
  tones recur after 15 s; startup explicitly says floor hazards are not monitored.
- Run exactly one hazard producer and one on-device companion. The laptop HTTP
  voice fallback does not transport hazards or issue the audio-health permission;
  running the fallback alone will no longer enable movement guidance.
- `enable_hazard_warnings:=false` disables the producer, **not** the direction
  node's permission requirement. This option is for mapping/bench work.

```bash
ros2 topic echo /hazard_warning
ros2 topic echo /hazard/guidance_permitted
```

`scripts/fake_hazard.py --burst 3` is explicitly simulated evidence for audio
bench tests. Stop the real detector first. Sparse `--every 5` messages also
exercise the producer-loss announcement between warnings. Keyboard `x` remains
a direct simulated generic warning; it does not authorize guidance.

## Software validation

```bash
python3 -m unittest discover companion/tests
clang++ -std=c++17 -Wall -Wextra -Wpedantic \
  -I ros_ws/src/hazard_warnings/include \
  ros_ws/src/hazard_warnings/test/geometry_test.cpp -o /tmp/hazard-geometry-test
/tmp/hazard-geometry-test
```

In the built ROS environment, run the synthetic depth/IMU pipeline separately
from hardware services (use a distinct `ROS_DOMAIN_ID`):

```bash
PYTHONPATH="$PWD" python3 ros_ws/src/hazard_warnings/test/ros_smoke.py \
  ros_ws/install/hazard_warnings/lib/hazard_warnings/hazard_warnings_node \
  ros_ws/install/realsense_mapper/lib/realsense_mapper/backpack_direction_node
```

These checks do not measure a real camera, audible output, walking coverage or
Pi load. The mandatory hardware gates remain in [specs.md](specs.md) §10.
