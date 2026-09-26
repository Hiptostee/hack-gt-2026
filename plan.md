# Wearable Spatial Guide — Implementation Plan

**Version:** 1.2 · **Reviewed:** 2026-09-26 17:00 · **Event:** HackGT 2026

A wearable spatial guide for blind and low-vision people, built as a complement
to a white cane. The prototype adds forward chest/head-height obstacle alerts,
tactile guidance, and on-demand scene questions and text reading. It is not a
certified mobility aid or a replacement for the cane.

This is the canonical implementation plan. Source paths below are relative to
the repo.
Evidence comes from code, `AGENTS.md`, `log.md`, `companion/specs.md` and the
branches inspected on 2026-09-26: `feature/voice-companion` at `55dd3b1`,
`main` at `6b01845`, `tactileESP32` at `33c6434`, and `origin/integeration`
(spelled that way on the remote) at `1d902c7`. Branch contents do not
establish that the combined wearable has been tested.

### 1a. Integration branch status (`origin/integeration` @ `1d902c7`)

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

Gaps and regressions to resolve before merging:

- **Voice regressions.** Commit `b065484` copied an older snapshot of the
  companion, not `feature/voice-companion` head. The integration branch lacks
  SSE streaming with answer-first parsing, image downscaling, the
  `/hazard_warning` speech preemption, the speech speed setting, and the GPIO
  button keyboard fallback (`9d682f6`, `55dd3b1`). These need to be re-applied
  on top of the guidance changes.
- **Tactile not merged.** `tactileESP32` is not an ancestor of the integration
  branch; `/backpack/direction` still has no consumer that drives the hands.
- **Direction output goes silent rather than neutral.** When the gate or inputs
  go stale the direction node stops publishing. The tactile bridge must treat
  silence as expiry (§7); otherwise the sender keeps repeating the last cue.
- **Suspect IMU transform.** `camera_to_imu_tf` in `mapping.launch.py` is now
  `x = -2.0 m` (set in `b065484`). A 2 m camera-to-IMU offset is implausible on
  a wearable; confirm or correct before trusting fused odometry.
- **Demo audio lives on the laptop.** In the current demo the button,
  microphone and speaker are the laptop browser's, not the Pi's. The on-device
  `companion.voice` path exists but is not what `laptop_launch.py` runs.
- **No hazard detector yet.** Nothing on any branch publishes
  `/hazard_warning`; see §5.

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
  repeat; late response/audio output must stay canceled. This requires a code
  fix and has not been implemented by this documentation update.
- **DECIDED WITH THE USER:** hold to record a question, release to send it
  with the camera image; retain the existing push-to-talk interaction.
- **DECIDED WITH THE USER:** double tap opens local spoken help/status,
  without internet and without contacting anyone. It must not wait behind a
  cloud request; that scheduling behavior still needs integration work.
- **REMOVED FROM SCOPE:** locator sound. Another double tap remains help/status;
  remove the legacy arming window, sound activation and locator prompts. This
  cleanup is planned, not performed by the documentation update.
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
| Voice companion | `feature/voice-companion`: Gemini, ElevenLabs/local speech, button state machine | IMPLEMENTED; integration branch carries an older copy (see §1a). Current demo uses laptop browser audio; Pi button/mic/speaker demonstration TO BE VALIDATED |
| Hazard interruption | `/hazard_warning` subscriber and companion `_hazard()` on `feature/voice-companion` | PARTIAL: acts only in busy state; missing from integration branch; no detector publishes the topic |
| Tactile transport/firmware | `tactileESP32`: C++ sender, protocol, hotspot scripts, SG90 firmware, PlatformIO build/flash | IMPLEMENTED ON BRANCH; not merged into `integeration`; log records host builds/tests, not flashed hardware |
| Local help | Voice status and last landmark; legacy locator code remains pending removal | PARTIAL: scheduling can delay help behind cloud work; locator excluded from product scope |
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

**TO BE IMPLEMENTED:** output arbitration, bounded work queues, freshness checks
and all-state hazard handling. Alerts must work while idle, recording, thinking,
speaking or handling help. Local help must not wait behind a cloud request.
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

- **TO BE IMPLEMENTED:** geometry detector independent of successful SLAM or
  cloud responses. Aligned depth may suffice; a full point cloud is not an
  automatic requirement.
- **TO BE IMPLEMENTED:** timestamped events with direction, severity, expiry and
  sensor health; local alert output in every voice state.
- **DECIDED FOR INITIAL IMPLEMENTATION:** the feature spec defines a versioned
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
- **STRETCH / TO BE IMPLEMENTED:** arbitrary target selection. Gemini may help
  identify a visible target, but textual distance/bearing guesses must not
  directly command movement. Require localized image evidence, aligned depth,
  timestamped transforms and a reachable approach point.
- **TO BE IMPLEMENTED if arbitrary goals are added:** a goal-input interface.
  `/backpack/goal` is currently a planner output, not a command subscription.

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

**TO BE IMPLEMENTED:** ROS-to-tactile bridge with source-command expiry,
subscribing to `/backpack/direction` on the integration branch. That topic goes
silent (no neutral message) when guidance stops or inputs expire, so the bridge
must time out on silence. Rotate left/right (3, 4) also need a mapping onto the
three-flag packet. The sender repeats its last state; a stopped planner can therefore leave an old
direction active while the ESP32 still receives fresh packets. The receiver
watchdog cannot detect that. Expire old commands, send neutral, and announce
guidance loss even while the sender remains alive.

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

Local status code exists, alongside legacy locator code. **TO BE IMPLEMENTED:**
remove locator arming/activation/prompts and make help independent of pending
Gemini work. **TO BE VALIDATED:** offline operation, physical gestures, repeated
help requests and cancellation without automatic repeat. Battery may be unknown; last landmark must
retain its observation time and must not imply current location.

Remote contact is **STRETCH / TO BE DECIDED**: retain phone-mediated handoffs or
build a separate authenticated phone/service integration. **Guardian Voice**
([spec](companion/guardian/specs.md)) is the planned Pi path: network-only,
separate from double-tap help, SMS to one configured contact via Twilio, sent
by the application only after a spoken preview and a fresh verbal yes.
ElevenLabs agent configured and API access verified; no Guardian code yet;
depends on the shared audio owner. The browser fallback
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
   re-apply the newer voice commits, merge `tactileESP32` and write the
   direction-to-tactile bridge, verify the IMU transform, assemble hardware and
   establish concurrent camera/audio/hotspot/internet operation on the Pi.
2. **Complete audio warnings and failure handling.** First finish the camera
   coverage survey, then geometry detection, all-state audio preemption, command
   expiry and audible health signals. Tactile hazard patterns stay exploratory.
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

Review evidence: the Python suite completed with 44 passes and one skip; seven
browser-help tests passed. These are software checks from the plan review,
not ROS integration, flashed firmware or wearable validation. Tactile host
build/test evidence comes from its branch log and was not rerun in that review.
