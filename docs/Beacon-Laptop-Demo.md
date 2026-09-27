# Beacon: one laptop demo for hackathon judges

Use **one stationary D415 view**, **one chair**, and **one readable room sign**.
The laptop supplies the microphone, speakers, controls, Gemini, ElevenLabs and
Guardian. The Pi supplies RGB/depth, mapping and the target planner. This replaces
the missing wearable button and dedicated microphone.

## The three-minute sequence

| Time | Operator action | What judges see/hear |
| --- | --- | --- |
| 0:00–0:20 | Explain Beacon and show the camera view. | “Beacon adds spatial information to a white cane. This is a stationary prototype demonstration.” |
| 0:20–1:00 | Hold Space: “What is in front of me? Read the sign.” | Gemini describes the current image; ElevenLabs speaks; a distinctive visible sign can enter the observation history. |
| 1:00–1:45 | Enter “chair” in Find an object & guide. Click Find, inspect the box, then Guide to chair. | Find highlights the candidate without routing. Guide identifies it in a new frame; the Pi uses that frame's timestamp and aligned depth for a route result or concrete failure. |
| 1:45–2:35 | Click Open Guardian; wait for ACTIVE; hold Space: “What did the camera last see?” | The existing ElevenLabs agent receives the recent observation and timestamp. Guidance stops on entry. |
| 2:35–3:00 | End Guardian, then Stop. | Summarize the camera → model → depth/planner → accessible feedback integration. |

Keep the camera still during inference. Choose a clearly separated chair in good
light, about 2–3 m away, with a textured background. Build a small map before the
judged sequence. Test the exact position and wording beforehand. Use only one
distinct chair: Guide reidentifies the requested label, not a tracked object ID,
so similar objects could be confused. Selection expires after 60 seconds; Stop,
a new Find or opening Guardian clears it.

The default hazard configuration is **uncalibrated**. The laptop demo does not
publish a positive hazard/audio permission lease. A successful target request
therefore reports a **stationary route preview**, with movement inhibited. Do not
change the calibration flags or publish a fake permission just to make the demo
move. There is no walking, stairs/drop-off detection, or verified wearable safety
claim in this sequence. ESP32 hands are not needed and the third `tactileESP32`
branch has not been folded into this two-feature integration.

## What is integrated

Code from `feature/navigate-target` at `b04eb8d` is incorporated in
`feature/guardian`, committed at `ffaa730`. Review fixes follow that integration;
see [merge readiness](Beacon-Merge-Readiness.md). Main has not been changed.
The other worktree is unchanged. The integration includes:

- Gemini target schema, normalized object boxes, timestamped camera frames and
  Pi HTTP metadata, target commands/events, ROS depth projection and target ownership.
- Guardian lifecycle, tools and SMS consent gate; one shared in-memory `Session`
  supplies scene history and observed landmarks to the laptop Guardian.
- `/`: the original dashboard with laptop push-to-talk, typed fallback, Find →
  inspect → Guide, live camera, Stop, Repeat, local status and Guardian controls.
  `/demo` opens the same dashboard. Radar, telemetry, language and judge simulation
  controls remain available; the radar is illustrative, not a live map.
- Expiring browser heartbeat, request generations and serialized local actions.
  Stop suppresses pending cloud replies and prevents them starting a new route.
- Hazard permission stays fail-closed. Direction codes 3/4 correctly mean rotate
  left/right. Missing measurements display as unknown rather than constant timings.

Scene audio plays in the browser. Guardian uses the existing native Python audio
owner and laptop microphone. The browser releases its mic when Guardian opens.
Guardian texting always uses `FakeSender`, even if the environment has Twilio
credentials. Say “texting is simulated” if you demonstrate it. Prefer leaving
texting out of the three-minute sequence.

## Prepare the laptop

From the repository root, using the same Python interpreter for installation and
launch:

```bash
python3 -m pip install -r companion/demo/requirements.txt
python3 -B scripts/demo_preflight.py
```

The launcher reads `.env` without executing shell commands. Exported environment
values take precedence. Required for the full sequence:

```dotenv
GEMINI_API_KEY=your_key
ELEVENLABS_API_KEY=your_key
ELEVENLABS_AGENT_ID=your_configured_guardian_agent
# Optional: select the model/voice already tested with your account.
# GEMINI_MODEL=...
# ELEVENLABS_VOICE_ID=...
```

Do not commit `.env`. Keep the existing Guardian agent configuration described in
[agent.md](../companion/guardian/agent.md). A key alone does not create an agent.
The configured agent needs its existing `get_status`, `describe_scene`, and
`prepare_sms` client tools and dynamic variables.

On macOS, allow microphone access for **Chrome** and the **terminal/Python host**
that launches Guardian. Set built-in microphone and speakers as the default audio
devices. Use open speakers; keep volume moderate to avoid feedback.

## Prepare and start the Pi

Put this integrated source tree on the Pi before building. An older installed
bridge does not have frame timestamps or named-target endpoints.

```bash
cd ros_ws
source /opt/ros/jazzy/setup.bash
rosdep install --from-paths src --ignore-src --rosdistro jazzy -y
colcon build --packages-select hazard_warnings realsense_mapper --cmake-args -DCMAKE_BUILD_TYPE=Release
cd ..
PI_VOICE=0 bash scripts/pi_launch.sh enable_icp:=false
```

Use the normal D415/IMU connection already configured for the project. ICP is
excluded from this narrow demo to reduce load. Keep `PI_VOICE=0`: the laptop is
the interaction owner, and starting a second companion creates competing guidance
publishers. Hardware coverage and the camera/IMU transform remain separate
validation work.

In a laptop terminal:

```bash
python3 -B scripts/laptop_launch.py --pi-ip 100.73.168.115 --pi-user raspi
```

Replace the address/user if this Pi uses different SSH details. The launcher
opens the SSH tunnel, checks the bridge, starts the threaded web server and opens
**http://localhost:8080/**. Docker is optional: append `--viewer` only if you
have already tested RViz. Leave it off for the minimal demo.

With the tunnel running, in a second laptop terminal:

```bash
python3 -B scripts/demo_preflight.py --pi-url http://127.0.0.1:8081
```

Click **Enable mic**. Hold the talk button or Space and
release to send. Typed questions use the same Gemini/target pipeline. Escape or
Stop cancels speech and pending replies and stops guidance. One operator tab
owns the demo; closing it or losing its heartbeat stops the session.

## Rehearse once, then freeze the setup

1. Verify a **live changing D415 image** and a timestamped frame in preflight.
2. Ask the scene question. Confirm intelligible laptop audio and a correct,
   observed sign in the history. Do not claim a landmark if the model omitted it.
3. Find the chair using the object card. Inspect the box, then click Guide to chair.
   Confirm a fresh identification and a real planner response.
   “No map,” “no depth,” or “stale” is a failed route attempt, not a successful demo.
4. Open Guardian. Wait for ACTIVE. Hold for longer than 0.6 s before speaking;
   release to receive its answer. Confirm it references the actual observation.
5. Press Stop while Gemini is thinking. Confirm no late answer or target starts.
6. Disconnect the Pi bridge. Confirm the camera/route are unavailable; there is
   no silent switch to an old image or “clear” claim.
7. End Guardian and verify another scene question works after re-enabling the
   browser mic. Close the tab and confirm Guardian’s mic/session stops.

No hardware-dependent step above has been marked passed by software unit tests.
Measure the actual end-to-end delay during rehearsal; the dashboard no longer
presents hardcoded numbers as timing measurements.

## Fallbacks

- **Mic permission trouble:** type the two scene questions. Guardian still needs
  native laptop microphone access; skip that segment if its audio setup fails.
- **Pi unavailable:** `python3 -B scripts/laptop_launch.py --standalone` uses a
  laptop webcam after permission. It can show questions and object boxes, but has
  no depth route. Say this is the camera-only fallback.
- **Static rehearsal:** add `--standalone --image /path/to/scene.jpg`. The page
  labels it as static. Never substitute it during a claimed live D415 demo.
- **ElevenLabs TTS unavailable:** the browser speaks the answer locally. State
  that the sponsor voice service fell back; do not claim that output used ElevenLabs.
- **Guardian connection unavailable:** its status/error is reported. Show the
  saved observations and finish the scene/target sequence.
- **Slow target inference:** the planner rejects old imagery. Retry once with a
  clear view and a short request. Do not enlarge freshness limits at the table.

## Verification recorded in this integration

- 212 Python tests pass, including Find without routing, fresh-frame Guide,
  missing/expired/stopped selections, HTTP target transport, shared history, typed
  payloads, cancellation races, missing live camera, browser heartbeat, Guardian
  recovery, rotation codes and permission expiry.
- 10 Node tests pass, covering browser help plus dashboard rotation, missing
  camera/measurement data and stale telemetry rendering. The dashboard's inline script and
  external operator controls pass Node syntax checking.
- Chrome inspection verified the unified dashboard, Find, highlighted candidate,
  Guide, Stop and radar switching using simulated model/planner responses. Earlier
  checks covered typed missing-key feedback and local status playback indication.
- Local preflight confirms configured credential presence, dependency imports,
  offline speech and supported default laptop input/output formats. It does not
  establish actual microphone capture quality.
- Live synthetic cloud check: Gemini returned a valid structured response;
  ElevenLabs returned speech audio (not played); the configured Guardian agent
  issued a signed session URL. No microphone samples or messages were sent.
  A full Guardian conversation still needs a spoken rehearsal.
- Both ROS packages compile in an isolated ARM64 Ubuntu 24.04/Jazzy container;
  hazard geometry, synthetic target routing and synthetic hazard pipeline tests
  pass. Installed Pi-bridge imports/assets, HTTP startup and Ctrl-C cleanup pass.
  The actual Pi was unreachable over SSH; its build, D415 routing and spoken
  rehearsal remain pending. No D415, ESP32 or walking test is claimed.

Feature contract: [companion/demo/specs.md](../companion/demo/specs.md).
