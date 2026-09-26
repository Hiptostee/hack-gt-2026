# ESP32 hand firmware

This is one Arduino sketch, `tactile_hand/`, flashed to both boards. The only
difference between them is `TACTILE_HAND_RIGHT` (0 = left, 1 = right). The design is in
[../SPEC.md](../SPEC.md), section 5. Step-by-step setup, including toolchain, wiring,
serial ports and troubleshooting, is in [../BUILD.md](../BUILD.md), section 3.

**Hardware:** an ESP32-WROOM-32 DevKit and a standard **180° SG90**. Connect the
servo signal to GPIO 18.
**Power the servo from its own 5 V supply**, share the ground with the ESP32, and add
a ~470 µF capacitor across the servo supply. If the servo draws from the ESP32's
supply, its current spikes cause brownout resets, and those look like Wi-Fi dropouts.

## Setup

1. Install the ESP32 core: Arduino Boards Manager → "esp32" by Espressif (3.x;
   compiled with 3.3.5; not yet run on a board).
2. `cp tactile_hand/secrets.example.h tactile_hand/secrets.h`, then set the same SSID
   and password you used for `scripts/pi_hotspot.sh up`. `secrets.h` is gitignored.

## Build and flash

With arduino-cli (`brew install arduino-cli`):

```bash
./flash.sh left                              # compile only
./flash.sh left  /dev/cu.usbserial-XXXX      # flash + serial monitor
./flash.sh right /dev/cu.usbserial-YYYY
```

With the Arduino IDE: open `tactile_hand/tactile_hand.ino`, choose board **ESP32 Dev
Module**, set `TACTILE_HAND_RIGHT` in `config.h`, and upload. Set it back to 0 afterwards.

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
