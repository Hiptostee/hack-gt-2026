# Wearable Spatial Guide — Implementation Plan

## Current integration — 2026-09-27

The current `feature/guardian` working tree combines the named-target work from
`feature/navigate-target` (`b04eb8d`) with Guardian (`3e24163`). This is a file-level
integration; no merge commit or push has been made. The branch snapshots below
are historical and are superseded by this section for laptop demo usage.

The committed scope decision is one stationary D415 scene, one named chair and
Guardian orientation help. Laptop microphone/speakers and browser controls
replace the missing dedicated microphone/button; typed scene questions provide
fallback. The Pi remains the camera/depth/planner host. The original dashboard at
`/` now includes the demo controls; `/demo` opens the same page. Find highlights
the candidate without routing; after inspection, Guide captures a fresh frame and
identifies the object again before the Pi planner receives it. The selection
expires after 60 seconds and is cleared by Stop, a new Find or Guardian entry.
Use one distinct target; identity across similar objects is not tracked. The page
shares observation memory across scene questions and Guardian. Stop invalidates cloud
replies, Guardian stops navigation, and browser heartbeat loss stops the session.

Default hazard calibration remains incomplete. A routed target can be presented
as a stationary preview, but physical direction permission is not fabricated.
ESP32 hardware is not required; the separate tactile branch is outside this
integration. Demo SMS is simulated. Dashboard timing constants were removed.

Software acceptance: 210 Python tests and 7 Node tests pass; the dashboard's inline
and external scripts parse. Chrome verified Find, highlighted candidate, Guide,
Stop and the radar switch using simulated model/planner responses. Pi build, live D415 routing and
full spoken Guardian rehearsal remain required. See the executable setup and
judging sequence in [docs/Beacon-Laptop-Demo.md](docs/Beacon-Laptop-Demo.md).


**Version:** 1.4 · **Updated:** 2026-09-26 · **Event:** HackGT 2026

A wearable spatial guide for blind and low-vision people, built as a complement
to a white cane. The prototype adds forward chest/head-height obstacle alerts,
tactile guidance, and on-demand scene questions and text reading. It is not a
certified mobility aid or a replacement for the cane.

This is the canonical implementation plan. Source paths below are relative to
the repo.
Evidence comes from code, `AGENTS.md`, `log.md`, the feature specs and the
branches inspected on 2026-09-26 18:30: `main` and `origin/integeration`
(spelled that way on the remote) both at `fca5a5e`; `feature/guardian`
(`e450198` tap mapping, audio owner, hazard phrase; `e31e770` Guardian;
`25f08f4` Pi preflight, trail, Twilio sender; pushed);
`feature/navigate-target` (`b0195a8` named-object navigation, on `e450198`;
pushed); and `origin/tactileESP32` at `84e942c` (firmware plus a
laptop-side tactile sender). None is merged into `main`. Branch contents do
not establish that the combined wearable has been tested.

**Latest implementation update:** Stage A hazard software is committed on
`feature/guardian` as `5fb2864`. It adds depth geometry,
strict hazard/health snapshots, offline phrases and an expiring companion-to-
direction permission. Synthetic ROS checks pass; mounting, coverage and Pi
latency remain unverified. Default configuration intentionally inhibits guidance.
See [hazard setup](ros_ws/src/hazard_warnings/README.md) and the newest log entry.

### 1a. Integration branch status (`integeration` = `main` @ `fca5a5e`)

The integration branch builds on all of `main` and adds a copy of the voice
companion plus the glue between them. What it contains:

- **Voice → ROS guidance gate.** Gemini's `device_action` enum gains
  `navigate_backpack` and `stop_navigation`. `companion/voice/guidance.py`
  (`RosGuidance`) accepts a start only if `/backpack/path_valid` was true within
  1.5 s, then publishes `/backpack/guidance_active` at 5 Hz. The direction node
  emits `/backpack/direction` only while that gate, path validity and path are
  all fresh (1.5 s timeouts).
- **Split Pi/laptop demo.** `companion/voice/pi_bridge.py` runs on the Pi
  (launched from `mapping.launch.py` via `enable_companion_bridge`, default
  true) and serves `/status`, `/frame` (JPEG from the ROS camera topic) and
  `/guidance/start|stop` on `127.0.0.1:8081`. The laptop reaches it through an
  SSH tunnel; the browser page on the laptop records the question, calls Gemini
  with the laptop's key and plays the answer. The Pi needs no Gemini key.
- **Two-command launch.** `scripts/pi_launch.sh` starts a Zenoh router and
  `hardware.launch.py` with `640x480x15`, backpack stack, IMU and ICP enabled.
  `scripts/laptop_launch.py` opens the SSH tunnel (default Pi address is the
  Tailscale IP `100.73.168.115`), starts the voice web page on `:8080` and the
  Docker RViz viewer (VNC `localhost:5901`).
- **Odometry from `main`.** ICP on a decimated ROS depth cloud (`/icp/points`)
  fused through the EKF, `valid_visual_odom_node`, and `stable_odometry_node`
  when ICP is off; optional loop closure (`enable_loop_closure`). OpenVINS was
  added and then removed again on `main` (`6b01845`), so it is not a dependency.

Remaining gaps:

- **Voice regressions — RESOLVED.** `b065484` had copied an older companion
  snapshot. The missing commits (`9d682f6`, `55dd3b1`: SSE streaming, image
  downscaling, hazard preemption, speech speed, GPIO fallback) were
  cherry-picked into `integeration` at `fca5a5e`, which is also `main`.
- **Newer voice work not in `main`.** The tap mapping, shared audio owner and
  hazard phrase are `e450198` on `feature/guardian`, with Guardian (`e31e770`)
  on top. `feature/navigate-target` branches from `e450198`. Merge steps are in
  `log.md` (18:30 handoff entry).
- **Tactile sender exists but is not merged.** `origin/tactileESP32`
  (`84e942c`) adds `companion/voice/tactile_link.py`: the laptop polls
  `/guidance/state` from the Pi bridge and sends the 3-byte packet to both
  hands at 20 Hz. It maps rotate left/right onto the left/right flags and sends
  neutral when guidance is inactive, the route is invalid or state is older
  than 0.4 s. A trial merge into `feature/guardian` had no conflicts. No hand
  has been flashed.
- **Direction output goes silent rather than neutral.** When the gate or inputs
  go stale the direction node stops publishing. The branch's tactile sender
  treats silence as expiry (§7); any other consumer must do the same.
- **Suspect IMU transform.** `camera_to_imu_tf` in `mapping.launch.py` is now
  `x = -2.0 m` (set in `b065484`). A 2 m camera-to-IMU offset is implausible on
  a wearable; confirm or correct before trusting fused odometry.
- **Demo audio lives on the laptop.** In the current demo the button,
  microphone and speaker are the laptop browser's, not the Pi's. The on-device
  `companion.voice` path exists but is not what `laptop_launch.py` runs. The
  tap mapping, audio owner, hazard phrase and Guardian exist only in the
  on-device path. **DECIDED WITH THE USER (18:20):** the demo runs the on-device
  companion on the Pi (its button, USB mic and speaker), started with
  `PI_VOICE=1 ./scripts/pi_launch.sh`. `--pi-url` lets the same service run on
  the laptop against the Pi camera as a fallback. Pi bring-up TO BE VALIDATED
  with `python3 -m companion.guardian.preflight --pin 17`.
- **Hazard detector exists on `feature/guardian` (`5fb2864`).**
  `/hazard_warning` has a Stage A publisher and strict consumer. Not integrated
  into `main`; hardware calibration/coverage and latency remain open (§5).

## 1. Scope and status

The initial demonstration combines selected upper-body obstacle alerts,
guidance toward one detected backpack in a controlled indoor area, scene
questions/text reading, and local help. Warning remains active during every
interaction; Navigate and Ask are not exclusive safety modes.

| Label | Meaning |
| --- | --- |
| DECIDED | Recorded design choice; not proof of implementation |
| IMPLEMENTED | Code exists; branch and validation evidence must be stated |
| TO BE INTEGRATED | Components exist separately; their connection is unfinished |
| TO BE IMPLEMENTED | Required behavior is missing or incomplete |
| TO BE DECIDED | Unresolved choice; no hidden implementation assumption |
| TO BE VALIDATED | Requires measurement or verification on the intended hardware |
| STRETCH | Outside the initial demonstration commitment |
| TO PURSUE | Selected by the team as a feature we want (§12); each item there states its own status; must not delay milestones 1–4 |

**Outside the initial scope:** outdoor route guidance, backward guidance,
reliable stairs/curbs/drop-off classification, arbitrary destination navigation,
persistent object re-identification, and automatic remote emergency dispatch.
These remain future work, not completed capabilities.

## 2. Hardware and deployment

| Component | Current evidence / decision | Remaining work |
| --- | --- | --- |
| Raspberry Pi 5, 16 GB RAM, 256 GB SSD | In hand per project inventory | TO BE VALIDATED: simultaneous services, thermals, power and runtime |
| RealSense D415 | Confirmed project camera; stereo depth, not LiDAR | TO BE VALIDATED: wearable mounting, coverage, motion and difficult surfaces |
| External MPU6050 | Integrated in ROS code; D415 has no onboard IMU | TO BE VALIDATED: mounting transform/calibration on assembled wearable |
| ESP32 + SG90 hand units | Two-hand firmware exists on tactile branch | TO BE VALIDATED: assembled inventory, flashing, motion, power and loss recovery |
| USB microphone | Voice capture implementation exists | TO BE VALIDATED: actual mic, gain and ambient-noise behavior |
| Audio output — options being explored | Likely open speaker for the hackathon demo; bone conduction is the future product direction after the hackathon | Hardware selection remains open; validate audibility, latency and ambient hearing |
| Momentary GPIO button | DECIDED: GPIO pin 17 + GND, internal pull-up, no external resistor | TO BE VALIDATED: wiring, placement and walking gestures |
| Portable power | Untethered operation not established | TO BE DECIDED: supply/distribution and battery telemetry; TO BE VALIDATED: simultaneous loads |
| Network | DECIDED: laptop Wi-Fi hotspot; Pi joins as client. Integration branch demo reaches the Pi over Tailscale + SSH tunnel; RViz over Zenoh | TO BE VALIDATED: venue operation, Tailscale reachability, SSH access and API connectivity |

Remove L515 from the inventory: the project records that it was never acquired.
Do not assume phone tethering provides GPS access or call control. Battery
percentage remains unknown without supported telemetry. Keep ears open; use
bone conduction or an open speaker rather than in-ear/over-ear headphones.

### 2a. Connectivity — laptop hotspot (DECIDED)

The Pi connects to a hotspot created by the development laptop over Wi-Fi.
This provides both SSH access for development and internet for cloud API calls
(Gemini, ElevenLabs). The ESP32 hand units remain on their own AP network
(`10.42.0.x`); the Pi bridges the two networks.

- Laptop creates a mobile hotspot (macOS: Internet Sharing or personal hotspot).
- Pi connects via `nmcli` or `/etc/NetworkManager/` config.
- Pi retains its `10.42.0.1` AP for the ESP32 hands via a secondary interface
  or by running `hostapd` on the onboard Wi-Fi while connected to the laptop
  hotspot on a USB Wi-Fi adapter. If only one radio is available, the ESP32 AP
  takes priority and the Pi accesses the internet through USB Ethernet or
  USB-C gadget mode to the laptop.
- **TO BE VALIDATED:** dual-network operation under load; venue Wi-Fi
  interference; API latency through hotspot chain.
- **IMPLEMENTED ON INTEGRATION BRANCH:** `scripts/laptop_launch.py` defaults to
  the Pi's Tailscale address and forwards `8081` over SSH; the Pi bridge binds
  only to `127.0.0.1`. Override with `--pi-ip` when on the hotspot LAN.

### 2b. Audio hardware decisions

**EXPLORING OPTIONS:** the likely hackathon demo output is an open speaker.
Bone conduction is the intended direction for the actual product after the
hackathon, not a required demo purchase or a finalized hardware selection.
Keep ears open in either case. Explore a USB speaker or USB audio dongle feeding
an open speaker, with a USB microphone; verify the actual devices on the Pi.

**Fallback options if USB audio is unavailable:**

| Fallback | Type | Notes |
| --- | --- | --- |
| USB-to-3.5mm adapter (~$5) | Output + mic (if TRRS) | Low latency, widely available |
| USB webcam with built-in mic | Mic input | Many register as ALSA capture; doubles as backup camera |
| Piezo buzzer on GPIO | Output (tones only) | No speech; beep patterns encode hazard type/urgency |
| Bluetooth open speaker | Output | Exploratory option; measure complete warning latency before considering demo use |
| Phone-as-mic (WO Mic) | Mic input | Streams phone audio over Wi-Fi as virtual ALSA device |
| HDMI audio extractor | Output | Bulky; functional if a small adapter is available |
| Offload TTS to laptop | Output | Pi streams text over Wi-Fi; laptop plays audio; demo-only hack |

**Audio failure:** tactile hazard/fault patterns and a possible buzzer are
brainstorming only, not an implemented fallback. Finish local audio warnings
first. If audio fails before an independent fault channel exists, inhibit
movement guidance and stop the supervised demo; do not silently substitute a
tactile-only demo or a different button mapping.

### 2c. Button (mapping decided; implementation/validation pending)

- **Hardware:** momentary push-button wired between GPIO 17 and GND.
- **Software:** `companion/voice/button.py` uses internal pull-up; no external
  resistor required. Falls back to keyboard input when `lgpio` is unavailable.
- **DECIDED WITH THE USER:** a single short press while idle repeats the last
  answer, without taking a new snapshot or asking Gemini again. If no answer
  exists, announce that there is nothing to repeat yet.
- **DECIDED WITH THE USER:** a tap while thinking or speaking cancels the
  response without automatically repeating. Release must not trigger idle
  repeat; late response/audio output must stay canceled. IMPLEMENTED on
  `feature/guardian` (unit-tested; Pi button TO BE VALIDATED).
- **DECIDED WITH THE USER:** hold to record a question, release to send it
  with the camera image; retain the existing push-to-talk interaction.
- **DECIDED WITH THE USER:** double tap opens local spoken help/status,
  without internet and without contacting anyone. It must not wait behind a
  cloud request: help now runs on its own worker (IMPLEMENTED on
  `feature/guardian`).
- **REMOVED FROM SCOPE:** locator sound. Another double tap remains help/status.
  Removed from the voice device code on `feature/guardian`; the browser
  fallback in `companion/static/` still has its locator buttons.
- **DECIDED WITH THE USER:** triple tap, or a spoken request returned by Gemini
  as `device_action: "guardian"`, enters Guardian Voice after a 2 s
  tap-to-cancel window. Inside Guardian: hold = talk to the agent, tap = stop
  its speech, double tap = exit locally with status. Replaces the earlier
  3-second-hold proposal. See [companion/guardian/specs.md](companion/guardian/specs.md).
- The approved mapping and implementation gaps live in
  [companion/specs.md §2](companion/specs.md#2-interaction-model).
  Hazard warnings remain automatic and require no button press.

## 3. Current implementation baseline

| Subsystem | Evidence | Status / limitation |
| --- | --- | --- |
| Mapping and target planning | RTAB-Map RGB-D odometry, ICP on decimated ROS depth cloud fused via EKF, MPU6050, YOLOX backpack detector and A* in `ros_ws/` | IMPLEMENTED; `pi_launch.sh` selects ICP + IMU at 640x480x15; wearable performance and IMU transform TO BE VALIDATED |
| Direction and pose stabilization | Direction, valid-visual-odometry and stable-odometry nodes | INTEGRATED on `integeration`; direction gated by `/backpack/guidance_active` |
| Voice → navigation | `integeration`: `navigate_backpack` / `stop_navigation` actions, `RosGuidance`, Pi bridge | IMPLEMENTED ON INTEGRATION BRANCH; end-to-end walk TO BE VALIDATED |
| Voice companion | `main` (`fca5a5e`): Gemini streaming, ElevenLabs/local speech, button state machine; newer tap mapping on `feature/guardian` | IMPLEMENTED; current demo uses laptop browser audio; Pi button/mic/speaker demonstration TO BE VALIDATED |
| Hazard detection/interruption | `feature/guardian` (`5fb2864`): C++ Stage A geometry, JSON snapshot validation, resident phrase bank, all-state interruption and expiring movement permission | IMPLEMENTED IN SOFTWARE: unit/synthetic ROS checks pass; defaults unavailable until mount/coverage configuration; Pi onset timing TO BE VALIDATED |
| Tactile transport/firmware | `origin/tactileESP32` (`84e942c`): C++ sender, protocol, hotspot scripts, SG90 firmware, PlatformIO build/flash, laptop-side `tactile_link.py` fed by the Pi bridge | IMPLEMENTED ON BRANCH; not merged into `main`; log records host builds/tests, not flashed hardware |
| Guardian Voice | `companion/guardian/`: agent configured and smoke-tested on a laptop; `GuardianController`, push-to-talk `GuardianAudio`, `SmsGate` with a fake sender | IMPLEMENTED ON BRANCH: `feature/guardian` (`e31e770`), wired into the on-device companion; tested live on a Mac; Pi preflight, observation trail and `TwilioSender` added after (mock-tested, no Twilio account); not run on the Pi |
| Local help | `status_text()` status and last landmark on its own worker; locator removed from voice code | IMPLEMENTED on `feature/guardian`; offline Pi run TO BE VALIDATED |
| Phone/browser help fallback | Confirmed call/SMS/share handoffs in `companion/static/` | IMPLEMENTED FALLBACK; phone/platform validation pending; not standalone Pi dispatch |
| Persistent remember-this | No object-memory pipeline found | TO BE IMPLEMENTED; STRETCH |

Use the existing RTAB-Map stack. OpenVINS was tried on `main` and removed
(`6b01845`); EISLAM was never added. Adding another SLAM stack requires a
specific demonstrated need and a separate decision.

## 4. Target architecture and priorities

The following connections describe the intended integration, not a completed
system:

1. Existing RGB-D/IMU processing → odometry and RTAB-Map → map.
2. Backpack detection + aligned depth + timestamped transform → target → A* →
   direction/validity → freshness-aware tactile bridge → ESP32 hands.
3. Local depth geometry → hazard event → priority arbitration → local audio
   first, preempting navigation and scene narration. The product target includes
   both audio and tactile hazard feedback; tactile patterns are brainstorming
   only at this stage.
4. Button/microphone + camera snapshot → Gemini → short answer →
   ElevenLabs, with local speech fallback.
5. Local button gestures → spoken help/status independent of cloud requests.

ROS and the voice companion stay separate processes and share existing camera
topics. Cloud calls must not gate local alerts or tactile guidance.

On the integration branch, connection 2 runs as far as `/backpack/direction`
(the tactile bridge is missing), and connection 4 runs laptop-side: laptop
browser mic → Pi frame over SSH tunnel → Gemini from the laptop → browser
playback, with `navigate_backpack` flowing back to the Pi's guidance gate.
Connections 3 and 5 are not yet present there.

**DECIDED:** immediate hazards and loss-of-guidance signals override navigation
cues and scene narration. Suppress invalid movement guidance and communicate why;
neutral servos alone cannot distinguish arrival, inactivity and failure.

**IMPLEMENTED ON `feature/guardian` (hardware TO BE VALIDATED):** audio
arbitration, bounded latest hazard state, event expiry and all-state hazard
handling, including Guardian. Local help has its own worker. The companion
publishes a 500 ms guidance permission lease; urgent hazards, required sensing
faults and output/service loss inhibit the direction node.
Invalidating a response generation currently suppresses its eventual answer; it
does not terminate the outstanding network request.

## 5. Hazard warnings

Detailed implementation sequence, object-detection scope, event/audio contracts
and hardware acceptance gates are in
[Voice warnings for hazards](ros_ws/src/hazard_warnings/specs.md).
That feature spec selects provisional parameters for testing; none are measured
hardware guarantees. Doors/passages and floor-change classification remain
staged extensions beyond the initial upper-body warning demonstration.

**MVP:** detect selected obstacles in a calibrated forward chest/head-height
volume using local depth geometry. Object labels are optional explanations,
not prerequisites for detecting occupied space. No LLM in this loop.

**DECIDED:** develop and verify audio hazard feedback first. The desired product
has both audio and tactile hazard feedback, but tactile patterns, hardware and
their relationship to navigation cues remain exploratory. See the
[tactile brainstorm](../brainstorming.md#tactile-hazard-feedback--ideas-only).

**TASK — camera coverage survey before wearable claims:** mount the D415 in
the proposed position and measure floor, chest and head coverage at 0.5, 1.0,
1.5 and 2.0 m, including leaning/turning and thin obstacles. Record blind zones,
tested mounting angles and supported hazard families. Owner: unassigned.
If one mount cannot cover both floor and head hazards, document the limitation
and compare remounting/additional sensing before enabling unsupported features.

- **IMPLEMENTED (`5fb2864`):** local aligned-depth geometry independent of
  SLAM/cloud, with explicit measured mount configuration and IMU posture guards.
  Calibration/coverage flags remain false until measured; no invented wearer dimensions.
- **IMPLEMENTED (`5fb2864`):** timestamped events with direction, severity,
  expiry and sensor health; strict consumer, local phrases and guidance inhibition.
- **IMPLEMENTED / HARDWARE TO BE VALIDATED:** the feature spec defines a versioned
  JSON snapshot on `/hazard_warning`, bounded local audio arbitration,
  persistence/hysteresis and repetition rules. Thresholds and cue comprehension
  remain **TO BE VALIDATED** on hardware.
- **TO BE VALIDATED:** camera-to-body transform, user height, forward coverage,
  near-field blind spots and mounting motion. Fixed camera-space heights must
  not be treated as wearer-relative heights.
- **TO BE VALIDATED:** thin obstacles, reflective/transparent surfaces, invalid
  depth and occlusion. Report unavailable coverage explicitly.
- **STRETCH / TO BE IMPLEMENTED:** stairs, curbs and drop-offs. Missing depth
  alone proves neither a drop-off nor clear floor; later classification needs
  ground observations and confidence checks.

The earlier 100–200 ms figure is an unverified target. Measure capture to
perceptible alert under full load and relate detection range to walking speed
and response time before selecting acceptance thresholds.

## 6. Navigation and find-a-target

**MVP:** guidance toward one detected backpack in a controlled indoor mapped
area. The detector selects dark COCO backpacks; it does not implement arbitrary
doors, stairs, branches or cabinets, or identify a backpack's owner.

The current planner permits unknown cells (`allow_unknown=true`). Its 0.55 m
`preferred_clearance` is a cost preference, not a guaranteed margin; obstacle
inflation defaults to 0.25 m. A grid path is not proof of a safe walking route.

- **TO BE IMPLEMENTED:** conservative unknown-space handling for wearable
  guidance; freshness checks for map/depth/pose/target; explicit cancellation,
  arrival, target-lost and no-path behavior.
- **TO BE DECIDED / VALIDATED:** wearer/cane footprint, obstacle margin, target
  approach distance and expiry thresholds. Camera position is not the whole
  user's footprint, and camera yaw may differ from walking direction.
- **INTEGRATED (integration branch):** 5 Hz direction output
  (0=forward, 1=left, 2=right, 3=rotate left, 4=rotate right), published only
  while `/backpack/guidance_active`, `/backpack/path_valid` and `/backpack/path`
  are fresh. A spoken "bring me to the backpack" starts the session if a valid
  route exists; "stop guidance" ends it. Arrival currently just stops output,
  with no spoken arrival message.
- **TO BE DECIDED:** steer versus rotate patterns and distinguishable feedback
  for arrival, no path and unavailable sensing.
- **STRETCH / IMPLEMENTED ON `feature/navigate-target`, HARDWARE TO BE
  VALIDATED:** arbitrary target selection —
  [Navigate to a named object](companion/navigate/specs.md). Gemini returns a
  box in the existing voice request; the companion publishes it on
  `/target/detection` with the frame's ROS stamp; the planner's existing
  depth/TF projection turns it into a remembered `odom` goal. Gemini's text
  never supplies distance or bearing; spoken distance/bearing come from the
  planner's `/target/status`. Adds `/target/clear`, target ownership (voice vs
  YOLO) and a 10 s time-based depth buffer. Verified by unit tests, a
  simulated planner smoke test and one live Gemini image (boxes on target);
  goal accuracy on real depth is unmeasured.

## 7. Tactile link and command lifetime

Preserve the existing tactile branch protocol during initial integration:

| Property | Existing implementation |
| --- | --- |
| Packet | Three bytes: `[0xA5, sequence, flags]` |
| Flags | bit 0 front, bit 1 left, bit 2 right; zero or one set; all zero = neutral |
| Delivery | Same packet unicast to both hands; sequence checks reject late/duplicate packets |
| Rate | 20 Hz resend; changes sent immediately |
| Receiver timeout | 500 ms without accepted packets commands rest |
| Idle actuation | Return to 90° rest; PWM releases after a separate configured interval |
| Active actuation | SG90 sweep; front moves both hands, left/right moves that hand |
| Network | Pi `10.42.0.1`; hands `.2` and `.3`; UDP port 4210 |

The tactile branch log records removal of backward guidance. Keep it excluded
for the MVP. **TO BE DECIDED only if scope reopens:** whether it is wanted and
how rear sensing and distinct feedback would support it. The old four-element
array is not the current wire format.

**IMPLEMENTED ON BRANCH (not merged):** direction-to-tactile bridge with
source-command expiry. On `origin/tactileESP32`, `RosGuidance` on the Pi
subscribes to `/backpack/direction` and the bridge serves `/guidance/state`;
the laptop's `companion/voice/tactile_link.py` polls it at 10 Hz and sends
neutral when guidance is inactive, the route is invalid, or state is older than
0.4 s. Rotate left/right (3, 4) map onto the left/right flags, so the hands
cannot distinguish steering from rotating. **TO BE IMPLEMENTED:** a spoken
guidance-loss announcement; today expiry only sends neutral, which the user
cannot tell apart from arrival or inactivity. **TO BE VALIDATED:** measured
expiry with the planner stopped and UDP alive, on flashed hands.

**TO BE DECIDED:** expiry budget, maneuver patterns and receiver-health
telemetry. Successful UDP sends do not establish receipt; the existing link has
no acknowledgements. **DECIDED:** direction means intended movement, not obstacle
location; hazard signals must be distinct. **TO BE VALIDATED:** cue recognition,
comfort, cane-hand interference, startup/rest, packet loss and reconnects.
Defer pace modulation until basic cues are understandable.

## 8. Scene questions, reading and sponsor integration

Use Gemini for explicit scene questions/read requests and ElevenLabs for answer
speech, with local speech fallback. Questions can cover visible doors, seats,
checkout counters, room numbers, menus and signs. Report unreadable or uncertain
details honestly. An image answer is neither movement clearance nor localization.

The voice loop currently captures a snapshot when processing an utterance and
waits for `gemini.ask()` before speaking. SSE and answer-first parsing do not
establish token-to-speech streaming in that loop. The integration branch uses
plain `generateContent` without image downscaling; its README cites ~2.5 s
Gemini responses, an unmeasured development figure.

- **TO BE DECIDED:** fresh-image versus same-snapshot follow-up semantics;
  reconcile current capture behavior with the companion spec and communicate
  image age/source where relevant.
- **TO BE VALIDATED:** text readability at actual resolution/JPEG settings;
  request a better stationary view rather than complete guessed text.
- **TO BE VALIDATED:** configured model access and performance on the event
  network. Model IDs in code are configuration, not availability guarantees.
  Confirm current Gemini/ElevenLabs sponsor requirements before submission.
- **TO BE IMPLEMENTED:** privacy cleanup. Transcripts and answers currently print
  to stdout; remove those defaults or use explicit development-only logging
  with clear retention. Do not claim that nothing is logged.

Disclose cloud data flow: Gemini receives requested audio/images; ElevenLabs
receives answer text. Local memory storage does not make these calls local.

## 9. Help and optional remote assistance

Hold-to-talk/release-to-send, idle tap to repeat and thinking/speaking tap to
cancel without automatic repeat are decided in §2c. Double tap for local spoken
help/status is also decided: it requires no internet and contacts no one.
Locator sound is removed from scope; every double tap keeps the same help/status
meaning. A long hold cannot also activate help while assigned to push-to-talk.

Local status is implemented on `feature/guardian`: locator removed from the
voice code, help on its own worker so it never waits behind Gemini, stale help
requests skipped. **TO BE VALIDATED:** offline operation, physical gestures, repeated
help requests and cancellation without automatic repeat. Battery may be unknown; last landmark must
retain its observation time and must not imply current location.

Remote contact is **STRETCH / TO BE DECIDED**: retain phone-mediated handoffs or
build a separate authenticated phone/service integration. **Guardian Voice**
([spec](companion/guardian/specs.md)) is the planned Pi path: network-only,
separate from double-tap help, SMS to one configured contact via Twilio, sent
by the application only after a spoken preview and a fresh verbal yes.
ElevenLabs agent configured and API access verified. `GuardianController`,
the push-to-talk audio path and `SmsGate` are implemented and wired into the
on-device companion on `feature/guardian`; SMS runs with the fake sender
until a Twilio account and verified number exist (`TwilioSender` is built and
mock-tested); nothing has run on the Pi. The browser fallback
opens confirmed call/message/share actions; it does not prove delivery or
implement a standalone Pi emergency service.

Any new integration requires explicit confirmation for each call, location
share or single scene share, including recipient and payload. Preserve location
age/accuracy. Distinguish requested, handed off, delivered and failed states.
Opening help alone must not contact anyone.

## 10. Remember-this and landmarks

Separate storing a labeled observation, recognizing the same physical object,
and relocalizing to a saved place across sessions. The current last-landmark
label is not persistent object memory or localization.

- **STRETCH / TO BE IMPLEMENTED:** local labeled snapshot/crop, spoken label,
  observation time and optional pose tied to a map/session identifier, with
  deletion controls and a retention policy.
- **TO BE DECIDED:** accessible object selection/confirmation when several
  objects are visible.
- **TO BE IMPLEMENTED / VALIDATED:** instance re-identification against similar
  objects. An embedding match is evidence, not proof of ownership.
- **TO BE DECIDED:** image versus embedding storage and runtime after Pi
  profiling. Embeddings are not automatically anonymous or privacy-safe.
- **STRETCH:** persistent map relocalization and cross-session place guidance.
  Saved coordinates cannot be reused blindly after a map reset.

Say “last seen near … at …” rather than “you left it …” unless that event was
observed. An old observation does not prove the item remains there.

## 11. Milestones and acceptance evidence

Write/reconcile feature-local specs before implementation. Owners and detailed
scheduling are **TO BE DECIDED** before work starts.

1. **Integrate existing work and hardware.** IN PROGRESS on `integeration`:
   `main` and a voice companion snapshot are combined, voice drives the backpack
   guidance gate, and ICP + IMU is the launcher's odometry choice. Remaining:
   integrate the newer Guardian and hazard commits, merge `tactileESP32` with
   its existing direction-to-tactile bridge, verify the IMU transform, assemble hardware and
   establish concurrent camera/audio/hotspot/internet operation on the Pi.
2. **Complete audio warnings and failure handling.** Stage A geometry,
   all-state audio preemption, expiry and audible health signals are implemented
   on `feature/guardian` (`5fb2864`), with synthetic checks. Remaining:
   complete the camera coverage survey, measured mount configuration and Pi
   full-load acceptance tests. Tactile hazard patterns stay exploratory.
   Verify camera/pose/planner loss without stale guidance; include either-hand
   loss when tactile navigation is integrated. Audio-only development demos are
   supervised and stop on output failure; independent tactile fault signaling
   remains required before claiming unsupervised wearable readiness.
3. **Demonstrate one navigation task.** Controlled backpack approach, including
   cancellation, arrival, unknown-space rejection and no-path behavior.
4. **Demonstrate scene assistance and local help under load.** Ask/read/follow
   up, interrupt speech, remove internet, inject hazards in every voice state,
   and use help while a cloud request is pending.
5. **Then attempt stretch work.** Labeled memory first; re-identification,
   remote help and broader navigation after the core demonstration is reliable.
   The features the team selected to pursue are in §12; the judge dashboard
   (§12a) can start earlier because it only reads existing state.

| Check | Required evidence |
| --- | --- |
| Hazard reaction | Capture-to-perceptible-alert latency, missed hazards and nuisance alerts for named obstacle/coverage cases |
| Voice response | Button-release-to-first-audible-word, not first token/byte; existing 3 s companion target TO BE VALIDATED |
| Guidance freshness | Measured expiry after stopping upstream publishers with UDP alive; separately stop UDP and measure receiver timeout |
| Concurrent operation | Sustained mapping/detection/speech/tactile run; CPU, temperature, dropped frames and input age |
| Failure behavior | Camera/pose/planner/internet/hand loss produces understandable feedback and suppresses invalid movement guidance |
| Accessibility | Controls/cues usable without sight and with a cane; supervised controlled-area trials record success and confusion |
| Privacy / remote actions | No default sensitive transcript logging; deletion if memory is built; cancelling external actions produces no contact/share |

Record repeated trials, configuration, sample count, median, p95 and worst
observed delays. Exact hazard/expiry targets, error tolerances and endurance
duration remain **TO BE DECIDED / VALIDATED** before claiming success. Disclose
simulations and replays in the demonstration.

Earlier log figures (~0.58 s first token and ~1.2 s suggested response time)
are development observations, not full-system guarantees. A 1024-byte PCM chunk
containing ~23 ms of audio does not establish 23 ms playback-start latency;
image compression time is not upload time.

Latest hazard software evidence (`5fb2864`): 140 companion Python tests,
seven browser-help tests, native/ROS C++ geometry checks and 12 synthetic ROS
pipeline checks passed. The hazard and changed direction nodes compile in ROS
Jazzy arm64; the full mapper-package build was not completed in the minimal
test image because OpenCV development files were absent. Pi acceptance remains
open. These results precede ongoing multilingual edits; see `log.md`.

Historical review evidence (17:45): the Python suite ran 75 tests with 6 errors, all in
hazard tests whose fake lacks the `current` attribute that the in-progress
Guardian wiring reads; the working tree was being edited during the run. Seven
browser-help tests passed. These are software checks, not ROS integration,
flashed firmware or wearable validation. Tactile host build/test evidence
comes from its branch log and was not rerun in this review.

## 12. Features to pursue

**TO PURSUE:** selected by the team on 2026-09-26 as features we want. The
timed trail (§12c) is implemented inside the Guardian spec; the others have
no code or spec yet. Each needs its own `specs.md` before implementation (per
`AGENTS.md`), and none may delay milestones 1–4. The designs below are
starting positions for those specs, not decisions.

### 12a. Judge debug dashboard

**IMPLEMENTED** (spec at [companion/dashboard/specs.md](companion/dashboard/specs.md)):
live visual HUD for HackGT judges and observers on `companion/voice/web_test.py`,
fed by `GET /debug/state` on `companion/voice/pi_bridge.py` polling at 3 Hz.

- Shows: camera status and frame dimensions; guidance active, route validity,
  direction arrow (`forward`, `left`, `right`, `stop`, `idle`), and tactile
  flags (`0x01` front, `0x02` left, `0x04` right, `0x00` neutral) with animated
  SVG servo sweep visualizers; hazard radar card with severity, distance,
  phrase and age counters; Guardian Voice status (`idle`, `opening`, `active`,
  `speaking`), SMS state and observation trail count; active language.
- Real-time sub-system latency & vitals meter (camera latency, hazard C++ loop,
  A* planner cycle, HTTP RTT, and safety budget status).
- 2D Costmap / A* Radar Canvas displaying distance rings, heading vector,
  inflation zones, goal waypoint, and planned trajectory.
- Interactive Demo Simulator / Judge Controls drawer for one-click hazard,
  heading, and heartbeat-drop fail-safe evaluation.
- Multilingual demo switcher (`EN / KO / ZH / JA / ES`) updating speech and UI on the fly.
- Stale or missing data is explicitly flagged (`NO SENSING`, `STALE (>0.5s)`,
  `FAULT`), never as a blank reading as "clear".
- Non-blocking lock-free telemetry compilation in `pi_bridge.py`; zero effect
  on wearer safety loop.
- Full test coverage in `companion/tests/test_dashboard.py` (11 tests pass).
- Hardware Pi bridge validation pending during full wearable integration.

### 12b. "Take me back to where I started"

- The companion stores a start point: automatically at the first valid
  odometry after launch, replaced when the user says "remember this spot".
  Stored as a point in `odom`, tagged with the mapping session it belongs to.
- A spoken request returns a new `device_action` (`return_to_start`). The
  companion sends the stored point to the planner as a goal. No Gemini box or
  detector is involved, so no cloud call sits in the guidance loop.
- Reuses the planner work in [Navigate to a named object](companion/navigate/specs.md)
  §4.4: target ownership, `/target/clear`, `plan_to` results and
  `/target/status`. Needs one extra input, a goal point (for example
  `/target/point`, `geometry_msgs/PointStamped` in `odom`), because
  `/target/detection` expects an image box.
- Confirmation, distance and bearing come from planner status and are composed
  locally: "Guiding you back to where you started, about 6 meters, behind
  you." Arrival is spoken.
- Refuses, with a spoken reason, when tracking is lost or the map or session
  was reset since the point was stored. Saved coordinates cannot be reused
  across map resets (§10).
- The same caveats as §6 apply: unknown cells are allowed and a route is a
  cost preference, not proof of a clear path.

### 12c. Timed trail of observations in Guardian's text

**IMPLEMENTED** on `feature/guardian`, unit-tested; specified in the
[Guardian spec §7](companion/guardian/specs.md#7-sms--application-enforced-verbal-confirmation).
Preview length with a full trail not yet checked on the Pi.

- The session keeps the last three distinct landmarks, each with its camera
  capture time, and drops entries older than 30 minutes (provisional). Today it
  keeps only one.
- Guardian's SMS template ([spec §7](companion/guardian/specs.md#7-sms--application-enforced-verbal-confirmation))
  lists them newest first: "Recent camera observations: elevator sign at
  3:55 PM; Room 204 sign at 3:52 PM." The app writes this list, not the model.
- `get_status` returns the same trail. The agent's greeting keeps only the
  most recent observation so it stays short.
- Wording stays "the camera saw X at Y". A trail is a history of what the
  camera saw, not a route or a current location, and the text must imply
  neither.
- The spoken SMS preview reads the whole text, so it gets longer. Check that
  it stays tolerable to listen to.

### 12d. Other languages

- **IMPLEMENTED (`d14e428`):** Full multilingual support for English (`en`),
  Korean (`ko`), Chinese (`zh`), Japanese (`ja`), and Spanish (`es`).
- Scene questions: Gemini system prompt instructs answering in the user's spoken
  language while keeping `device_action` and schema fields in English. The
  companion's cloud TTS (`eleven_flash_v2_5`) is natively multilingual.
- Guardian: the agent uses `Flash` (`eleven_flash_v2_5`, ~98 ms latency), which
  supports 32 languages. Prompt configured to reply in the user's spoken language
  while tool calls stay in English.
- Local offline speech: configured via `DEVICE_LANG` env var (default: `en`).
  The phrase table in `companion/i18n/__init__.py` provides offline translations
  for all hazard warnings, status/battery announcements, error messages, and SMS
  previews, selecting `espeak-ng` or macOS `say` voices per language.
- SMS text stays in English for the contact; spoken preview is in `DEVICE_LANG`.
- **TO BE VALIDATED:** transcription and answer quality per language on the
  event network and hardware audio by ear.
