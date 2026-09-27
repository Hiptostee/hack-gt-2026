# Judge and Observer Debug Dashboard — Specification

**Version:** 1.0 · **Target:** HackGT 2026 · **Status:** SPECIFICATION

A live visual telemetry dashboard for HackGT judges, mentors, and spectators to inspect the wearable spatial guide's real-time perception, navigation planning, tactile actuator commands, and voice assistant state.

---

## 1. What Changes and Why

### The Problem
The wearable spatial guide's interface is deliberately non-visual and tactile:
1. Speech is heard via bone conduction or an open directional speaker by the wearer.
2. Directional guidance is felt as physical servo sweeps on the wearer's hands.
3. Obstacle warnings are immediate audio cuts or low-latency tones.

Judges standing next to the wearer cannot feel the hand units, cannot see what the camera observes, and cannot tell why the device instructed a turn or stopped. Furthermore, during live demonstrations in crowded hackathon venues, observers need instant visual proof that the system is operating autonomously and sensing real depth geometry rather than running a canned script.

### What is Added
1. **Bridge Telemetry Aggregation (`GET /debug/state`):** A lightweight, non-blocking HTTP endpoint on `companion/voice/pi_bridge.py` running on the Pi (default port `8081`) that aggregates snapshots from:
   - Camera frame status and framerate/timestamp.
   - Guidance state: `active`, `path_valid`, `direction`, `tactile_flags`, target label, target distance & bearing.
   - Hazard warning detector: latest snapshot from `/hazard_warning`, severity (`none`, `caution`, `urgent`), direction, distance, health, and age.
   - Guardian Voice: mode state (`idle`, `cancel_window`, `active`, `speaking`), observation trail count, tool activity, and SMS draft/delivery state.
   - System metadata: `device_lang`, monotonic timestamp, and subsystem freshness ages.
2. **Planner Overlay Feed (`GET /debug/planner-image` or `/frame` overlay):** Streaming the annotated planner visualization (`/backpack/planner_image`) showing obstacle costmaps and the computed A* path.
3. **Live Observer UI in `companion/voice/web_test.py`:**
   - Dedicated Dashboard View and overlay panel on the laptop browser interface.
   - High-contrast visualizer for the ESP32 tactile hands (live servo animation for neutral, front, left, right).
   - Prominent guidance heading indicator (Forward, Turn Left, Turn Right, Stop, Inactive).
   - Real-time hazard alert banner with sub-second age counters.
   - Guardian conversation monitor and observation trail inspector.
   - Explicit staleness badges (green < 0.3s, amber 0.3–1.0s, red > 1.0s).

### Explicitly Out of Scope
- **No wearer-facing impact:** The dashboard is an observer tool. The Pi wearable operates identically whether the dashboard is open or closed.
- **No transcript broadcasting by default:** To respect privacy guidelines ([plan.md §8](../../plan.md#8-scene-questions-reading-and-sponsor-integration)), user voice transcripts and private conversation text remain off the dashboard screen by default, requiring an explicit toggle (`--show-transcripts`) for judge evaluations.
- **No new ROS nodes:** The bridge reads existing ROS topics and Python companion state objects. It introduces zero additional node scheduling overhead.
- **No remote control over debug endpoints:** `GET /debug/state` is strictly read-only. Guidance activation and button triggers remain governed by their dedicated authenticated pathways.

---

## 2. Interaction Model

### Observer Experience
1. An observer or judge opens `http://localhost:8080/` (or `http://localhost:8080/dashboard`) on the development laptop.
2. The page polls `GET /debug/state` at 3 Hz (every 333 ms) and streams the live video / planner overlay at 2–5 FPS.
3. When the wearer moves or receives guidance:
   - **Tactile Hands:** An animated SVG diagram of the left and right hand units mirrors the exact command flags sent to the ESP32 servos. A forward command shows both hands sweeping; a left command shows the left hand sweeping; neutral shows both hands centered at 90°.
   - **Direction:** A large compass/arrow widget displays the active navigation direction (Forward, Left, Right, Stop).
   - **Hazards:** If an obstacle is detected in the chest/head zone, a prominent warning card lights up with the obstacle zone, distance, and spoken phrase.
   - **Guardian Voice:** If the wearer enters Guardian mode (triple-tap or spoken trigger), the card indicates the state, the last camera observation, and SMS draft status.
4. **Staleness Indication:** Every card displays its data age (`age_s`). If the Pi stops sending updates, indicators turn amber at 500 ms and red at 1500 ms with "STALE / NO DATA" warnings. A dead sensor or missing packet is **never** presented as an empty or clear space.

---

## 3. Telemetry Schema (`GET /debug/state`)

The response is a single unnested-object JSON payload designed for rapid serialization (< 5 ms):

```json
{
  "timestamp": 1727395200.450,
  "device_lang": "en",
  "camera": {
    "status": "Ready",
    "updated_at": 1727395200.410,
    "age_s": 0.040,
    "width": 640,
    "height": 480
  },
  "guidance": {
    "active": true,
    "path_valid": true,
    "hazard_permitted": true,
    "direction": 0,
    "direction_label": "forward",
    "tactile_flags": 1,
    "tactile_label": "front",
    "target_label": "backpack",
    "target_distance_m": 2.4,
    "target_bearing_deg": -8.5,
    "age_s": 0.050
  },
  "hazard": {
    "available": true,
    "severity": "none",
    "urgent": false,
    "caution": false,
    "direction": "clear",
    "distance_m": null,
    "phrase": null,
    "sensor_health": "ok",
    "age_s": 0.080
  },
  "guardian": {
    "state": "idle",
    "is_speaking": false,
    "last_tool": null,
    "sms_state": "idle",
    "last_observation": "elevator sign at 3:55 PM",
    "trail_count": 2,
    "age_s": 0.150
  }
}
```

### Field Definitions & Semantics

| Field | Type | Description |
|---|---|---|
| `timestamp` | Float | Pi monotonic epoch timestamp when telemetry was compiled. |
| `device_lang` | String | Active system language code (`en`, `ko`, `zh`, `ja`, `es`). |
| `camera.status` | String | `"Ready"`, `"No frames received"`, or error message. |
| `camera.age_s` | Float | Elapsed seconds since last camera frame was received. |
| `guidance.active` | Bool | Whether path guidance is currently engaged. |
| `guidance.path_valid` | Bool | Whether planner has an active, valid A* route (< 1.5s old). |
| `guidance.hazard_permitted` | Bool | Whether `/hazard/guidance_permitted` lease is currently valid (< 0.5s old). |
| `guidance.direction` | Int / Null | Raw ROS direction code: `0`=Forward, `1`=Left, `2`=Right, `3`=Stop. |
| `guidance.tactile_flags` | Int | Bitmask flags: `0x01`=Front, `0x02`=Left, `0x04`=Right, `0x00`=Neutral. |
| `guidance.target_label` | String / Null | Name of targeted object (e.g., `"backpack"`, `"water fountain"`, `"start point"`). |
| `hazard.available` | Bool | Whether hazard warning detector heartbeat is active and calibrated. |
| `hazard.severity` | String | `"none"`, `"caution"`, or `"urgent"`. |
| `hazard.sensor_health` | String | `"ok"`, `"degraded"`, `"uncalibrated"`, `"tilted"`, or `"dead"`. |
| `guardian.state` | String | `"idle"`, `"cancel_window"`, `"active"`, or `"speaking"`. |
| `guardian.sms_state` | String | `"idle"`, `"drafting"`, `"awaiting_verbal_confirmation"`, `"sent"`, or `"failed"`. |

---

## 4. Pipeline & Latency Budget

```
Pi Camera + Depth
  │
  ├─ ROS hazard_node ──> /hazard_warning ───┐
  ├─ ROS planner ──────> /backpack/direction ──┤
  └─ companion.voice ──> RosGuidance state ────┤
                                               ▼
                              pi_bridge.py: GET /debug/state
                                               │
                                (SSH tunnel / Wi-Fi LAN)
                                               ▼
                              web_test.py: Judge Dashboard UI
                                     (3 Hz Poll Loop)
```

### Latency Budget
- **Pi Bridge aggregation:** `< 5 ms` (in-memory lock read, no I/O).
- **Network transport (Hotspot / SSH Tunnel):** `< 20 ms`.
- **Browser DOM update:** `< 10 ms`.
- **Total Glass-to-Dashboard Latency:** `< 350 ms` (dominated by the 333 ms polling interval).

---

## 5. Failure Behavior

| Failure Mode | Dashboard Display | Wearer Experience |
|---|---|---|
| **Pi Bridge offline / SSH tunnel dropped** | Full-width Red Banner: *"Pi Telemetry Link Disconnected. Retrying..."* All metrics grayed out. | Wearer unaffected. Safety loop runs locally on Pi. |
| **Camera disconnected / no frames** | Camera card: *"No Camera Signal"*. Guidance card: *"Inhibited (Camera Fault)"*. | Wearer hears: *"I can't see anything right now"*. Guidance disabled. |
| **Hazard detector heartbeat lost (> 0.5s)** | Hazard card turns Red: *"Hazard Heartbeat Stale / Sensor Fault"*. Guidance card: *"Inhibited"*. | Wearer hears local fault alert. Tactile output commanded to neutral. |
| **Guidance route stale (> 1.5s)** | Direction indicator switches to *"Stop / Path Invalid"*. Tactile flags show *"Neutral"*. | Wearer tactile units return to 90° rest. |
| **Guardian network loss** | Guardian card displays: *"Offline (Local Help Available)"*. | Wearer hears local speech informing that cloud connection dropped. |

---

## 6. Architecture & Implementation Plan

### 1. `companion/voice/pi_bridge.py`
- Add telemetry aggregator `get_debug_state()`:
  - Ingests `server.guidance.snapshot()`.
  - Ingests `server.camera.status()` and frame capture timestamp.
  - Ingests hazard snapshot from `RosCamera` or hazard topic listener.
  - Ingests Guardian session state if running in bridge process or forwarded.
- Register route `GET /debug/state`.

### 2. `companion/voice/web_test.py`
- Add Dashboard UI markup and styling into `PAGE` HTML template:
  - Dark mode aesthetic with high-contrast judge HUD styling (black background, clean cards, glowing indicators).
  - SVG Dual-Hand Servo visualizer.
  - Directional heading arrow widget.
  - Hazard status alert tile with real-time countdown/age badge.
  - Guardian Voice status tile.
  - Telemetry freshness indicators.
- Client-side JavaScript poll loop fetching `/debug/state` every 333 ms and updating DOM elements.
- Add `--dashboard-only` mode allowing a dedicated observer screen.

### 3. Unit Tests (`companion/tests/test_dashboard.py`)
- Test `/debug/state` returns HTTP 200 with valid schema.
- Test missing or uncalibrated hazard detector correctly sets `available: false` and `sensor_health`.
- Test stale camera timestamps increment `camera.age_s`.
- Test tactile flag bitmasks correspond exactly to active direction.

---

## 7. Acceptance Criteria

1. **Endpoint Latency:** `GET /debug/state` responds in under 15 ms on localhost.
2. **Tactile Fidelity:** When `direction` is 0 (Forward), tactile flags show `0x01` and both hands visually indicate forward sweep. When 1 (Left), only the left hand indicates sweep. When stopped/invalid, flags are `0x00` and hands show neutral.
3. **Hazard Propagation:** Injecting a simulated hazard via `fake_hazard.py` updates the dashboard hazard card to Urgent/Caution within 400 ms.
4. **Staleness Truthfulness:** Killing the hazard publisher causes the dashboard hazard tile to turn red within 600 ms, explicitly displaying data age rather than clear space.
5. **Non-Interference:** Polling `/debug/state` at 10 Hz introduces no measurable audio stutter, frame drops, or scheduling latency in the companion voice loop.

---

## 8. Interactive Demo Simulator & Edge-Case Injector

To enable live judge evaluations without requiring dangerous physical obstacles (e.g. drop-offs or head-level branches) during a booth presentation:
- **API Endpoint:** `POST /debug/simulate`
  - Accepts JSON:
    ```json
    {
      "action": "hazard" | "direction" | "fault" | "reset",
      "value": "urgent_head" | "caution_corridor" | "dropoff" | "clear" | "left" | "right" | "forward" | "stop" | "heartbeat_drop"
    }
    ```
- **UI Component:** A dedicated "Demo Simulation / Judge Controls" collapsible drawer in the dashboard HUD containing one-click triggers:
  - `[⚠️ Head Obstacle]` → Injects urgent alert; forces tactile servos to snap to neutral (inhibit); updates phrase.
  - `[🕳️ Drop-off / Stairs]` → Injects ground-plane drop-off alert.
  - `[↰ Turn Left]` / `[↱ Turn Right]` / `[↑ Forward]` / `[🛑 Stop]` → Forces active guidance heading & tactile servo sweep.
  - `[⚡ Drop Sensor Heartbeat]` → Simulates dead depth sensor; tests "Fail Toward the Cane" policy (turns red > 500 ms).
  - `[🔄 Reset to Live]` → Clears simulation overrides, returning to physical telemetry.

---

## 9. Planner Costmap Radar Overlay (`/backpack/planner_image`)

- Visual costmap feed showing the 2D occupancy grid and A* path trajectory:
  - **Feed Stream:** `GET /planner-frame` (or SVG/Canvas radar visualization in the UI when no ROS planner image stream is active).
  - **Display Layers:**
    - Robot origin marker (orientation arrow).
    - Obstacle risk grid (cells with inflation boundaries).
    - Planned trajectory line (green path waypoints to target).
    - Active goal point (labeled waypoint).
  - **View Toggle:** Quick pill buttons above the video stream: `[📷 Camera Feed]` and `[🗺️ Planner Costmap]`.

---

## 10. Real-Time Latency & Sensor Vitals Meter

- A fixed telemetry vitals banner across the top of the HUD displaying live sub-system timing:
  - **Depth Camera:** Framerate & elapsed frame capture time (e.g. `30 FPS · 32ms`).
  - **Hazard Loop:** C++ node cycle execution time (e.g. `16ms`).
  - **Planner Compute:** A* cycle time (e.g. `42ms`).
  - **Link RTT:** Sub-second browser-to-server request round-trip time measured dynamically.
  - **Safety Budget:** Visual status chip: `SAFETY BUDGET: PASS (<100ms)`.

---

## 11. Multilingual Switcher (EN / KO / ZH / JA / ES)

- Direct language switching for international judges:
  - **API Endpoint:** `POST /debug/language` with `{"lang": "en" | "ko" | "zh" | "ja" | "es"}`.
  - **UI Component:** Language selector pills in the dashboard header:
    `[🇺🇸 EN | 🇰🇷 KO | 🇨🇳 ZH | 🇯🇵 JA | 🇪🇸 ES]`.
  - **Behavior:** Updates `DEVICE_LANG` on the server in real-time, dynamically switching:
    - Active language badge on HUD.
    - Spoken hazard phrases (from `companion/voice/i18n.py`).
    - Assistant speech synthesis voice and responses.

