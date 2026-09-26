import base64
import io
import json
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from companion.server import AppError, CompanionServer, Gemini, State

JPEG = b"\xff\xd8\xfftest-image"


class FakeGemini:
    key = "test-only"

    def answer(self, snapshot, question, mode):
        return {"answer": "A door is visible on the left.", "landmark": "Room 201"}, question


class StateTests(unittest.TestCase):
    def setUp(self):
        self.state = State(FakeGemini())

    def test_followups_keep_original_image_and_bounded_history(self):
        snap = self.state.add(JPEG, "image/jpeg", "upload")
        self.state.add(JPEG + b"different", "image/jpeg", "upload")
        for _ in range(7):
            result = self.state.ask({"snapshot_id": snap["id"], "question": "Where?"})
            self.assertEqual(result["snapshot_id"], snap["id"])
        self.assertEqual(self.state.snapshots[snap["id"]]["image"], JPEG)
        self.assertEqual(len(self.state.snapshots[snap["id"]]["history"]), 4)

    def test_expired_image_is_not_sent_to_model(self):
        snap = self.state.add(JPEG, "image/jpeg", "upload")
        self.state.snapshots[snap["id"]]["created"] = time.monotonic() - 601
        with self.assertRaises(AppError) as caught:
            self.state.ask({"snapshot_id": snap["id"], "question": "Where?"})
        self.assertEqual(caught.exception.status, 410)
        self.assertNotIn(snap["id"], self.state.snapshots)

    def test_bounded_snapshot_storage(self):
        first = self.state.add(JPEG, "image/jpeg", "upload")
        for _ in range(4):
            self.state.add(JPEG, "image/jpeg", "upload")
        self.assertEqual(len(self.state.snapshots), 4)
        self.assertNotIn(first["id"], self.state.snapshots)

    def test_rejects_invalid_images_and_empty_questions(self):
        with self.assertRaises(AppError):
            self.state.add(b"not-an-image", "image/jpeg", "upload")
        with self.assertRaises(AppError):
            self.state.ask({"question": " "})

    def test_cloud_work_does_not_block_status_and_rejects_duplicate_requests(self):
        self.state.inference.acquire()
        try:
            self.assertTrue(self.state.status()["gemini_configured"])
            with self.assertRaises(AppError) as caught:
                self.state.ask({"question": "Where?"})
            self.assertEqual(caught.exception.status, 429)
        finally:
            self.state.inference.release()

    def test_missing_key_is_explicit(self):
        state = State(Gemini("", "test-model"))
        snap = state.add(JPEG, "image/jpeg", "upload")
        with self.assertRaises(AppError) as caught:
            state.ask({"snapshot_id": snap["id"], "mode": "describe"})
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(state.last_cloud, "Last request failed")


class GeminiTests(unittest.TestCase):
    def setUp(self):
        self.gemini = Gemini("not-a-real-key", "test-model")
        self.snap = {"image": JPEG, "mime": "image/jpeg", "history": []}

    def test_request_contains_image_and_returns_structured_answer(self):
        response = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": json.dumps({"answer": "Room 201", "landmark": "Room 201"})}]}}]}
        with patch("companion.server.urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as call:
            result, _ = self.gemini.answer(self.snap, "", "read")
        request = call.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(result["answer"], "Room 201")
        self.assertEqual(payload["contents"][0]["parts"][0]["inlineData"]["data"], base64.b64encode(JPEG).decode())
        self.assertNotIn("not-a-real-key", request.full_url)
        self.assertNotIn("location", payload)

    def test_blocked_or_truncated_output_is_not_presented_as_answer(self):
        for response in ({"candidates": []}, {"candidates": [{"finishReason": "MAX_TOKENS"}]}):
            with patch("companion.server.urlopen", return_value=io.BytesIO(json.dumps(response).encode())):
                with self.assertRaises(AppError) as caught:
                    self.gemini.answer(self.snap, "", "describe")
                self.assertEqual(caught.exception.status, 502)

    def test_network_failure_and_quota_are_reported_without_secrets(self):
        for error in (URLError("secret-detail"), HTTPError("https://example.test", 429, "quota", {}, None)):
            with patch("companion.server.urlopen", side_effect=error):
                with self.assertRaises(AppError) as caught:
                    self.gemini.answer(self.snap, "", "describe")
                self.assertNotIn("secret-detail", str(caught.exception))
                self.assertIn(caught.exception.status, (502, 504))


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = State(FakeGemini())
        cls.server = CompanionServer(("127.0.0.1", 0), cls.state, "test-pairing-code")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, data=None, authenticated=True):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer test-pairing-code"
        req = Request(self.base + path, headers=headers,
                      data=None if data is None else json.dumps(data).encode())
        return urlopen(req, timeout=3)

    def test_sensitive_endpoints_require_pairing(self):
        for path, data in (("/api/status", None), ("/api/snapshot", {}), ("/api/ask", {})):
            with self.assertRaises(HTTPError) as caught:
                self.request(path, data, authenticated=False)
            self.assertEqual(caught.exception.code, 401)

    def test_upload_question_and_delete_end_to_end(self):
        with self.request("/api/upload", {"image": base64.b64encode(JPEG).decode(), "mime": "image/jpeg"}) as response:
            snap = json.load(response)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIsNone(snap["captured_at"])
        with self.request("/api/ask", {"snapshot_id": snap["id"], "question": "Where is the door?"}) as response:
            self.assertIn("door", json.load(response)["answer"])
        with self.request("/api/forget", {"snapshot_id": snap["id"]}) as response:
            self.assertTrue(json.load(response)["deleted"])
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/ask", {"snapshot_id": snap["id"], "question": "Where?"})
        self.assertEqual(caught.exception.code, 410)

    def test_camera_missing_is_not_a_fake_snapshot(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/snapshot", {})
        self.assertEqual(caught.exception.code, 503)

    def test_idle_server_expires_images(self):
        snap = self.state.add(JPEG, "image/jpeg", "upload")
        self.state.snapshots[snap["id"]]["created"] = time.monotonic() - 601
        self.server.service_actions()
        self.assertNotIn(snap["id"], self.state.snapshots)

    def test_malformed_json_object_and_static_path_traversal(self):
        for path, data, status in (("/api/ask", [], 400), ("/../server.py", None, 404)):
            with self.assertRaises(HTTPError) as caught:
                self.request(path, data)
            self.assertEqual(caught.exception.code, status)


if __name__ == "__main__":
    unittest.main()
