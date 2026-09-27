# Voice Companion — Specification

> This was the voice-only design before integration. The current branch also
> accepts spoken backpack guidance requests and gates the existing ROS direction
> output; see `companion/README.md` for the current behavior.
> The selected demo uses `PI_VOICE=1` on the Pi (button, microphone, speaker).
> The laptop browser remains a fallback for scene assistance, but does not
> supply the hazard/audio health lease now required for movement guidance.

On-device voice interface for the wearable spatial guide. Replaces the
phone/browser companion prototype in `companion/`.

Status: voice code exists; historical test results are recorded in `../log.md`.
2026-09-26 planning update: idle single tap repeats; a tap while thinking or
speaking cancels without automatic repeat; hold to talk and release to send is
also decided, along with double tap for local help/status. Locator sound is
removed from scope by user decision and from the voice code. The §2 mapping,
triple tap and the shared audio owner are implemented on `feature/guardian`
and unit-tested; Pi button timing is not yet validated. Audio hardware
options remain open, with an open speaker likely for the demo and bone conduction
the future product direction. Planned hazard behavior is not implemented merely
because it appears in this spec. Stage A geometry, strict hazard snapshots,
local phrases and an expiring guidance permission are now implemented in the
working tree; calibration, coverage and Pi performance are not verified.

--- 

## 1. What changes and why

The current companion works, but it assumes a phone: a browser UI, a pairing
code typed on a touchscreen, tap targets, and phone-mediated help actions. A
blind user walking with a cane has one hand occupied and no reason to hold a
second device. The interface should be the wearable itself.

| | Old (`companion/`) | New (this spec) |
| --- | --- | --- |
| Interface | Phone browser page | Button + microphone + speaker on the device |
| Input | Tap buttons, type or dictate | Hold button, speak |
| Output | On-screen text + browser TTS | Spoken audio only |
| Pairing | Token typed into a phone | None |
| Help mode | `tel:` / `sms:` / Web Share on phone | Local actions only (see §7) |
| Camera | ROS topic **or** phone photo upload | ROS topic only |

Unchanged: the ROS 2 navigate/warning stack, and Gemini as the scene reasoner.

### Scope boundary

This spec covers the **Ask/Answer** module only — scene questions, text reading,
and local help. The **Navigate** and **Warning** modules (SLAM, obstacle
detection, ESP32 haptics) are separate and are not redesigned here. §8 defines
how they interact.

---

## 2. Interaction model

**DECIDED — hold to talk, release to send; idle single tap repeats; a tap while
thinking or speaking cancels without automatic repeat; double tap opens local
help/status. No locator sound or second-double-tap action.** One physical button
is used. The table distinguishes approved behavior from existing code. Timing
thresholds remain implementation settings to validate on hardware. All planning
documents should refer to this shared decision record.

| Gesture/context | Current code / baseline | Approved behavior / implementation gap |
| --- | --- | --- |
| Hold, then release | Record question, then send it with a camera image | DECIDED by user: hold to talk, release to send |
| Single short press while idle | Repeat the last answer | DECIDED by user: repeat the last answer |
| Tap while thinking/speaking | Stops output and invalidates the answer; the release is consumed | DECIDED: cancel only, no automatic repeat. IMPLEMENTED |
| Double short press | Local help/status on its own worker, after the tap window | DECIDED by user: local spoken help/status, no internet required and no contact action. IMPLEMENTED |
| Another double tap | Local help/status again | Locator, arming state and locator prompts removed |
| Triple short press | Guardian entry point; announces that Guardian isn't available yet | DECIDED by user: enter Guardian Voice (`guardian/specs.md` §2). Taps resolve `TAP_WINDOW` (0.5 s) after the last tap, so double-tap help waits for that window; validate the delay on hardware |
| Hazard detected | Local tone + "Obstacle ahead." interrupts every state (hazard spec §7) | Automatic; no button activation needed |

**DECIDED:** a single short press while idle repeats the last answer; it does
not capture a new scene or ask Gemini again. With no previous answer, announce
“There is nothing to repeat yet,” matching the existing code.

**DECIDED:** a tap while thinking or speaking cancels the response and stops
ordinary playback without automatically repeating. Classify this tap by the
state at press time: its release must not become an idle-repeat action.
Invalidate late answer/audio output and consume that tap without dispatching
a new question. Cancellation need not wait for an outstanding network request
to finish. Implemented and unit-tested; a hold while busy still asks a new
question. A later separate idle tap can repeat the last answer.

**DECIDED:** hold the button to record a spoken question, then release to send
it with the camera image. This retains the existing push-to-talk interaction.
Timing thresholds remain implementation settings to validate on hardware.

**DECIDED:** double tap opens local spoken help/status. This action must work
without internet, must not wait behind a cloud request and contacts no one.
Report only known device status; unknown battery or other unavailable telemetry
remains unknown. Help runs on its own worker lane, separate from Gemini and
from cloud speech, so it never waits behind them.

**REMOVED FROM SCOPE:** locator sound. Every recognized double tap has the
same help/status meaning; there is no arming window or special second action.
Locator state, activation, prompts and tests are removed from the voice code.
A repeated help request coalesces: a worker skips any task superseded before it
starts, so pending status work never builds a queue. The user selected repeat over a fresh scene description
for idle tap.
Do not silently change controls when hardware or network fails.

### Timing and capture baseline (approved behavior above takes precedence)

| Gesture | Behavior |
| --- | --- |
| Press and hold (≥ 600 ms) | Listening tone, record while held |
| Release | Recording stops, thinking earcon, answer is spoken |
| Press while speaking | Stop playback immediately (barge-in) |
| Short press (150–600 ms) while idle | Repeat the last answer |
| Double short press | Enter Help mode (§7) |
| Press under 150 ms | Ignored as accidental contact, with a short low tone |

Press duration is only known at release, so recording starts on press and is
discarded if the press turns out to be short. This avoids clipping the first
syllable when the user starts speaking immediately.

Barge-in fires on press rather than on release, because stopping playback early
is harmless and waiting to classify the press would add a perceptible delay.

**Help is a double press, not a long hold.** A long hold is already how the user
talks, so it cannot also mean help. Help is additionally reachable by simply
saying "help", which Gemini returns as a device action (§5) — but the double
press exists because it is the path that still works with no network, which is
exactly when help may be needed.

Rationale for hold-to-talk over a wake word: no false triggers from ambient
speech, no always-on mic, and the recording boundary is unambiguous to the user.
The user always knows when the device is listening because they are holding the
button.

**Recording cap:** 30 seconds. At 25 s, play a warning tone. At 30 s, stop and
process what was captured rather than discarding it.

**Minimum press:** ignore presses under 150 ms as accidental contact — but do
not silently ignore them; play a short low tone so the user knows the press
registered as too brief.

### No fixed command grammar

The user speaks naturally. Gemini interprets. There is no list of magic phrases
to memorize, which is the point — "what's in front of me", "read that sign",
"is there anywhere to sit", and "how many doors" all take the same path.

Device-level actions (repeat, volume, help) are returned by Gemini as a
structured field rather than matched by a local parser. See §5.

---

## 3. Hardware

| Part | Choice | Notes |
| --- | --- | --- |
| Compute | Raspberry Pi 5 (existing) | Shared with the ROS stack |
| Button | Momentary push button on GPIO, internal pull-up | Debounce in software, 20 ms |
| Microphone | USB mic | Zero-config ALSA; avoids I2S device-tree work |
| Speaker | Options being explored; likely open speaker for demo | Bone conduction is the post-hackathon product direction; see below |
| Camera | RealSense, via existing ROS topic | No new camera handling |

**Audio hardware remains exploratory.** We expect to use an open speaker for
the hackathon demo and explore bone conduction for the actual product after
the hackathon. Neither device is finalized or validated. Keeping ears open is
the requirement; bone conduction is not a prerequisite for this demo. Compare
availability, warning latency, audibility, comfort and ambient-sound access.

**Camera task:** before wearable warning tests, complete the mounting/coverage
survey in `../ros_ws/src/hazard_warnings/specs.md` §4. Confirm which floor,
chest and head regions are visible and document blind zones and motion effects.

**Audio capture format:** 16 kHz, mono, 16-bit PCM, written as WAV. Gemini
downsamples audio to mono anyway, so capturing higher is wasted bytes. A 10 s
clip at this format is roughly 320 KB, far inside the 20 MB inline request
limit.

---

## 4. Pipeline

```
button hold ──► record mic ──┐
                             ├──► one Gemini request ──► {answer, landmark, action}
latest ROS frame ── JPEG ────┘                                    │
                                                                  ▼
                                              ElevenLabs stream ──► speaker
                                              (fallback: local TTS)
```

Components, each independently testable:

1. **Button watcher** — GPIO edge detection, debounced, emits press/release events.
2. **Recorder** — captures 16 kHz mono WAV to memory between press and release.
3. **Frame grabber** — reuse `companion/ros_camera.py` nearly as-is. It already
   subscribes to `/camera/color/image_raw`, enforces a 2-second freshness rule,
   rejects future timestamps, converts RGB8/BGR8, resizes to 1600 px, and JPEG
   encodes on demand. This is the single largest piece of reusable work.
4. **Gemini client** — sends audio + image + system prompt, receives structured JSON.
5. **Speech output** — ElevenLabs streaming to `aplay`, with a local fallback.
6. **Earcons** — short pre-generated WAVs played locally. Never network-dependent.
7. **Session state** — last image, last answer, and the last 4 exchanges, in memory.

### Latency budget

Perceived responsiveness matters more than answer quality here; a correct answer
arriving 8 seconds late is a worse experience than a good-enough answer in 2.

| Stage | Target |
| --- | --- |
| Button release → thinking earcon | < 50 ms (local, always) |
| Gemini round trip | 1–3 s |
| ElevenLabs first audio byte | 300–800 ms (streaming, do not wait for full file) |
| **Button release → first spoken word** | **< 3 s** |
| No answer yet at 5 s | Play a soft "still working" earcon, repeat every 4 s |

Stream the TTS. Do not buffer the complete audio file before playing — that
alone can double perceived latency on a long answer.

---

## 5. Gemini request contract

Single request per interaction: recorded audio + current camera frame + system
prompt + recent history.

**API surface:** The Interactions API is GA as of 2026 and is the recommended
target for new work. The legacy `generateContent` endpoint also accepts inline
audio and is what the current companion already uses successfully — if the
Interactions API migration costs more than an hour, ship on `generateContent`
and migrate later. Verify the exact request shape against current docs at
implementation time; do not copy the shape from this document.

**Response schema** — extends the existing `{answer, landmark}` schema the
current companion already uses, adding a device action field:

```json
{
  "answer":        "string, spoken aloud, at most 3 short sentences",
  "landmark":      "string, empty unless a clear distinctive landmark is visible",
  "device_action": "none | repeat | louder | quieter | stop | help | save_landmark | guardian | navigate_backpack | navigate_target | stop_navigation",
  "target":        "string, short noun phrase; empty unless navigate_target",
  "box_2d":        "[y_min, x_min, y_max, x_max] on 0-1000, or null; only for navigate_target"
}
```

`guardian` is returned when the user asks for guardian mode or says they are
lost and want help talking it through; it opens Guardian Voice
([guardian/specs.md](guardian/specs.md) §2). Plain "help" stays `help`.

`navigate_target` is an explicit request to be taken to a visible object other
than the backpack. Gemini's box goes to the planner, and the spoken reply is
composed locally from the planner's measured distance and bearing
([navigate/specs.md](navigate/specs.md)). "Where is X?" stays a question.

Routing device actions through the model rather than a local keyword matcher
means "say that again", "repeat", and "I didn't catch that" all work without
enumerating phrasings. The tradeoff is that device actions need network. This is
acceptable because every action in this list except `stop` is non-urgent, and
`stop` is bound to the button.

**Carry over the existing system prompt** from `companion/server.py`. It is
already well-tuned for this problem: image-relative left/center/right, no
inferred metric distance, no claims that unseen areas are clear, explicit
uncertainty on unreadable text, and treating image text as observation rather
than instruction. Add to it: answers are spoken, so no markdown, no lists, no
formatting — only plain sentences a voice can read naturally.

**History:** keep the last 4 exchanges attached to the same image, matching
current behavior. Invalidate the image and history after 60 seconds — a
wearable camera's scene goes stale far faster than a phone photo's. When the
user asks a follow-up after expiry, capture a fresh frame and say so.

---

## 6. Failure behavior

The user cannot see a log, a spinner, or an error toast. **Every state must be
audible, and the system must never go silent.** Silence is indistinguishable
from a dead device.

| Condition | Behavior |
| --- | --- |
| No network | Local TTS: "Scene questions are unavailable without network." Report warning/navigation health independently. |
| No recent camera frame | "I can't see anything right now — the camera isn't sending images." Do **not** answer from a stale frame. |
| Gemini error / timeout | "I couldn't get an answer. Try again." Play error earcon first. |
| ElevenLabs unavailable | Fall back to local TTS silently. The answer matters more than the voice. |
| Audio output device missing at boot | Inhibit guidance and stop the supervised demo. An independent tactile fault signal is a future requirement; its pattern is currently brainstorming, not an available fallback. |
| Microphone missing at boot | Announce voice-input failure through working local audio; retain automatic audio hazard warnings. No silent button remapping. |
| Recording was empty/silent | "I didn't hear anything." |
| Model expresses uncertainty | Pass it through verbatim. Never smooth uncertainty into confidence. |

A local TTS engine (Piper or `espeak-ng`) must be installed and working before
ElevenLabs is integrated, not after. It is the substrate that makes every
network failure recoverable.

---

## 7. Help mode

**DECIDED:** double tap opens local spoken help/status, without internet or
contacting anyone. Spoken "help" remains a separate cloud-interpreted baseline
path; it is not the offline shortcut. Locator sound is removed from scope;
repeat double taps remain help/status requests (§2).

Available actions, all local:

- **Status** — spoken: battery, network reachability, camera freshness, whether
  Gemini is configured, and the outcome of the last cloud request.
- **Last landmark** — the most recently observed landmark label with its
  observation time, stated explicitly as a past observation and *not* as the
  user's current location.

**Calling and messaging are out of scope for this build.** They required the
phone's `tel:`/`sms:` handlers, and this design has no phone. Adding them back
needs either a cellular HAT on the Pi or a background companion phone app over
Bluetooth. Recorded as future work; do not claim this capability in the demo.

Help mode itself stays local and contacts no one. The separate, network-only
Guardian Voice mode ([guardian/specs.md](guardian/specs.md)) adds an
application-confirmed SMS to one configured contact via Twilio; it does not
change this section.

---

## 8. Relationship to the ROS stack

This service runs as a **separate process** from the navigate and warning nodes
and must never block them.

- **Hazard warnings preempt speech.** If the warning module raises an obstacle
  alert, validate its schema/capture age, then cut lower-priority speech in every
  state. Stage A speaks generic torso/head obstacles with direction and urgency;
  floor/stair detection is not implemented. Health loss is spoken locally.
- `/hazard/guidance_permitted` is a true/false lease from the companion event
  loop; the direction node expires it after 500 ms. A true lease requires fresh
  healthy depth/body pose, working audio/phrase assets and no urgent event.
  Unmeasured calibration defaults, output loss and process death inhibit guidance.
- After a preemption, do not silently resume mid-sentence. Play a short tone;
  once idle and warnings allow it, a single short press repeats the last answer.
  A tap during ordinary thinking/speaking cancels without automatic repeat (§2).
  Urgent-hazard muting policy remains a separate interaction to review.
- The product target is **audio and tactile hazard feedback**. Complete and
  validate local audio first; tactile hazard patterns are brainstorming only.
  Follow `../ros_ws/src/hazard_warnings/specs.md` for audio priority, expiry and
  interruption in every state. Existing navigation servos do not establish
  implemented tactile hazard or audio-failure feedback.
- The Gemini path is network-bound, not CPU-bound, so it competes little with
  SLAM for CPU. Verify this holds under load — measure SLAM latency while
  requesting snapshots (this is already item 6 on the existing companion's
  validation list).
- Camera access is read-only via an existing ROS topic. This service must never
  open or claim the camera device.

---

## 9. Migration map

Concrete inventory of what survives the rewrite:

| File | Disposition |
| --- | --- |
| `companion/ros_camera.py` | **Reuse**, essentially unchanged |
| `companion/errors.py` | **Reuse** |
| `companion/server.py` → `SYSTEM` prompt | **Reuse**, add voice-output constraints |
| `companion/server.py` → `Gemini.answer()` | **Adapt** — add audio part, extend schema |
| `companion/server.py` → HTTP/token/handler layer | Delete |
| `companion/static/` (`index.html`, `app.js`, `style.css`) | Delete |
| `companion/tests/help.test.cjs` | Delete (tested phone-only help actions) |
| `companion/tests/test_camera.py` | **Reuse** |
| `companion/tests/test_server.py` | Rewrite against the new entry point |

Keep the old companion on a branch or tag before deleting — it is a working
fallback demo if the voice path fails on the day.

---

## 10. Privacy

- Audio and images are held in memory only, never written to disk.
- Audio is discarded immediately after the Gemini response returns.
- Images expire with their session (60 s, §5).
- Nothing is logged: not audio, not transcripts, not answers. The existing
  server already suppresses request logging; preserve that.
- Google receives the audio and images under the project's Gemini API terms;
  ElevenLabs receives the answer text. Say this plainly in the demo rather than
  implying on-device processing.

---

## 11. Open decisions

| Item | Status |
| --- | --- |
| Interactions API vs legacy `generateContent` | Decide at implementation; legacy is known-working |
| Audio hardware | Exploring options; likely open speaker for demo, bone conduction for product after hackathon |
| Button gesture mapping | Hold/release = ask; idle tap = repeat; thinking/speaking tap = cancel without repeat. Double tap = local help/status every time; triple tap = Guardian; locator removed. Implemented and unit-tested on `feature/guardian`; Pi button timing TO BE VALIDATED |
| Camera mounting/coverage | Required task before wearable hazard claims; hazard spec §4 |
| Tactile hazard feedback | Desired alongside audio; patterns/hardware exploratory, implement audio first |
| ElevenLabs latency over venue wifi | Unmeasured; local TTS fallback is mandatory regardless |
| Button placement on the wearable | Undecided — must be findable without sight, distinct from any other control |
| Speech rate / voice selection | Undecided; blind users commonly prefer faster-than-default rates |
| Accidental button press while walking | Needs a guard — recessed button, or ignore presses under 150 ms (§2) |
| Wake word | Explicitly out of scope |
| Other languages | TO PURSUE ([plan §12d](../plan.md#12d-other-languages)): Gemini answers in the language the user spoke; `device_action` stays English; ElevenLabs `eleven_flash_v2_5` is multilingual. Local offline phrases (hazard, help/status, errors) stay in one configured device language |
| Judge debug dashboard | TO PURSUE ([plan §12a](../plan.md#12a-judge-debug-dashboard)): read-only panel on the laptop page (`web_test.py`) fed by a Pi bridge `GET /debug/state`; no user function depends on it |
| "Take me back to where I started" | TO PURSUE ([plan §12b](../plan.md#12b-take-me-back-to-where-i-started)): new `return_to_start` device action; the goal comes from a stored `odom` point, not from Gemini |

---

## 12. Acceptance criteria

Verify the approved mapping on the actual Pi and selected demo audio hardware.
These checks do not establish completion of the separate hazard spec.

1. Holding the button, asking "what's in front of me", and releasing produces a
   spoken answer with the first word audible within 3 seconds.
2. A follow-up question referencing the same scene is answered from the same
   image, without a second capture.
3. Asking to read a sign transcribes it, and an unreadable sign is reported as
   unreadable rather than guessed.
4. Unplugging the network produces a spoken failure message, and the navigation
   stack keeps running.
5. Stopping camera frames produces "I can't see anything right now" rather than
   an answer from a stale image.
6. A hazard warning during playback cuts the speech.
7. A tap during thinking/playback cancels without automatic repeat on release
   or late answer playback. A separate idle tap repeats the last answer.
8. Nothing in the interaction requires sight, touch precision beyond one button,
   or a second device.
9. Double tap reports local help/status offline and while a cloud request is
   blocked. Repeat it within the former 20-second window: it still reports
   status, with no locator sound or locator prompt.

Criterion 8 is the one that matters. Test it by running the whole demo
blindfolded.
