# Beacon: review and main handoff — 2026-09-27

The combined laptop demo is on `feature/guardian`, committed and pushed at
`ffaa730`. Review fixes following that commit are in the working tree. Main has
not been changed. This review covers the laptop dashboard + Guardian + named
object integration; the separate `tactileESP32` branch is not included.

## Review findings fixed

| Problem | Correction |
| --- | --- |
| Docker's ROS install referenced companion sources absent from the image | Copy companion before colcon; install full OpenCV development metadata and Python camera dependencies |
| Native rosdep did not know the Pi bridge's Python requirements | Declare rclpy, python3-numpy and python3-opencv in the package manifest |
| An unexpected model action during Guide could start backpack guidance | Only a fresh named-target result with a box may proceed |
| Dashboard code 3 displayed arrival; code 4 was missing | Render rotate left/right; simulated Stop has no direction code |
| Lost telemetry left prior direction/hand/measurement displays visible | Clear them on timeout or failure; missing camera/latency no longer gets a positive default |
| `.env` Pi address/user settings were loaded after parser defaults | Resolve connection settings after loading `.env`, preserving CLI priority |
| Ctrl-C could publish or shut down an already-invalid ROS context | Guard guidance teardown and use idempotent ROS shutdown |
| Target smoke test aborted after passing assertions | Join its ROS executor before Python teardown |

## Evidence

- Both ROS packages compile on **ARM64 Ubuntu 24.04 / ROS Jazzy** in an isolated
  container: `hazard_warnings` and `realsense_mapper`. Only GCC ABI notes appeared.
- CTest hazard geometry passes.
- Synthetic ROS target checks pass: capture-time world position, current-pose
  distance/bearing, target ownership, tracking loss/recovery, arrival, stale
  frame rejection, near-target behavior, clear and clean executor shutdown.
- Synthetic ROS hazard pipeline passes: urgent/caution geometry, depth holes,
  tilt, stale frames, camera/producer loss and audio-permission expiry.
- The **installed** companion imports, includes dashboard assets, starts its
  Pi HTTP bridge, reports absent sensors as unavailable and exits cleanly on Ctrl-C.
- 212 Python tests and 10 Node tests pass; JavaScript/Python/shell syntax and
  whitespace checks pass. Laptop preflight confirms credential presence,
  dependencies, offline speech and supported default audio formats.
- Earlier Chrome checks exercised Find → highlighted object → Guide → Stop
  and radar switching with simulated model/planner responses.

The full optional Docker viewer image was not built. The ARM64 compilation and
installed-package check above validate the ROS package build/install path.
No live microphone samples or messages were sent during this review.

## Required actual-setup rehearsal

SSH to the configured `raspi@100.73.168.115` timed out. These checks remain open:

1. Connect the Pi and confirm its current address; update `PI_LAN_IP` in `.env`
   or use `--pi-ip`. Ensure the Pi has this exact integrated source revision.
2. Install updated manifest dependencies and rebuild on the Pi:

   ```bash
   cd hack-gt-2026/ros_ws
   source /opt/ros/jazzy/setup.bash
   rosdep install --from-paths src --ignore-src --rosdistro jazzy -y
   colcon build --packages-select hazard_warnings realsense_mapper --cmake-args -DCMAKE_BUILD_TYPE=Release
   cd ..
   PI_VOICE=0 bash scripts/pi_launch.sh enable_icp:=false
   ```

3. On the laptop, run `python3 -B scripts/laptop_launch.py`, then
   `python3 -B scripts/demo_preflight.py --pi-url http://127.0.0.1:8081`.
4. Verify a changing D415 image and timestamp, a mapped scene, correct chair box
   and an actual stationary route-preview result. A depth/map/stale error is not
   a passed route. The default calibration and audio gates stay unchanged.
5. In Chrome, enable the laptop mic, ask a scene question, hear the reply, open
   Guardian and complete a spoken turn. Verify Stop during a request, Pi loss,
   and returning to scene mode after Guardian. Grant microphone access to Chrome
   and the terminal/Python host. Keep exactly one operator tab open.

Use the full [demo runbook](Beacon-Laptop-Demo.md). ARM64 simulation does not
establish D415/IMU timing, worn coverage or microphone/speaker quality.

## Merge preparation

Remote tips checked during review: main `fca5a5e`, guardian `ffaa730`, named-target
`b04eb8d`. Main is an ancestor of guardian: the present integration admits a
fast-forward. The named-target changes are already incorporated at file level;
a second merge of that branch is unnecessary for the demo's contents. Its Git
history is not recorded as a merge parent. Keep that branch until the team agrees
it can be retired. No tactile branch work is implied by this merge.

First commit and push the review fixes on `feature/guardian` using the explicit
paths supplied with the review. Once the team accepts the hardware rehearsal and
the branch is clean, the user can promote the tested branch:

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git merge --ff-only feature/guardian
git push origin main
```

If either fast-forward fails because main advanced, stop and review the new
changes; do not force-push or replace main. The untracked Word lock file
`docs/~$acon-Project-Showcase.docx` is unrelated and must not be staged.
