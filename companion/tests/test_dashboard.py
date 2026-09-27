"""Tests for the Judge Debug Dashboard telemetry schema and pi_bridge endpoints."""
import io
import json
import time
import unittest
from unittest.mock import MagicMock, patch

from companion.voice.hazard_state import Alert, HazardState
from companion.voice.pi_bridge import Handler, RemotePi, get_debug_state


class FakeCamera:
    def __init__(self, status="Ready", received=100.0):
        self._status = status
        self.received = received
        self.frame = type("Frame", (), {"width": 640, "height": 480})()

    def status(self):
        return self._status

    def capture(self):
        return b"\xff\xd8fakejpeg", self.received


class FakeGuidance:
    def __init__(self, active=True, path_valid=True, direction=0, flags=1):
        self.active = active
        self.path_valid = path_valid
        self.direction = direction
        self.flags = flags

    def snapshot(self):
        dir_names = {0: "forward", 1: "left", 2: "right", 3: "stop"}
        tactile_names = {0: "front", 1: "left", 2: "right", 3: "neutral"}
        return {
            "active": self.active,
            "path_valid": self.path_valid,
            "hazard_permitted": True,
            "direction": self.direction,
            "direction_label": dir_names.get(self.direction),
            "tactile_flags": self.flags,
            "tactile_label": tactile_names.get(self.direction, "neutral"),
            "target_label": "backpack",
            "distance_m": 2.5,
            "bearing_deg": 10.0,
            "age_s": 0.05,
        }


class FakeGuardian:
    def __init__(self):
        self.state = "active"

    def snapshot(self):
        return {
            "state": "active",
            "is_speaking": False,
            "sms_state": "idle",
            "last_observation": "water fountain at 10:45 PM",
            "trail_count": 1,
        }


class TestHazardStateSnapshot(unittest.TestCase):
    def test_empty_snapshot(self):
        hs = HazardState()
        snap = hs.snapshot(10.0)
        self.assertFalse(snap["available"])
        self.assertEqual(snap["severity"], "none")
        self.assertFalse(snap["urgent"])
        self.assertFalse(snap["caution"])
        self.assertIsNone(snap["age_s"])

    def test_live_hazard_snapshot(self):
        hs = HazardState()
        now = 100.0
        with hs.lock:
            hs.received = now - 0.1
            hs.health = {"depth": "ok", "body_pose": "ok", "floor": "unavailable", "labels": "unavailable"}
            hs.alerts = [Alert("urgent:head:center", "Obstacle ahead, stop.", 0, now + 0.4, 2.0)]
        snap = hs.snapshot(now)
        self.assertTrue(snap["available"])
        self.assertEqual(snap["severity"], "urgent")
        self.assertTrue(snap["urgent"])
        self.assertEqual(snap["phrase"], "Obstacle ahead, stop.")
        self.assertAlmostEqual(snap["age_s"], 0.1, places=2)

    def test_stale_hazard_snapshot(self):
        hs = HazardState()
        now = 100.0
        with hs.lock:
            hs.received = now - 1.0  # > 0.5s heartbeat
            hs.health = {"depth": "ok", "body_pose": "ok", "floor": "unavailable", "labels": "unavailable"}
        snap = hs.snapshot(now)
        self.assertFalse(snap["available"])
        self.assertGreater(snap["age_s"], 0.5)


class TestBridgeDebugState(unittest.TestCase):
    def setUp(self):
        self.server = MagicMock()
        self.server.camera = FakeCamera(status="Ready", received=time.monotonic())
        self.server.guidance = FakeGuidance(active=True, path_valid=True, direction=0, flags=1)
        hs = HazardState()
        with hs.lock:
            hs.received = time.monotonic()
            hs.health = {"depth": "ok", "body_pose": "ok", "floor": "unavailable", "labels": "unavailable"}
            hs.alerts = []
        self.server.hazard_state = hs
        self.server.guardian = FakeGuardian()
        self.server.lang = "en"

    def test_get_debug_state_function(self):
        state = get_debug_state(self.server)
        self.assertIn("timestamp", state)
        self.assertEqual(state["device_lang"], "en")
        self.assertEqual(state["camera"]["status"], "Ready")
        self.assertEqual(state["guidance"]["direction_label"], "forward")
        self.assertEqual(state["guidance"]["tactile_flags"], 1)
        self.assertEqual(state["guidance"]["tactile_label"], "front")
        self.assertTrue(state["hazard"]["available"])
        self.assertEqual(state["guardian"]["state"], "active")
        self.assertEqual(state["guardian"]["trail_count"], 1)

    def test_get_debug_state_missing_subsystems(self):
        empty_server = MagicMock(spec=[])
        state = get_debug_state(empty_server)
        self.assertEqual(state["camera"]["status"], "No camera")
        self.assertFalse(state["guidance"]["active"])
        self.assertFalse(state["hazard"]["available"])
        self.assertEqual(state["guardian"]["state"], "closed")

    def test_handler_routes(self):
        handler = MagicMock()
        handler.server = self.server
        sent = []

        def fake_send(status, data, content_type):
            sent.append((status, data, content_type))

        handler._send = fake_send

        # Test /debug/state route
        handler.path = "/debug/state"
        Handler.do_GET(handler)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], 200)
        self.assertIn("guidance", sent[0][1])

        # Test /guidance/state route
        sent.clear()
        handler.path = "/guidance/state"
        Handler.do_GET(handler)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], 200)
        self.assertTrue(sent[0][1]["active"])

    def test_remote_pi_client_methods(self):
        client = RemotePi("http://127.0.0.1:8081")
        fake_state = {"camera": {"status": "Ready"}, "guidance": {"active": True}}

        with patch.object(client, "_request", return_value=(json.dumps(fake_state).encode(), {})):
            debug = client.debug_state()
            self.assertEqual(debug["camera"]["status"], "Ready")
            guidance = client.guidance_state()
            self.assertEqual(guidance["camera"]["status"], "Ready")


class TestWebTestLocalDebugState(unittest.TestCase):
    def test_web_test_handler_local_debug_state(self):
        from companion.voice.web_test import Handler as WebHandler

        fake_server = MagicMock()
        fake_server.ros_camera = None
        fake_server.fallback_image = None
        fake_server.guidance = FakeGuidance(active=False)
        fake_server.hazard_state = None
        fake_server.guardian = None
        fake_server.demo = None
        fake_server.simulation = {}

        handler = MagicMock()
        handler.server = fake_server
        state = WebHandler._local_debug_state(handler)

        self.assertIn("timestamp", state)
        self.assertEqual(state["camera"]["status"], "Webcam")
        self.assertFalse(state["guidance"]["active"])
        self.assertFalse(state["hazard"]["available"])
        self.assertEqual(state["guardian"]["state"], "idle")

        fake_server.demo = MagicMock()
        fake_server.demo.snapshot.return_value = {"guardian": {"state": "active"}}
        state = WebHandler._local_debug_state(handler)
        self.assertEqual(state["guardian"]["state"], "active")


class TestSimulationAndLanguage(unittest.TestCase):
    def test_apply_simulation_hazard_and_direction(self):
        from companion.voice.pi_bridge import apply_simulation

        server = MagicMock()
        server.simulation = {}
        server.lang = "en"

        # Inject urgent head obstacle
        res = apply_simulation(server, "hazard", "urgent_head")
        self.assertEqual(res["status"], "ok")
        self.assertTrue(server.simulation["hazard"]["urgent"])
        self.assertEqual(server.simulation["hazard"]["severity"], "urgent")

        # Inject direction
        apply_simulation(server, "direction", "left")
        self.assertEqual(server.simulation["guidance"]["direction"], 1)
        self.assertEqual(server.simulation["guidance"]["tactile_flags"], 2)
        self.assertEqual(server.simulation["guidance"]["tactile_label"], "left")

        # Inject fault
        apply_simulation(server, "fault", "heartbeat_drop")
        self.assertFalse(server.simulation["hazard"]["available"])
        self.assertEqual(server.simulation["hazard"]["sensor_health"], "fault")

        # Reset
        res_reset = apply_simulation(server, "reset", "")
        self.assertFalse(res_reset["active"])
        self.assertEqual(len(server.simulation), 0)

    def test_debug_state_includes_vitals_and_simulation(self):
        server = MagicMock()
        server.camera = FakeCamera(status="Ready")
        server.guidance = FakeGuidance(active=True)
        server.hazard_state = None
        server.guardian = None
        server.lang = "ko"
        server.simulation = {}

        state = get_debug_state(server)
        self.assertEqual(state["device_lang"], "ko")
        self.assertIn("vitals", state)
        self.assertIsNone(state["vitals"]["depth_fps"])
        self.assertIsNone(state["vitals"]["safety_budget_pass"])

    def test_handler_simulation_and_language_post(self):
        from companion.voice.pi_bridge import Handler as PiHandler

        server = MagicMock()
        server.simulation = {}
        server.lang = "en"

        handler = MagicMock()
        handler.server = server
        handler.headers = {"Content-Length": "38"}
        handler.rfile = io.BytesIO(b'{"action": "hazard", "value": "clear"}')

        sent = []
        handler._send = lambda status, data, ct: sent.append((status, data, ct))

        handler.path = "/debug/simulate"
        PiHandler.do_POST(handler)
        self.assertEqual(sent[0][0], 200)
        self.assertTrue(sent[0][1]["active"])

        # Test /debug/language
        sent.clear()
        handler.headers = {"Content-Length": "15"}
        handler.rfile = io.BytesIO(b'{"lang": "es"}')
        handler.path = "/debug/language"
        PiHandler.do_POST(handler)
        self.assertEqual(sent[0][0], 200)
        self.assertEqual(sent[0][1]["device_lang"], "es")


if __name__ == "__main__":
    unittest.main()
