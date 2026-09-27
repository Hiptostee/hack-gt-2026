"""ROS bridge between spoken navigation intent and the planner/direction nodes."""
import itertools
import json
import os
import threading
import time

import rclpy
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Bool, Empty, String, UInt8

from companion.voice import target as target_text

PATH_VALID_TIMEOUT_S = 1.5
STATUS_TIMEOUT_S = 2.0
KEPT_EVENTS = 20
DIRECTION_TIMEOUT_S = 0.6
DIRECTION_NAMES = {0: "forward", 1: "left", 2: "right", 3: "rotate left", 4: "rotate right"}
TACTILE_FLAGS = {0: 1, 1: 2, 2: 4, 3: 2, 4: 4}
TACTILE_NAMES = {0: "front", 1: "left", 2: "right", 3: "left", 4: "right"}


class RosGuidance:
    def __init__(self, on_event=None):
        # RosCamera has initialized rclpy before this bridge is constructed.
        self.node = Node("voice_backpack_guidance")
        self.publisher = self.node.create_publisher(Bool, "/backpack/guidance_active", 10)
        self.detection_publisher = self.node.create_publisher(String, "/target/detection", 10)
        self.clear_publisher = self.node.create_publisher(Empty, "/target/clear", 10)
        self.lock = threading.Condition()
        self.path_valid = False
        self.valid_at = 0.0
        self.active = False
        self.hazard_permitted = False
        self.hazard_at = 0.0
        self.direction = None
        self.direction_at = 0.0
        self.node.create_subscription(Bool, "/hazard/guidance_permitted", self._hazard_gate, 1)
        self.node.create_subscription(UInt8, "/backpack/direction", self._direction, 10)
        self.on_event = on_event
        self.requests = itertools.count(1)
        self.request_id = None
        self.intent_generation = 0
        self.label = None
        self.status = None
        self.events = []
        self.event_count = 0
        self.node.create_subscription(Bool, "/backpack/path_valid", self._valid, 10)
        self.node.create_subscription(String, "/target/status", self._status, 10)
        self.timer = self.node.create_timer(0.2, self._publish)
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()

    def _spin(self):
        try:
            self.executor.spin()
        except ExternalShutdownException:
            pass

    def _valid(self, message):
        with self.lock:
            self.path_valid = message.data
            self.valid_at = time.monotonic()
            self.lock.notify_all()

    def _status(self, message):
        try:
            status = json.loads(message.data)
        except (ValueError, TypeError):
            return
        if not isinstance(status, dict):
            return
        with self.lock:
            if self.request_id is None or status.get("request_id") != self.request_id:
                return
            state = status.get("state")
            if state not in target_text.EVENTS:
                self.status = status
                self.lock.notify_all()
                return
            text, priority = target_text.event(self.label, state)
            if state == "tracking_restored" and not self.active:
                text = "Tracking is back. Movement guidance remains inhibited."
            if state in ("arrived", "expired"):
                self.active = False
                self.request_id = None
            self.event_count += 1
            self.events = (self.events + [{"n": self.event_count, "text": text,
                                           "priority": priority}])[-KEPT_EVENTS:]
            on_event = self.on_event
        if state in ("arrived", "expired"):
            self._publish()
        if on_event:
            on_event(text, priority)

    def _hazard_gate(self, message):
        with self.lock:
            self.hazard_permitted = message.data
            self.hazard_at = time.monotonic()

    def _direction(self, message):
        with self.lock:
            self.direction = message.data if message.data in range(5) else None
            self.direction_at = time.monotonic()

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            valid = self.path_valid and (now - self.valid_at <= PATH_VALID_TIMEOUT_S)
            hazard = self.hazard_permitted and (now - self.hazard_at <= 0.5)
            dir_fresh = (now - self.direction_at <= DIRECTION_TIMEOUT_S) if self.direction_at else False
            raw_dir = self.direction if dir_fresh else None
            active_dir = raw_dir if (self.active and valid and hazard) else None

            flags = TACTILE_FLAGS.get(active_dir, 0) if active_dir is not None else 0
            dir_label = DIRECTION_NAMES.get(active_dir)
            tactile_label = TACTILE_NAMES.get(active_dir, "neutral")

            return {
                "active": self.active,
                "path_valid": valid,
                "hazard_permitted": hazard,
                "direction": active_dir,
                "direction_label": dir_label,
                "tactile_flags": flags,
                "tactile_label": tactile_label,
                "target_label": self.label or ("backpack" if self.active else None),
                "distance_m": (self.status or {}).get("distance_m"),
                "bearing_deg": (self.status or {}).get("bearing_deg"),
                "age_s": round(now - self.direction_at, 3) if self.direction_at else None,
            }

    def _publish(self):
        with self.lock:
            if self.active and (not self.hazard_permitted or time.monotonic() - self.hazard_at >= 0.5):
                self.active = False
            active = self.active
        self.publisher.publish(Bool(data=active))

    def _clear_target(self):
        """Hands the planner back to the backpack detector. Caller holds the lock."""
        had_target = self.request_id is not None
        self.request_id = None
        self.label = None
        self.status = None
        self.lock.notify_all()
        self.clear_publisher.publish(Empty())
        return had_target

    def start(self):
        with self.lock:
            if not self.hazard_permitted or time.monotonic() - self.hazard_at >= 0.5:
                return "Guidance is unavailable while hazard sensing or audio is unavailable, or an urgent obstacle is present."
            self.intent_generation += 1
            generation = self.intent_generation
            cleared_at = time.monotonic()
            if self._clear_target():
                # The last path_valid may belong to the object route just cleared.
                self.lock.wait_for(lambda: (self.valid_at > cleared_at and self.path_valid) or self.intent_generation != generation,
                                   PATH_VALID_TIMEOUT_S)
            if generation != self.intent_generation:
                return "Guidance stopped."
            if not self.hazard_permitted or time.monotonic() - self.hazard_at >= 0.5:
                return "Guidance is unavailable while hazard sensing or warning audio is unavailable."
            if not self.path_valid or time.monotonic() - self.valid_at > PATH_VALID_TIMEOUT_S:
                return "I cannot find a current route to the backpack. Please try again."
            self.active = True
        self._publish()
        return "Guidance to the backpack started."

    def go_to(self, label, box_2d, stamp, width, height):
        """Asks the planner to guide to a box in the frame captured at stamp.
        Returns the sentence to speak; guidance starts only if the planner routed."""
        request_id = f"v{os.getpid()}-{next(self.requests)}"
        message = target_text.detection_message(
            request_id, label, target_text.to_pixels(box_2d, width, height), stamp)
        with self.lock:
            self.intent_generation += 1
            self.active = False
            self.request_id = request_id
            self.label = label
            self.status = None
            self.detection_publisher.publish(String(data=message))
            self.lock.wait_for(lambda: self.status is not None or self.request_id != request_id, STATUS_TIMEOUT_S)
            if self.request_id != request_id:
                return "Guidance stopped."
            text, guiding = target_text.reply(label, self.status)
            if guiding:
                permitted = self.hazard_permitted and time.monotonic() - self.hazard_at < 0.5
                self.active = permitted
                if not permitted:
                    text = (f"The planner found a route to the {label}. This is a stationary route preview. "
                            "Movement guidance is inhibited because hazard sensing or warning audio is unavailable.")
            else:
                self._clear_target()
        self._publish()
        return text

    def events_since(self, n):
        with self.lock:
            return [event for event in self.events if event["n"] > n]

    def stop(self):
        with self.lock:
            self.intent_generation += 1
            self.active = False
            self._clear_target()
        self._publish()

    def close(self):
        try:
            if rclpy.ok():
                self.stop()
        except Exception:
            # SIGINT can invalidate the context between ok() and publication.
            # If ROS is still live, preserve real publication failures.
            if rclpy.ok():
                raise
        self.executor.shutdown()
        self.thread.join(timeout=2)
        self.executor.remove_node(self.node)
        self.node.destroy_node()
