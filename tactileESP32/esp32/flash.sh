#!/usr/bin/env bash
# Builds, and optionally flashes, one hand with arduino-cli and the ESP32
# Arduino core (Boards Manager: "esp32" by Espressif).
#
#   ./flash.sh left                              compile only
#   ./flash.sh left  /dev/cu.usbserial-0001      compile, flash, open serial monitor
#   ./flash.sh right /dev/cu.usbserial-0002
#
# ARDUINO_CLI overrides the arduino-cli binary. In the Arduino IDE instead:
# open tactile_hand/, set TACTILE_HAND_RIGHT in config.h, board "ESP32 Dev Module".

set -euo pipefail

HAND="${1:-}"
PORT="${2:-}"
case "$HAND" in
  left) RIGHT=0 ;;
  right) RIGHT=1 ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac

CLI="${ARDUINO_CLI:-arduino-cli}"
DIR="$(cd "$(dirname "$0")" && pwd)"
FQBN="esp32:esp32:esp32"
BUILD="$DIR/build/$HAND"

if [ ! -f "$DIR/tactile_hand/secrets.h" ]; then
  echo "missing tactile_hand/secrets.h: copy secrets.example.h and set the hotspot password" >&2
  exit 1
fi

"$CLI" compile --fqbn "$FQBN" --build-path "$BUILD" \
  --build-property "compiler.cpp.extra_flags=-DTACTILE_HAND_RIGHT=$RIGHT" \
  "$DIR/tactile_hand"

if [ -n "$PORT" ]; then
  "$CLI" upload --fqbn "$FQBN" --input-dir "$BUILD" --port "$PORT"
  "$CLI" monitor --port "$PORT" --config baudrate=115200
fi
