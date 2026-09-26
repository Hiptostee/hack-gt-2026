"""Laptop-side UDP sender for the two ESP32 tactile hands."""

import argparse
import json
import socket
import threading
import time
from urllib.request import urlopen


MAGIC = 0xA5
PORT = 4210
PERIOD_S = 0.05
STATE_TIMEOUT_S = 0.4
DIRECTION_FLAGS = {0: 0x01, 1: 0x02, 2: 0x04, 3: 0x02, 4: 0x04}


def flags_for_state(state):
    if not isinstance(state, dict) or state.get("active") is not True or state.get("path_valid") is not True:
        return 0
    direction = state.get("direction")
    return DIRECTION_FLAGS.get(direction, 0) if type(direction) is int else 0


def encode_packet(seq, flags):
    if not 0 <= seq <= 255 or flags not in (0, 1, 2, 4):
        raise ValueError("Invalid tactile sequence or direction")
    return bytes((MAGIC, seq, flags))


class GuidanceFeed:
    def __init__(self, bridge_url):
        self.url = bridge_url.rstrip("/") + "/guidance/state"
        self.lock = threading.Lock()
        self.state = None
        self.updated_at = 0.0
        self.stop = threading.Event()

    def poll(self):
        while not self.stop.is_set():
            try:
                with urlopen(self.url, timeout=0.3) as response:
                    state = json.load(response)
                with self.lock:
                    self.state = state
                    self.updated_at = time.monotonic()
            except (OSError, ValueError, TypeError):
                with self.lock:
                    self.state = None
            self.stop.wait(0.1)

    def flags(self):
        with self.lock:
            if time.monotonic() - self.updated_at > STATE_TIMEOUT_S:
                return 0
            return flags_for_state(self.state)


def run(bridge_url, left, right):
    feed = GuidanceFeed(bridge_url)
    poller = threading.Thread(target=feed.poll, daemon=True)
    poller.start()
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    targets = [("left", left), ("right", right)]
    resolved = {}
    retry_at = 0.0
    seq = 0
    next_send = time.monotonic()
    last_flags = None
    try:
        while True:
            now = time.monotonic()
            if now >= retry_at:
                for name, host in targets:
                    try:
                        address = (socket.gethostbyname(host), PORT)
                        if resolved.get(name) != address:
                            resolved[name] = address
                            print(f"{name} hand: {host} -> {resolved[name][0]}:{PORT}", flush=True)
                    except OSError:
                        resolved.pop(name, None)
                        print(f"Waiting to resolve {name} hand ({host})", flush=True)
                retry_at = now + 5

            flags = feed.flags() if len(resolved) == 2 else 0
            if flags != last_flags:
                names = {0: "neutral", 1: "front", 2: "left", 4: "right"}
                print(f"Tactile direction: {names[flags]}", flush=True)
                last_flags = flags
            packet = encode_packet(seq, flags)
            for target in resolved.values():
                try:
                    sender.sendto(packet, target)
                except OSError:
                    pass
            seq = (seq + 1) & 0xFF
            next_send += PERIOD_S
            time.sleep(max(0, next_send - time.monotonic()))
            if next_send < time.monotonic() - PERIOD_S:
                next_send = time.monotonic()
    except KeyboardInterrupt:
        pass
    finally:
        feed.stop.set()
        poller.join(timeout=1)
        for _ in range(3):
            packet = encode_packet(seq, 0)
            for target in resolved.values():
                try:
                    sender.sendto(packet, target)
                except OSError:
                    pass
            seq = (seq + 1) & 0xFF
            time.sleep(PERIOD_S)
        sender.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pi-url", default="http://127.0.0.1:8081")
    parser.add_argument("--left", default="tactile-left.local")
    parser.add_argument("--right", default="tactile-right.local")
    args = parser.parse_args()
    run(args.pi_url, args.left, args.right)


if __name__ == "__main__":
    main()
