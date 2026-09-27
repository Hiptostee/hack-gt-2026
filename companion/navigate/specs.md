# Navigate to a Named Object — Specification

## Combined branch contract (2026-09-27)

Integrated with Guardian's hazard permission gate in the current working tree.
A successful planner response without a fresh hazard/audio permission is a
**stationary route preview**, explicitly spoken as such, with guidance inactive.
Directions 3 and 4 are rotation left/right. Stop invalidates a pending planner
request so its later response cannot reactivate guidance. The laptop adapter
preserves capture timestamps, supports typed requests and shares observation
history with Guardian. See [../demo/specs.md](../demo/specs.md).


The user holds the button and says *"take me to the water fountain."* Gemini
finds the object in the current camera frame and returns a 2D box. The ROS
planner turns that box into a remembered 3D goal using aligned depth and the
camera pose **at the moment the frame was captured**, then the existing A* and
direction pipeline guides the user there.

Status: implemented 2026-09-26 on branch `feature/navigate-target`; **not yet
run on the Pi or the worn hardware**. Verified so far:
- Companion unit tests.
- The planner built in a ROS 2 Jazzy container and driven by
  `scripts/voice_target_smoke.py` with simulated map, depth, TF and odometry.
- One live Gemini run on `scene.jpeg`: the action was right on 6 of 6 spoken
  requests, and 3 of 3 boxes landed on the object.

This extends the backpack guidance already on the integration branch
(`navigate_backpack`, `RosGuidance`, `backpack_path_planner_node`,
`backpack_direction_node`). Nothing here counts as working until it passes §11
on the worn hardware.

Code lives outside this folder:
- `companion/voice/target.py` (new: box conversion, planner message, spoken
  sentences)
- `companion/voice/gemini.py`, `companion/voice/__main__.py`,
  `companion/voice/guidance.py`, `companion/voice/pi_bridge.py`,
  `companion/voice/web_test.py`, `companion/ros_camera.py`
- `ros_ws/src/realsense_mapper/src/backpack_path_planner_node.cpp`
- `scripts/voice_target_smoke.py` (new: planner check without a camera)

---

## 1. What changes and why

Today guidance can only target a dark backpack, because the only detector is
YOLOX filtered to COCO class 24. Anything else a blind user wants to reach — a
water fountain, a trash can, a door, a counter — has no path to the planner.

Gemini already sees the camera frame on every spoken request, and it can return
open-vocabulary bounding boxes. This feature uses that single existing request
to also **locate** the named object, then hands the box to the planner's
existing, already-tested depth-projection path.

| | Backpack guidance (unchanged) | Named-object guidance (new) |
| --- | --- | --- |
| Detector | YOLOX, continuous at ~4 FPS | Gemini, once per spoken request |
| Target updates | Re-detected live, smoothed | Fixed at request time, remembered in `odom` |
| Spoken trigger | "take me to the backpack" | "take me to the \<anything else\>" |
| `device_action` | `navigate_backpack` | `navigate_target` (new) |
| Confirmation speech | Local | Local, with measured distance and bearing |

### Why the companion does not compute the 3D point

The planner's `detection_callback` already matches a depth frame to the image
timestamp (≤ 70 ms), takes the median depth of the box's center third,
back-projects in the depth image's optical frame and transforms to `odom` at
the image timestamp. Reusing it avoids three bugs a companion-side
implementation would have to solve again:

1. **Motion during the Gemini call.** The wearer keeps moving for 2–4 s. The
   goal must be computed from the depth frame and camera pose at capture time,
   not "now."
2. **Frame convention.** Back-projected coordinates are in the optical frame
   (z forward), not `camera_link` (x forward).
3. **Dependencies.** The companion would need depth and `camera_info`
   subscriptions and numpy math on the Pi.

The companion's job is therefore: get a box, convert it to pixels of the
original ROS frame, and publish it with that frame's ROS timestamp.

### Scope boundary

- **In scope:** one static object that is visible in the current frame, at
  0.3–6 m, indoors, in a mapped area.
- **Unchanged:** backpack guidance, hazard warnings (they still preempt all
  speech), button mapping, scene questions, help, Guardian.
- **Excluded:** objects out of view or behind the user (no scanning or
  search), moving targets such as people, continuous Gemini re-grounding,
  multi-stop routes, remembering targets across sessions, stairs and
  outdoor use, and any claim that the route is safe.
- **"Where is X?" is a scene question, not a navigation request.** Only an
  explicit request to go, be taken or be guided somewhere starts guidance.

---

## 2. Interaction model

### Happy path — DECIDED

1. User holds and says *"take me to the water fountain,"* then releases.
2. Thinking earcon (existing). Working earcon after 5 s (existing ticker).
3. Gemini returns `device_action: navigate_target`, `target: "water
   fountain"`, `box_2d: [y_min, x_min, y_max, x_max]` on a 0–1000 scale.
4. The planner projects the box, plans, and reports distance and bearing.
5. Companion speaks a **locally composed** sentence:
   *"I think I see the water fountain about 3 meters away, slightly to your
   right. Guiding you now."*
6. Tactile/direction output runs from the existing direction node.
7. On arrival: *"You should be near the water fountain. It should be about an
   arm's length ahead."* Guidance stops.

Gemini's own `answer` is **not spoken** when a box is returned. Distance and
"guidance started" come only from local, measured state, which the existing
system prompt already requires ("never infer metric distance", "never claim
guidance has started"). Gemini's answer is spoken only when there is no box,
where it explains what it sees instead.

### Spoken phrasing — DECIDED (wording tunable)

- **Distance:** "about N meters away", rounded to the nearest half meter;
  under 1 m says "less than a meter away."
- **Bearing** (measured in `camera_link`, positive = left), from the current
  camera pose when the plan succeeds:

| Bearing | Phrase |
| --- | --- |
| ≤ 15° | "straight ahead" |
| 15–45° | "slightly to your left" / "slightly to your right" |
| 45–120° | "to your left" / "to your right" |
| > 120° | "behind you" (the user turned during the request) |

- **"I think I see"** is always used, because the identification comes from
  Gemini and may be wrong.

### Replace, stop, cancel — DECIDED

- **A new navigation request replaces the current target**, whether it is a
  backpack or another object. The new confirmation sentence is the
  announcement.
- **"Stop guidance"** (`stop_navigation`) clears the target in the planner and
  says "Guidance stopped." (existing sentence).
- **A tap while speaking** cancels speech only; guidance keeps running. This
  matches existing behavior.
- **The target expires** after 5 minutes without arrival: *"Guidance to the
  water fountain timed out."* (Provisional.)

### Multiple matches — DECIDED

Gemini returns exactly one box, for the most prominent or nearest-looking
instance. The spoken bearing comes from the measured goal, so it cannot
contradict the box.

---

## 3. Hardware and dependencies

- D415 aligned depth at the color resolution (`align_depth.enable: true`,
  already set in `mapping.launch.py`). The Pi launch uses 640x480x15.
- Fresh visual odometry (`/visual_odom_valid`) and `/rtabmap/map`, which the
  planner already requires.
- `GEMINI_API_KEY`. No new cloud service, library or process.
- Model: the existing `GEMINI_MODEL` (default `gemini-3.5-flash-lite`). How
  good its boxes are is **unmeasured**; see §11 and §13.

---

## 4. Pipeline

```
button release
  → capture_frame(): JPEG + ROS stamp + original width/height   (Pi)
  → Gemini: audio + JPEG → {device_action, target, box_2d, answer}   (laptop or Pi)
  → validate box, descale 0–1000 → pixels of the ORIGINAL ROS frame
  → RosGuidance.go_to(label, pixel_box, stamp)   (Pi)
       publishes /target/detection, waits ≤ 2 s for matching /target/status
  → planner: depth at stamp → optical point → odom at stamp → A* → status
  → companion speaks the local sentence; guidance_active = true
  → direction node → /backpack/direction → tactile (existing)
  → planner status "arrived" / "tracking_lost" → spoken asynchronously
```

### 4.1 Frame metadata

`RosCamera` gains `capture_frame()`, which returns a dict:
`{jpeg, captured_at, stamp_sec, stamp_nanosec, width, height}`. `width` and
`height` are the **ROS message dimensions before any resize**. Aligned depth
has the same dimensions, so pixel boxes must be in that space. `capture()`
stays as a wrapper returning `(jpeg, captured_at)` so existing callers are
untouched.

- **Laptop demo path:** the Pi bridge's `GET /frame` adds headers
  `X-Frame-Stamp: <sec>.<nanosec 9 digits>`, `X-Frame-Width` and
  `X-Frame-Height`. `RemotePi.capture_frame()` reads them.
- **`StaticImageCamera` / webcam frames have no ROS stamp.** A navigation
  request from them says *"Guidance needs the live camera."*

Normalized coordinates survive the companion's downscaling (1600 px, then
1024 px) because the aspect ratio is preserved.

### 4.2 Gemini contract changes (`companion/voice/gemini.py`)

Schema additions:

```python
"target": {"type": "STRING"},
"box_2d": {"type": "ARRAY", "items": {"type": "INTEGER"}, "nullable": True},
```

Both are added to `required`. `navigate_target` is added to the
`device_action` enum. The key is named `box_2d` because that is the name
Google's detection examples use.

System prompt additions (draft):

```
Set device_action to navigate_target when the user asks to go to, be taken
to, or be guided to a visible object or place other than the backpack.
Asking where something is, is a question, not a navigation request.
For navigate_target, set target to a short noun phrase for the object, and
set box_2d to [y_min, x_min, y_max, x_max] for the single most prominent or
nearest matching instance, each value on a 0-1000 scale relative to the
image. If the object is not clearly visible, set box_2d to null and say in
the answer that you do not see it. Never box something you are unsure of.
For every other action, set target to an empty string and box_2d to null.
```

`_parse` validation: the box is kept only when the action is
`navigate_target` and the box is 4 integers in 0–1000 with
`y_min < y_max` and `x_min < x_max`. Otherwise it becomes `None`. `target` is
stripped of `"` and `\` and truncated to 40 characters.

### 4.3 Detection message (`/target/detection`, `std_msgs/String`)

Same shape as `/yolo/black_backpack`, plus `request_id`:

```json
{"stamp":1790000000.123456789,"request_id":"v1790000003-7","detections":[{"label":"water fountain","confidence":1,"x":212,"y":140,"width":96,"height":180}]}
```

> **Constraint:** the planner parses this with regexes that are sensitive to
> key order and spacing (`parse_best_box`, `detection_stamp`). Emit compact
> JSON with `confidence, x, y, width, height` in exactly that order, and
> format `stamp` by hand from the integer sec/nanosec fields. A float cannot
> hold 9 decimal places at this magnitude.

### 4.4 Planner changes (`backpack_path_planner_node.cpp`)

1. **Shared projection function.** Extract the body of `detection_callback`
   into one function returning either an `odom` point or a failure reason
   (`tracking_lost`, `no_map`, `no_camera_info`, `no_depth_frame`,
   `no_depth`, `too_far`, `no_tf`, `stale`). The YOLO callback keeps its
   throttled logs and ignores the reason. The voice callback reports it.
2. **Target ownership.** A `target_source_` member is either `backpack`
   (default) or `voice`. The YOLO callback returns early while the source is
   `voice`, so a backpack in view cannot steal the target.
3. **`/target/detection` subscription.** A new voice target replaces
   `target_odom_` outright (no smoothing), clears the current path, publishes
   `path_valid=false`, sets the source to `voice`, then plans immediately.
4. **`/target/clear` subscription (`std_msgs/Empty`).** Resets
   `target_odom_`, clears the path, sets the source back to `backpack` and
   publishes status `cleared`. Sent by `stop_navigation`, by
   `navigate_backpack` (so the backpack can take over) and on target expiry.
5. **`plan_to` returns a result** (`ok`, `no_tf`, `outside_map`,
   `no_free_cell`, `no_path`) instead of returning silently.
6. **Voice-only depth checks** (provisional ROS parameters): median depth at
   most `voice_max_depth` = 6.0 m (otherwise `too_far`), and at least
   `voice_min_valid_fraction` = 25 % of the sampled pixels valid (otherwise
   `no_depth`). Reject if the detection stamp is more than `voice_max_age` =
   8 s older than now (`stale`). The TF cache defaults to 10 s. A malformed
   message gets `invalid`.
7. **Depth buffer sized by time, not frame count.** Keep the last
   `depth_buffer_seconds` = 10 s, capped at 600 frames. That is 150 frames at
   15 fps (≈ 90 MB at 640x480, 16-bit) and 300 at 30 fps. Today's fixed 150
   frames is only 5 s at 30 fps.
8. **`/target/status` publisher (`std_msgs/String`, JSON)**, for voice
   targets only:

```json
{"request_id":"v1790000003-7","state":"ok","distance_m":3.1,"bearing_deg":-22.0}
```

   `state` is one of: `ok`, `near`, the failure reasons from items 1 and 5,
   `arrived`, `tracking_lost`, `tracking_restored`, `expired`, `cleared`.
   `near` means the goal is inside the 0.7 m standoff distance: report it, but
   do not guide. Distance and bearing are from the **current** `camera_link`
   pose to the remembered target.
   - Arrival fires **once**, when the camera is within `arrival_distance` =
     1.0 m of the target (0.7 m standoff + 0.3 m margin; provisional).
     Expiry is `voice_expiry` = 300 s.
   - Tracking loss and restoration fire on transitions only.
9. **Overlay.** Project the remembered voice target into
   `/backpack/planner_image` as a marker plus label. Do not draw Gemini's box
   there: it belongs to a frame several seconds old.

Topics keep their `/backpack/*` names (`path`, `path_valid`, `goal`,
`guidance_active`, `direction`). Renaming them is out of scope.

### 4.5 Guidance bridge (`companion/voice/guidance.py`)

New method `RosGuidance.go_to(label, pixel_box, stamp) -> str`:

1. Generate a `request_id`. Publish the `/target/detection` message.
2. Wait up to 2.0 s for `/target/status` with that `request_id`.
3. On `ok`, set `active = True` (the existing 5 Hz `guidance_active`
   publisher) and return the confirmation sentence. On anything else, return
   the matching failure sentence from §6 and leave guidance inactive.

`stop()` also publishes `/target/clear`. `start()` (backpack) publishes
`/target/clear` first.

Asynchronous events (`arrived`, `tracking_lost`, `tracking_restored`,
`expired`) are turned into sentences and delivered by an `on_event(text,
priority)` callback:

- **On-device** (`__main__.py`): the callback puts a `("guidance", text,
  priority)` event on the main loop. The loop speaks it through the audio
  owner without changing the button state machine. Tracking loss uses fault
  priority; the others use info priority.
- **Laptop demo** (`pi_bridge.py` / `web_test.py`): the bridge keeps the last
  20 sentences behind `GET /guidance/events?since=<n>`. Whenever guidance is
  available, the web page polls every 1 s and speaks new sentences with
  browser speech. Events from before the page loaded are skipped. Guidance
  itself never depends on this poll.

Arrival sets `active = False`, so tactile output stops. Neutral servos alone
cannot distinguish arrival from failure, so arrival is always spoken.

### 4.6 Call sites

- `__main__.py` `_act`: `navigate_target` → `guidance.go_to(...)` if the frame
  has a stamp and the result has a box. Otherwise speak Gemini's answer
  (no box) or "Guidance needs the live camera." (no stamp).
- `pi_bridge.py`: `POST /guidance/target` with `{label, box, stamp}` →
  `guidance.go_to` → `{answer}`. `RemotePi.go_to` calls it.
- `web_test.py`: same branch as `__main__.py`. The page draws Gemini's box on
  the captured frame (debug and demo view).

---

## 5. Latency budget (targets to measure, not assumptions)

| Stage | Target |
| --- | --- |
| Release → thinking earcon | < 50 ms (existing) |
| Frame capture (+ tunnel on laptop path) | < 200 ms |
| Gemini round trip with box | 2–4 s, **unmeasured** |
| Detection → planner status | < 500 ms (A* on the current map) |
| **Release → confirmation sentence starts** | **median ≤ 6 s** |

The existing working earcon every 4 s after 5 s covers the wait. If Gemini
retries push the capture past 8 s, the planner rejects it as `stale` rather
than guiding from an old view.

---

## 6. Failure behavior

Every failure is spoken. Guidance never starts on a failure.

| Situation | Detected by | User hears |
| --- | --- | --- |
| Object not visible | `box_2d` null | Gemini's answer (e.g. "I don't see a water fountain right now.") |
| Frame has no ROS stamp | Companion | "Guidance needs the live camera." |
| No ROS / guidance unavailable | Companion | "Guidance is unavailable here." |
| `no_depth` / `no_depth_frame` | Planner | "I think I see the water fountain, but I can't judge how far it is. Try again from a little closer." |
| `too_far` (> 6 m) | Planner | "I think I see the water fountain, but it's too far to judge the distance. Ask again when you're closer." |
| `near` (inside standoff) | Planner | "I think the water fountain is less than a meter away, slightly to your left." |
| `stale` | Planner | "That took too long. Ask again so I can use a fresh view." |
| `tracking_lost` at request | Planner | "I can't track movement right now, so I can't guide you. Use your cane." |
| `no_map` / `no_camera_info` | Planner | "The map isn't ready yet, so I can't plan a route." |
| `no_tf` / `outside_map` / `no_free_cell` / `no_path` / `invalid` | Planner | "I think I see the water fountain, but I can't find a route to it on my map." |
| No status within 2 s | Guidance bridge | "The navigation system didn't respond, so guidance has not started." |
| Tracking lost mid-guidance | Planner event | "Tracking lost. Guidance paused. Use your cane." (fault priority) |
| Tracking restored | Planner event | "Tracking is back. Guidance resumed." |
| Target expired | Planner event | "Guidance to the water fountain timed out." |
| Gemini error / network | Existing | Existing error sentences |
| Hazard during any of the above | Existing | Hazard warning preempts speech; guidance state unchanged |

**Known blind spot:** the depth checks catch boxes that land on empty space or
depth holes. They do **not** catch a confident box drawn on the wrong real
object. That is why the phrasing is "I think I see," and why §11 measures the
false-box rate.

**Known geometric limits:**
- A doorway's center samples the room beyond it. That routes through the
  door, which is acceptable.
- A chair's center may sample the wall behind it. The goal lands too far away
  by the gap between them.
- Small, high signs such as exit signs are where D415 stereo depth is weakest.
- Record which targets work in `log.md`, and do not advertise the ones that
  fail.

---

## 7. Relationship to other modules

- **Backpack guidance:** shares the planner, direction node and gate. The two
  modes cannot run at the same time: the last request owns the target, and
  `navigate_backpack` hands ownership back to YOLO via `/target/clear`.
- **Hazard warnings:** unchanged priority. Hazards preempt confirmation and
  arrival speech. The direction node keeps running.
- **Scene questions:** same Gemini request. Adds two small schema fields; no
  extra call.
- **Guardian:** no interaction. Guardian does not start or stop guidance.
- **Pi CPU:** no new process. The added planner work is one projection per
  request, and memory for the time-based depth buffer.

---

## 8. Privacy

No new data leaves the device. The same frame and audio already go to Gemini
for every question. Boxes and targets are not stored beyond the session
history that already exists.

---

## 9. Safety statements (demo and docs)

- This is a supplement to the white cane, not a certified mobility aid.
- A grid route crosses unknown cells (`allow_unknown=true`). A route is a
  cost preference, not proof of a clear path.
- Identification is Gemini's guess and is spoken as one.
- No LLM is in the guidance loop: Gemini is called once, and every replan
  after that uses the remembered point and local sensing only.

---

## 10. Testing without hardware

Unit tests (`companion/tests/test_voice.py`, `test_web_voice_ros.py`):

- `_parse` keeps a valid box only for `navigate_target`. Wrong length,
  out-of-range values, inverted corners and non-integers become `None`.
- Descaling uses the original ROS width and height, not the JPEG's
  downscaled size.
- The detection JSON matches the planner's regexes. Copy the two patterns
  into the test and assert they match, including the 9-digit nanoseconds.
- `go_to` returns the right sentence for each status state, and the timeout
  sentence when no status arrives (fake publisher/subscriber).
- The distance/bearing phrasing table from §2.
- `stop()` and backpack `start()` publish `/target/clear`.

These live in `companion/tests/test_target.py`, `test_voice.py`,
`test_web_voice_ros.py` and `test_camera.py`.

**Box check:** run `python3 -m companion.voice.web_test --image scene.jpeg`,
ask to be taken to a visible object, and check that the "Target" card's box
covers it. This measures box quality before any ROS work.

**Planner check:** inside a ROS 2 Jazzy environment with the planner running,
run `python3 scripts/voice_target_smoke.py`. It simulates a free map, flat
3 m depth, camera intrinsics, TF and odometry, and checks:
- the goal comes from the capture-time pose even after the camera moves 1 m
  during the request;
- a backpack detection cannot take over the target;
- tracking loss, recovery, arrival, stale, near, straight-ahead and clear
  all produce the right status.

Measured 2026-09-26 in a stock `ros:jazzy-ros-base` container: 12/12 pass.

---

## 11. Acceptance criteria (worn, on the Pi, open speaker)

Numbers are provisional targets. Record the measured values in `log.md`.

1. **Box quality.** 3 target types (e.g. water fountain, trash can, door) at
   ~2 m and ~4 m, 5 trials each: the box visibly covers the object in ≥ 80 %
   of trials.
2. **Goal accuracy.** For the same trials, `/backpack/goal` is within 0.5 m
   of the object's tape-measured position in ≥ 80 %.
3. **Motion during the request.** Walk about 1 m while Gemini is answering.
   The goal error is still ≤ 0.5 m, which proves capture-time stamps are used.
4. **Ownership.** With a black backpack in view during a voice target, the
   goal does not move toward the backpack.
5. **Not visible.** Ask for 10 objects that are not in view: no guidance
   starts. At most 1 false box. Record which objects produced one.
6. **Every §6 row reachable in a test** produces its sentence: cover depth
   hole, too far, near, no path, tracking lost at request and mid-guidance,
   stop, replace, expiry (with the timer shortened for the test).
7. **Arrival.** Walk a guided route to the target. Arrival is spoken once,
   and tactile output stops.
8. **Latency.** Over 10 trials, the median from release to the start of the
   confirmation sentence is ≤ 6 s. No silent gap longer than 5 s.
9. **Hazard preemption.** A hazard during the confirmation sentence cuts it
   off, and guidance continues afterward.
10. **Laptop demo path.** Criteria 1, 5, 6 and 7 pass through the bridge,
    including arrival spoken via polling.

---

## 12. Implementation order

1. **Gemini contract** (§4.2) and unit tests. Measure box quality with
   `web_test --image` and a box overlay. *~1 h*
2. **Frame metadata** (§4.1): `capture_frame`, bridge headers, `RemotePi`.
   *~30 min*
3. **Planner** (§4.4): projection refactor, ownership, `/target/detection`,
   `/target/clear`, time-based depth buffer, status and events. *~2 h*
4. **Guidance bridge and call sites** (§4.5–4.6), including the failure
   sentences. *~1 h*
5. **Async events**: on-device callback, bridge polling on the web page.
   *~45 min*
6. **Overlays**: planner marker, web page box. *~30 min*
7. **Hardware acceptance** (§11).

Steps 1–2 can be tested without the Pi. Step 3 can be developed against
`mac_test.launch.py`.

---

## 13. Open decisions

- **Box vs. point.** Gemini can also return a point on the object's surface.
  That could fix the chair and sign cases in §6. Decide after criterion 2 if
  the center-third median proves unreliable.
- **Model.** If `gemini-3.5-flash-lite` fails criterion 1, add a second,
  detection-only call to a stronger model, made only for `navigate_target`.
  That adds roughly 1–2 s. First look (one image, `scene.jpeg`, 2026-09-26):
  - Trash can and back door boxes were tight; the refrigerator box covered
    its upper half.
  - "Where is the trash can?" stayed a question, a missing water fountain got
    no box, and "the backpack" went to `navigate_backpack`.
  - Round trips were 1.9–3.9 s.
- **Numeric thresholds.** 6 m depth limit, 25 % valid samples, 1.0 m arrival,
  8 s staleness, 5 min expiry: all provisional until measured.
- **Unknown-space policy** is shared with backpack guidance
  ([plan.md §6](../../plan.md#6-navigation-and-find-a-target)) and not
  decided here.
- **Tactile patterns** for arrival and failure are shared with the tactile
  work and still undecided.
- **Topic names.** Whether to eventually rename `/backpack/*` to `/target/*`.
- **Return to start (TO PURSUE, [plan §12b](../../plan.md#12b-take-me-back-to-where-i-started)).**
  "Take me back to where I started" reuses this spec's target ownership,
  `/target/clear`, `plan_to` results and `/target/status`, but its goal is a
  stored `odom` point rather than an image box. Keep those pieces generic
  enough to accept a point goal (for example `/target/point`) without a
  second code path.
