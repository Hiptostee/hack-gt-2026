"""Laptop browser requests use the Pi camera and activate ROS guidance."""
import base64
import json
import threading
import unittest
from http.server import HTTPServer
from urllib.request import Request, urlopen
from unittest import mock

from companion.voice.web_test import Handler
from companion.voice.pi_bridge import Handler as PiHandler, RemotePi


class Camera:
    def status(self):
        return "Ready"

    def capture(self):
        return b"pi-camera-jpeg", 0

    def capture_frame(self):
        return {"jpeg": b"pi-camera-jpeg", "captured_at": 0,
                "stamp": "1790000000.000000042", "width": 640, "height": 480}


class Gemini:
    model = "test"
    key = "test"

    def __init__(self):
        self.image = None
        self.action = "navigate_backpack"
        self.box = None

    def ask(self, _audio, image, _history, stream=False, on_answer_chunk=None):
        self.image = image
        return {"transcript": "bring me to the backpack", "answer": "Okay",
                "landmark": "", "device_action": self.action,
                "target": "water fountain" if self.box else "", "box_2d": self.box}


class WebRosVoice(unittest.TestCase):
    def test_laptop_client_uses_pi_bridge_for_frame_and_guidance(self):
        pi = HTTPServer(("127.0.0.1", 0), PiHandler)
        pi.camera = Camera()
        pi.guidance = mock.Mock()
        pi.guidance.start.return_value = "Guidance started."
        thread = threading.Thread(target=pi.serve_forever, daemon=True)
        thread.start()
        try:
            remote = RemotePi(f"http://127.0.0.1:{pi.server_port}")
            self.assertEqual(remote.status(), "Ready")
            self.assertEqual(remote.capture()[0], b"pi-camera-jpeg")
            self.assertEqual(remote.start(), "Guidance started.")
            remote.stop()
            pi.guidance.start.assert_called_once()
            pi.guidance.stop.assert_called_once()
        finally:
            pi.shutdown()
            pi.server_close()
            thread.join(timeout=2)

    def test_remote_pi_carries_frame_metadata_and_target_requests(self):
        pi = HTTPServer(("127.0.0.1", 0), PiHandler)
        pi.camera = Camera()
        pi.guidance = mock.Mock()
        pi.guidance.go_to.return_value = "Guiding you now."
        pi.guidance.events_since.return_value = [{"n": 3, "text": "Arrived.", "priority": 4}]
        thread = threading.Thread(target=pi.serve_forever, daemon=True)
        thread.start()
        try:
            remote = RemotePi(f"http://127.0.0.1:{pi.server_port}")
            jpeg, _ = remote.capture()
            frame = remote.frame_info(jpeg)
            self.assertEqual(frame["stamp"], "1790000000.000000042")
            self.assertEqual((frame["width"], frame["height"]), (640, 480))
            answer = remote.go_to("water fountain", [100, 200, 300, 400],
                                  frame["stamp"], frame["width"], frame["height"])
            self.assertEqual(answer, "Guiding you now.")
            pi.guidance.go_to.assert_called_once_with(
                "water fountain", [100, 200, 300, 400], "1790000000.000000042", 640, 480)
            self.assertEqual(remote.events_since(2)[0]["text"], "Arrived.")
            pi.guidance.events_since.assert_called_once_with(2)
        finally:
            pi.shutdown()
            pi.server_close()
            thread.join(timeout=2)

    def test_bridge_rejects_an_invalid_box(self):
        pi = HTTPServer(("127.0.0.1", 0), PiHandler)
        pi.camera = Camera()
        pi.guidance = mock.Mock()
        thread = threading.Thread(target=pi.serve_forever, daemon=True)
        thread.start()
        try:
            remote = RemotePi(f"http://127.0.0.1:{pi.server_port}")
            self.assertEqual(remote.go_to("x", [300, 0, 100, 10], "1.0", 640, 480),
                             "Bad target request")
            pi.guidance.go_to.assert_not_called()
        finally:
            pi.shutdown()
            pi.server_close()
            thread.join(timeout=2)

    def serve(self, ros_camera=True):
        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.ros_camera = Camera() if ros_camera else None
        server.guidance = mock.Mock() if ros_camera else None
        server.gemini = Gemini()
        server.el_key = ""
        server.el_voice = ""
        server.el_voice_source = "none"
        server.el_model = ""
        server.fallback_image = None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def ask(self, server):
        payload = {"audio": base64.b64encode(b"wav").decode(),
                   "image": base64.b64encode(b"laptop-image").decode(),
                   "play_host": False}
        request = Request(f"http://127.0.0.1:{server.server_port}/ask",
                          data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            return json.load(response)

    def test_navigate_target_routes_the_pi_frame_box(self):
        server = self.serve()
        server.gemini.action = "navigate_target"
        server.gemini.box = [100, 200, 300, 400]
        server.guidance.go_to.return_value = "I think I see the water fountain."
        result = self.ask(server)
        server.guidance.go_to.assert_called_once_with(
            "water fountain", [100, 200, 300, 400], "1790000000.000000042", 640, 480)
        self.assertEqual(result["answer"], "I think I see the water fountain.")
        self.assertEqual(result["box_2d"], [100, 200, 300, 400])
        self.assertEqual(base64.b64decode(result["target_image"]), b"pi-camera-jpeg")

    def test_navigate_target_without_ros_still_shows_the_box(self):
        server = self.serve(ros_camera=False)
        server.gemini.action = "navigate_target"
        server.gemini.box = [100, 200, 300, 400]
        result = self.ask(server)
        self.assertEqual(result["answer"], "Guidance is unavailable here.")
        self.assertEqual(base64.b64decode(result["target_image"]), b"laptop-image")

    def test_navigate_target_without_a_box_speaks_gemini(self):
        server = self.serve()
        server.gemini.action = "navigate_target"
        result = self.ask(server)
        self.assertEqual(result["answer"], "Okay")
        server.guidance.go_to.assert_not_called()

    def test_pi_frame_and_navigation_action(self):
        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.ros_camera = Camera()
        server.guidance = mock.Mock()
        server.guidance.start.return_value = "Guidance started."
        server.gemini = Gemini()
        server.el_key = ""
        server.el_voice = ""
        server.el_voice_source = "none"
        server.el_model = ""
        server.fallback_image = None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/pi-frame?t=1") as response:
                self.assertEqual(response.read(), b"pi-camera-jpeg")
            payload = {"audio": base64.b64encode(b"wav").decode(),
                       "image": base64.b64encode(b"laptop-image").decode(),
                       "play_host": True}
            request = Request(f"http://127.0.0.1:{server.server_port}/ask",
                              data=json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json"})
            with mock.patch("companion.voice.web_test.play_host_audio") as host_audio:
                with urlopen(request) as response:
                    result = json.load(response)
            self.assertEqual(server.gemini.image, b"pi-camera-jpeg")
            server.guidance.start.assert_called_once()
            self.assertEqual(result["answer"], "Guidance started.")
            host_audio.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
