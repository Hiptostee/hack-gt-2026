# Build and setup

This file covers everything needed to go from a fresh checkout to servos moving:
the Pi 5 sender, the hotspot, and the two ESP32 hands. What the system does and why is in
[SPEC.md](SPEC.md).

| Part | Where it runs | Toolchain |
|------|---------------|-----------|
| Pi sender, tools, tests | Raspberry Pi 5, Ubuntu 24.04 (also builds on macOS/Linux for development) | CMake ≥ 3.20, C++17 compiler |
| Hotspot | Pi 5 | NetworkManager (`nmcli`) |
| Hand firmware | 2 × ESP32-WROOM-32 DevKit | Arduino ESP32 core 3.x (Arduino IDE 2 or arduino-cli) |

---

## 1. Pi 5: build the sender

```bash
sudo apt update
sudo apt install -y build-essential cmake git

cd tactileESP32
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
ctest --test-dir build --output-on-failure      # expect: all tests passed
```

This produces:

| Binary | Purpose |
|--------|---------|
| `build/tactile_send` | Sends directions to both hands (interactive or `--demo`) |
| `build/tactile_listen` | Pretends to be a hand, to check the link without an ESP32 |
| `build/test_tactile` | Unit tests plus a loopback test |
| `build/libtactile_link.a` | Library for code that needs to send directions |

This part doesn't depend on ROS or Docker.

CTest runs the Pi library tests and simulated left/right firmware tests. If Python 3
is available when CMake configures, it also runs `test_tools` (four tests total),
covering the CLI, two-hand loopback delivery, signal shutdown, and listener reports.
Python is only needed for that optional test; the Pi runtime remains C++.
These tests need permission to bind local UDP sockets. Firmware simulation uses
test doubles for Arduino/Wi-Fi and does not replace testing on real boards.

**macOS (development only):** run `xcode-select --install` and `brew install cmake`,
then use the same commands as above.

## 2. Pi 5: Wi-Fi hotspot

### One-time setup: make sure NetworkManager manages Wi-Fi

```bash
nmcli --version && nmcli device status     # wlan0 should be listed, not "unmanaged"
```

Ubuntu Desktop already uses NetworkManager. **Ubuntu Server doesn't.** If `nmcli` is
missing or `wlan0` shows as unmanaged, do the following **with Ethernet or a keyboard and
screen attached**, because the Pi's Wi-Fi may drop while you switch:

```bash
sudo apt install -y network-manager
sudo tee /etc/netplan/01-network-manager.yaml >/dev/null <<'EOF'
network:
  version: 2
  renderer: NetworkManager
EOF
sudo chmod 600 /etc/netplan/01-network-manager.yaml
sudo netplan apply
```

### Pick a channel and start the hotspot

```bash
./scripts/pi_hotspot.sh scan                 # before `up`: shows busy channels
export TACTILE_AP_PSK='choose-a-password'    # 8–63 characters; reused in secrets.h
TACTILE_AP_CHANNEL=6 sudo -E ./scripts/pi_hotspot.sh up
./scripts/pi_hotspot.sh status               # Pi at 10.42.0.1, ping both hands
```

- Pick whichever of channels 1, 6 or 11 has the fewest and weakest networks.
- The hotspot is saved with `autoconnect yes`, so it comes back after a reboot.
  Stop it with `sudo ./scripts/pi_hotspot.sh down`.
- While it's up, the Pi has no internet over Wi-Fi. Use Ethernet if you need both.
- To SSH in from a laptop, join `TactileNet` (the laptop gets an address from .10 upward) and
  run `ssh <user>@10.42.0.1`.

If `up` fails:

| Symptom | Fix |
|---------|-----|
| `Wi-Fi is disabled` / blocked | `sudo rfkill unblock wifi && nmcli radio wifi on` |
| AP starts but no channels / fails to activate | Set the Wi-Fi country: `sudo iw reg set US`. To make it permanent, add `cfg80211.ieee80211_regdom=US` to `/boot/firmware/cmdline.txt` |
| ESP32 or laptop can't join / authentication fails | The script already forces WPA2-AES with PMF off. Check that the password matches `secrets.h` exactly |

## 3. ESP32 hands

### Toolchain (on the laptop that flashes the boards)

Use either of these:

- **Arduino IDE 2:** Boards Manager → install **"esp32" by Espressif Systems**, version 3.x.
- **arduino-cli:**
  ```bash
  brew install arduino-cli                  # or see arduino.github.io/arduino-cli
  arduino-cli core update-index
  arduino-cli core install esp32:esp32
  ```

No extra libraries are needed. The firmware only uses `WiFi` and `WiFiUdp` from the core.

### Wi-Fi password

```bash
cd tactileESP32/esp32
cp tactile_hand/secrets.example.h tactile_hand/secrets.h
# edit secrets.h: TACTILE_AP_SSID "TactileNet", TACTILE_AP_PSK = same as TACTILE_AP_PSK on the Pi
```

`secrets.h` is gitignored; never commit it.

### Wiring (each hand)

| SG90 wire | Connect to |
|-----------|------------|
| orange (signal) | ESP32 **GPIO 18** |
| red (+) | **separate 5 V supply** (+) |
| brown (−) | 5 V supply (−) **and** ESP32 **GND** (shared ground) |

- Put a **~470 µF capacitor** across the servo's 5 V supply, close to the servo.
- Power the ESP32 from USB or its own supply. **Don't power the servo from the ESP32's
  3V3 or 5V pin.** Servo current spikes brown out the ESP32, and a brownout looks like
  a Wi-Fi dropout.

### Find the serial port

```bash
arduino-cli board list          # or: ls /dev/cu.*   (macOS)   /  ls /dev/ttyUSB*  (Linux)
```

- The board shows up as something like `/dev/cu.usbserial-0001` or `/dev/cu.wchusbserial*`.
- If nothing appears, first try another USB cable; many are charge-only. If it still
  doesn't appear, install the USB-serial driver for your board's chip: **CP210x** (Silicon Labs)
  or **CH340** (WCH). The chip name is printed next to the USB port.

### Flash

With arduino-cli, use the helper script. It passes the correct hand setting for you:

```bash
./flash.sh left                           # compile only (a quick check)
./flash.sh left  /dev/cu.usbserial-AAAA   # flash the left board, then open its serial log
./flash.sh right /dev/cu.usbserial-BBBB   # flash the right board
```

The serial monitor opens after flashing; press Ctrl-C to leave it. Build output goes to
`esp32/build/` (gitignored). To use a specific arduino-cli binary, set `ARDUINO_CLI=/path/to/arduino-cli`.

With Arduino IDE 2:
1. Open `esp32/tactile_hand/tactile_hand.ino`.
2. Tools → Board → **ESP32 Dev Module**, then pick the port.
3. In `config.h`, set `TACTILE_HAND_RIGHT` to `0` (left board) or `1` (right board).
4. Upload. Open Serial Monitor at **115200** baud.
5. Set `TACTILE_HAND_RIGHT` back to `0` so the change isn't committed.

If the upload stops at `Connecting....`, hold the **BOOT** button on the board until
writing starts.

The sketch folder contains `tactile_protocol.h`, which is a symlink to
`../../protocol/tactile_protocol.h`. On Windows, enable symlinks in git
(`git config core.symlinks true`, then re-checkout) or copy the file in its place.

## 4. First run

1. Start the Pi hotspot (section 2).
2. Power both hands. The status LED **fast-blinks** until the board joins Wi-Fi, then
   **slow-blinks**.
3. On the Pi, run `./scripts/pi_hotspot.sh status`. Both `10.42.0.2` and `10.42.0.3` should be `up`.
4. On the Pi, run `./build/tactile_send --demo`. The LEDs go **solid**, and the servos sweep
   in turn: front (both) → left → right, with a rest in between, 1 s each.
5. Watch a hand's serial log. The 5 s report should show `0 lost` or close to it,
   a max gap well under 500 ms, and RSSI better than about −70 dBm.

To control it by hand: run `./build/tactile_send`, then type `f`, `l`, `r`, `n` (rest), `s` (stats) or `q`.
`--rate HZ` accepts finite values with `2 < HZ <= 1000`; slower rates would reach
the 500 ms receiver failsafe between packets. Keep 20 Hz for normal use.

### Testing without the ESP32s

| Where | Commands |
|-------|----------|
| One machine (loopback) | `build/tactile_listen --port 4210` and `build/tactile_listen --port 4211`, then `build/tactile_send --left 127.0.0.1:4210 --right 127.0.0.1:4211 --demo` |
| Over the real hotspot | Join a laptop to `TactileNet` with a **manual** IP of `10.42.0.2`, mask `255.255.255.0`, router `10.42.0.1` (turn the real left hand off first). Run `tactile_listen` on the laptop and `tactile_send --demo` on the Pi |

## 5. Tuning the servo

Everything is in [esp32/tactile_hand/config.h](esp32/tactile_hand/config.h). Re-flash after each change.

| Problem | Change |
|---------|--------|
| The sweep is too big or too small | `TACTILE_SWEEP_DEG` (default 60 = 30°–150°) |
| The sweep is too fast or too slow | `TACTILE_SWEEP_PERIOD_MS` (default 1000) |
| The arm buzzes or strains at one end | Move `TACTILE_PULSE_0_US` / `TACTILE_PULSE_180_US` toward 1500 |
| The rest position isn't centred on the hand | `TACTILE_REST_DEG` |
| The arm droops at rest | `TACTILE_RELEASE_AFTER_MS 0` (keeps holding, but may hum) |
| The servo is wired to a different pin | `TACTILE_SERVO_PIN` |
