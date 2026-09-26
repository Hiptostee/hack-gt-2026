#!/usr/bin/env python3
"""Publish test messages on /hazard_warning. For bench tests only; not a detector.

    python3 scripts/fake_hazard.py                  # one warning
    python3 scripts/fake_hazard.py --every 5        # one every 5 s until Ctrl+C
    python3 scripts/fake_hazard.py --burst 3        # 10 Hz for 3 s (tests the 2 s repeat limit)

Payloads follow the hazard spec §6 JSON shape so a future parser can read them.
The companion currently reacts to any message and ignores the payload.
"""
import argparse
import json
import time
import uuid

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


def payload(sequence, session):
    now = time.time_ns()
    return json.dumps({
        "schema_version": 1,
        "source_session": session,
        "sequence": sequence,
        "published_stamp_ns": now,
        "health": {"depth": "ok", "body_pose": "ok", "floor": "unavailable", "labels": "ok"},
        "events": [{
            "id": "fake-1", "observed_stamp_ns": now, "ttl_ms": 250,
            "kind": "upper_body_obstacle", "severity": "urgent", "direction": "center",
            "height_band": "head", "distance_m": None, "label": None, "evidence": "fake_test",
        }],
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--topic", default="/hazard_warning")
    parser.add_argument("--every", type=float, help="seconds between warnings, until Ctrl+C")
    parser.add_argument("--burst", type=float, help="publish at 10 Hz for this many seconds")
    args = parser.parse_args()

    rclpy.init()
    node = Node("fake_hazard")
    publisher = node.create_publisher(String, args.topic, 10)
    session = f"fake-{uuid.uuid4().hex[:8]}"
    # Give discovery a moment, or the first message goes to no one.
    time.sleep(1.0)
    sequence = 0

    def send():
        nonlocal sequence
        sequence += 1
        publisher.publish(String(data=payload(sequence, session)))
        print(f"published #{sequence} on {args.topic}", flush=True)

    try:
        if args.burst:
            end = time.monotonic() + args.burst
            while time.monotonic() < end:
                send()
                time.sleep(0.1)
        elif args.every:
            while True:
                send()
                time.sleep(args.every)
        else:
            send()
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
