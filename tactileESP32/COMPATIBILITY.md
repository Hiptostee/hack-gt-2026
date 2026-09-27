# Host-computer integration review — 2026-09-26

Reviewed fetched heads: `tactileESP32` `6668469`, `origin/main` / `integeration`
`fca5a5e`, `feature/guardian` `a5f8f0d`, `feature/navigate-target` `b04eb8d`.
This is a source and host-test review, not a combined hardware acceptance test.
Neither feature branch was merged. Both contain implementation, not just plans;
Guardian's log says named-target work is paused pending separate hardware tests.

## Deployment contract

The host computer owns the network connection to the hands and sends UDP.
The Pi still owns the D415, ROS perception, localization and route planning.
Moving the access point does not move ROS compute onto the host.

```text
Pi camera / ROS / guidance gate
             | localhost HTTP :8081 through SSH
             v
host tactile sender -- unicast UDP :4210 --> left and right ESP32
```

Use a host-provided 2.4 GHz hotspot or a shared LAN that allows client traffic.
The hands use DHCP and `tactile-left.local` / `tactile-right.local`; explicit IP
overrides handle missing mDNS. The launcher does not create a hotspot or enable
internet sharing. Configure those separately, including a working internet
uplink for whichever machine runs cloud voice. The Pi can use its hotspot LAN
address (`--pi-ip`) or a reachable Tailscale address. The ESP32s do not use
Tailscale. No Pi AP, second Pi Wi-Fi radio, fixed `10.42.0.x` addresses or
Pi-to-hand routing is required.

`origin/main:plan.md` §2a and §7 still describe the older dual-network Pi AP.
Replace those paragraphs with this topology when integrating. Its claims that
the tactile consumer and rotation mapping are absent are also stale relative
to this branch. Keep the historical C++ sender/hotspot tools clearly labeled:
their fixed-address defaults are not DHCP discovery.

## Compatibility findings

| Area | Finding / required integration |
| --- | --- |
| Wire protocol | Compatible with both features: unchanged `[0xA5, seq, flags]`, 20 Hz, UDP 4210. Codes 3/4 map to left/right; they do not create new rotation patterns. |
| Guardian deployment | Its chosen demo runs voice on the Pi (`PI_VOICE=1`); browser voice does not implement Guardian's controls/audio owner. Use host `--no-voice` to keep tunnel, hands and RViz without a second voice UI or a host Gemini key. |
| Guidance ownership | Previously the HTTP bridge and Pi voice each created a publisher and private `active` state. This could mix true/false gate heartbeats and make the hands read the wrong controller. The bridge now observes when `PI_VOICE=1` is inherited, or with `--observe-guidance`. Only Pi voice publishes the gate; observer HTTP mutations return 409. |
| Guardian entry | **Still a blocker on that branch:** `Companion._enter_guardian()` opens Guardian without calling `guidance.stop()`, contrary to Guardian spec §9. Add stop before opening; exit must not restart. Test entry, canceled entry, failure and exit with the hands neutral. |
| Named targets | Retains `/backpack/direction`, `/backpack/path_valid` and `/backpack/guidance_active`; no firmware change needed. Preserve tactile snapshot/subscription code alongside target status, clear, events and frame metadata. |
| Hazards | Audio arbitration on the feature branches does not establish a hazard-to-guidance inhibit. Current tactile state has no hazard/audio-health field. Agree and implement the gate policy before claiming hazard-safe movement; never encode hazards as ordinary turn cues. |
| Localization | Preserve this branch's `enable_icp:=false` default when retaining Guardian's `PI_VOICE` startup. The older feature launch enables ICP. Actual Pi load/pose quality remains a hardware check. |

The new observer mode is preparation for the combined branch. This branch alone
does not implement `PI_VOICE` voice startup or Guardian. After integration, run
`PI_VOICE=1 ./scripts/pi_launch.sh` on the Pi and
`python3 scripts/laptop_launch.py --no-voice` on the host. `PI_VOICE` must reach
the bridge process as an environment variable; do not set it only inside the
voice subprocess. Do not run a second guidance owner.

For the existing browser demo, leave `PI_VOICE` unset and use the default
launch commands. The HTTP bridge remains the owner in that mode.

## Merge requirements

Read-only three-way `git merge-tree` review found textual conflicts with the
named-target branch in `companion/voice/guidance.py` and `pi_bridge.py`.
Guardian versus the reviewed tactile commit had no textual conflict markers;
that does not detect the ownership and Guardian-entry issues above. Recheck
after these local changes and whenever branch heads advance.

- Keep target's `Condition` lock, `on_event`, target publishers/subscribers,
  `go_to()`, clear and event methods **and** tactile `UInt8` subscription,
  direction timestamp, `snapshot()` and observer ownership checks. Extend the
  constructor to accept both `on_event` and keyword `read_only`.
- Observer mode must also reject target mutations and must not create any
  target/gate publishers. Its subscriptions still report navigation state.
  Closing an observer must not clear an owner's target.
- Retain `/guidance/state` with target's parsed `url.path` routing, plus
  `/guidance/target`, `/guidance/events` and original ROS frame headers.
- Preserve the host tactile process, hostname/IP flags, `--no-voice` and its
  shutdown neutral burst. Preserve Guardian's Pi voice lifecycle separately.
- Preserve target's planner changes and this branch's localization changes;
  rebuild `realsense_mapper` after integration.
- Update canonical `plan.md` and root `log.md` on the integration branch;
  those files are absent from this checkout. This report avoids importing a
  stale duplicate of the other branches' full plans.

## Failure timing and validation

Current limits are separate stages, not a measured end-to-end guarantee:
direction age ≤0.6 s; path-valid age ≤1.5 s; observer gate age ≤0.6 s;
host snapshot age ≤0.4 s; receiver silence timeout 0.5 s. HTTP polling and the
50 ms send interval add delay. Fresh UDP packets alone do not prove fresh
planning or that both hands received anything. The protocol has no ACKs.

Host tests cover gate ownership/expiry, neutral on stale direction/path,
observer write rejection, transport-only launch, shared packet encoding, C++
UDP loopback and both simulated hand receivers. These do not verify real ROS
delivery, Wi-Fi, audio or servo performance.

Validation on this checkout: all 60 companion tests and all four CTest
tests passed; C++ Release build and `git diff --check` passed. Socket tests
required execution outside the sandbox to bind localhost ports.

Follow-up validation ran the actual HTTP poller and UDP sender in a subprocess
against localhost stand-ins: both logical hand sends match, rotation codes map
correctly, invalid paths and stopped guidance send neutral, and closing the
HTTP bridge produces sustained neutral packets while the sender remains alive.
SIGINT exits successfully. This test uses one UDP socket for both logical hands;
it does not measure independent hand delivery or venue timing.

Temporary three-way merges including the local edits confirmed Guardian's five
reviewed integration files merge without text conflicts and preserve
`enable_icp:=false` alongside `PI_VOICE` startup. Named-target still conflicts
in `guidance.py` and `pi_bridge.py`. Both Pi launch variants pass shell syntax
checks. No combined branch was created or run; clean text merging does not
resolve the Guardian-entry and hazard-policy blockers above.

Before combined acceptance, verify:

1. Both hands join the host network; DHCP renewal/mDNS or IP overrides work.
   Test with the Pi AP disabled and with internet disconnected separately.
2. Exactly one `/backpack/guidance_active` publisher. Start/stop on Pi voice is
   reflected in HTTP state and both hands; kill voice while keeping bridge and
   sender alive and measure neutral latency.
3. Cut the SSH tunnel, stop planner output, invalidate the path, disconnect a
   hand and terminate the sender. Record neutral latency and audible loss
   reporting; neutral alone does not explain a fault.
4. Guardian entry stops guidance and exit does not resume it. Confirm local
   warnings preempt speech and exercise the agreed movement-inhibit policy.
5. Named target replacement, failed target, arrival, expiry and tracking loss
   cannot retain the previous cue. Preserve capture-time depth/TF metadata.
6. Measure under concurrent mapping, voice, RViz and hand traffic. Disable
   planner `allow_unknown` for field guidance; it is still true in this branch.
