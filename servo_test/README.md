# Automatic standalone servo test

Open `servo_test.ino` in Arduino IDE. Select **ESP32 Dev Module**, the connected
USB serial port, and **Tools > Upload Speed > 115200**. Upload once.

After successful flashing, disconnect USB and power the ESP32 and servo from
appropriate battery supplies. Signal goes to **GPIO 18**; both grounds must be
connected. Battery voltage must suit the board input and servo; do not connect
a battery directly to 3V3 or assume any battery pack is a regulated 5 V source.

Every power-up or reset automatically runs:

1. **Neutral (90°)** for 2 seconds.
2. Slowly move to **left (60°)** and wait 2 seconds.
3. Slowly move to **right (120°)** and wait 2 seconds.
4. Slowly return to **neutral (90°)** and hold it until powered off or reset.

No buttons, Serial commands, Wi-Fi, or computer are required. Serial at 115200
is optional progress output. Left/right depends on how the servo is mounted.
This is a limited-travel test of a 180° servo, not a full endpoint sweep.

The sketch continuously commands neutral at the end, so it can draw holding
current. Test with the horn unloaded; power off if it presses against anything
or stalls. This sketch cannot measure torque. The separate `tactile_hand`
firmware retains its configurable travel limits and idle PWM release.

Angles and pulse endpoints are constants at the top of the sketch. Defaults
are 500–2400 µs (nominal 90° = 1450 µs), matching the existing hand configuration.
Actual mechanical center depends on the servo and horn mounting.

## If upload fails after connecting

The ESP32 can be correctly detected even if the subsequent flash transfer fails.
First lower upload speed from 921600 to 115200. Disconnect the servo and battery
and flash the ESP32 alone over USB. Close other serial terminals. If it still
fails, try a different data cable and a direct USB connection without a hub.
Reconnect the servo/battery after a successful upload.

See [Espressif troubleshooting](https://docs.espressif.com/projects/esptool/en/latest/esp32/troubleshooting.html).
