"""Send the shared direction packets to one tactile hand over USB serial.

Run from the repository root:
    python3 -m tactileESP32.tools.tactile_serial_test --port /dev/cu.usbserial-10
"""

import argparse
import time

import serial

from companion.voice.tactile_link import encode_packet


PERIOD_S = 0.05
STEPS = (
    ("neutral", 0, 0.5),
    ("front", 1, 2.0),
    ("neutral", 0, 0.5),
    ("left", 2, 1.0),
    ("neutral", 0, 0.5),
    ("right", 4, 2.0),
    ("neutral", 0, 1.0),
)


def run(port):
    seq = 0
    with serial.Serial(port, 115200, timeout=0, write_timeout=1) as link:
        # Opening the USB UART resets many ESP32 DevKits. Let setup finish.
        time.sleep(2)
        print(link.read_all().decode(errors="replace"), end="", flush=True)
        try:
            for name, flags, seconds in STEPS:
                print(f"Sending {name} for {seconds:g} s", flush=True)
                for _ in range(round(seconds / PERIOD_S)):
                    link.write(encode_packet(seq, flags))
                    seq = (seq + 1) & 0xFF
                    time.sleep(PERIOD_S)
                print(link.read_all().decode(errors="replace"), end="", flush=True)
        finally:
            # Rest immediately even if the test is interrupted.
            for _ in range(3):
                link.write(encode_packet(seq, 0))
                seq = (seq + 1) & 0xFF
                time.sleep(PERIOD_S)
            link.flush()
            time.sleep(0.2)
            print(link.read_all().decode(errors="replace"), end="", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="ESP32 USB serial port")
    args = parser.parse_args()
    run(args.port)


if __name__ == "__main__":
    main()
