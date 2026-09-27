// Per-board settings. Every value can also be overridden at build time
// (the PlatformIO left/right environments and flash.sh pass TACTILE_HAND_RIGHT),
// so this file normally stays as is.

#pragma once

// 0 = tactile-left.local, 1 = tactile-right.local. In the Arduino IDE,
// change this before flashing the right-hand board.
#ifndef TACTILE_HAND_RIGHT
#define TACTILE_HAND_RIGHT 0
#endif

// Set to 1 for a USB serial packet test. Wi-Fi is not started in this build.
#ifndef TACTILE_USB_ONLY
#define TACTILE_USB_ONLY 0
#endif

// Servo signal pin. GPIO 18 is PWM-capable and not a boot strapping pin.
#ifndef TACTILE_SERVO_PIN
#define TACTILE_SERVO_PIN 18
#endif

// 180-degree SG90. The arm rests at TACTILE_REST_DEG. While a direction is
// active it sweeps TACTILE_SWEEP_DEG to either side of rest (90 +/- 60 gives
// 30..150 degrees), completing one back-and-forth every TACTILE_SWEEP_PERIOD_MS.
#ifndef TACTILE_REST_DEG
#define TACTILE_REST_DEG 90
#endif
// Mechanical travel limits for the assembled hand. Tune each board separately.
// The sweep automatically shrinks symmetrically around REST to fit these limits.
#ifndef TACTILE_MIN_DEG
#define TACTILE_MIN_DEG 30
#endif
#ifndef TACTILE_MAX_DEG
#define TACTILE_MAX_DEG 150
#endif
#ifndef TACTILE_SWEEP_DEG
#define TACTILE_SWEEP_DEG 60
#endif
#ifndef TACTILE_SWEEP_PERIOD_MS
#define TACTILE_SWEEP_PERIOD_MS 1000
#endif

// Pulse widths for 0 and 180 degrees. SG90s vary; if the arm buzzes against
// its end stop, move these toward 1500.
#ifndef TACTILE_PULSE_0_US
#define TACTILE_PULSE_0_US 500
#endif
#ifndef TACTILE_PULSE_180_US
#define TACTILE_PULSE_180_US 2400
#endif

static_assert(TACTILE_MIN_DEG >= 0 && TACTILE_MAX_DEG <= 180 &&
              TACTILE_MIN_DEG < TACTILE_MAX_DEG, "Invalid servo travel limits");
static_assert(TACTILE_REST_DEG >= TACTILE_MIN_DEG &&
              TACTILE_REST_DEG <= TACTILE_MAX_DEG, "Neutral must be inside travel limits");
static_assert(TACTILE_SWEEP_DEG >= 0 && TACTILE_SWEEP_PERIOD_MS > 0,
              "Invalid servo sweep settings");
static_assert(TACTILE_PULSE_0_US > 0 && TACTILE_PULSE_0_US < TACTILE_PULSE_180_US &&
              TACTILE_PULSE_180_US < 20000, "Invalid servo pulse endpoints");

// After returning to rest, stop the pulses after this long so the idle servo
// does not hum or jitter. 0 keeps holding the rest angle.
#ifndef TACTILE_RELEASE_AFTER_MS
#define TACTILE_RELEASE_AFTER_MS 500
#endif

// Status LED: solid = receiving, slow blink = Wi-Fi up but no packets,
// fast blink = no Wi-Fi. GPIO 2 is the blue LED on ESP32 DevKit boards.
#ifndef TACTILE_LED_PIN
#define TACTILE_LED_PIN 2
#endif
