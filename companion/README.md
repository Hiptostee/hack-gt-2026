# Scene and help companion

Two front ends share this package:

- **`companion.voice`** — the on-device voice service. One button, a microphone,
  a speaker, no phone. This is the design target; see [specs.md](specs.md).
- **`companion.server`** — the original phone/browser demo, documented below.
  Kept as a working fallback until the voice path is proven on the Pi.

## Voice companion

Button mapping (implemented, unit-tested; Pi timing not yet validated): hold to
talk and release to send; one tap repeats the last answer; two taps give local
spoken help/status; three taps are the Guardian entry point (Guardian itself is
not built yet). A tap while thinking or speaking only cancels. Help needs no
internet, contacts no one and runs on its own worker. The locator sound is
removed. See [specs.md §2](specs.md#2-interaction-model). Audio hardware options remain exploratory: likely open speaker
for the hackathon demo, bone conduction for the future product after it.
Hazards should eventually use both audio and tactile feedback; implement local
audio first and keep tactile patterns as brainstorming. Complete the camera
coverage task in the [hazard spec](../ros_ws/src/hazard_warnings/specs.md) before
wearable capability claims.

```bash
pip3 install sounddevice numpy          # Pi also needs: sudo apt install libportaudio2
sudo apt install espeak-ng              # local speech fallback (macOS uses `say`)

export GEMINI_API_KEY='your-key'
export ELEVENLABS_API_KEY='your-key'    # optional; falls back to the local engine
export ELEVENLABS_VOICE_ID='voice-id'   # optional; the first available voice is used

python3 -m companion.voice --ros --pin 17          # on the Pi
python3 -m companion.voice --image path/to.jpg     # dev machine, keyboard instead of GPIO
```

Without `--pin`, a keyboard stand-in replaces the button: Enter starts and ends a
turn, `r` is a short press, `h` is a double press, `g` is a triple press, `q`
quits. Without `--ros`, `--image` supplies a static JPEG so the pipeline can be
exercised off the robot.

Taps resolve half a second after the last one, so help starts after that pause.
Help uses the local speech engine so it still works with no network.

All sound goes through one output owner in `voice/audio.py`: hazard warnings,
then help, then ordinary answers, with earcons mixed on top. A hazard warning
("Obstacle ahead.", rendered locally at startup) cuts off anything less
urgent in any state, and the cut-off speech never resumes. Button presses do
not silence a warning.

With `--ros`, the camera is shared with the independent YOLO and mapping nodes.
Ask for a scene description while YOLO keeps tracking the black backpack. Saying
"bring me to the backpack" asks Gemini to return `navigate_backpack`; the voice
service checks `/backpack/path_valid` and activates `/backpack/guidance_active`.
The existing A* planner continuously refreshes `/backpack/path`, and the direction
node emits `/backpack/direction` only during an active guidance session. Say
"stop guidance" to end it. Start the ROS backpack stack with
`enable_backpack_stack:=true`; without a current valid route, the voice service
reports that guidance cannot start.

`ELEVENLABS_MODEL` defaults to `eleven_flash_v2_5` for latency. If ElevenLabs
rejects it, the HTTP error is printed and speech falls back to the local engine
— check stderr rather than assuming the network is down.

### Web Testing & Push-to-Talk Simulator

For testing on laptops or desktop environments without wiring physical GPIO:

```bash
python3 -m companion.voice.web_test --image scene.jpeg
```

Opens `http://localhost:8080`. Features:
- **Push-to-talk button**: Click & hold or hold Space to simulate the physical hardware button.
- **Dual audio playback**: Plays voice output via Web Audio API in the browser and directly through the host computer's speakers (using `afplay` on macOS or `mpv`/`aplay` on Linux).
- **Realistic Pi earcons**: Synthesizes listening, thinking, and error sound effects.
- **Live debug stream**: Shows Gemini and ElevenLabs timing breakdowns, request latency, and raw subsystem logs.

### Current demo: click and speak on the laptop, camera and navigation on the Pi

Run one command on the Pi from the repository root. It starts the Zenoh router,
mapping, YOLO backpack guidance, and the camera/guidance bridge. Build the ROS
workspace after pulling updates (`cd ros_ws && colcon build --packages-select
realsense_mapper && cd ..`). The Pi does not need a Gemini key:

```bash
./scripts/pi_launch.sh
```

Run one command on the laptop from the repository root:

```bash
python3 scripts/laptop_launch.py
```

The laptop launcher uses the Pi's Tailscale address `100.73.168.115` by default.
Change it with `--pi-ip ADDRESS` if needed. It prompts for the Gemini API
key if `GEMINI_API_KEY` is unset, asks for the Pi SSH password, opens the browser
voice page, and starts the Docker RViz viewer. Connect TigerVNC Viewer to
`localhost:5901`. `Ctrl-C` in each terminal stops its services. An optional
ElevenLabs key may be set on the laptop for synthesized speech; browser audio
works without it.

Open `http://localhost:8080`. Click and hold the browser button to speak through
the laptop microphone, then release it. The laptop fetches one current JPEG from
the Pi through the tunnel and sends the audio and JPEG to Gemini from the laptop.
Answers play in the laptop browser. A spoken backpack navigation request goes
back through the tunnel to activate the ROS guidance gate on the Pi.

### Model & Performance Configuration

- `GEMINI_MODEL` defaults to `gemini-3.5-flash-lite`, delivering consistent multimodal responses.
  - Set via environment variable: `export GEMINI_MODEL="gemini-3.5-flash-lite"`
  - Streaming SSE: Responses use `:streamGenerateContent?alt=sse` with `answer` ordered first in the JSON schema, reducing time-to-first-token to ~0.58s.
  - Automatic image downscaling: Images >1024px are downscaled to ~100KB JPEG using Pillow, OpenCV, or macOS `sips` before transmission, cutting upload latency by ~95%.
- `COMPANION_SPEECH_SPEED`: Playback speed factor for voice responses (default `1.15`, 15% faster). Supports ElevenLabs `voice_settings.speed` and local `say`/`espeak-ng`.
- `--hazard-topic`: ROS 2 topic (default `/hazard_warning`). Current handling only interrupts a busy companion and plays a stopped tone; payload-aware spoken warnings in every state are planned in the hazard spec, not implemented yet.

### Raspberry Pi Production Deployment Notes

When deploying `python3 -m companion.voice` to Raspberry Pi hardware:

1. **Audio Devices (ALSA)**: Ensure your default ALSA capture and playback devices are configured properly in `~/.asoundrc` or `/etc/asound.conf`, since USB mics and audio jacks are on separate cards.
2. **Microphone Capture Gain**: Audio with peak amplitude below `MIN_PEAK = 200` is discarded to prevent hallucination on silence. Run `alsamixer` (`F4` Capture) on the Pi and turn mic gain up to 80–90%.
3. **Button Wiring**: The GPIO button uses internal pull-up (`pull_up=True`). Wire your momentary switch between the GPIO pin and **GND (Ground)**, not 3.3V.
4. **Local Speech Fallback**: Install `espeak-ng` (`sudo apt install -y espeak-ng libportaudio2`) so status and emergency help speech work offline without internet.
5. **Camera Frame Staleness**: The ROS bridge requires camera frames fresher than 2.0s. Keep system clocks synchronized between robot and camera publishers.

### Bring-up self-test

Checks each stage separately, so a failure points at one component instead of
"it didn't talk". The spoken question is synthesized locally rather than
recorded, so the Gemini and ElevenLabs path can be tested before the microphone
works.

```bash
python3 -m companion.voice.selftest --image path/to/scene.jpg
python3 -m companion.voice.selftest --skip-mic --text "What does the sign say"
```

It reports the Gemini round-trip time and the ElevenLabs time to first audio
byte — the number the 3-second target in [specs.md](specs.md) is measured
against.

Tests: `python3 -m unittest discover companion/tests`

---

## Phone/browser companion (original demo)

A separate, on-demand service for scene questions, finding visible targets, and
reading signs/text with Gemini. It does not publish movement commands or block
the navigation nodes. Help actions are phone-mediated and do not require Gemini.

## Run without ROS (phone photo or laptop file input)

From the repository root, with Python 3.9 or newer:

```bash
export GEMINI_API_KEY='your-key'
# Optional: choose a vision-capable model available to your Gemini project.
export GEMINI_MODEL='gemini-3.8-flash'
python3 -m companion.server
```

Open `http://localhost:8088` and enter the pairing code printed by the server.
Choose a photo, then Describe scene, Read text, or Ask. Follow-up questions use
the same image and the last four question/answer pairs. Use a new snapshot when
the scene changes. Gemini is called only after an explicit question/read action.
Without an API key, image selection and help controls work, but AI answers do not.
No placeholder answers are returned. The Gemini key is never sent to the browser.

## Use the wearable's existing ROS camera

On the Pi, install the optional bridge dependencies:

```bash
sudo apt install python3-opencv python3-numpy ros-jazzy-rclpy ros-jazzy-sensor-msgs
source /opt/ros/jazzy/setup.bash
source ros_ws/install/setup.bash
# Match ROS_DOMAIN_ID and RMW_IMPLEMENTATION to the running camera stack.
export ROS_DOMAIN_ID=42
python3 -m companion.server --ros
```

Run this in a separate terminal alongside `hardware.launch.py`. It subscribes to
`/camera/color/image_raw`; override with `--topic`. It does not open or claim the
USB camera itself. Only the latest image is retained, and JPEG encoding occurs
on request. Frames older than two seconds, missing timestamps, or timestamps
more than 100 ms in the future are rejected. Keep host/ROS clocks synchronized.
The bridge accepts RGB8/BGR8. It does not use depth to infer target distance.

## Connect a phone

For an initial trusted-LAN image demo, add `--host 0.0.0.0` and visit
`http://PI_LAN_ADDRESS:8088`. The phone and Pi must be able to reach each other.
Do not expose this development server directly to the public internet.

**Phone geolocation and native file sharing require a secure browser context.**
Plain HTTP to a Pi LAN address generally cannot provide them. Serve with a TLS
certificate trusted by the phone and valid for the address used:

```bash
python3 -m companion.server --ros --host 0.0.0.0 \
  --cert /path/to/trusted-certificate.pem --key /path/to/private-key.pem
```

Alternatively use an authenticated HTTPS reverse proxy on your private network.
A self-signed certificate warning does not establish a trusted secure context.
The default server is loopback-only. All image/status API calls require its
pairing token. `COMPANION_TOKEN` can set a stable code (at least 24 characters).
HTTP sends the token and images unencrypted; use HTTPS for actual phone use.

## Scene features

- Describe visible features and locate visible targets relative to the image.
- Ask questions such as “How many doors are visible?” or “Where is the counter?”
- Read room numbers, restroom signs, menus, notices, and transit text. Unreadable
  text should be reported as uncertain, not completed by guessing.
- Read answers aloud through browser speech synthesis. Optional browser dictation
  fills the question box; the user still chooses Ask. Browser dictation may use
  the browser vendor's speech service and is not supported everywhere.
- A clearly observed landmark label can be saved locally with its observation
  time. It is a model-generated observation, not a localization result. Uploaded
  photos have unknown capture times; they are never labeled as current location.

Photos are resized to at most 1600 pixels along their longest side. Small text
in the D415's 640x480 image may remain unreadable; move closer while stationary
or use a higher-resolution phone photo. This is snapshot assistance, not target
tracking, route guidance, or proof that an area is safe. Phone coordinates are
not sent to Gemini.

## Help features and confirmation

Set a trusted phone number, including country code. Hold Help for two seconds,
or tap and confirm (keyboard/screen-reader alternative), to open help controls.
Opening help does not contact anyone. Each call/message/photo-share action has
its own explicit confirmation. The model has no tools that can activate them.

- **Call:** opens the phone's `tel:` handler for the saved person. It cannot
  verify whether a call connected and does not auto-call emergency services.
- **Location:** asks the phone for a fresh fix, displays time and accuracy, and
  keeps it in browser memory. After confirmation, opens an SMS draft with a map
  link. User sends it in their messaging app. Fixes older than two minutes are
  rejected. Actual phone accuracy and SMS URI behavior vary by platform.
- **Scene:** after confirmation, opens the native share sheet with one selected
  image and its time information. The user chooses the recipient and sends it.
  Location is not attached. Unsupported file-sharing browsers show an explanation.
- **Legacy locator (excluded from current product scope):** existing browser
  code plays a pulsed tone on the phone for 15 seconds or until stopped.
  Phone volume, mute settings, connected headphones, and browser behavior affect
  audibility. This does not control an ESP32 buzzer or the Pi speaker.
- **Status:** reports whether the wearable server is reachable, camera freshness,
  Gemini configuration, and the outcome of the last cloud request. It does not
  claim cellular connectivity from Wi-Fi or browser online status. Browser battery
  is shown only when supported; Pi battery remains unknown without monitoring hardware.
- **Last landmark:** can be spoken, with the original image's time/source. It
  must not be treated as the wearer's current location.

Help calling/SMS needs a capable phone and its normal service. Desktop browsers
may have no `tel:`/`sms:` handler. No call, message, or third-party share is
performed by the Python server. The help UI must already be loaded to use its
local controls when the Pi connection fails; offline page reload is not supported.

## Privacy and retention

Images and scene conversations are held in server memory, capped at four images
and four exchanges per image, and expire after ten minutes. Delete scene removes
the selected image/history; server restart removes all server images/history.
Deleting during an in-flight request cannot retract an image already sent to
Gemini. Google receives requested images/questions under your Gemini API project's
terms; this app makes no claim about Google's retention policy.

Only the trusted number and last landmark label/time persist in this browser's
local storage. Forget saved number and landmark removes them and clears the
in-memory location. Pairing codes stay in tab memory. Request logs are suppressed;
images, location, and questions are not written to disk by the service.

## Validation

```bash
python3 -m unittest discover -s companion/tests -v
node --check companion/static/app.js
node --test companion/tests/help.test.cjs
```

Tests use a fake Gemini response or mocked HTTP transport, not paid API calls.
Before a wearable demo, verify on the actual Pi and phone:

1. Capture from ROS, ask a scene question, read a room sign, and ask a follow-up.
2. Stop camera frames and confirm capture is rejected while help stays usable.
3. Remove internet access and confirm cloud errors do not block local navigation.
4. Cancel every help confirmation and verify no external action opens.
5. With your own test recipient, confirm `tel:`, SMS draft, geolocation, native
   photo share, and locator stop behavior on the intended phone/browser.
6. Run mapping concurrently and measure its latency while requesting snapshots.

References: [Gemini generateContent](https://ai.google.dev/api/generate-content),
[model availability](https://ai.google.dev/gemini-api/docs/models),
[Web Share](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/share),
[Geolocation](https://developer.mozilla.org/en-US/docs/Web/API/Geolocation_API).
