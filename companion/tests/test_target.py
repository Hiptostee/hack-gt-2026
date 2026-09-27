"""Named-object guidance without ROS or a planner (companion/navigate/specs.md)."""
import importlib
import json
import re
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from companion.voice import target
from companion.voice.audio import FAULT, INFO

# Copied from backpack_path_planner_node.cpp. If these drift, the planner
# silently ignores every voice target.
PLANNER_BOX = re.compile(r'"confidence":([0-9eE+.-]+),"x":([0-9-]+),"y":([0-9-]+),'
                         r'"width":([0-9-]+),"height":([0-9-]+)')
PLANNER_STAMP = re.compile(r'"stamp":([0-9]+)\.([0-9]{9})')
PLANNER_REQUEST = re.compile(r'"request_id":"([A-Za-z0-9_-]{1,40})"')
PLANNER_LABEL = re.compile(r'"label":"([^"\\]{0,40})"')


class Conversion(unittest.TestCase):
    def test_box_is_descaled_to_the_ros_frame(self):
        self.assertEqual(target.to_pixels([250, 500, 750, 1000], 640, 480),
                         (320, 120, 320, 240))

    def test_degenerate_box_keeps_one_pixel(self):
        self.assertEqual(target.to_pixels([500, 500, 501, 501], 640, 480)[2:], (1, 1))

    def test_message_matches_the_planner_parsers(self):
        message = target.detection_message("v12-3", "water fountain", (212, 140, 96, 180),
                                           "1790000000.000000042")
        box = PLANNER_BOX.search(message)
        self.assertEqual(box.groups(), ("1", "212", "140", "96", "180"))
        self.assertEqual(PLANNER_STAMP.search(message).groups(), ("1790000000", "000000042"))
        self.assertEqual(PLANNER_REQUEST.search(message).group(1), "v12-3")
        self.assertEqual(PLANNER_LABEL.search(message).group(1), "water fountain")
        json.loads(message)


class Phrasing(unittest.TestCase):
    def test_distance(self):
        self.assertEqual(target.distance_phrase(0.8), "less than a meter away")
        self.assertEqual(target.distance_phrase(1.1), "about 1 meter away")
        self.assertEqual(target.distance_phrase(3.1), "about 3 meters away")
        self.assertEqual(target.distance_phrase(3.3), "about 3.5 meters away")

    def test_bearing_is_positive_to_the_left(self):
        self.assertEqual(target.bearing_phrase(10), "straight ahead")
        self.assertEqual(target.bearing_phrase(-30), "slightly to your right")
        self.assertEqual(target.bearing_phrase(90), "to your left")
        self.assertEqual(target.bearing_phrase(-150), "behind you")

    def test_only_ok_starts_guidance(self):
        ok = {"state": "ok", "distance_m": 3.1, "bearing_deg": -22.0}
        self.assertEqual(target.reply("water fountain", ok),
                         ("I think I see the water fountain about 3 meters away, "
                          "slightly to your right. Guiding you now.", True))
        for state in ("near", "no_depth", "no_depth_frame", "too_far", "stale",
                      "tracking_lost", "no_map", "no_camera_info", "no_tf", "outside_map",
                      "no_free_cell", "no_path", "invalid"):
            status = {"state": state, "distance_m": 0.5, "bearing_deg": 0.0}
            text, guiding = target.reply("water fountain", status)
            self.assertFalse(guiding, state)
            self.assertTrue(text, state)

    def test_no_reply_says_guidance_did_not_start(self):
        text, guiding = target.reply("water fountain", None)
        self.assertIn("didn't respond", text)
        self.assertFalse(guiding)

    def test_tracking_loss_is_a_fault(self):
        self.assertEqual(target.event("door", "tracking_lost")[1], FAULT)
        self.assertEqual(target.event("door", "arrived")[1], INFO)


class Guidance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Message:
            def __init__(self, data=None):
                self.data = data

        modules = {
            "rclpy": Mock(),
            "rclpy.node": SimpleNamespace(Node=Mock()),
            "rclpy.executors": SimpleNamespace(ExternalShutdownException=RuntimeError,
                                               SingleThreadedExecutor=Mock()),
            "std_msgs": Mock(),
            "std_msgs.msg": SimpleNamespace(Bool=Message, Empty=Message, String=Message, UInt8=Message)}
        with patch.dict(sys.modules, modules):
            sys.modules.pop("companion.voice.guidance", None)
            cls.module = importlib.import_module("companion.voice.guidance")
            sys.modules.pop("companion.voice.guidance", None)
        cls.Message = Message

    def setUp(self):
        self.events = []
        self.guidance = self.module.RosGuidance(
            on_event=lambda text, priority: self.events.append((text, priority)))
        self.guidance._hazard_gate(self.Message(True))
        self.guidance.publisher = Mock()
        self.guidance.detection_publisher = Mock()
        self.guidance.clear_publisher = Mock()

    def planner_replies(self, **status):
        def publish(message):
            request_id = json.loads(message.data)["request_id"]
            self.guidance._status(self.Message(json.dumps(dict(status, request_id=request_id))))
        self.guidance.detection_publisher.publish.side_effect = publish

    def go_to(self):
        return self.guidance.go_to("water fountain", [250, 500, 750, 1000],
                                   "1790000000.000000042", 640, 480)

    def test_ok_status_starts_guidance(self):
        self.planner_replies(state="ok", distance_m=3.1, bearing_deg=-22.0)
        self.assertIn("Guiding you now", self.go_to())
        self.assertTrue(self.guidance.active)
        self.guidance.clear_publisher.publish.assert_not_called()
        sent = self.guidance.detection_publisher.publish.call_args[0][0].data
        self.assertEqual(PLANNER_BOX.search(sent).groups(), ("1", "320", "120", "320", "240"))

    def test_route_is_preview_without_hazard_permission(self):
        self.guidance._hazard_gate(self.Message(False))
        self.planner_replies(state="ok", distance_m=3.1, bearing_deg=0)
        self.assertIn("stationary route preview", self.go_to())
        self.assertFalse(self.guidance.active)
        self.assertEqual(self.guidance.snapshot()["tactile_flags"], 0)

    def test_rotate_directions_and_expired_permission(self):
        self.planner_replies(state="ok", distance_m=3.1, bearing_deg=0)
        self.go_to()
        self.guidance._valid(self.Message(True))
        for direction, flag in ((3,2),(4,4)):
            self.guidance._direction(self.Message(direction))
            self.assertEqual(self.guidance.snapshot()["tactile_flags"],flag)
        self.guidance.hazard_at -= 1
        self.assertEqual(self.guidance.snapshot()["tactile_flags"],0)
        self.guidance._publish()
        self.assertFalse(self.guidance.active)

    def test_stop_cancels_pending_planner_reply(self):
        entered=threading.Event()
        self.guidance.detection_publisher.publish.side_effect=lambda m: entered.set()
        result=[]
        worker=threading.Thread(target=lambda: result.append(self.go_to()))
        worker.start()
        self.assertTrue(entered.wait(1))
        self.guidance.stop()
        worker.join(1)
        self.assertEqual(result,["Guidance stopped."])
        self.assertFalse(self.guidance.active)

    def test_failure_leaves_guidance_off_and_clears_the_target(self):
        self.planner_replies(state="no_depth")
        self.assertIn("can't judge how far", self.go_to())
        self.assertFalse(self.guidance.active)
        self.guidance.clear_publisher.publish.assert_called_once()

    def test_silent_planner_times_out(self):
        with patch.object(self.module, "STATUS_TIMEOUT_S", 0.05):
            self.assertIn("didn't respond", self.go_to())
        self.assertFalse(self.guidance.active)

    def test_status_for_another_request_is_ignored(self):
        self.guidance.detection_publisher.publish.side_effect = lambda _m: self.guidance._status(
            self.Message(json.dumps({"request_id": "someone-else", "state": "ok",
                                     "distance_m": 2, "bearing_deg": 0})))
        with patch.object(self.module, "STATUS_TIMEOUT_S", 0.05):
            self.assertIn("didn't respond", self.go_to())

    def test_arrival_is_announced_once_and_ends_guidance(self):
        self.planner_replies(state="ok", distance_m=3.1, bearing_deg=0.0)
        self.go_to()
        request_id = self.guidance.request_id
        arrived = self.Message(json.dumps({"request_id": request_id, "state": "arrived"}))
        self.guidance._status(arrived)
        self.guidance._status(arrived)
        self.assertEqual(len(self.events), 1)
        self.assertIn("near the water fountain", self.events[0][0])
        self.assertFalse(self.guidance.active)
        self.assertEqual(self.guidance.events_since(0)[0]["text"], self.events[0][0])
        self.assertEqual(self.guidance.events_since(1), [])

    def test_tracking_loss_keeps_guidance_and_is_a_fault(self):
        self.planner_replies(state="ok", distance_m=3.1, bearing_deg=0.0)
        self.go_to()
        self.guidance._status(self.Message(json.dumps(
            {"request_id": self.guidance.request_id, "state": "tracking_lost"})))
        self.assertEqual(self.events[-1][1], FAULT)
        self.assertTrue(self.guidance.active)

    def test_stop_clears_the_planner_target(self):
        self.planner_replies(state="ok", distance_m=3.1, bearing_deg=0.0)
        self.go_to()
        self.guidance.stop()
        self.assertFalse(self.guidance.active)
        self.guidance.clear_publisher.publish.assert_called_once()

    def test_stop_cancels_backpack_handoff_wait(self):
        self.planner_replies(state="ok", distance_m=3.1, bearing_deg=0)
        self.go_to()
        cleared=threading.Event()
        self.guidance.clear_publisher.publish.side_effect=lambda m: cleared.set()
        result=[]
        worker=threading.Thread(target=lambda: result.append(self.guidance.start()))
        worker.start()
        self.assertTrue(cleared.wait(1))
        self.guidance.stop()
        self.guidance._valid(self.Message(True))
        worker.join(1)
        self.assertEqual(result,["Guidance stopped."])
        self.assertFalse(self.guidance.active)

    def test_backpack_start_waits_for_a_path_after_clearing_an_object_target(self):
        self.planner_replies(state="ok", distance_m=3.1, bearing_deg=0.0)
        self.go_to()
        self.guidance._valid(self.Message(True))  # The object route, about to be cleared.
        timer = threading.Timer(0.05, self.guidance._valid, args=(self.Message(True),))
        timer.start()
        self.assertEqual(self.guidance.start(), "Guidance to the backpack started.")
        timer.join()
        self.guidance.clear_publisher.publish.assert_called_once()


if __name__ == "__main__":
    unittest.main()
