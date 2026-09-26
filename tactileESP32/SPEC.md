# Tactile direction link: Raspberry Pi 5 → two ESP32 hands

Status: the Pi sender is implemented and tested on loopback. The ESP32 firmware
is not started. Change history is in [LOG.md](LOG.md).

## 1. Goal and scope

The Pi 5 tells the user which way to go by moving one SG90 servo on each hand.
Each hand has its own ESP32-WROOM-32. The Pi sends both hands the same 4 direction
flags over Wi-Fi UDP. **The priority is a smooth, stable wireless link.**

| Flag  | Meaning       | Which servo reacts |
|-------|---------------|--------------------|
| front | go forward    | both hands         |
| back  | go back       | both hands         |
| left  | turn left     | left hand only     |
| right | turn right    | right hand only    |

No flags set means "no direction", and both servos go to neutral. This is movement,
not vibration.

In scope here: the network, the packet, the Pi sender (C++), and test tools.
The firmware lives in [esp32/](esp32/) and has only requirements so far (section 5).

## 2. Network

```
               Wi-Fi "TactileNet", 2.4 GHz, WPA2
   ┌───────────────────────┐
   │ Raspberry Pi 5        │── unicast UDP :4210 ──▶ left ESP32   10.42.0.2
   │ hotspot  10.42.0.1    │── unicast UDP :4210 ──▶ right ESP32  10.42.0.3
   └───────────────────────┘
        ▲ optional: laptop joins the hotspot for SSH/VNC (gets DHCP .10–.254)
```

- **The Pi hosts the hotspot** through NetworkManager: `scripts/pi_hotspot.sh up`.
  Both hands join as stations. There's no router, phone or venue Wi-Fi involved,
  so it works the same anywhere. The trade-off is that the Pi has no internet over
  Wi-Fi while the hotspot is up; Ethernet still works.
- **2.4 GHz only (band `bg`)**, because the ESP32-WROOM-32 has no 5 GHz. The default
  channel is 6. At the venue, run `pi_hotspot.sh scan` first and pick whichever of
  1, 6 or 11 is least crowded (`TACTILE_AP_CHANNEL=11`).
- **Static IPs for the hands**, outside NetworkManager's DHCP pool
  (10.42.0.10–254), so a laptop can never take a hand's address. The Pi
  always knows where to send without discovery. (Setting the DHCP range directly
  needs NetworkManager 1.52; Ubuntu 24.04 ships an older version.)
- **Security:** WPA2-PSK with AES/CCMP and PMF off. This is the combination both the
  Pi's Broadcom AP mode and the ESP32 handle reliably. Set the password through
  `TACTILE_AP_PSK` so it isn't committed.

## 3. Packet

3 bytes, defined in [protocol/tactile_protocol.h](protocol/tactile_protocol.h),
which the Pi and ESP32 code share:

| Byte | Field | Value |
|------|-------|-------|
| 0 | magic | `0xA5`. Anything else is ignored |
| 1 | seq   | +1 for every packet sent, wraps 255 → 0 |
| 2 | flags | bit0 front, bit1 back, bit2 left, bit3 right; bits 4–7 must be 0 |

Example: front+left with seq 200 → `A5 C8 05`.

- **Flags are packed into one byte**, since each direction is just 0/1. This replaces
  the original `[F, B, L, R]` one-per-slot layout.
- **The magic byte** makes a receiver ignore stray traffic that happens to hit port 4210.
- **seq** lets a receiver drop late, reordered or duplicated datagrams:
  it accepts a packet only if `seq_is_newer(seq, last_seq)` (8-bit serial-number
  comparison, so it handles the wrap). After a failsafe (no packet for 300 ms) the
  receiver accepts any seq, because the Pi may have restarted and reset its counter.
  At 20 Hz, seq also acts as a coarse clock: 1 step = 50 ms.
- **Validation happens before packing.** front+back together and left+right
  together are rejected on the Pi (section 4) and never reach the encoder. The
  decoder rejects them again on the receiving side as a second check.
  The 9 valid states are: neutral, the 4 single directions, and the 4 diagonals
  (e.g. front+left). Whether diagonals should stay valid is an open question (section 7).

## 4. Pi sender (C++)

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

**Sending behaviour**

| Rule | Why |
|------|-----|
| Unicast, the same packet sent once to each hand per cycle | Unicast frames get up to about 7 automatic Wi-Fi retries. Broadcast frames get none and go out at the lowest data rate. |
| Resend the current state every 50 ms (20 Hz) on a fixed grid | A lost packet is replaced 50 ms later. No acks or retry logic are needed, and the rate doesn't drift. |
| Send a change immediately, then restart the grid | A direction change doesn't wait up to 50 ms for the next tick. |
| 3 neutral packets on `stop()` | The servos release right away instead of after the 300 ms failsafe. |
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

## 5. ESP32 receiver requirements (for `esp32/`, not implemented yet)

1. Join `TactileNet` with a static IP (left `10.42.0.2`, right `10.42.0.3`,
   gateway `10.42.0.1`/24). The hand's role (left/right) is a build-time setting.
2. **Turn Wi-Fi power saving off** after connecting: `WiFi.setSleep(false)`
   (Arduino) or `esp_wifi_set_ps(WIFI_PS_NONE)`. The default modem-sleep mode
   adds 100–300 ms of latency and is the most common cause of lag.
3. Reconnect automatically if the connection drops. Keep the servo at neutral
   while disconnected.
4. Listen on UDP 4210. Decode with `tactile::decode_packet`, drop packets
   where `!seq_is_newer`, and accept any seq after a failsafe.
5. **Failsafe:** if no valid packet arrives for 300 ms (`kFailsafeTimeoutMs`), go to neutral.
6. Map flags to this hand: front/back → move; own side (left or right) → move;
   the other side's flag → ignore. The exact servo motion is not decided yet
   (section 7).
7. Power the SG90 from a separate 5 V supply with a shared ground and a bulk
   capacitor. Servo current spikes on the ESP32's supply cause brownout resets,
   and those show up as Wi-Fi dropouts.

[tools/tactile_listen.cpp](tools/tactile_listen.cpp) implements rules 4–5
exactly and is the reference for the firmware.

## 6. Build, test, run

On the Pi (Ubuntu 24.04), or any Linux or macOS machine for development:

```bash
cd tactileESP32
cmake -S . -B build && cmake --build build -j
ctest --test-dir build --output-on-failure
```

Tools:

| Command | Purpose |
|---------|---------|
| `build/tactile_send` | Interactive: type `f`, `b`, `l`, `r`, combinations like `fl`, `n` (neutral), `s` (stats), `q` (quit). |
| `build/tactile_send --demo` | Cycles front → back → left → right with neutral in between, 1 s each. |
| `build/tactile_send --trace` | Logs every packet with a timestamp. |
| `build/tactile_send --left IP[:PORT] --right IP[:PORT] --rate HZ` | Overrides the defaults. |
| `build/tactile_listen [--port N] [--verbose]` | Stand-in for an ESP32: decode, seq check, failsafe, plus a loss/gap report every 5 s. |

Testing without hardware:
- **On one machine:** run `tactile_listen --port 4210`, `tactile_listen --port 4211`,
  and `tactile_send --left 127.0.0.1:4210 --right 127.0.0.1:4211 --demo`.
- **Over real Wi-Fi:** join a laptop to the hotspot with a static IP of 10.42.0.2
  and run `tactile_listen`. Its 5 s reports show loss and the worst gap between packets.

## 7. Open questions (to decide before the firmware)

1. **Servo reaction.** What should the servo do for front, back, own side and
   neutral: which angles, a hold or a tap pattern, and how fast should it sweep?
   Does it need timing from the Pi, such as how long a state has been held, or
   are local arrival times (`millis()`) plus seq enough?
2. **Diagonals.** Should front+left, front+right, back+left and back+right stay valid?
   If so, the left hand sees both "front" and "left". What should it show?
3. **Failsafe timeout.** 300 ms equals 6 missed packets. Should it be shorter for
   quicker release, or longer to ride out Wi-Fi hiccups?

## File layout

```
tactileESP32/
  SPEC.md, LOG.md
  CMakeLists.txt
  protocol/tactile_protocol.h     shared wire format (Pi + ESP32)
  include/tactile/, src/          Pi library: validation + UDP link
  tools/tactile_send.cpp          manual/demo sender
  tools/tactile_listen.cpp        ESP32 stand-in and link meter
  test/test_tactile.cpp           unit + loopback tests
  scripts/pi_hotspot.sh           hotspot scan/up/down/status
  esp32/                          firmware (next)
```
