# AGENTS.md

Guidance for AI agents working in this repository. Read this first, then read
[log.md](log.md) for what is actually built right now.

This file describes the project and how we work. It deliberately does **not**
describe the current architecture or feature set — those change, and they live
in `log.md` and in per-feature specs.

---

## What we are building

A **wearable spatial guide for blind and low-vision people**, built at HackGT
2026 by a four-person team.

It is worn on the body, senses the space ahead with a depth camera, and reports
what it finds through audio and touch. The user asks it questions out loud and
it answers.

**It complements a white cane — it does not replace one.** A cane reads the
ground by contact and is reliable in ways no camera is. This device is for what
a cane cannot reach: obstacles at head and chest height, objects and signs
across the room, the layout of an unfamiliar space, and finding a specific
thing. Design every feature as an addition to the cane's information, never as
a substitute for it.

---

## Who it is for, and what that demands

The user cannot see the device, its screen, its status lights, or its error
messages. This is not a constraint to work around; it is the design premise.
These hold for every feature, regardless of implementation:

- **Never go silent.** Silence is indistinguishable from a dead device. Every
  state — working, failed, no network, no data — needs an audible or tactile
  signal.
- **Never occlude the ears.** Blind users navigate by ambient sound and traffic.
  Bone conduction or an open speaker only. Earbuds and headphones are a safety
  regression, not a comfort tradeoff.
  Hardware options remain under exploration: likely open speaker for the demo,
  bone conduction for the future product after the hackathon. This is a working
  direction, not a finalized device choice; keep current decisions in `plan.md`.
- **Distinguish "not observed" from "clear."** The device must never imply that
  an area it cannot see is safe. Missing depth is not proof of open floor.
- **Never present a guess as an observation.** Unreadable text is reported as
  unreadable. An uncertain identification is reported as uncertain.
- **Fail toward the cane.** When a subsystem breaks, say so and stop producing
  guidance from it. Stale directional cues are worse than no cues.
- **This is not a certified mobility aid** and must never be presented as the
  user's only navigation safeguard — in the code, the docs, or the demo.

---

## Hardware

| Part | Detail | Status |
| --- | --- | --- |
| Compute | Raspberry Pi 5, 16 GB RAM, 256 GB SSD | In hand |
| Depth camera | Intel RealSense **D415** | In hand, working — this is the camera |
| IMU | MPU6050, I2C address `0x68` | In hand, integrated |
| Microcontroller | ESP32 | In hand; intended for haptic output |
| Haptics | Two ESP32 hand units driving SG90 servos | Firmware and sender on the `tactileESP32` branch; not flashed or merged |

**The camera is a D415.** The original idea notes mention an L515 — ignore that;
it was never acquired. The D415 has no onboard IMU, which is why the MPU6050 is
a separate part. Depth is stereo, not LiDAR, so expect it to degrade on blank
walls, in low light, and under fast rotation.

**Pi ↔ ESP32 link:** Wi-Fi/UDP. The packet is three bytes
`[0xA5, sequence, flags]` with front/left/right flag bits; backward guidance was
dropped, so the old 4-element `[front, back, left, right]` array is obsolete.
See `plan.md` §7 and `log.md` for its current state before assuming anything.

---

## Constraints that apply to every feature

- **No LLM in the real-time loop.** Hazard warnings and movement feedback must
  be fast and local. Cloud reasoning is for on-demand questions, never for
  telling someone there is a step in front of them.
- **Hazard feedback direction:** target both audio and tactile output. Flesh out
  audio first; tactile hazard patterns are brainstorming only for now. Do not
  describe them as an implemented fallback. Idle single tap repeats the last
  answer; thinking/speaking tap cancels without automatic repeat; hold to talk
  and release to send; double tap for local spoken help/status without internet
  or contact actions. Locator sound is excluded; every double tap keeps the
  help/status meaning. `companion/specs.md` §2 holds the approved mapping and
  distinguishes implementation gaps from decisions.
- **Verify worn coverage.** Complete the camera mounting/coverage task before
  claiming both floor and head-height detection; record blind zones and limits.
- **Sponsor tracks:** Gemini and ElevenLabs. Where a feature can reasonably use
  them, it should — but the integration has to serve the user's interaction, not
  be bolted on for the track.
- **The Pi runs everything at once.** Any new process shares CPU with the
  real-time stack. It must not block or starve it.
- **Hackathon time budget.** Prefer a working narrow thing to a broad broken
  one. A feature that cannot be demonstrated does not exist.
- **Demo honesty.** Never claim a capability that has not been verified on the
  actual hardware. If part of a demo is simulated, replayed, or hardcoded, say
  so out loud.

---

## How we work

### 1. Every feature gets its own `specs.md`

Before implementing a feature, write a spec for it at `<feature>/specs.md`
— alongside the feature's code, not in a central docs folder. See
[companion/specs.md](companion/specs.md) for the established shape.

A spec should commit to decisions rather than survey options. Cover at least:

- **What changes and why** — including what is explicitly out of scope
- **Interaction model** — exactly what the user does and what happens
- **Hardware and dependencies** it needs
- **Pipeline/architecture** and, where latency is perceptible, a latency budget
- **Failure behavior** — every failure mode and what the user hears or feels
- **Relationship to the other modules** — what it shares, what it must not block
- **Open decisions** — the things still genuinely undecided, named as such
- **Acceptance criteria** — concrete, testable, on real hardware

Write specs for the engineers implementing them. Be specific and opinionated;
flag assumptions rather than hiding them.

### 2. Keep `log.md` current

[log.md](log.md) at the repo root is the running record of project state.
**Update it as work lands — without being asked.**

Add an entry when:

- a decision is made (record **why**, not just what — the reasoning is what gets
  lost between sessions and teammates)
- a component starts working, or breaks
- scope changes, or something is dropped
- a blocker or hardware surprise turns up

Newest entries at the top. Routine edits do not each need an entry; anything
that changes the project's direction or state does.

`log.md` holds volatile state. This file holds durable context. Keep them
separate — do not copy the current architecture into AGENTS.md.

### 3. Repository layout

| Path | Contents |
| --- | --- |
| `plan.md` | Canonical implementation plan — scope, status, architecture, open questions |
| `log.md` | Running project state and decision log |
| `README.md` | Repo README — setup, usage, tech stack |
| `AGENTS.md` | This file |
| `ros_ws/` | ROS 2 workspace — perception, mapping, and navigation nodes |
| `ros_ws/src/hazard_warnings/specs.md` | Hazard warning system spec — zones, thresholds, audio/tactile staging |
| `companion/` | On-device voice companion (scene questions, text reading, help) |
| `companion/specs.md` | Voice companion spec — interaction model, button gestures, pipeline |
| `companion/README.md` | Companion package docs — voice service + browser demo fallback |
| `companion/guardian/` | Guardian Voice — push-to-talk ElevenLabs agent for a disoriented user; not an emergency service |
| `companion/guardian/specs.md` | Guardian Voice spec — activation, agent tools, SMS contact, failure behavior |
| `companion/navigate/specs.md` | Navigate-to-named-object spec — Gemini box → planner goal, status topic, failure speech |
| `host_streamer/` | macOS dev bridge: tethered camera → containerized ROS stack |
| `scripts/` | Setup, launch, and check scripts |
| `../brainstorming.md` | Early feature exploration, outside the repo. Deliberately non-committal — a source of ideas, not decisions |

### 4. Docs to update after changes

When you make a design decision, land a feature, or change scope, update the
relevant docs **without being asked.** Use this checklist:

- **`log.md`** — Always. Add a dated entry with what changed and why.
- **`plan.md`** — When scope, status labels, architecture, or open questions
  change. This is the canonical plan; keep it current.
- **The relevant `specs.md`** (`companion/specs.md` or
  `ros_ws/src/hazard_warnings/specs.md`) — When a feature's contract, pipeline,
  failure behavior, or acceptance criteria change.
- **`README.md`** or **`companion/README.md`** — When setup steps, usage
  instructions, or advertised capabilities change.

Do **not** update `AGENTS.md` for volatile state — that belongs in `log.md`.
Do **not** update `../brainstorming.md` with decisions — it is idea input only.

### 5. Working notes

- Read `log.md` before assuming what exists. Several things described in the
  original project notes have not been built.
- The ROS stack and the companion are separate processes by design. Keep them
  decoupled.
- Prefer editing existing code to adding parallel implementations — in a
  four-person hackathon, duplicate stacks are how merge conflicts and wasted
  hours happen.
- When a task's direction is genuinely ambiguous and the answer changes the
  design, ask. When it is a judgment call inside a decided design, make it and
  note it in `log.md`.

### 6. Git commits — the user runs them

- **Never run commands that create commits or change GitHub:** `git commit`
  (including `--amend`), `merge`, `rebase`, `cherry-pick`, `tag`, `push`,
  `gh pr create`. Read-only commands (`status`, `diff`, `log`, `show`) are fine.
- When work is ready to commit, **give the user the exact commands in one
  code block and stop.** Stage by explicit path (never `git add -A`, `git add .`
  or `git commit -a`), then commit, then push:

  ```bash
  git add companion/guardian/session.py companion/tests/test_guardian.py
  git commit -m "feat(guardian): describe the change"
  git push origin feature/guardian
  ```

- **No attribution of any kind** in commit messages, PR titles or
  descriptions, tags or code: no `Co-authored-by:`, `Made-with:`,
  `Generated with`, `Signed-off-by:` for an AI, tool names, links or emoji
  badges. No `--author` override. The message describes the change only.
- Docs are committed with the code: include the `log.md`, `plan.md` and
  `specs.md` changes the work produced in the staged paths.
- On every machine used for this repo, turn off **Cursor Settings > Agent >
  Attribution** (Git & PRs > Attribution from Cursor 3.11); it adds a
  `Co-authored-by: Cursor` trailer to agent commits by default.
