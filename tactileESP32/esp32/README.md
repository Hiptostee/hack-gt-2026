# ESP32 hand firmware

This is one Arduino sketch, `tactile_hand/`, flashed to both boards. The only
difference between them is `TACTILE_HAND_RIGHT` (0 = left, 1 = right). The design is in
[../SPEC.md](../SPEC.md), section 5. Step-by-step setup, including toolchain, wiring,
serial ports and troubleshooting, is in [../BUILD.md](../BUILD.md), section 3.

**Hardware:** an ESP32-WROOM-32 DevKit and a standard **180° SG90**. The servo
signal uses **GPIO 18**, matching `TACTILE_SERVO_PIN` in
[`tactile_hand/config.h`](tactile_hand/config.h).

## Wiring: one 4 × AA pack per hand, no external regulator

Repeat this wiring for each hand. The battery feeds the servo and ESP32 through
separate branches; the ESP32 DevKit still uses its **onboard 3.3 V regulator**.

**Check voltage compatibility before connecting the pack.** Four alkaline AAs in
series are nominally **6 V** (higher when fresh); four NiMH AAs are nominally
**4.8 V** (higher immediately after charging). Neither is a regulated 5 V supply.
Direct battery power is suitable only if the **full battery voltage range** is
within both your exact DevKit input and servo ratings. The generic `esp32dev`
firmware setting does not identify those ratings.
[Espressif's DevKitC guide](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32/esp32-devkitc/user_guide.html)
specifies a **5V** power header, and
[TowerPro's SG90 specification](https://towerpro.com.tw/product/sg90-7/)
lists **4.8 V**; these do not establish compatibility with a fresh 6 V pack.
If the ratings do not cover your pack, this no-external-regulator setup needs a
different supply or compatible hardware.

### Connection diagram

The battery-positive connections below are **conditional on that voltage check**.
Use the board's printed pin labels, not physical pin numbers.

```text
       4 × AA in series                 ESP32 DevKit
       + ------------+----------------> VIN / 5V (*)
                     |
                     +----------------> Servo red: V+
                     |
                     |     near servo
                     +---- (+) 470 uF (-) ----+
                     |                       |
                     +---- [100 nF ceramic] -+
                                             |
       - ------------+-----------------------+--> Servo brown/black: GND
                     |
                     +---------------------> ESP32 GND

       ESP32 GPIO 18 ----------------------> Servo orange/yellow: signal

       (*) Verified battery-compatible power input only; NEVER 3V3.
```

| Connection | Destination |
|------------|-------------|
| Battery pack **+** | Servo **red / V+**, and a separate wire to the DevKit's verified power input |
| DevKit power input | **VIN** if documented for your pack voltage; **5V** only if its documented input range covers your pack |
| Battery pack **−** | ESP32 **GND** and servo **brown/black / GND** (common ground) |
| ESP32 **GPIO 18** (often labelled `18` or `D18`) | Servo **orange/yellow / signal** |
| **470 µF electrolytic**, rated **10 V or higher** | **+** leg to servo V+, **−** leg (striped side) to servo GND |
| **100 nF ceramic** | Across the same servo V+ and GND connections; either orientation |

The capacitors are **in parallel across servo power and ground**, not in series
and not on GPIO 18. Place them close to the servo connector with short leads.
Wire colors are typical; check your servo's labels or datasheet.

Keep the servo's power and ground branches direct to the battery junction so its
motor current does not flow through the DevKit or its thin jumper wires. Bulk
capacitance helps with short current spikes, but does not regulate voltage or
prevent brownouts from weak batteries. If moving the servo resets the ESP32 or
drops Wi-Fi, check battery voltage under load and the power connections.

**USB flashing:** disconnect the battery pack before plugging in USB, and unplug
USB before reconnecting the pack. Espressif specifies using only one board power
input at a time. Never connect the AA pack to **3V3** or power the servo from
**3V3**.

## Setup

1. Install [PlatformIO](https://platformio.org/install) (`brew install platformio`, or
   the PlatformIO IDE extension for VS Code). The first build downloads the ESP32
   platform pinned in `platformio.ini` (pioarduino 55.03.312 = Arduino core 3.3.12).
2. `cp tactile_hand/secrets.example.h tactile_hand/secrets.h`, then set the same SSID
   and password you used for `scripts/pi_hotspot.sh up`. `secrets.h` is gitignored.

## Build and flash

Run these from this folder. The `left` and `right` environments set `TACTILE_HAND_RIGHT`
for you:

```bash
pio run -e left                                                   # compile only
pio run -e left  -t upload -t monitor --upload-port /dev/cu.usbserial-XXXX
pio run -e right -t upload -t monitor --upload-port /dev/cu.usbserial-YYYY
```

arduino-cli (`./flash.sh left|right [port]`) and the Arduino IDE still work; see
[../BUILD.md](../BUILD.md), section 3.

`tactile_protocol.h` in the sketch folder is a symlink to
`../../protocol/tactile_protocol.h`, so the Pi and the ESP32 always share one packet
definition. (On Windows, enable git symlinks, or copy the file.)

## Behaviour

| Packet  | Left servo | Right servo |
|---------|------------|-------------|
| front   | sweeps | sweeps |
| left    | sweeps | at rest |
| right   | at rest | sweeps |
| neutral, failsafe or no Wi-Fi | at rest | at rest |

**Sweep:** the arm moves smoothly back and forth, 30° → 150° → 30°, once per second,
centred on the 90° rest angle. It follows a sine, so it eases in and out at the ends.
**Rest:** the arm returns to 90°, and after 500 ms the PWM pulses stop, so an idle
servo doesn't hum.

To tune it, edit `config.h`:

| Setting | Default | What it does |
|---------|---------|--------------|
| `TACTILE_REST_DEG` | 90 | rest angle |
| `TACTILE_SWEEP_DEG` | 60 | sweep to each side of rest |
| `TACTILE_SWEEP_PERIOD_MS` | 1000 | one full back-and-forth |
| `TACTILE_PULSE_0_US` / `_180_US` | 500 / 2400 | pulse widths at 0° / 180°. If the arm buzzes at an end, move these toward 1500 |
| `TACTILE_RELEASE_AFTER_MS` | 500 | stop pulses this long after reaching rest (0 = keep holding) |

## Status LED (GPIO 2)

- solid: receiving packets
- slow blink: Wi-Fi connected, but no packets
- fast blink: no Wi-Fi

## Serial log (115200 baud)

Every line is timestamped in seconds since boot. The log shows Wi-Fi up or lost
(with channel and RSSI), every state and servo change, failsafes, and a report
every 5 s: packets received, lost, stale and malformed, the longest gap, and RSSI.
The receive rules match `tools/tactile_listen.cpp`, so the two logs compare directly.
