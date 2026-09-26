# Voice Companion — Specification

> This was the voice-only design before integration. The current branch also
> accepts spoken backpack guidance requests and gates the existing ROS direction
> output; see `companion/README.md` for the current behavior.
> For the current demo, the push-to-talk button, microphone, and spoken answer
> are in the laptop browser. Gemini runs from the laptop web service; the Pi
> supplies the ROS camera frame and guidance through an SSH tunnel.

On-device voice interface for the wearable spatial guide. Replaces the
phone/browser companion prototype in `companion/`.

Status: implemented and tested, 2026-09-26. 48 unit tests passing, web push-to-talk
simulation and hardware selftest verified.

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

One physical button. Push-to-talk, walkie-talkie style.

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
| Speaker | **Bone conduction transducer** | See below |
| Camera | RealSense, via existing ROS topic | No new camera handling |

**Bone conduction is a real requirement, not a preference.** Blind and
low-vision users rely heavily on ambient sound for spatial awareness and
traffic. Occluding either ear with an earbud removes information they depend on
and is a safety regression. If bone conduction hardware is unavailable for the
demo, use a small open speaker — never in-ear or over-ear headphones, and say so
explicitly in the demo.

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
  "device_action": "none | repeat | louder | quieter | stop | help | save_landmark"
}
```

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
| No network | Local TTS: "No network. Navigation is still working." |
| No recent camera frame | "I can't see anything right now — the camera isn't sending images." Do **not** answer from a stale frame. |
| Gemini error / timeout | "I couldn't get an answer. Try again." Play error earcon first. |
| ElevenLabs unavailable | Fall back to local TTS silently. The answer matters more than the voice. |
| Audio output device missing at boot | Hard failure. Escalate to the haptic channel — a distinct ESP32 pattern — because a spoken error is useless with no speaker. |
| Microphone missing at boot | Same as above. |
| Recording was empty/silent | "I didn't hear anything." |
| Model expresses uncertainty | Pass it through verbatim. Never smooth uncertainty into confidence. |

A local TTS engine (Piper or `espeak-ng`) must be installed and working before
ElevenLabs is integrated, not after. It is the substrate that makes every
network failure recoverable.

---

## 7. Help mode

Double-press the button, or say "help". Opening Help contacts no one.

Available actions, all local:

- **Locator sound** — loud pulsed tone from the device speaker, 15 s or until
  the button is pressed. Helps a nearby person find the user, or the user find a
  set-down device.
- **Status** — spoken: battery, network reachability, camera freshness, whether
  Gemini is configured, and the outcome of the last cloud request.
- **Last landmark** — the most recently observed landmark label with its
  observation time, stated explicitly as a past observation and *not* as the
  user's current location.

**Calling and messaging are out of scope for this build.** They required the
phone's `tel:`/`sms:` handlers, and this design has no phone. Adding them back
needs either a cellular HAT on the Pi or a background companion phone app over
Bluetooth. Recorded as future work; do not claim this capability in the demo.

---

## 8. Relationship to the ROS stack

This service runs as a **separate process** from the navigate and warning nodes
and must never block them.

- **Hazard warnings preempt speech.** If the warning module raises an obstacle
  alert while an answer is being read, duck or cut the speech. A scene
  description is never more important than a stair edge.
- After a preemption, do not silently resume mid-sentence. Play a short tone;
  the user can short-press to repeat from the start.
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
| Bone conduction hardware availability | Unverified — check inventory today |
| ElevenLabs latency over venue wifi | Unmeasured; local TTS fallback is mandatory regardless |
| Button placement on the wearable | Undecided — must be findable without sight, distinct from any other control |
| Speech rate / voice selection | Undecided; blind users commonly prefer faster-than-default rates |
| Accidental button press while walking | Needs a guard — recessed button, or ignore presses under 150 ms (§2) |
| Wake word | Explicitly out of scope |

---

## 12. Acceptance criteria

The build is done when, on the actual Pi with the actual hardware:

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
7. Short-pressing during playback stops it immediately.
8. Nothing in the interaction requires sight, touch precision beyond one button,
   or a second device.

Criterion 8 is the one that matters. Test it by running the whole demo
blindfolded.
