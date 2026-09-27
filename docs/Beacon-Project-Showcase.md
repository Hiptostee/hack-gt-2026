# Beacon

**More context. Through sound and touch.**

Project showcase and presentation sourcebook · HackGT 2026 · September 27, 2026

## 01 / Project at a glance

Beacon is a wearable spatial-assistance prototype for blind and low-vision people. It brings together depth sensing, local mapping, spoken scene understanding, and a tactile guidance interface to add information beyond the reach of a white cane.

The experience is organized around everyday questions: What is in front of me? What does that sign say? Where is the backpack? Is the device still working? The intended interface is one physical button, a microphone, and audio that leaves the ears open, with directional cues delivered through two hand units.

**Beacon complements a white cane.** It is a hackathon prototype, not a certified mobility aid. The software includes scene questions, local upper-body warning logic, backpack route planning, and Guardian Voice. Tactile integration and validation of the assembled wearable remain unfinished in the reviewed repository.

### One-sentence description

Beacon combines depth sensing, spoken AI assistance, and a tactile guidance prototype to give blind and low-vision users more information about their surroundings alongside a white cane.

### Short Devpost description

A wearable spatial-assistance prototype combining local depth warnings, spoken scene understanding, and tactile guidance to complement a white cane.

### Thirty-second pitch

Beacon helps blind and low-vision people ask questions about the space beyond their cane. A depth camera feeds local warning and mapping software, while Gemini answers spoken scene questions and ElevenLabs voices the response. Our prototype also includes backpack route planning, a tactile hand interface on a separate branch, and Guardian Voice for conversational assistance. The key design choice is that urgent warnings stay local and can interrupt AI speech. We are now bringing these components together and validating the wearable.

### How to use this sourcebook

- Sections 02–04: Devpost project-story copy, written in the team's voice.
- Section 05: architecture and sponsor integration material.
- Sections 06–08: slide outline, spoken pitch, and demonstration sequence.
- Sections 09–10: judge Q&A, implementation status, and repository evidence.

**Review basis:** the local `feature/guardian` checkout at `3e24163`, including visible working-tree edits, plus relevant locally available tactile and named-target branch material. This is a repository-based description, not a new hardware test report.

## 02 / Devpost story: inspiration and experience

### Inspiration

A white cane gives its user direct, dependable information through contact. Our starting question was what a wearable could add: a sign across the room, the layout of an unfamiliar space, a visible object the user wants to find, or an obstruction above the cane's reach.

We designed Beacon around access to that extra information. The wearer should be able to ask a natural question and hear a concise answer without finding a screen or operating a phone. Audio should leave ambient sound available. A device failure should be communicated in a form the user can actually perceive.

Those requirements shaped the whole project. They led us to a physical button, local spoken status, a priority-based audio system, and a separation between time-sensitive depth processing and cloud-powered scene questions.

### What it does

Beacon's prototype brings together four areas of assistance:

**Ask about the scene.** Hold the button, ask a question, and release. The companion sends the recorded question and a current camera image to Gemini, then speaks the answer using ElevenLabs. Requests can include “What is in front of me?” and “Read that sign.” The model is instructed to report uncertainty and describe only what is visible.

**Warn about observed upper-body obstacles.** Our local C++ warning software processes depth geometry in configured torso and head zones. It produces short directional warnings through locally rendered speech. Warnings take priority over ordinary answers and Guardian speech. The software has synthetic test coverage; worn detection coverage and response timing still require measurement.

**Plan toward a visible backpack.** The robotics stack detects a dark backpack, estimates its position using depth, and computes a route through an occupancy map. Direction output is gated by current route and hazard/audio permission signals. A separate branch contains ESP32 hand-unit firmware and a sender that translates directions into servo cues. Combined wearable guidance is still being integrated and validated.

**Offer conversational assistance.** Guardian Voice uses an ElevenLabs conversational agent to help a user check device status or request a scene description. It also includes a controlled text-message workflow for one trusted contact: the device reads a preview, and a new affirmative user turn is required before sending. The recorded demo uses a fake sender; the Twilio adapter is implemented and mock-tested.

The companion also includes local phrase translations for English, Korean, Chinese, Japanese, and Spanish, plus a prompt for answers in the user's spoken language. Full interaction and pronunciation quality across languages remain to be validated.

## 03 / Devpost story: engineering and challenges

### How we built it

Beacon uses a Raspberry Pi 5 and an Intel RealSense D415 stereo depth camera. An external MPU6050 provides motion measurements. The main software layers are C++ ROS 2 nodes for perception and planning, and a separate Python voice companion.

The mapping stack uses RTAB-Map to build a spatial representation from RGB-D data. It includes visual-odometry validation, a stabilized pose path, and an optional ICP/EKF configuration. YOLOX, running through OpenCV DNN, detects backpacks; a darkness filter narrows the demonstration target. A heading-aware A* planner favors routes with fewer turns and greater obstacle clearance.

The immediate warning path operates independently of SLAM and cloud requests. It checks depth and motion data, projects observations into a configured body-relative volume, and publishes timestamped obstacle and sensor-health information. The companion rejects stale observations and plays local warning phrases through a shared audio output controller. Movement permission expires unless the companion keeps renewing it with healthy sensing and audio.

For scene assistance, one Gemini request carries the user's audio, an image, and recent conversational context. Its structured response separates spoken text from device actions. ElevenLabs streams the answer audio, with local synthesis available as a fallback. Guardian adds a conversational session with application-controlled tools for status, scene description, and message preparation.

Our development tools include a native macOS camera bridge into containerized ROS, a Pi-to-laptop camera bridge, RViz, and an observer dashboard. These let us inspect individual parts while developing across hardware and laptops.

### Challenges we ran into

**Keeping useful speech interruptible.** An answer can be correct and still be the wrong thing to hear during an obstacle warning. We built a single audio owner with explicit priorities and cancellation rules, including protection against cloud audio arriving after an interruption.

**Knowing when information is too old.** Camera frames, poses, routes, and speech requests arrive at different times. We added freshness checks and expiring permissions because a previous observation should not silently become a current instruction.

**Working within a shared compute budget.** The Pi runs camera processing, mapping, detection, planning, and voice services together. Detector throttling, bounded point-cloud processing, and separate workers help manage contention; combined-load performance still needs measurement.

**Handling the hardware we actually had.** The D415 has no onboard IMU. It required an external motion sensor, and mounting geometry must be measured before body-height warnings can be trusted. Native point-cloud generation also caused an ARM camera-process failure during development; the stack instead supports depth projection in a separate ROS node.

**Keeping the demonstration faithful to the build.** Features exist across branches, and the observer dashboard includes simulation. We distinguish implemented software, simulated inputs, and verified hardware behavior in our presentation.

## 04 / Devpost story: accomplishments and next steps

### Accomplishments that we're proud of

We built the software foundation for a wearable that connects spatial sensing with a nonvisual interaction model: RGB-D mapping, backpack detection and planning, a spoken companion, local warning logic, and Guardian Voice.

One of our strongest engineering results is the audio priority system. Hazard warnings can interrupt lower-priority speech, and cancelled responses cannot resume simply because a network request finishes later. Local help has a separate worker so it does not queue behind scene reasoning.

We also implemented a message-confirmation boundary outside the conversational model. Guardian can prepare a draft, but the application owns the recipient, preview, confirmation, and send. The message can include recent camera observations with their times, explicitly described as observations rather than the user's current location.

The repository records unit tests, synthetic ROS checks, and live laptop API experiments. These give us evidence about individual software components while making clear what remains to be proved on the assembled wearable.

### What we learned

Accessibility decisions reach far into system architecture. A missing sensor cannot be represented only by a red status light. A network delay needs audible feedback. Stopping an answer has to invalidate work that is still in flight. An old landmark needs an observation time.

We also learned to separate three different jobs: recognizing an object, measuring where it is, and deciding whether movement guidance is available. A scene model can help interpret an image; depth, tracking, planning, and runtime checks supply different kinds of evidence.

Most of all, we learned that a useful prototype needs to communicate its limits as clearly as its capabilities.

### What's next for Beacon

Our next milestone is a supervised, measured demonstration of the combined wearable: camera, local warnings, voice interaction, navigation, and tactile output running together.

That requires completing the mounting and coverage survey, reconciling the feature branches, validating hand-unit behavior, tightening unknown-space planning, and measuring warning onset and voice responsiveness under load. We also want feedback from blind and low-vision participants and orientation-and-mobility specialists to evaluate controls, cue comprehension, comfort, and interference with cane use.

Beyond that, we want to explore bone-conduction audio, broader visible-object selection, and remembering a starting point within a mapping session. Named-object navigation already has a separate software branch; return-to-start and persistent object recognition remain future work. Floor hazards, outdoor navigation, and emergency dispatch are outside the demonstrated scope.

### Built with

Raspberry Pi 5; Intel RealSense D415; MPU6050; ROS 2 Jazzy; RTAB-Map; C++17; Python; OpenCV DNN; YOLOX; A*; robot_localization; Gemini API; ElevenLabs TTS and Agents; NumPy; sounddevice; espeak-ng; Docker; Zenoh; RViz; HTML, CSS, and JavaScript.

Additional branch/optional integrations: ESP32, SG90 servos, Wi-Fi/UDP tactile transport, and a Twilio SMS adapter.

## 05 / Architecture and sponsor integration

### Three paths through Beacon

| Path | Input and processing | Output |
| --- | --- | --- |
| Local warnings | Aligned depth + camera calibration + motion checks → body-zone geometry → fresh hazard/health state | Locally rendered warning speech; expiring permission for guidance |
| Local planning | RGB-D mapping + backpack detection + depth projection → A* route → direction gate | Forward/left/right/rotation codes; tactile sender and hands on a separate branch |
| On-demand assistance | Button-held audio + camera snapshot + recent context → Gemini structured reply | ElevenLabs speech or local TTS; explicit device-action requests |

Guardian is an additional cloud conversation path. Its client tools can read local status, request a Gemini scene description, or prepare an SMS preview. It shares the companion's audio owner, so warnings take priority over agent speech.

### Why this separation matters

An image answer and a movement cue have different requirements. Beacon keeps immediate obstacle processing local and independent of whether a cloud model responds. The warning module also does not require a successfully built map. A spoken navigation request may use Gemini to interpret intent, but subsequent route computation and direction generation are local.

This is an architectural choice, not a measured latency guarantee. The assembled device still needs coverage, load, and response-time validation.

### Gemini's role

Gemini interprets a spoken request together with a camera image. It produces concise scene descriptions, visible-text readings, a transcript, an optional landmark, and a structured device action. Prompts prohibit invented metric distances, claims about unseen areas, and claims that a route is safe. These are prompt constraints, not guarantees that the model cannot make mistakes.

On the separate named-target branch, Gemini can propose an image bounding box. The planner uses the corresponding depth and capture-time transform to estimate a goal; the model does not supply the metric position.

### ElevenLabs' role

ElevenLabs provides streamed speech for ordinary answers and the conversational agent behind Guardian Voice. The project makes use of both speech generation and tool-enabled conversation. Local warning phrases and local help do not require ElevenLabs connectivity.

### Data flow to explain to judges

Scene questions send the requested audio and image to Gemini. Answer text goes to ElevenLabs for synthesis. Guardian sends push-to-talk audio to ElevenLabs and can request a scene description through Gemini. Configured SMS sending sends the contact number and message to Twilio. Development code logs some transcripts and answers, so Beacon should not be described as entirely local or as storing/logging nothing.

## 06 / Seven-slide presentation outline

Use one main idea per slide. Allow approximately three minutes for the talk; demonstrate separately if the judging format provides extra time.

### Slide 1 — Beacon: more context through sound and touch

**On slide:** Wearable spatial assistance for blind and low-vision people. Built to complement a white cane.

**Visual:** Actual prototype photo with three simple labels: depth camera, compute, nonvisual interface. Mark any unattached parts as planned/integration hardware.

**Speaker point:** Introduce the information gap using a sign across a room or an obstruction above cane reach. Avoid a fabricated user testimonial.

### Slide 2 — An interface built around the wearer

**On slide:** Hold to ask. Tap to repeat or interrupt. Double tap for local status. Triple tap for Guardian.

**Visual:** Four button gestures; show the idle/busy distinction for a single tap.

**Speaker point:** The intended interaction requires no screen. Open-ear audio and tactile cues are design requirements; wearing comfort and gesture usability still need evaluation.

### Slide 3 — Local sensing and on-demand reasoning

**On slide:** Local: depth warnings, mapping, planning. Cloud: scene questions and conversation.

**Visual:** The three paths from Section 05, with the warning-to-audio priority connection highlighted.

**Speaker point:** No LLM sits in the immediate hazard loop. Cloud delay must not hold the audio output needed for warnings.

### Slide 4 — From a visible backpack to a direction

**On slide:** Detect → measure depth → plan → issue a fresh direction.

**Visual:** A real YOLO/planner image and RViz map when available. Label the dashboard radar as illustrative if used.

**Speaker point:** This is a controlled backpack prototype. Tactile firmware exists separately; a planned route is not proof of a safe walking route.

### Slide 5 — A useful voice, with controlled actions

**On slide:** Gemini scene understanding. ElevenLabs speech and Guardian. Local message preview and confirmation.

**Visual:** A real scene question and answer, followed by the Guardian draft → preview → fresh yes → send sequence.

**Speaker point:** Distinguish an actual cloud conversation from simulated SMS delivery.

### Slide 6 — What we have proved, and what comes next

**On slide:** Software and synthetic tests. Laptop API experiments. Wearable integration and measurement next.

**Visual:** Three clearly labeled evidence groups; use the status table in Section 10.

**Speaker point:** Discuss one concrete challenge: stale information or speech preemption. Do not display dashboard timing constants as performance results.

### Slide 7 — The next milestone

**On slide:** Integrate. Measure. Evaluate with users.

**Visual:** Prototype beside a short roadmap: coverage, tactile integration, supervised usability evaluation.

**Speaker point:** Close with Beacon's purpose: more useful information, delivered in a form the wearer can choose to use.

## 07 / Three-minute spoken pitch

*Approximately 390 words; rehearse with the actual speaker. Stage directions and demonstration time are additional.*

A white cane gives its user direct information through contact. But some useful information is across the room or above the cane's reach: a sign, a backpack, or an upper-body obstruction.

We built Beacon to explore how a wearable can add that information through sound and touch.

Beacon is a spatial-assistance prototype for blind and low-vision people. The intended interface is one button, a microphone, open-ear audio, and tactile hand units. Hold the button to ask a question. Tap while idle to repeat an answer, or while it is speaking to interrupt. Double tap for local status.

Underneath that simple interaction are three software paths.

First, a RealSense depth camera feeds local obstacle-warning software. It checks observations in configured torso and head zones and produces short spoken alerts. Those alerts have priority over other speech, including a cloud answer that is still arriving.

Second, our ROS stack maps the space, detects a dark backpack, measures its position with depth, and plans a route. Direction output requires fresh route and hazard/audio permission signals. We also built a separate tactile branch that translates directions into servo movements on two hand units. Bringing those pieces together on the wearable is still in progress.

Third, Gemini receives a spoken question and a camera image. It can describe the visible scene or read visible text, and ElevenLabs speaks the answer. That cloud path is on demand; it does not run the immediate obstacle-warning loop.

We also built Guardian Voice with ElevenLabs Agents. A user can ask about the scene, check status, or prepare a text to a trusted contact. The device reads the preview, and a new affirmative user turn is required before the application sends it. Our recorded messaging demo uses a fake sender; the real SMS adapter is implemented but still needs live validation.

The hardest part was making these systems coexist without allowing old information or delayed speech to take control. We implemented expiring observations, interruptible audio, and local help that does not wait behind a model request.

Our evidence includes software tests, synthetic ROS checks, and live laptop API experiments. We still need to measure worn camera coverage, integrated performance, and tactile usability.

Beacon complements a white cane and is not a certified mobility aid. Our next step is a supervised, measured wearable demonstration and evaluation with the people the device is intended to serve.

## 08 / Demonstration sequence and captions

### A focused two-minute demonstration

| Time | Demonstration | Narration |
| --- | --- | --- |
| 0:00–0:15 | Show the actual rig and identify the camera and interface. | “This is Beacon. Today we are showing [name the actual hardware/laptop configuration].” |
| 0:15–0:40 | Ask about a visible scene, then ask to read a prepared sign. | “This request sends the question and image to Gemini; ElevenLabs voices the answer.” |
| 0:40–1:00 | Show a warning interrupting speech, using a calibrated bench setup or an explicitly labeled injected event. | “The warning is local and has priority.” If injected: “This is a simulated sensor event testing the audio path.” |
| 1:00–1:25 | Show backpack detection and an actual ROS planner output. If hands are integrated and validated, demonstrate their cues while stationary. | “Depth locates the target; the local planner computes this route. These are prototype direction cues.” |
| 1:25–1:45 | Double tap for local status; show Guardian only if already rehearsed. | “Local status remains available without a cloud response. Guardian is the separate online conversation mode.” |
| 1:45–2:00 | Show the current status and next milestone. | “Our next step is validating the complete wearable under load and with user feedback.” |

For a shorter slot, prioritize scene assistance and one interruption demonstration. Guardian and multilingual examples can be follow-up demonstrations.

### Demo boundaries that follow from this repository

The default hazard configuration intentionally blocks guidance until mounting and coverage are measured. Keep that gate intact. The browser scene-assistance fallback does not provide the hazard/audio permission required by the current direction node. If the complete wearable is not ready, show the working subsystems and explain their connection.

The dashboard's simulation controls override display state. They do not by themselves exercise the physical detector, audio owner, or ESP32 receivers. An injected ROS hazard tests more of the software path, but still does not establish real obstacle detection. A simulated drop-off is not implemented floor-hazard sensing.

### Ready-to-use image and video captions

- **Prototype:** “Beacon's development hardware: a RealSense D415 depth camera and Raspberry Pi compute, with a nonvisual wearer interface under integration.”
- **Mapping:** “RTAB-Map and the local A* planner visualize a route toward a detected backpack. Controlled prototype; route validity does not establish walking safety.”
- **Voice:** “A spoken question and camera snapshot go to Gemini; ElevenLabs speaks the response.”
- **Warning injection:** “Simulated hazard input demonstrates local speech interruption. Worn detection coverage is pending measurement.”
- **Dashboard:** “Observer dashboard with live fields where connected and explicitly simulated/illustrative elements.”

## 09 / Likely judge questions

### Why build a wearable when a phone can describe images?

Beacon explores a body-mounted depth view, a physical control, and continuous local sensing alongside on-demand questions. That creates a way to connect scene interpretation with local geometry and tactile output. The current browser interfaces are development and demonstration paths; the wearer-facing design targets the device itself.

### What is distinctive about the project?

The combination of depth-based warning software, local spatial planning, a nonvisual interaction model, and cloud conversation with shared audio priorities. The engineering contribution is in how those parts exchange observations, expire old information, and control output. We are not claiming to be the first assistive wearable.

### What happens when the internet goes down?

Scene questions and Guardian require connectivity. Local warning processing, its resident phrases, and local status are designed to remain available when their hardware is healthy. Route computation is local, but starting guidance through a spoken intent uses Gemini in the current implementation. Beacon should not be described as fully functional offline.

### Does Gemini tell the wearer where it is safe to walk?

The scene prompt explicitly prohibits that. Gemini interprets requests and visible content. Local depth, mapping, planning, and permission checks handle geometry and direction output. The planner still allows unknown map cells in the reviewed configuration, so a route is not a safety guarantee.

### Does Beacon detect stairs or drop-offs?

The implemented warning stage covers generic upper-body depth geometry. Floor hazards and semantic hazard labels are later work. A drop-off button in the dashboard is a simulation option, not evidence of detection.

### Can it find my specific backpack?

The current detector recognizes the backpack category and applies a darkness filter. It does not identify ownership or reliably distinguish identical bags. A separate branch expands selection to named visible objects, but it is not integrated into this checkout or validated for worn goal accuracy.

### Is Guardian an emergency service?

No. It is conversational assistance with an optional trusted-contact SMS workflow. The application requires a preview and fresh confirmation. The recorded sender is fake; a submitted message is also distinct from confirmed delivery. Guardian cannot promise that help is coming or identify the user's current location from a landmark.

### How fast, accurate, or affordable is it?

The repository does not establish full-system warning latency, obstacle-detection accuracy, runtime, final weight, or a priced bill of materials. Report measured results only when a trial configuration and method are available. The dashboard timing values are not wearable benchmarks.

### Have blind or low-vision users evaluated it?

No such evaluation is documented in the reviewed repository. That is a next step, alongside orientation-and-mobility input. Software tests and a team demonstration do not establish user benefit or usability.

## 10 / Presenter evidence and status

*Internal preparation notes. Use these to keep submission copy aligned with the actual demo configuration.*

| Area | Evidence in the reviewed repository | Defensible status |
| --- | --- | --- |
| Scene questions and speech | Gemini client, audio/image requests, ElevenLabs streaming, local speech fallback; laptop API experiments in the log | Implemented software with recorded laptop evidence; Pi interaction pending |
| Local upper-body warnings | C++ geometry/policy, strict snapshot consumer, local phrase bank and audio priorities | Implemented with synthetic tests; mounting, worn coverage and latency unverified |
| Backpack mapping/planning | RTAB-Map launch, YOLOX detector, depth projection, A* planner and gated direction node | Implemented prototype; no verified complete wearable approach |
| Tactile guidance | Sender and ESP32/SG90 firmware in `origin/tactileESP32` | Separate branch; flashing and integrated worn operation not established by project records |
| Guardian and SMS | Controller, push-to-talk audio, observation trail and confirmation gate; fake-sender laptop experiments | Guardian software exists; real Twilio delivery and Pi operation unverified |
| Multilingual experience | Five-language phrase table and same-language Gemini prompt | Partial implementation; some dynamic text, local voice paths and SMS confirmation remain English-oriented |
| Observer dashboard | HTTP snapshots, UI, simulated state overrides, illustrative radar | Implemented observer tool with integration gaps; not a performance or actuator-verification instrument |
| Named-object navigation | Separate `feature/navigate-target` branch, target box/projection/status software | Branch implementation with recorded synthetic/laptop checks; not integrated here |
| Return-to-start and persistent recognition | Roadmap/spec discussion | Future work |

### Evidence interpretation

The log records a Stage A run of 140 companion Python tests, seven browser-help tests, native/ROS geometry checks, and 12 synthetic ROS pipeline checks. Later entries record 11 dashboard tests. These are historical results from different development snapshots, not a new combined suite result or proof of hardware readiness. No performance or hardware tests were run while preparing this document.

### Details to resolve before stronger demo claims

- Dashboard vitals contain fixed values (30 FPS; 33/16/42 ms; a 91 ms budget). Browser HTTP round-trip time is measured separately. The radar draws an illustrative curve rather than consuming the actual A* path.
- The default Pi bridge attaches camera and guidance objects, but does not wire the on-device Guardian and hazard-state objects into its aggregator. Their dashboard cards therefore cannot be assumed to show live companion state.
- The dashboard mapping treats direction code 3 as stop and omits code 4, while the ROS node uses 3/4 for rotate left/right. Its hand animations cannot establish correct physical command handling.
- Guardian entry does not yet implement the planned session-wide navigation pause. The current local SMS affirmation matcher recognizes English phrases. Do not promise complete multilingual contact confirmation.
- The SLAM camera-to-IMU offset needs verification; calibration/coverage defaults remain false; unknown map cells remain allowed in planning. These are relevant to a wearable navigation claim.

### Repository source map

Paths below are relative to the repository root. Current code and measured records take precedence where older specifications conflict.

- **Purpose and scope:** [AGENTS.md](../AGENTS.md), [plan.md](../plan.md), [log.md](../log.md).
- **User interaction and AI:** [companion specification](../companion/specs.md), [event loop](../companion/voice/__main__.py), [Gemini client](../companion/voice/gemini.py), [speech](../companion/voice/speech.py), [audio owner](../companion/voice/audio.py).
- **Warnings and coverage:** [hazard package](../ros_ws/src/hazard_warnings/README.md), [geometry](../ros_ws/src/hazard_warnings/include/hazard_warnings/geometry.hpp), [coverage record](../ros_ws/src/hazard_warnings/coverage.md), [snapshot validation](../companion/voice/hazard_state.py).
- **Mapping and guidance:** [ROS launch](../ros_ws/src/realsense_mapper/launch/mapping.launch.py), [planner](../ros_ws/src/realsense_mapper/src/backpack_path_planner_node.cpp), [direction node](../ros_ws/src/realsense_mapper/src/backpack_direction_node.cpp).
- **Guardian and languages:** [Guardian controller](../companion/guardian/session.py), [SMS gate](../companion/guardian/sms.py), [phrase table](../companion/i18n/__init__.py).
- **Observer UI and evidence:** [Pi bridge](../companion/voice/pi_bridge.py), [browser dashboard](../companion/voice/web_test.py), `companion/tests/`, `ros_ws/src/hazard_warnings/test/`.
- **Separate branch material:** `origin/tactileESP32:companion/voice/tactile_link.py`, `origin/tactileESP32:tactileESP32/esp32/tactile_hand/hand.cpp`, `feature/navigate-target:companion/navigate/specs.md`.

Before submitting, supply the final repository/video links and team credits, and update only the capability claims backed by newly recorded demonstration evidence.
