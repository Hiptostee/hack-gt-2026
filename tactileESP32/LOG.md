# Tactile link log

Decisions and work on the Pi → ESP32 direction link, newest first. The current
design is in [SPEC.md](SPEC.md).

## 2026-09-26: automatic battery-powered servo test

Changed root `servo_test/` to run once at boot with no Serial commands:
90° neutral, slow move to 60°, slow move to 120°, then slow return to 90°.
Intermediate positions pause two seconds; final neutral holds PWM indefinitely
as requested. Power cycling repeats the sequence. The hand firmware's release
and travel limits are unchanged. Added upload troubleshooting for the reported
921600-baud flash failure; hardware upload remains unverified.

## 2026-09-26: independent servo tester and bounded hand travel

Moved the standalone sketch out of the hand project to root `servo_test/` for
Arduino IDE use only. It starts at neutral, supports small ±10° movement tests
and neutral adjustment, and releases PWM after each one-second move. It neither
imports nor writes tactile configuration.

Hand firmware now has configurable minimum/maximum angles, validates its rest
angle at compile time, and reduces sweep amplitude symmetrically to fit the
available travel around rest. Idle/disconnect still returns to neutral and
releases PWM. Position limits do not detect torque or obstructions.
Validated both ESP32 builds with the pinned toolchain, plus simulated left,
right, and shifted-neutral receivers. The shifted-neutral test verifies a
140° rest produces a 130–150° sweep rather than driving past its upper limit.
No physical servo was flashed or tested.

## 2026-09-26: standalone servo neutral calibration

Added `esp32/servo_neutral/servo_neutral.ino`: GPIO 18, 50 Hz PWM with the same
pulse endpoints as the hand firmware. Serial commands adjust the rest angle,
release/hold the servo, and print the `TACTILE_REST_DEG` value and maximum
in-range sweep. No Wi-Fi or extra libraries; settings are intentionally volatile.
Flash the normal hand firmware again after transferring each hand's calibration.
Verified: standalone PlatformIO build succeeds for `esp32dev` with the repo's
pinned Arduino-ESP32 3.3.12 toolchain. Not flashed or tested on a physical servo.

## 2026-09-26: compatibility validation follow-up

All 60 companion tests and four CTest tests pass. Added a real localhost
HTTP-to-UDP subprocess regression covering matching two-hand sends, rotation
mapping, invalid path, stop, sustained neutral on bridge loss, and SIGINT exit.
Python and Pi launcher syntax checks and `git diff --check` pass.

Temporary three-way merge checks include the uncommitted compatibility edits:
Guardian's reviewed integration files merge textually and retain non-ICP plus
Pi voice startup; named-target requires conflict resolution in guidance and
the HTTP bridge. No branch was merged. Guardian entry, hazard inhibition and
physical Pi/ESP32 acceptance remain outstanding as documented in the review.

## 2026-09-26: host-network and feature compatibility review

Reviewed fetched main, Guardian and named-target heads; details and exact
revisions are in [COMPATIBILITY.md](COMPATIBILITY.md). The host sends directly
to DHCP/mDNS hands; the canonical plan's Pi AP topology is stale. The Pi retains
ROS compute. Neither feature branch was merged.

Added host `--no-voice` for Guardian's on-Pi voice deployment and a read-only
guidance observer for the Pi HTTP bridge (`PI_VOICE=1` / `--observe-guidance`).
This prevents a second gate publisher and expires the observed owner's
heartbeat. Added regression checks for ownership, expiry, HTTP mutation
rejection and transport-only startup.

Remaining integration requirements include preserving named-target and tactile
interfaces together, stopping navigation on Guardian entry (missing in its
current code), and implementing hazard/audio-fault guidance inhibition.
Hardware validation is still outstanding; host tests are not a wearable demo.
Validation: 59 companion tests and all four CTest tests passed, including local
HTTP/UDP tests with socket permissions. C++ Release build and diff checks passed.

## 2026-09-26: follow-up review — startup, outage reports, hotspot updates

**Fixed**
- Send the initial servo rest pulse before Wi-Fi setup. Previously, setup taking
  longer than the 500 ms release interval could cause the first loop to stop PWM
  without ever commanding the rest angle.
- Track maximum packet silence through failsafes and across report windows on
  both the ESP32 and the Pi listener. The old listener reported a maximum of about
  49 ms despite several seconds without packets. Gap averages and sequence-loss
  accounting still exclude failsafe resets, since the sender may have restarted.
- Update an existing hotspot profile in place, then activate it. Previously `up`
  deleted the working profile before attempting to create its replacement, so a
  rejected setting could leave no saved hotspot. Uses the documented
  [nmcli connection modify](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nmcli.html)
  operation; first-time setup still creates a profile.

**Verified**
- New startup and outage regressions fail against the previous implementation.
- Release CMake build and all four CTest tests pass on macOS. Hotspot tests use
  a fake `nmcli` to cover creation, modification, rejected updates, and invalid
  password/channel values; they do not change the host network.
- Both PlatformIO environments compile with the pinned pioarduino 55.03.312 /
  Arduino-ESP32 3.3.12, using staged example credentials. Flash 70.5%, RAM 14.6%.
- Both shell scripts pass `bash -n`; `git diff --check` is clean.

**Still needs hardware**
- Pi/Linux execution, real hotspot updates/reconnection, servo startup and motion,
  and Wi-Fi loss/jitter. No board was flashed and no hotspot was started.

## 2026-09-26: review and regression fixes

**Fixed**
- ESP32 reception could stop indefinitely after one oversized UDP datagram: only
  three bytes were read, but Arduino-ESP32 refuses to parse another packet while
  the previous packet has unread bytes. Drain the full datagram and check the read
  count. Confirmed against the installed core and [Espressif's 3.3.5 implementation](https://github.com/espressif/arduino-esp32/blob/3.3.5/libraries/Network/src/NetworkUdp.cpp).
- Check the 500 ms failsafe before comparing an arriving sequence number. The old
  firmware could reject a restarted sender's first packet at the timeout boundary.
  The listener now also checks expiry after its blocking poll returns.
- Bound each firmware receive batch to 64 packets and refresh time after receiving,
  so a continuously busy socket cannot indefinitely starve servo/failsafe updates.
- Retry failed UDP binds once per second while Wi-Fi remains connected; close the
  old UDP socket when Wi-Fi goes down.
- Serialize sender lifecycle operations: simultaneous `stop()` calls previously
  could both join the same thread. Document the sender-thread callback contract.
- Apply `IP_TOS` before `SO_PRIORITY`. Linux's TOS setter also overwrites socket
  priority, undoing the original request for priority 6. Confirmed in
  [Linux 6.8's `__ip_sock_set_tos`](https://github.com/torvalds/linux/blob/v6.8/net/ipv4/ip_sockglue.c).
- Parse CLI ports/rates completely. Reject NaN, infinity, overflow, trailing junk,
  zero/out-of-range ports, and rates that reach the receiver timeout between sends.
  The library also rejects invalid periods, zero ports, and negative stop bursts.
- Poll interactive stdin with a timeout; a signal delivered to the sender thread
  no longer leaves the main thread blocked on a line read.
- Listener averages now use the actual number of measured gaps, including across
  reporting windows and excluding failsafe resets.
- Validate hotspot password length and channel before deleting the existing profile.
- Correct the stale sketch comment describing a 360-degree servo.

**Verified**
- Release CMake build on macOS; all four CTest tests pass. Coverage includes both
  hand mappings, malformed and short reads, duplicate/reordered/wrapped sequences,
  exact failsafe expiry, `millis()` rollover, servo release, bounded receive work,
  bind recovery, concurrent stops, CLI rejection, identical packets to two hands,
  SIGINT/SIGTERM with partial stdin, and listener gap statistics.
- The new firmware regression test fails against the original `hand.cpp` and
  passes against the fixed implementation for both hands.
- Both hand variants compile with Arduino-ESP32 3.3.5 and `--warnings all`, using
  staged example credentials. No sketch warnings; the core's `WiFi.cpp` emits
  missing-field-initializer warnings. Flash use is 69%, RAM 14%.
- `bash -n` passes for both shell scripts. No board was flashed and no hotspot
  was started. All edits are confined to `tactileESP32/`.

**Still needs hardware**
- Pi/Linux build and actual Wi-Fi queue priority, hotspot join/reconnection,
  venue packet loss/jitter, and SG90 movement/power stability.

## 2026-09-26: BUILD.md added

- New `BUILD.md`: one place for setup and building. It covers:
  - Pi packages and the CMake build.
  - NetworkManager on Ubuntu Server via netplan, and hotspot troubleshooting (rfkill, Wi-Fi
    country, WPA2).
  - ESP32 toolchain (Arduino IDE 2 or arduino-cli), `secrets.h`, wiring, serial port and USB
    drivers, flashing, the BOOT button, and symlinks on Windows.
  - A first-run checklist, testing without ESP32s, and a servo tuning table.
- SPEC §6 and `esp32/README.md` now link to it.
- **Verified:** `cmake -S . -B build && cmake --build build && ctest` works exactly as
  written in BUILD.md, on macOS with CMake 4.4.3 (a temporary copy, not installed).
  All 4 targets build with no warnings, and 1/1 test passes. Before this, the CMake path
  hadn't been run.
- **Not verified:** the Ubuntu Server netplan switch and the hotspot commands. They need the Pi.

## 2026-09-26: back removed, 180° servos

**From you**
- There is no back direction. Only front, left and right, one at a time.
- The servos are standard 180° SG90s, not 360°, so they can't keep rotating.

**My decisions**
- **Flag bits renumbered** to front = bit0, left = bit1, right = bit2; bits 3–7 are
  reserved. They now match the direction order with no gap where back was. Nothing
  was deployed yet, so compatibility isn't a concern. Example packet for left:
  `A5 C8 02` (it was `04`).
- **"Rotating" becomes a continuous sweep.** The arm follows
  `90° + 60°·sin(2π·t / 1000 ms)`, i.e. 30° → 150° → 30° once per second for as
  long as the direction is active.
  - A sine rather than a straight back-and-forth: it slows at the turning points, so
    there's no slam at the ends, less wear and less noise. It also starts at the rest
    angle, so starting the sweep causes no jump.
  - The peak speed is about 377°/s, within the SG90's roughly 600°/s (0.1 s/60°), so the arm can keep up.
  - The range is 30°–150°, not 0°–180°, to keep clear of the end stops, which vary between SG90s.
- **Rest = 90°, then release.** When the direction ends (neutral, failsafe or Wi-Fi
  lost), the arm goes back to 90°. After 500 ms the pulses stop, so the idle servo
  doesn't hum or jitter. Set `TACTILE_RELEASE_AFTER_MS 0` to keep holding the position instead.
- **front vs a side direction:** front sweeps both hands; a side direction sweeps only
  that hand. With back gone, the forward/reverse distinction isn't needed any more.
- **Servo updated once per 20 ms PWM frame.** The servo can't use faster updates.
- **Removed** `TACTILE_SERVO_SPEED_US` and `TACTILE_SERVO_REVERSED`. Added `TACTILE_REST_DEG`,
  `TACTILE_SWEEP_DEG`, `TACTILE_SWEEP_PERIOD_MS`, `TACTILE_PULSE_0_US` / `_180_US` (500/2400 µs)
  and `TACTILE_RELEASE_AFTER_MS`.

**Changed**
- Protocol header, `Direction` (no `back` field), `direction.cpp`, `tactile_send` (`b` is
  now an unknown command; the demo is f → l → r), `tactile_listen` names, and tests: 8
  combinations with 4 valid ones, bit 3 now reserved.
- Firmware `hand.cpp` (sweep and rest-then-release replace spin and stop), `config.h`,
  `esp32/README.md`, and SPEC §1, §3, §5–7.

**Verified**
- Pi: all tests pass. `b` is an unknown command, and `fl` and `lr` are rejected.
- Firmware: both hands compile with no warnings (`--warnings all`, core 3.3.5).

**Not verified yet**
- On a real board: sweep feel, end-stop buzz, release at rest.

## 2026-09-26: servo behaviour decided, ESP32 firmware written

**From you**
- front → both servos spin 360° continuously. back → both spin. left → only the
  left servo spins. right → only the right servo spins.
- One direction at a time. There are no combinations like front+left.

**My decisions** (you asked me to choose)
- **front vs back = forward vs reverse spin.** Otherwise front and back would feel
  identical, since both spin both servos. left/right spin their own hand forward, like front.
  The other hand staying still is what tells a side direction apart from front.
- **Servos stop when the state stops, not after a timed spin.** A servo spins for as
  long as the Pi keeps sending that direction. The Pi already resends at 20 Hz, so no
  extra timing field is needed in the packet. The ESP32 timestamps arrivals with
  `millis()` and logs them. This settles the "time log" question from the first entry.
- **Failsafe raised from 300 ms to 500 ms (10 missed packets).**
  - A false failsafe stops the servo in the middle of a direction, which feels like stutter.
    Stability is the stated priority, and Wi-Fi interference bursts of 100–300 ms are
    common in crowded 2.4 GHz.
  - The cost is only a longer spin if the Pi crashes or goes out of range. A normal
    `stop()` still stops the servos at once through the neutral burst.
  - Change `kFailsafeTimeoutMs` if needed; the Pi, ESP32 and listener all use it.
- **Stop = no PWM pulses**, instead of a 1500 µs "stop" pulse. On a continuous
  servo the true stop point varies (about 1480–1520 µs), so a 1500 µs pulse can creep.
  Sending no pulses always stops it, with no trim needed.
- **Speed: ±200 µs from 1500** (moderate). You can change it in `config.h`, along with a
  per-hand reverse flag in case the servos are mounted mirror-image.
- **Arduino sketch (core 3.x), not PlatformIO.** Your machine already has the Arduino
  ESP32 core 3.3.5, while PlatformIO has no ESP32 platform installed. The logic is in
  `hand.cpp`, not the `.ino`, because Arduino's prototype generator breaks on
  namespaces and `enum class`. There's a fallback for core 2.x's LEDC API.
- **One sketch for both hands:** `TACTILE_HAND_RIGHT` is set by `flash.sh`, or edited in
  `config.h` when using the Arduino IDE.
- **Protocol header is a symlink** in the sketch folder, pointing to
  `protocol/tactile_protocol.h`. This keeps one source of truth, and arduino-cli
  follows the link.
- **Wi-Fi password in a gitignored `secrets.h`**, never committed.
- **Status LED on GPIO 2** (solid / slow / fast blink), to debug at the demo without a laptop.
- **Servo on GPIO 18:** it has PWM and isn't a boot strapping pin.

**Changed**
- Protocol: `flags_valid` now allows at most one bit. `kFailsafeTimeoutMs` changed from 300 to 500.
- Pi: `Rejection` is now a single `kMultipleDirections`. `tactile_send` help and tests were updated,
  with 5 valid states instead of 9.
- New: `esp32/tactile_hand/` (the sketch), `esp32/flash.sh`, `esp32/.gitignore`,
  and an updated `esp32/README.md` and SPEC §1, §3, §5–7.

**Verified**
- Pi: `test_tactile` passes. `tactile_send` rejects `fl` and `fb` with "only one direction at a time".
- Firmware: compiles for both hands with arduino-cli and ESP32 core 3.3.5 (board
  "ESP32 Dev Module"), with no warnings under `--warnings all`. One `%d` format warning
  was found and fixed. It uses 69% of flash and 14% of RAM. The right-hand build has
  `TACTILE_HAND_RIGHT=1`. arduino-cli was a standalone copy in a temp folder;
  nothing was installed.

**Not verified yet**
- Nothing has run on a board: Wi-Fi join, servo direction and speed, LED, failsafe on real Wi-Fi.
- Whether your SG90s are the 360° version. A 180° SG90 can't spin continuously.

## 2026-09-26: Pi sender implemented (C++)

**Built**
- `protocol/tactile_protocol.h`: shared 3-byte wire format, validation of flags,
  seq ordering, and the timing constants (period 50 ms, failsafe 300 ms). It is
  C++11 with no STL, so the ESP32 firmware can include it unchanged.
- `tactile_link` library: `Direction` → `validate()` → `ValidDirection` →
  `TactileLink`. It has a sender thread for unicast to each hand, a fixed 20 Hz grid,
  immediate send on change, a neutral burst on stop, and per-hand stats plus
  timestamped events (the "time log").
- `tactile_send`: interactive, `--demo` and `--trace` modes.
- `tactile_listen`: ESP32 stand-in and link meter (loss, stale packets, average and max gap, failsafe).
- `test_tactile`: tests every flag combination, packet round-trip, rejected packets,
  seq wrap, and a loopback test of `TactileLink` covering rate, immediate change,
  rejection and the neutral burst on stop.
- `scripts/pi_hotspot.sh`: `scan`, `up`, `down`, `status` for the hotspot.
- `esp32/README.md`: firmware requirements, as a placeholder for the next step.

**Verified** (macOS, Apple clang 17, `-Wall -Wextra -Wpedantic`, no warnings)
- `test_tactile` passed on 5 runs in a row.
- On loopback with two listeners (ports 4210/4211) and `--demo`, both hands saw
  identical states, with 0 lost, 0 stale and 0 malformed packets.
- First measurement: the average gap was 52.3 ms instead of 50 ms, because the
  next tick was scheduled from after sending. I fixed this with a fixed tick grid,
  and re-measured a 49.9 ms average and 55.5 ms max over a steady 5 s window.
- The listener's failsafe fired 309 ms after the last packet, as expected.
- Interactive mode rejects `fb` and `lr` with a reason, and the state is unchanged.
- `pi_hotspot.sh` passes `bash -n`.

**Not verified yet**
- CMake build: `cmake` isn't installed on the dev Mac, so the sources were compiled
  directly with the same flags. Check `cmake -S . -B build` on the Pi.
- Everything on real hardware: the hotspot on the Pi 5, Linux `SO_PRIORITY`, real
  Wi-Fi loss and jitter, the ESP32s.

**Decisions made while building**
- **Hand IPs are .2 (left) and .3 (right), not .11/.12 as first suggested.**
  NetworkManager's shared mode gives out DHCP addresses from 10.42.0.10–254, so
  .11/.12 could go to a laptop. Changing the range directly needs NetworkManager
  1.52, and Ubuntu 24.04 ships an older version. .2 and .3 are outside the pool.
- **A state change is sent immediately**, not on the next 50 ms tick, then the tick
  grid restarts.
- **`stop()` wins over a pending change**, and the last thing sent is always neutral.
- **Send errors are logged only on transitions** (failing / recovered), so an absent
  hand doesn't flood the log.
- **Socket priority set for the Wi-Fi voice queue** (`SO_PRIORITY 6`, DSCP EF), best effort.

## 2026-09-26: requirements agreed

- **Hardware:** Pi 5 sender. Two ESP32-WROOM-32 boards, one per hand, each with one
  SG90 servo. The servo moves; it does not vibrate.
- **Directions:** front → both hands, back → both hands, left → left hand, right → right hand.
- **Packet history:**
  1. `[0,1,2,3,PWM×4]`: motor IDs plus PWM (first idea).
  2. `[F,B,L,R]` as 0/1: the ESP32 decides the motion, so PWM was dropped.
  3. Magic byte and seq added, to ignore stray traffic and drop late packets.
  4. The 4 flags packed into 1 byte, giving the final 3 bytes `[0xA5, seq, flags]`.
- **Invalid combinations:** front+back and left+right are rejected on the Pi in a
  validation step *before* packing. They never reach the packet.
- **Network: option A.** The Pi 5 runs the hotspot and both ESP32s join with static IPs.
  - Rejected: an ESP32 hosting the hotspot. It's weaker, and one hand resetting would drop both.
  - Rejected: a phone hotspot or router. It adds a hop and delay, and venue Wi-Fi
    blocks devices from talking to each other.
- **Delivery:** unicast the same packet to each hand. Resend the current state at 20 Hz.
  No acks, and the ESP32 sends nothing back.
- **Firmware must turn off ESP32 Wi-Fi power saving.** This is noted in SPEC §5 for the esp32 work.
- **Failsafe:** 300 ms without a packet → servo neutral.
- **Language:** C++ for everything on the Pi. Python was considered and dropped.
- **Layout:** Pi code in `tactileESP32/`, firmware in `tactileESP32/esp32/`, all on main.
- **Deferred to the next discussion:** how the servo reacts, which needs the timing log.
