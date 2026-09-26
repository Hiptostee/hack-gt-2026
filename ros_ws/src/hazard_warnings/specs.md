# Voice warnings for hazards — implementation plan

Status: planned, not implemented or hardware-validated. Written 2026-09-26.
Feature scope follows [the canonical plan](../../../plan.md), especially §5,
and [AGENTS.md](../../../AGENTS.md). The workspace brainstorming document is
idea input; its L515 inventory is obsolete. This spec covers all requested
hazards in stages without changing the initial demo commitment.

## 1. Outcome and scope

Automatically announce relevant hazards using the D415, Raspberry Pi 5,
external MPU6050 and local audio output. Audio hardware options remain under
exploration: likely an open speaker for the hackathon demo, with bone conduction
the intended direction for the actual product after the hackathon. No selection
is finalized or validated. No button press, LLM,
internet access or live cloud speech synthesis is required for a warning.
This complements a cane; it does not establish that a route is safe.

**Feedback scope:** the product should provide both audio and tactile hazard
feedback. Flesh out and implement audio first. Tactile patterns are brainstorming
only for now; they are not Stage A deliverables or an already working fallback.
Hold-to-talk/release-to-send, idle tap to repeat and thinking/speaking tap to
cancel without automatic repeat are decided. Double tap opens local spoken
help/status without internet or contacting anyone. Locator sound is removed
from scope; the approved mapping is in `../../../companion/specs.md` §2;
automatic hazard warnings do not depend on the eventual gesture mapping.

**Decision:** combine local depth geometry with local object detection.
Geometry determines observed obstruction and proximity; the detector supplies
supported object labels. A missing label must never suppress an obstacle alert.
An RGB box alone cannot establish distance, clearance or a traversable opening.

| Requested feature | Detection evidence | Spoken example | Delivery stage |
| --- | --- | --- | --- |
| Branches, hanging signs, shelves, open cabinets | Occupied chest/head volume; optional trained object label | “Head-height obstacle ahead.” / “Shelf ahead.” | A: generic geometry; B: validated labels |
| Other physical obstacles | Connected depth surfaces intersecting body corridor, even without a known class | “Obstacle ahead, left.” | A |
| Doors | Trained door label plus observed door plane/opening geometry | “Door ahead.” / “Door partly open.” | B; state requires its own evidence |
| Narrow passages | Observed left/right boundaries and minimum width over body height and forward extent | “Narrow passage ahead.” | B |
| Curbs / single steps | Supported floor plane, visible edge and positive/negative height change | “Step up ahead.” / “Step down ahead.” | C |
| Stairs | Repeated supported tread/riser structure and direction of height change | “Stairs down ahead.” | C |
| Drop-offs | Supported near floor ending at an edge plus observed lower surface/vertical structure | “Drop ahead.” | C |

Stage A is the initial indoor milestone. Stages B/C remain separate gates,
not promises for the first demo. For an observed edge with insufficient lower
geometry, say “Floor ahead uncertain”; depth holes alone are a sensing limitation,
not a confirmed drop. Describe a curb geometrically as a step unless a validated
classifier supports the curb label.

Out of scope: route planning, steering around hazards, OCR/sign reading,
outdoor reliability claims, rear protection, ownership recognition and new
SLAM infrastructure. Never say “all clear” or “safe to proceed.”

## 2. What exists and what must change

Inspected code in the voice checkout:

- `realsense_mapper` already publishes/consumes RGB, aligned depth, camera
  calibration and IMU topics. Reuse this camera owner; do not open USB again.
- `backpack_detector_node.cpp` uses OpenCV DNN/YOLOX, hardcodes COCO class 24
  and applies a darkness filter. Its default cap is 4 FPS, overridden to 6 FPS
  by `mapping.launch.py`; neither value is measured throughput.
- `companion/ros_camera.py` subscribes to `/hazard_warning` as `String` but
  discards the payload. Subscription errors are silently ignored.
- `Companion._hazard()` only handles `busy`; it stops speech and plays a
  stopped earcon. It neither speaks the hazard nor handles idle/recording.
- Cloud work and help share one worker. Audio playback clears a shared stop
  flag on each new play, so simply adding another warning thread can race.

Therefore “publish a string and call TTS” is insufficient. Payload delivery,
all-state interruption and exclusive audio ownership are required work.

## 3. Runtime architecture

```text
D415 aligned depth + CameraInfo + calibrated body transform
                    │
                    ▼
          C++ hazard_warnings_node ───────► current hazard/health snapshot
          geometry + risk rules                         │
                    ▲                                   ▼
RGB → existing YOLOX worker → timestamped detections  companion alert controller
                                                        │
                                    priority audio owner + local WAV bank
                                                        │
                                              open-ear audio output
```

Create one ROS package `hazard_warnings` alongside `realsense_mapper` for
geometry, event policy and replay tests. Keep perception independent of SLAM
success. Reuse/refactor the existing detector for semantic output instead of
running a second copy of the same neural network. Keep its backpack output
compatible with the planner. No map lookup is required to issue an alert.

Inputs (verify deployed remappings during bring-up):

- `/camera/aligned_depth_to_color/image_raw`
- `/camera/color/camera_info` for the aligned image geometry
- `/camera/color/image_raw` for semantic detection only
- `/imu/data` for gravity consistency, with calibrated camera/IMU transform
- New `/perception/detections` containing capture stamps, model/class-map
  identity, boxes and class scores from the existing detector

Use C++17 to match the repository, `rclcpp`, OpenCV, `sensor_msgs`,
`std_msgs`, `geometry_msgs`, `tf2`/`tf2_ros`; Python audio integration reuses
the companion. No new inference runtime is needed for Stage A.

## 4. Geometry and coverage

### Calibration is the first implementation gate

Measure camera height, orientation relative to the torso, wearer height,
shoulder/body width and a clearance margin. Define `wearer_base` with x
forward, y left and z up, rooted at the ground projection of the wearer in
the calibration pose. Transform optical coordinates explicitly; camera image
vertical is not body height. Account for gravity/mount motion when estimating
floor-relative heights; a fixed transform alone cannot remove torso pitch.

Map actual floor and head coverage at 0.5, 1.0, 1.5 and 2.0 m using fixtures.
Record near blind zones, image boundaries and the minimum tested obstacle
thickness. One chest-mounted D415 may not cover both approaching floor edges
and head hazards. If it cannot, keep unsupported families disabled and announce
the limitation; evaluate another mounting angle or additional camera separately.

**Tracked task — COVERAGE-01 (not started; owner unassigned):** perform the
survey above in the proposed worn position, including leaning and turning.
Deliver `coverage.md` beside this spec with mount measurements, visibility at
each distance/height, thin-obstacle results, blind zones and a supported-hazard
table. Compare candidate mount angles if necessary. Review that evidence before
enabling floor/head capability claims; do not assume both fit in one view.

### Stage A: occupied volume

1. Receive depth into a latest-frame slot. Validate stamp, encoding, dimensions,
   calibration and metric scale. Explicitly handle `16UC1`/`32FC1`; verify units
   for the actual publisher, including the host bridge. Reject zero, nonfinite
   and out-of-range samples.
2. Deproject valid samples using the matching intrinsics and distortion model;
   use the rectified pinhole equation only when those assumptions hold.
3. Transform into the calibrated body volume. Filter a forward corridor with
   half-width = half wearer width + configurable margin; divide height into
   torso and head bands. Use measured dimensions, not a universal 1–2 m band.
4. Group connected/neighboring returns. Reject isolated noise, but retain thin
   structures using spatial support across frames. Avoid averaging a small
   foreground hazard into its background. Begin with full-resolution ROI
   processing; any decimation must pass thin-obstacle tests.
5. Compute nearest supported surface, corridor overlap, left/center/right and
   height band. Warn for occupied space even if YOLO recognizes nothing.
6. Track candidates in local geometry with short lifetime and association
   gates. Confirm ordinary alerts in two consecutive fresh frames; a strongly
   supported urgent close obstacle may bypass temporal confirmation.

Maintain validity/coverage separately from occupancy. Do not fill unknown
pixels and then count them as observed floor. RealSense describes invalid
depth and hole-filling artifacts; preserve that distinction in this pipeline.
[Depth filtering reference](https://dev.realsenseai.com/docs/depth-post-processing-for-intel-realsense-depth-camera-d400-series/).

### Stage B: passages and doors

Measure lateral boundaries at multiple distances and body-height bands;
require observed interior depth and relevant floor coverage. Compare the
smallest supported opening width with wearer width plus margins. Missing
boundary/interior evidence means unknown, not a wide opening. A door box does
not measure an opening. Door state needs leaf/frame geometry or separately
validated state training; a rectangular gap alone is reported as an opening.
Transparent doors are unsupported unless directly validated.

### Stage C: floor changes

Fit a robust local floor plane constrained by gravity, near-floor support,
residual limits and enough spatial coverage; do not select the largest plane
blindly. Examine forward strips for discontinuities. A supported higher plane
and edge establish a candidate step up; a lower observed plane establishes a
candidate step down. Repeated consistent levels support stairs. Test ramps
and sloping floors as negative cases. Floor-fit loss disables these labels
while independent body-obstacle detection continues where calibrated.

Require a visible edge and supporting geometry for a drop label. If the lower
surface is out of range, retain uncertainty. Object classification may reinforce
these results but cannot convert missing floor depth into a measured drop.

## 5. Object detection and model work

Start with the existing OpenCV YOLOX ONNX path and preserve its preprocessing
and box decoding. Publish all selected supported classes before the separate
backpack darkness filter. The Zoo model is YOLOX-S; the current COCO vocabulary
does not include doors, stairs, curbs, branches, shelves or open cabinets as
those classes. “Stop sign” does not cover arbitrary hanging signage.
[OpenCV model](https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_yolox),
[YOLOX class list](https://github.com/Megvii-BaseDetection/YOLOX/blob/main/yolox/data/datasets/coco_classes.py).

For Stage B, fine-tune a small YOLOX model offline on the desired labels, export
ONNX and validate it on the installed OpenCV build. Start with door, shelf,
hanging sign, branch and cabinet-door panel. Annotate cabinet open/closed and
door states only when the image supports that distinction. Train off-device;
inference remains on the Pi. Do not claim custom classes before weights exist.

Data tasks:

- Capture consented examples at intended chest-camera angles: several objects,
  distances, lighting conditions, partial occlusion and backgrounds per class.
- Include hard negatives: posters, windows, wall patterns, closed cupboards,
  railings, ramps and objects outside the walking corridor.
- Split by physical object/location/session, not neighboring video frames.
  Reserve a held-out on-device test set and annotate missed thin structures.
- Pin weights, class map, preprocessing, dataset provenance/license and model
  checksum. Verify that custom training preserves backpack detection, or keep
  custom labels disabled until that compatibility is resolved.
- Record per-class precision/recall and inference age under full load. If
  YOLOX-S is too costly, evaluate a smaller compatible model/export; do not
  assume an input-size change or INT8 conversion works with existing decoding.

Associate labels only with corresponding RGB/depth capture times and a coherent
foreground depth cluster inside the box. Never use the box center or whole-box
median blindly: both can return the wall behind a branch or open door. Maintain
the generic geometry alert if association is ambiguous, delayed or expired.
Labels never delay an urgent alert or cause an immediate second announcement
merely because a name becomes available.

## 6. Event contract, timing and risk policy

For the hackathon, retain `/hazard_warning` as `std_msgs/msg/String` with a
strict versioned JSON payload. Publish a complete current snapshot at 10 Hz
and immediately on important changes; bound active events to eight, ordered
by severity then proximity. Consumers replace the previous snapshot. An empty
list means no current supported detection, never verified free space.

```json
{
  "schema_version": 1,
  "source_session": "boot-unique-id",
  "sequence": 123,
  "published_stamp_ns": 1790400000000000000,
  "health": {"depth": "ok", "body_pose": "ok", "floor": "unavailable", "labels": "ok"},
  "events": [{
    "id": "obstacle-7",
    "observed_stamp_ns": 1790399999980000000,
    "ttl_ms": 250,
    "kind": "upper_body_obstacle",
    "severity": "urgent",
    "direction": "center",
    "height_band": "head",
    "distance_m": 0.8,
    "label": null,
    "evidence": "depth_cluster"
  }]
}
```

Define enums and validation in producer/consumer fixtures. Class scores are
detector scores, not probabilities of safety. Unknown distance is `null`, never
zero. Keep observation time distinct from publication time: republishing must
not refresh old evidence. Health snapshots continue when camera input stops.

Use shallow compatible sensor QoS and a latest-frame slot. Start warning
snapshots with reliable, volatile, keep-last-1 QoS, separate from perception
callbacks. Application age checks remain mandatory. No latched alert replay.
Ignore duplicates/out-of-order sequence numbers within a source session; accept
restart only as a new source session. Consumers validate ROS observation age
and use monotonic elapsed time after receipt; reject future/invalid stamps and
reset state on clock jumps. Replay uses an explicit simulation clock.

Initial tuning values below are **proposed test settings, not measured limits**:

| Parameter | Initial value / rule |
| --- | --- |
| Geometry processing | Target 20–30 Hz at current 640×480 stream; never queue old frames |
| Input age rejection | 150 ms; do not reuse companion snapshot's 2 s limit |
| Label association | Capture skew ≤50 ms and label age ≤150 ms, otherwise generic |
| Event expiry | 250 ms from observation; recheck before starting audio |
| Producer heartbeat loss | 500 ms without a valid snapshot → unavailable |
| Forward caution / urgent band | Start 1.5 m / 0.8 m, subject to coverage and speed gate |
| Body lateral margin | Start 0.15 m per side, then measure wearer/cane needs |
| Clear hysteresis | Five valid observed frames outside threshold + 0.2 m |
| Repetition | Same event: at most every 3 s caution, 2 s urgent; escalation bypasses cooldown |

Leaving view or losing depth expires the observation but does not prove the
obstacle cleared. Distinguish that from a confirmed exit. Aggregate overlapping
detections into one warning; rank urgent collisions before caution or door
information. A new urgent hazard can interrupt a less urgent phrase. Movement
cues must be inhibited while an urgent event or required-coverage fault is
active; coordinate this contract with the tactile bridge without repurposing
its existing direction bits as hazard signals.

Use distance/occupancy rules first. Closing speed/time-to-contact is a later
improvement requiring reliable motion; missing velocity must not suppress an
alert. Select the permitted demo speed only after checking
`warning distance ≥ speed × (measured processing delay + response allowance)
+ stopping distance + margin`. The response allowance is a stated test
assumption until evaluated with intended users. Do not call 200 ms “safe.”

## 7. Voice interaction and audio ownership

Use a finite local PCM/WAV phrase bank loaded at startup. Examples:
“Obstacle ahead,” “Head-height obstacle ahead, left,” “Narrow passage ahead,”
“Step down ahead,” “Hazard sensing unavailable. Use your cane.” Avoid continuous
distance narration. Unsupported labels use generic obstacle wording.

Audio-first implementation vocabulary (proposed wording to test with listeners):

| Situation | Local phrase / behavior |
| --- | --- |
| Supported upper-body obstacle | “Head-height obstacle ahead.” Add “left” or “right” only when supported. |
| Urgent close obstacle | Short distinct alert tone followed immediately by “Stop. Obstacle ahead.” |
| Sensing lost | “Hazard sensing unavailable. Use your cane.” Inhibit dependent movement cues. |
| Sensing restored | “Hazard sensing restored.” Do not claim the route is clear. |
| Validated named object later | Substitute a supported name, e.g. “Shelf ahead.” No extra announcement solely for naming. |
| Passage/floor feature later | Enable “Narrow passage ahead” or “Step down ahead” only after that stage passes its own tests. |

Use a consistent voice and volume, no background music, and brief phrases.
The urgent tone must be short enough to preserve the first-word timing target.
Maintain the repetition limits in §6 so the same stationary obstacle does not
produce continuous speech. Test whether “ahead, left” is understood as obstacle
location rather than an instruction to turn. Wording, volume and warning tones
need listener validation on the selected demo speaker.

Generate assets locally with `espeak-ng` during setup. ElevenLabs may generate
the same fixed assets ahead of time for sponsor integration, but no warning
may wait for it at runtime. Validate files, sample rate and playback at boot.
Missing phrase assets fall back to a resident alert tone plus available local
speech; record degraded voice status instead of silently claiming readiness.

Refactor `audio.py`/`speech.py` to one persistent output owner. Producers submit
bounded PCM blocks with priority and generation tokens; only this owner writes
to the output device. Drop old-generation cloud chunks on arrival. A blocked
network read must never hold the device/owner lock or delay warning playback.
Route earcons, local speech and streamed speech through this owner. Locator
sound is excluded from scope; remove its legacy activation separately.

Priority: urgent hazard → critical sensing fault → caution hazard → local
help → ordinary informational cue → scene answer. Cap pending alerts and
replace stale entries; never speak a FIFO backlog of obsolete warnings.

| State when warning arrives | Required behavior |
| --- | --- |
| Idle | Play local warning immediately |
| Recording | Stop/discard recording; cancel cap/repeat timers; ignore release for that press; play warning |
| Thinking/cloud request | Invalidate response generation; play warning without awaiting network cancellation |
| Speaking | Revoke current output generation; play warning; no automatic answer resume |
| Help/status | Interrupt lower-priority audio; play warning; a new double tap requests status again |
| Guardian Voice session | Revoke Guardian output generation and drop late agent chunks; force mic stream to silence until the warning ends; play warning; cancel any pending SMS draft; Guardian may acknowledge afterward, at most once per 30 s ([guardian spec](../../../companion/guardian/specs.md) §4.2) |

Proposed alert policy: button gestures should not mute an active urgent hazard;
validate this exception to ordinary speech cancellation during hazard testing. After the alert,
repeating an answer is allowed only if priority/health policy permits it; the
decided gesture is a single short press while idle. A tap during ordinary
thinking/speaking cancels without automatic repeat; this does not finalize the
separate urgent-hazard muting policy.
Use state messages: local startup self-test, “Hazard warnings ready,” fault
announcement on transition, and “Hazard sensing restored” after stable recovery.
An unobtrusive health tone every 15 s while otherwise quiet distinguishes
working from dead without narrating every frame; test its comprehension.
Name partial losses precisely (“Floor sensing unavailable”) while keeping
working obstacle alerts enabled. Never claim navigation works from internet
failure alone.

For the complete wearable, audio-device failure needs an independently working
fault channel. Tactile fault patterns are currently brainstorming, not a Stage A
implementation prerequisite or a working fallback. During supervised audio-first
development demos, inhibit guidance and stop the demo on audio failure. Do not
claim unsupervised wearable readiness until independent fault signaling exists.
Process supervision must monitor the audio service itself; a crashed service
cannot announce its own failure.

### Tactile hazard feedback — exploratory only

See `../../../../brainstorming.md` §7 for the shared idea list. Candidates are:
brief simultaneous taps on both hands as an attention cue while speech describes
the hazard; a distinctive repeated rhythm for urgent hazards; a separate wrist
or body vibration point to avoid changing navigation-hand meanings; and a
separate, recognizable fault pattern for lost sensing/audio. None is selected.
Directional hazard pulses are another possibility, but a left-hand warning
must not be confused with the existing instruction to move left.

Compare recognition, comfort, cane interference and confusion with navigation
before selecting a pattern. Existing SG90 servo motion and vibration motors are
different actuators; do not assume a proposed “buzz” can use current hardware.
Audio timing and acceptance do not depend on this exploratory work.

## 8. Latency and compute budget

Provisional Stage A acceptance target: p95 capture-to-first-warning-word ≤200 ms
under full system load; measure alert-tone onset separately, target ≤150 ms.
These are engineering targets, not hard real-time guarantees on Linux/Python.

| Stage | Planning allocation |
| --- | --- |
| Capture delivery / dispatch | 35 ms |
| Geometry + optional next-frame persistence | 55 ms |
| Policy + ROS transport | 15 ms |
| Audio preemption + device buffering | 45 ms |
| First-word onset / scheduling margin | 50 ms |

The sum is a budget, not a sum of measured percentiles. Also measure real
fixture-entry-to-alert time, including wait for camera exposure. Full phrase
completion is a separate, longer metric. Semantic labeling has no place in
the blocking latency path. A 4 FPS inference cap already permits 250 ms between
inferences before processing time; it cannot promise sub-200 ms semantic alerts.

Limit inference threads (start with one), use one replaceable pending RGB
frame, and keep drawing/debug publication optional. Measure SLAM and guidance
freshness versus the existing baseline. Under overload shed annotations and
reduce semantic work first; never build a hazard backlog. If timing still
fails, report degraded sensing and reduce demo scope/speed; do not relabel the
failed budget as achieved.

## 9. Implementation sequence and files

Roles below are workstreams, not assigned teammates. Estimates are planning
ranges and depend on hardware access; Stage C is not assumed to fit the event.

| Order | Work / concrete files | Exit gate | Estimate |
| --- | --- | --- | --- |
| 0 | Calibration and baseline captures; `hazard_warnings/config/hazards.yaml`; coverage record | Know observable body volume, stream units, mount pose and baseline load | 1–2 h |
| 1 | JSON contract fixtures; `companion/voice/hazards.py`; pass payload through `ros_camera.py` and `voice/camera.py` | Injected warnings spoken offline in every state; malformed/stale messages rejected | 2–3 h |
| 2 | Audio ownership in `voice/audio.py`, `speech.py`, `__main__.py`; phrase bank | Delayed cloud chunks cannot resume/mask warning; recording and help interruption pass. **Passed in unit tests and on a Mac speaker (`voice/on-integration`); Pi onset timing not yet measured.** | 3–5 h |
| 3 | `hazard_warnings/CMakeLists.txt`, `package.xml`, `src/hazard_warnings_node.cpp`, `include/hazard_warnings/geometry.hpp`, `policy.hpp` | Measured shelf/panel/branch-like fixtures produce generic warnings | 3–5 h |
| 4 | Launch/config install; `realsense_mapper/launch/mapping.launch.py`; health and navigation inhibit integration | Concurrent system meets Stage A latency/fault criteria | 2–4 h |
| 5 | Refactor `backpack_detector_node.cpp` and shared decoding as needed; `/perception/detections`; optional custom weights | Supported labels work without changing generic-alert timing or breaking backpack output | 2–4 h for existing classes; custom data/training separate |
| 6 | Passage/door geometry and held-out custom model validation | Stage B fixture and negative tests pass | Separate milestone |
| 7 | `src/floor_geometry.cpp`, floor fixtures and calibration extension | Stage C tests pass with adequate floor coverage | Separate milestone |

Audio/contract and geometry can be developed independently against identical
fixtures once the schema is fixed. Integrate Stage A before expanding classes.
Do not rename existing ROS topics or merge unrelated branches in this feature.
Update companion integration docs and `log.md` as each gate actually passes.

## 10. Validation and acceptance

Automated tests planned for implementation:

- C++ synthetic depth: optical/body transform, metric units, thin foreground
  versus background, invalid samples, corridor overlap, hysteresis and expiry.
- Ground fixtures: raised/lowered planes, repeated steps, ramp, vertical wall,
  missing depth and bad gravity. Missing data must not become a drop label.
- Python contract/audio tests: every interaction state; duplicate/out-of-order
  events; publisher restart; clock jump; stale-before-playback; late cloud
  chunks; press/release races; failed subscriptions and unavailable output.
- Regression: existing companion tests and backpack detection/output contract.
  ROS build and replay checks on the supported ROS environment, not inferred
  from passing laptop unit tests.

Real Pi acceptance gates (record every run, no cherry-picking):

1. Stage A: at least 20 controlled approaches per fixture (shelf, hanging panel,
   cabinet-like panel, branch-like fixture), distributed across center/left/right
   overlap, heights and tested lighting. Require no missed warning within the
   defined coverage/speed envelope; any miss blocks that capability claim.
   Report the sample size: passing this gate does not establish general safety.
2. At least 20 negative trials: side objects outside corridor, floor/wall,
   isolated depth noise; plus a 10-minute clear controlled path. Initial nuisance
   gate ≤1 false hazard announcement/minute. Record intended repeat reminders
   separately from false detections and distinguish semantic mislabels.
3. Collect ≥100 alert events under concurrent mapping/detection/Q&A/tactile load.
   Report capture age, median/p95/worst tone onset and first-word latency,
   missed/late warnings, CPU, temperature and guidance gaps. Require the §8
   targets; verify perceptible output with an audio recording/loopback and
   synchronized stimulus, not just enqueue timestamps.
4. Sustain full-load operation for 20 minutes. Stop camera, freeze/replay old
   stamps, block inference, remove internet, kill the hazard producer, unplug
   audio and terminate the companion. Require correct partial/full failure
   signaling through working audio and expired guidance; heartbeat loss becomes a fault within
   500 ms plus ≤200 ms output onset. For audio/service failure, verify guidance
   inhibition and stop the supervised demo; tactile fault output is a later
   acceptance gate. No old warning/answer restarts afterward.
5. Stage B: test openings narrower/wider than wearer envelope, one hidden
   boundary, partly open/closed doors and transparent surfaces. For each enabled
   semantic class require ≥90% precision and ≥90% recall on a held-out set with
   ≥30 positives plus hard negatives; report counts and generic-alert performance
   separately. Disable failing labels without disabling geometry warnings.
6. Stage C: bench/static-camera trials of up/down steps, staircase, supported
   drop edge, ramp and absent floor coverage (≥20 each). No confident floor-change
   labels on invalid-depth-only scenes. Only extend to supervised walking once
   detection distance and floor coverage pass the speed/range calculation.
7. Verify phrase/direction comprehension without relying on a display. Use
   stationary and supervised obstacle fixtures, a cane and a sighted spotter;
   do not ask testers to approach real exposed drops. Teammate tests establish
   engineering behavior, not accessibility validation with intended users.

Record configuration, model checksum, mount dimensions, fixture geometry,
failure counts and known unsupported surfaces. RGB/depth recording for tests
is opt-in, local and deleted under an agreed retention period; runtime diagnostics
default to timing/counts without audio, images or transcripts.

## 11. Remaining decisions and first deliverable

Before the supervised audio demo, complete COVERAGE-01, measure body dimensions,
select/test the exploratory demo speaker and determine allowed approach speed
from measured range/latency. The button mapping is decided in the companion
spec; its cancel/help fixes and locator removal are implemented on
`voice/on-integration`, with Pi button validation still pending.
Independent fault signaling and tactile hazard patterns are later work before
unsupervised wearable claims; bone conduction remains a post-hackathon direction.
Stage B also
needs an owner and time allocation for data collection/training; no suitable
custom weights have been established by this planning work.

The first deliverable is **an offline spoken upper-body obstacle warning that
interrupts every companion state**, followed by a measured shelf/panel demo on
the Pi. Then add validated object names and passages, then floor changes.

Technical references used for design verification:

- [RealSense projection and coordinate systems](https://dev.realsenseai.com/docs/projection-in-realsense-sdk-2-0/)
- [RealSense alignment and occlusion](https://dev.realsenseai.com/docs/projection-texture-mapping-and-occlusion-with-intel-realsense-depth-cameras/)
- [OpenCV YOLOX implementation/model](https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_yolox)
- [YOLOX COCO class vocabulary](https://github.com/Megvii-BaseDetection/YOLOX/blob/main/yolox/data/datasets/coco_classes.py)
