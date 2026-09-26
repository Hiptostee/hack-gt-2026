"""ROS bridge between spoken navigation intent and the planner/direction nodes."""
import itertools
import json
import os
import threading
import time

import rclpy
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Bool, Empty, String

from companion.voice import target as target_text

PATH_VALID_TIMEOUT_S = 1.5
STATUS_TIMEOUT_S = 2.0
KEPT_EVENTS = 20


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
        self.on_event = on_event
        self.requests = itertools.count(1)
        self.request_id = None
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
        except ValueError:
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

    def _publish(self):
        with self.lock:
            active = self.active
        self.publisher.publish(Bool(data=active))

    def _clear_target(self):
        """Hands the planner back to the backpack detector. Caller holds the lock."""
        had_target = self.request_id is not None
        self.request_id = None
        self.label = None
        self.clear_publisher.publish(Empty())
        return had_target

    def start(self):
        with self.lock:
            cleared_at = time.monotonic()
            if self._clear_target():
                # The last path_valid may belong to the object route just cleared.
                self.lock.wait_for(lambda: self.valid_at > cleared_at and self.path_valid,
                                   PATH_VALID_TIMEOUT_S)
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
            self.active = False
            self.request_id = request_id
            self.label = label
            self.status = None
            self.detection_publisher.publish(String(data=message))
            self.lock.wait_for(lambda: self.status is not None, STATUS_TIMEOUT_S)
            text, guiding = target_text.reply(label, self.status)
            if guiding:
                self.active = True
            else:
                self._clear_target()
        self._publish()
        return text

    def events_since(self, n):
        with self.lock:
            return [event for event in self.events if event["n"] > n]

    def stop(self):
        with self.lock:
            self.active = False
            self._clear_target()
        self._publish()

    def close(self):
        self.stop()
        self.executor.shutdown()
        self.thread.join(timeout=2)
        self.executor.remove_node(self.node)
        self.node.destroy_node()
