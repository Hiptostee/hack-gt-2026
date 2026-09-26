"""Laptop browser requests use the Pi camera and activate ROS guidance."""
import base64
import json
import threading
import unittest
from http.server import HTTPServer
from urllib.request import Request, urlopen
from unittest import mock

from companion.voice.web_test import Handler


class Camera:
    def status(self):
        return "Ready"

    def capture(self):
        return b"pi-camera-jpeg", 0


class Gemini:
    model = "test"
    key = "test"

    def __init__(self):
        self.image = None
        self.action = "navigate_backpack"

    def ask(self, _audio, image, _history):
        self.image = image
        return {"transcript": "bring me to the backpack", "answer": "Okay",
                "landmark": "", "device_action": self.action}


class WebRosVoice(unittest.TestCase):
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
