# Tactile direction link: laptop → two ESP32 hands

Status: the laptop sender reads fresh guidance from the Pi bridge and transmits
the shared UDP packet. The ESP32 firmware compiles, but hasn't run on hardware yet. Change history is
in [LOG.md](LOG.md).

## 1. Goal and scope

The Pi 5 plans the route; the laptop tells the user which way to go by moving one SG90 servo on each hand.
Each hand has its own ESP32-WROOM-32. The laptop sends both hands the same 3 direction
flags over Wi-Fi UDP. **The priority is a smooth, stable wireless link.**

| Flag  | Meaning    | Left servo (180°) | Right servo (180°) |
|-------|------------|-------------------|--------------------|
| front | go forward | sweeps            | sweeps             |
| left  | turn left  | sweeps            | at rest            |
| right | turn right | at rest           | sweeps             |

**Only one direction is sent at a time.** There is no "back". No flags set means
"no direction", and both servos return to rest. The servos are standard 180°
SG90s, so they can't spin continuously. Instead, "rotating" is a continuous smooth sweep
back and forth (30°–150° around a 90° rest) for as long as the direction is held.

In scope: the network, the packet, the laptop sender, the ESP32 firmware
([esp32/](esp32/)), and test tools.

## 2. Network

```
   Pi ROS guidance ── SSH tunnel ──▶ laptop ── Wi-Fi UDP :4210 ──▶ two ESP32 hands
```

- The hands join the **same Wi-Fi network as the laptop**. It must offer 2.4 GHz
  service and allow peer-to-peer UDP traffic; guest networks with client isolation
  will block this link. The laptop keeps internet access for Gemini.
- Each hand gets an address through DHCP and advertises `tactile-left.local` or
  `tactile-right.local` over mDNS. The laptop launcher accepts explicit hand IPs
  if the network does not pass mDNS.
- The Pi remains reachable over Tailscale. The laptop polls its bridge over the
  SSH tunnel, then unicasts the three-byte packet to each hand at 20 Hz.
- The old Pi hotspot and C++ sender are retained as standalone test tools. They
  are not used by the integrated laptop launch.

## 3. Packet

3 bytes, defined in [protocol/tactile_protocol.h](protocol/tactile_protocol.h),
which the Pi and ESP32 code share:

| Byte | Field | Value |
|------|-------|-------|
| 0 | magic | `0xA5`. Anything else is ignored |
| 1 | seq   | +1 for every packet sent, wraps 255 → 0 |
| 2 | flags | bit0 front, bit1 left, bit2 right; bits 3–7 must be 0 |

Example: left with seq 200 → `A5 C8 02`.

- **Flags are packed into one byte**, since each direction is just 0/1. This replaces
  the original `[F, B, L, R]` one-per-slot layout.
- **The magic byte** makes a receiver ignore stray traffic that happens to hit port 4210.
- **seq** lets a receiver drop late, reordered or duplicated datagrams:
  it accepts a packet only if `seq_is_newer(seq, last_seq)` (8-bit serial-number
  comparison, so it handles the wrap). After a failsafe (no packet for 500 ms) the
  receiver accepts any seq, because the Pi may have restarted and reset its counter.
  At 20 Hz, seq also acts as a coarse clock: 1 step = 50 ms.
- **Validation happens before packing.** Any request with more than one direction
  is rejected on the Pi (section 4) and never reaches the encoder. The decoder
  rejects it again on the receiving side as a second check. There are 4 valid
  states: neutral, front, left, right.

## 4. Sender

The integrated laptop sender is `companion.voice.tactile_link`. The Pi bridge
exposes `/guidance/state` from ROS `/backpack/direction`, `/backpack/path_valid`
and the voice guidance gate. Direction codes 0/1/2/3 map to
front/right/left/neutral. A missing, invalid or stale state sends neutral.
The ESP32 also returns to rest after 500 ms without packets. The laptop launch
starts the sender automatically.

The original C++ sender below remains useful for direct packet and hardware tests.

Library `tactile_link` ([include/tactile/](include/tactile/), [src/](src/)):

```cpp
#include "tactile/udp_link.hpp"

tactile::TactileLink link(tactile::LinkConfig{}, on_event);   // defaults: .2/.3 :4210, 20 Hz
link.start();

tactile::Direction d;
d.front = true;
tactile::Rejection why;
if (!link.try_set(d, &why)) { /* invalid combination, state unchanged */ }

// or validate yourself: only a ValidDirection can be set
if (auto valid = tactile::validate(d, &why)) link.set(*valid);

link.stop();   // sends 3 neutral packets so the servos release at once
```

**Validation as a separate step.** Callers describe a request with the plain
`Direction` struct. Only `tactile::validate()` can create a `ValidDirection`, and
`TactileLink::set()` accepts only `ValidDirection`. So an invalid combination is
stopped at compile time from ever reaching the packet encoder. `try_set()` is a
shortcut for validating and then setting. A rejected request leaves the current
state unchanged.

`LinkConfig` rejects port 0, negative stop-burst counts, and send periods outside
1–499 ms (the period must be shorter than the receiver failsafe). `--rate` accepts
finite numbers greater than 2 and at most 1000 Hz; 20 Hz remains the default.
Lifecycle calls are serialized, including concurrent `stop()` calls. Event handlers
run on the sender thread: they must be quick, must not throw, and must not call
`start()`, `stop()`, or destroy the link.

**Sending behaviour**

| Rule | Why |
|------|-----|
| Unicast, the same packet sent once to each hand per cycle | Unicast frames get up to about 7 automatic Wi-Fi retries. Broadcast frames get none and go out at the lowest data rate. |
| Resend the current state every 50 ms (20 Hz) on a fixed grid | A lost packet is replaced 50 ms later. No acks or retry logic are needed, and the rate doesn't drift. |
| Send a change immediately, then restart the grid | A direction change doesn't wait up to 50 ms for the next tick. |
| 3 neutral packets on `stop()` | The servos return to rest right away instead of after the 500 ms failsafe. |
| Non-blocking `sendto`, errors counted per hand | A missing hand never stalls the other one. |
| Socket priority: `SO_PRIORITY 6` + DSCP EF, best effort | Puts the packets in the Wi-Fi voice queue (WMM AC_VO), ahead of SSH/VNC traffic. |

The Pi gets **nothing back** from the hands, by design. A hand that is off or out
of range is usually invisible to `sendto`, which only fails when the Pi's own
network is down. Use `scripts/pi_hotspot.sh status` (ping) or `tactile_listen`
(section 6) to check the link.

**Time log.** Every `Event` carries a `steady_clock` timestamp and the seq it applies to:
`kStarted`, `kStateChanged` (when the first packet with a new state left),
`kSendFailed` / `kSendRecovered` (on transitions only, so they don't flood the log),
`kStopped`, and `kPacketSent` for every packet when `trace` is on.
`tactile_send` prints these as `[seconds.ms] ...`. Together with the listener's
arrival timestamps, this gives end-to-end timing for tuning the servo reaction.

## 5. ESP32 firmware

The sketch is [esp32/tactile_hand/](esp32/tactile_hand/). One sketch serves both
boards; `TACTILE_HAND_RIGHT` picks the side. Setup and flashing are in
[esp32/README.md](esp32/README.md).

1. **Wi-Fi:** joins the laptop's 2.4 GHz Wi-Fi network through DHCP. The SSID
   and password live in a gitignored `secrets.h`. Each hand advertises its side
   as an mDNS hostname and prints its assigned IP to serial.
2. **Power saving off:** `WiFi.setSleep(false)` at startup and again on every
   (re)connect. The default modem-sleep mode adds 100–300 ms of latency.
3. **Reconnect:** uses the core's auto-reconnect, and starts over after 10 s without Wi-Fi.
   The servo is at rest while Wi-Fi is down. A failed UDP bind is retried every second.
4. **Receive:** UDP 4210, parsed with `tactile::decode_packet`. Packets that aren't
   newer by `seq_is_newer` are dropped. Any seq is accepted after a failsafe.
   Queued packets are drained in batches of at most 64 per loop, so sustained traffic
   cannot indefinitely delay servo updates. Oversized datagrams are fully discarded.
   Timeout expiry is checked before sequence comparison, including at packet arrival.
5. **Failsafe:** no valid packet for 500 ms (`kFailsafeTimeoutMs`) → the servo returns to rest.
6. **Servo:** a 180° SG90 on GPIO 18, driven by the ESP32's LEDC PWM at 50 Hz with
   one update per 20 ms frame.
   - **Active:** the angle follows `90° + 60°·sin(2π·t / 1 s)`, a smooth sweep
     from 30° to 150° and back once per second. It starts at the rest angle, so there's no
     jump. Being a sine, it slows at the ends instead of slamming into them.
   - **Inactive:** the arm returns to 90° and, after 500 ms, the pulses stop, so an
     idle servo doesn't hum or jitter.
   - Rest angle, sweep width, sweep period and the 0°/180° pulse calibration
     (500/2400 µs) are all set in `config.h`.
7. **Diagnostics:** the status LED (solid / slow / fast blink = receiving / Wi-Fi only /
   no Wi-Fi) and a timestamped serial log, including a loss/gap/RSSI report every 5 s.
8. **Power:** the SG90 runs from its own 5 V supply with a shared ground and a bulk
   capacitor. On the ESP32's supply, servo current spikes cause brownout resets,
   and those look like Wi-Fi dropouts.

The receive rules (4, 5) are identical to [tools/tactile_listen.cpp](tools/tactile_listen.cpp),
so a laptop running the listener behaves like a hand.

## 6. Build, test, run

Full setup, including toolchains, the hotspot, wiring, flashing and troubleshooting, is in
[BUILD.md](BUILD.md). Quick reference:

On the Pi (Ubuntu 24.04), or any Linux or macOS machine for development:

```bash
cd tactileESP32
cmake -S . -B build && cmake --build build -j
ctest --test-dir build --output-on-failure
```

Tools:

| Command | Purpose |
|---------|---------|
| `build/tactile_send` | Interactive: type `f`, `l`, `r`, `n` (neutral), `s` (stats), `q` (quit). |
| `build/tactile_send --demo` | Cycles front → left → right with neutral in between, 1 s each. |
| `build/tactile_send --trace` | Logs every packet with a timestamp. |
| `build/tactile_send --left IP[:PORT] --right IP[:PORT] --rate HZ` | Overrides the defaults. |
| `build/tactile_listen [--port N] [--verbose]` | Stand-in for an ESP32: decode, seq check, failsafe, plus a loss/gap report every 5 s. |

Testing without hardware:
- **On one machine:** run `tactile_listen --port 4210`, `tactile_listen --port 4211`,
  and `tactile_send --left 127.0.0.1:4210 --right 127.0.0.1:4211 --demo`.
- **Over real Wi-Fi:** join a laptop to the hotspot with a static IP of 10.42.0.2
  and run `tactile_listen`. Its 5 s reports show loss and the worst gap between packets.
- **Firmware:** `esp32/flash.sh left|right [port]` (see [esp32/README.md](esp32/README.md)).

## 7. Decisions and remaining checks

The earlier open questions have been decided. The reasons are in [LOG.md](LOG.md):
only front, left and right exist, one at a time. The 180° servos sweep back and forth
while a direction is active, and the failsafe is 500 ms.

Still to check on hardware:
- The sweep width and speed feel right on the hand, and the SG90 doesn't buzz at the
  ends of its travel (`config.h`).
- Real Wi-Fi loss and gap figures at the venue; pick the channel with `pi_hotspot.sh scan`.
- The CMake build on the Pi itself (it has been checked on macOS).

## File layout

```
tactileESP32/
  SPEC.md, BUILD.md, LOG.md
  CMakeLists.txt
  protocol/tactile_protocol.h     shared wire format (Pi + ESP32)
  include/tactile/, src/          Pi library: validation + UDP link
  tools/tactile_send.cpp          manual/demo sender
  tools/tactile_listen.cpp        ESP32 stand-in and link meter
  test/test_tactile.cpp           unit + loopback tests
  scripts/pi_hotspot.sh           hotspot scan/up/down/status
  esp32/flash.sh                  build/flash one hand with arduino-cli
  esp32/tactile_hand/             Arduino sketch (hand.cpp, config.h, secrets.h)
```
