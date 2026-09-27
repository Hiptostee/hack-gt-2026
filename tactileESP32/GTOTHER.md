# Pi 5 → GTother → ESP32 hands

The Pi and both hands join GTother as clients. The Pi does not create a hotspot.
The existing three-byte UDP packet, port **4210**, 20 Hz sender and 500 ms
receiver failsafe stay the same. The ESP32 firmware already uses station mode
and DHCP; it does not need the Pi's IP address.

## 1. Configure and flash the hands

Follow [Georgia Tech's device onboarding](https://getonline.gatech.edu/affiliated)
for device registration and the current GTother Wi-Fi key. Each hand prints its
own Wi-Fi MAC at boot at 115200 baud, even before it connects. Register each
board as required; one board's registration does not identify the other.

From the repository root:

```bash
cd tactileESP32/esp32
cp -n tactile_hand/secrets.example.h tactile_hand/secrets.h
```

Edit the gitignored `secrets.h` (if it already exists, update it):

```cpp
#pragma once
#define TACTILE_AP_SSID "GTother"
#define TACTILE_AP_PSK "replace-with-GTother-wifi-key"
```

Use the Wi-Fi key supplied by GT. `TACTILE_AP_*` are legacy variable names;
the boards still join as clients. The ESP32-WROOM-32 requires 2.4 GHz coverage.

```bash
pio run -e left -t upload -t monitor --upload-port /dev/cu.usbserial-AAAA
pio run -e right -t upload -t monitor --upload-port /dev/cu.usbserial-BBBB
```

Replace ports with those from `pio device list`. Exit each monitor with Ctrl-C.
Record the IPv4 address from each board's `wifi up:` message. These are the
**destination addresses**, not the Pi's address. DHCP addresses can change after
reconnection. No changes to `config.h`, servo behavior or packet decoding are needed.

## 2. Connect the Pi and send directly

If the old hotspot is active, disable it using a local console or Ethernet;
doing this over its Wi-Fi SSH connection disconnects that session:

```bash
sudo nmcli connection modify tactile-ap connection.autoconnect no
sudo nmcli connection down tactile-ap
```

These commands apply only if that saved connection exists. Use
`nmcli connection show` to check its name. Join GTother through NetworkManager:

```bash
sudo nmcli --ask device wifi connect GTother ifname wlan0
```

Enter the Wi-Fi key when prompted and complete GT's onboarding for the Pi.
Do not run `scripts/pi_hotspot.sh up` for this setup.

Build and run on the Pi, from the repository root:

```bash
cmake -S tactileESP32 -B tactileESP32/build
cmake --build tactileESP32/build -j
./tactileESP32/build/tactile_send --left LEFT_IP --right RIGHT_IP
```

Replace `LEFT_IP` and `RIGHT_IP` with the actual numeric IPv4 addresses from the
serial logs. Type `f`, `l`, `r`, `n` (neutral), or `q`. The C++ tool accepts
numeric IPv4 addresses, not `.local` names. Its no-argument defaults remain
legacy hotspot addresses (`10.42.0.2` / `10.42.0.3`), so always pass both targets.
Code using the library directly must set `LinkConfig::targets` to those same
addresses with port 4210 before constructing `TactileLink`.

## 3. Integrated navigation currently sends from the laptop

The repository's integrated path is Pi mapping → laptop guidance → ESP32.
Switching Wi-Fi does not move guidance or the sender onto the Pi. For that path,
run this on the laptop from the repository root:

```bash
python3 scripts/laptop_launch.py --left-hand LEFT_IP --right-hand RIGHT_IP
```

The laptop must be able to reach the hands over the campus network. Run only
one sender at a time: the Pi test sender and laptop guidance must not send
concurrently, because the hands share a sequence counter per receiving stream.

## Verify the campus link

Being connected to the same SSID does not prove that device-to-device UDP is
permitted. Check the hands' logs for increasing `ok` counts and `state ->`
messages while sending. A successful send on the Pi only means its operating
system accepted the datagram; it is not a delivery acknowledgment.

- Fast-blinking LED: check credentials, registration and 2.4 GHz connectivity.
- Slow-blinking LED with `wifi up:`: check current destination IPs and whether
  campus routing/firewall rules permit sender-to-hand UDP on port 4210.
- Solid LED: valid packets are arriving. Verify `f` moves both hands, `l` only
  left, `r` only right, and `n` returns both to neutral.

Use explicit IPs if campus mDNS discovery is unavailable. Explicit IPs do not
fix blocked UDP; if the network blocks the path, ask GT networking to provide
an allowed device-to-device connection. This repository cannot change campus
network policy. Actual GTother connectivity must be verified on the hardware.
