# Build and setup

This file covers everything needed to go from a fresh checkout to servos moving:
the Pi 5 sender, the hotspot, and the two ESP32 hands. What the system does and why is in
[SPEC.md](SPEC.md).

| Part | Where it runs | Toolchain |
|------|---------------|-----------|
| Pi sender, tools, tests | Raspberry Pi 5, Ubuntu 24.04 (also builds on macOS/Linux for development) | CMake ≥ 3.20, C++17 compiler |
| Hotspot | Pi 5 | NetworkManager (`nmcli`) |
| Hand firmware | 2 × ESP32-WROOM-32 DevKit | Arduino ESP32 core 3.x (PlatformIO, Arduino IDE 2 or arduino-cli) |

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
- To see or change the password, and to check that the link is healthy, see
  [section 6](#6-wi-fi-password-and-monitoring).

If `up` fails:

| Symptom | Fix |
|---------|-----|
| `Wi-Fi is disabled` / blocked | `sudo rfkill unblock wifi && nmcli radio wifi on` |
| AP starts but no channels / fails to activate | Set the Wi-Fi country: `sudo iw reg set US`. To make it permanent, add `cfg80211.ieee80211_regdom=US` to `/boot/firmware/cmdline.txt` |
| ESP32 or laptop can't join / authentication fails | The script already forces WPA2-AES with PMF off. Check that the password matches `secrets.h` exactly |

## 3. ESP32 hands

### Toolchain (on the laptop that flashes the boards)

**Option 1: PlatformIO (recommended).** Install one of these:

- **PlatformIO Core (CLI):**
  ```bash
  brew install platformio                   # or: pipx install platformio
  pio --version
  ```
  If you installed it through the VS Code extension instead, the CLI is at
  `~/.platformio/penv/bin/pio`. Add that folder to your `PATH`, or use the full path.
- **VS Code:** install the **PlatformIO IDE** extension, then open the `tactileESP32/esp32`
  folder (the one that contains `platformio.ini`).

You don't need to install the ESP32 platform yourself. The first build downloads it
(about 1 GB, a few minutes). [esp32/platformio.ini](esp32/platformio.ini) pins the
[pioarduino](https://github.com/pioarduino/platform-espressif32) platform 55.03.312, which
provides Arduino ESP32 core 3.3.12. PlatformIO's official `espressif32` platform only
ships core 2.x, so don't switch to it.

**Option 2: without PlatformIO.** Use either of these:

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

`secrets.h` is gitignored; never commit it. To look up the password the Pi is using, see
[section 6](#6-wi-fi-password-and-monitoring).

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
pio device list                 # with PlatformIO
arduino-cli board list          # or: ls /dev/cu.*   (macOS)   /  ls /dev/ttyUSB*  (Linux)
```

- The board shows up as something like `/dev/cu.usbserial-0001` or `/dev/cu.wchusbserial*`.
- If nothing appears, first try another USB cable; many are charge-only. If it still
  doesn't appear, install the USB-serial driver for your board's chip: **CP210x** (Silicon Labs)
  or **CH340** (WCH). The chip name is printed next to the USB port.

### Build and flash with PlatformIO

The PlatformIO project is in `esp32/`. It has two environments, `left` and `right`,
which differ only in `TACTILE_HAND_RIGHT`. Leave `config.h` alone; the environment sets it.

```bash
cd tactileESP32/esp32

pio run -e left                              # compile only (a quick check)
pio run -e right

pio run -e left  -t upload -t monitor --upload-port /dev/cu.usbserial-AAAA
pio run -e right -t upload -t monitor --upload-port /dev/cu.usbserial-BBBB
```

- **Always pass `-e`.** Without it, `pio run` uses the default environment, `left`.
- If only one board is plugged in, PlatformIO finds the port itself, so you can leave out
  `--upload-port`.
- `-t monitor` opens the serial log at 115200 baud after flashing, with timestamps and the
  ESP32 crash decoder. Press Ctrl-C to leave it. To reopen it later, run
  `pio device monitor -e left --port /dev/cu.usbserial-AAAA`.
- Build output goes to `esp32/.pio/` (gitignored). `pio run -t clean` removes it.

In VS Code, open `esp32/`, click the PlatformIO icon in the sidebar, then under
**Project Tasks → left** (or **right**) click **Build**, **Upload** or **Monitor**.

### Flash without PlatformIO (arduino-cli or Arduino IDE 2)

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

## 6. Wi-Fi: password and monitoring

### How the pieces fit

```
            Wi-Fi network "TactileNet" (2.4 GHz, WPA2, no internet)
   ┌─────────────────────────────────────────────────────────┐
   │   Pi 5 = access point + sender     10.42.0.1            │
   │      │  UDP, 3-byte packet, 20×/s, to port 4210         │
   │      ├──────────────► left hand ESP32   10.42.0.2       │
   │      └──────────────► right hand ESP32  10.42.0.3       │
   │   laptops (optional, for SSH/testing)  10.42.0.10+      │
   └─────────────────────────────────────────────────────────┘
```

- **The Pi is the router.** `pi_hotspot.sh up` saves a NetworkManager connection called
  `tactile-ap` that turns `wlan0` into an access point. It has `autoconnect yes`, so it comes
  back on every boot. Run `up` once, not every session.
- **The hands have fixed addresses.** They don't ask the Pi for an address. They set
  10.42.0.2 or 10.42.0.3 themselves, chosen by the `left`/`right` build. The Pi hands out
  addresses from .10 upward to laptops, so the two never clash.
- **The Pi repeats the current direction every 50 ms.** It doesn't send one message per
  change, so a lost packet is covered by the next one. A hand that hears nothing for
  500 ms puts its servo back to rest.
- **The ESP32 reconnects by itself** if Wi-Fi drops. It starts over completely if it's
  still down after 10 s. You don't need to reboot it.

### The password

There's no default. You choose it when you start the hotspot, and the same value must
be compiled into both hands.

| | Pi (hotspot) | ESP32 (both hands) |
|---|---|---|
| **Where it lives** | Saved by NetworkManager in `/etc/NetworkManager/system-connections/tactile-ap.nmconnection` (root only) | `esp32/tactile_hand/secrets.h`, compiled into the firmware |
| **Set it** | `export TACTILE_AP_PSK='…'` then `sudo -E ./scripts/pi_hotspot.sh up` | `cp secrets.example.h secrets.h`, then edit `TACTILE_AP_PSK` |
| **See it** | `sudo nmcli -s -g 802-11-wireless-security.psk connection show tactile-ap` | open `secrets.h` |
| **Change it** | Re-run `up` with the new value | Edit `secrets.h` and **re-flash both hands** |

- **The password must be 8–63 characters** and match exactly. It's case-sensitive, and a
  trailing space counts. A mismatch shows up as a fast-blinking LED that never stops.
- **`sudo` needs `-E`.** Without it, `sudo` drops `TACTILE_AP_PSK` and the script stops with
  "set TACTILE_AP_PSK".
- **`nmcli device wifi show-password`** prints the password and a QR code for joining from
  a phone or laptop. The hotspot must be running.
- **`secrets.h` is gitignored.** Each person who flashes a board needs their own copy.
  Share the password directly, never through the repo.
- **`export` puts the password in your shell history.** To avoid that, use
  `read -rs TACTILE_AP_PSK && export TACTILE_AP_PSK`, which prompts without echoing.
- **The network name** works the same way: `TACTILE_AP_SSID` on the Pi (default
  `TactileNet`) and in `secrets.h`. The **channel** only needs changing on the Pi, because
  the hands find it automatically.

Before you run `up`:
- **Do all installs and builds first.** While the hotspot is up, the Pi has no internet
  over Wi-Fi.
- **If you're SSHed in over Wi-Fi, `up` cuts off your session.** Join `TactileNet` and
  run `ssh <user>@10.42.0.1`.
- **To get the Pi's normal Wi-Fi back,** run `sudo ./scripts/pi_hotspot.sh down`. It returns
  on the next boot unless you also run `sudo nmcli connection delete tactile-ap`.

### Monitoring

**On the Pi**, `./scripts/pi_hotspot.sh status` shows whether the hotspot is active and
pings both hands.

**The status LED on each hand** shows which layer is failing:

| LED | Meaning | Check |
|-----|---------|-------|
| Fast blink | Not on Wi-Fi | Hotspot up? Password and SSID match `secrets.h`? Board in range? |
| Slow blink | On Wi-Fi, no packets | Is `tactile_send` running? Are `--left`/`--right` pointing at the right addresses? |
| Solid | Receiving packets | Working |

**The serial log** (`pio device monitor -e left --port …` at 115200 baud) prints
`wifi up: 10.42.0.2, channel 6, rssi -48 dBm` on connect, then a report every 5 s.
`tactile_listen` prints the same report, without RSSI:

```
5 s: 100 ok, 0 lost (0.0%), 0 stale, 0 malformed, max gap 62 ms, rssi -48 dBm
```

| Field | Healthy | What it means |
|-------|---------|---------------|
| `ok` | ≈ 100 | Packets accepted. 20/s × 5 s = 100 |
| `lost` | 0 to a few % | Gaps in the sequence number. Small losses are harmless, because every packet repeats the state |
| `max gap` | well under 500 ms | Longest silence between packets. **This is the number that matters.** Near 500 ms, the failsafe starts firing and the servo stutters |
| `rssi` | −30 to −60 dBm | Signal strength. Around −70 is marginal; −80 or worse causes dropouts |
| `stale` / `malformed` | 0 | Late or out-of-order packets, or packets that aren't ours on port 4210 |

A `FAILSAFE: no packet for … ms -> rest` line means the hand went 500 ms without a packet.

### Wi-Fi troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Fast blink forever | Password or SSID mismatch, the hotspot isn't up, or the board is out of range |
| Slow blink, but `status` shows the hand up | Sender not running, or pointed at the wrong address |
| Large `max gap` and RSSI below −75 | Too far away or a crowded channel. Run `down`, `scan`, then `up` on a quieter channel (1, 6 or 11) |
| Both hands drop out together | Channel congestion. Rescan and switch channel |
| One hand resets or drops while the servo moves | Brownout. The servo is drawing power from the ESP32 (see Wiring) |
| A laptop standing in as a hand breaks the real one | Both have the same IP. Turn the real hand off first |
