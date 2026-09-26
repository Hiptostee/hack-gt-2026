"""State machine tests. No audio device, no network, no Gemini calls."""
import base64
import io
import json
import queue
import unittest
from unittest import mock

from companion.errors import AppError
from companion.voice import gemini as gemini_module
from companion.voice.__main__ import HOLD, SHORT_PRESS, Companion
from companion.voice.session import Session


class FakeAudio:
    capture_rate = 16000

    def __init__(self, peak=5000):
        self.gain = 1.0
        self.earcons = []
        self.recording = False
        self.peak = peak
        self.played = 0

    def earcon(self, name):
        self.earcons.append(name)

    def record_start(self):
        self.recording = True

    def record_stop(self):
        self.recording = False
        return b"RIFFfake", 2.0, self.peak

    def play(self, _samples, _rate):
        self.played += 1
        return True

    def pulsed(self, **_kwargs):
        return []

    def stop(self):
        pass


class FakeSpeech:
    def __init__(self):
        self.said = []
        self.stops = 0
        self.key = ""
        self.voice_id = ""

    def say(self, text, local=False):
        self.said.append((text, local))

    def stop(self):
        self.stops += 1


class FakeGemini:
    key = "test-key"

    def __init__(self, result=None, error=None):
        self.result = result or {"transcript": "what is ahead", "answer": "A door ahead.",
                                 "landmark": "", "device_action": "none"}
        self.error = error
        self.calls = []

    def ask(self, audio, image, history):
        self.calls.append({"audio": audio, "image": image, "history": list(history)})
        if self.error:
            raise self.error
        return self.result


class FakeCamera:
    def __init__(self, image=b"\xff\xd8\xffJPEG", error=None):
        self.image = image
        self.error = error

    def status(self):
        return "Ready"

    def capture(self):
        if self.error:
            raise self.error
        return self.image, None

    def close(self):
        pass


def build(gemini=None, camera=None, audio=None):
    audio = audio or FakeAudio()
    speech = FakeSpeech()
    companion = Companion(audio, speech, gemini or FakeGemini(), camera or FakeCamera(),
                          Session(), button=None, events=queue.Queue())
    return companion, audio, speech


class PressGestures(unittest.TestCase):
    def test_hold_then_release_dispatches_utterance(self):
        companion, audio, _ = build()
        companion._press(0.0)
        self.assertTrue(audio.recording)
        self.assertIn("listening", audio.earcons)
        companion._release(HOLD + 0.5)
        kind, _generation, payload = companion.tasks.get_nowait()
        self.assertEqual(kind, "utterance")
        self.assertEqual(payload, b"RIFFfake")
        self.assertEqual(companion.state, "busy")

    def test_press_under_threshold_is_ignored(self):
        companion, audio, _ = build()
        companion._press(0.0)
        companion._release(SHORT_PRESS / 2)
        self.assertIn("too_brief", audio.earcons)
        self.assertTrue(companion.tasks.empty())
        self.assertEqual(companion.state, "idle")

    def test_short_press_repeats_after_double_window(self):
        companion, _audio, _ = build()
        companion._press(0.0)
        companion._release(SHORT_PRESS + 0.05)
        self.assertTrue(companion.tasks.empty())  # Still waiting for a second press.
        companion.short_timer.cancel()
        companion._repeat_due(0.0)
        kind, _generation, _payload = companion.tasks.get_nowait()
        self.assertEqual(kind, "repeat")

    def test_double_short_press_opens_help(self):
        companion, _audio, _ = build()
        for _ in range(2):
            companion._press(0.0)
            companion._release(SHORT_PRESS + 0.05)
        kind, _generation, _payload = companion.tasks.get_nowait()
        self.assertEqual(kind, "help")
        self.assertIsNone(companion.short_timer)

    def test_silent_capture_is_not_sent_to_gemini(self):
        companion, _audio, _ = build(audio=FakeAudio(peak=10))
        companion._press(0.0)
        companion._release(HOLD + 0.5)
        kind, _generation, payload = companion.tasks.get_nowait()
        self.assertEqual(kind, "say")
        self.assertIn("did not hear", payload)

    def test_press_while_busy_barges_in(self):
        companion, audio, speech = build()
        companion.state = "busy"
        before = companion.generation
        companion._press(0.0)
        self.assertEqual(speech.stops, 1)
        self.assertIn("stopped", audio.earcons)
        self.assertGreater(companion.generation, before)
        self.assertEqual(companion.state, "recording")

    def test_stale_done_event_does_not_clear_new_state(self):
        companion, _audio, _ = build()
        companion.state = "recording"
        companion._done(companion.generation - 1)
        self.assertEqual(companion.state, "recording")


class Utterance(unittest.TestCase):
    def test_answer_is_spoken_and_recorded(self):
        gemini = FakeGemini()
        companion, _audio, speech = build(gemini=gemini)
        companion._utterance(b"wav", companion.generation)
        self.assertEqual(speech.said[0][0], "A door ahead.")
        self.assertEqual(companion.session.last_answer, "A door ahead.")
        self.assertEqual(gemini.calls[0]["image"], b"\xff\xd8\xffJPEG")

    def test_missing_camera_still_asks_without_an_image(self):
        gemini = FakeGemini()
        camera = FakeCamera(error=AppError("No recent camera frame.", 503))
        companion, _audio, speech = build(gemini=gemini, camera=camera)
        companion._utterance(b"wav", companion.generation)
        self.assertIsNone(gemini.calls[0]["image"])
        self.assertEqual(len(speech.said), 1)

    def test_superseded_answer_is_not_spoken(self):
        companion, _audio, speech = build()
        generation = companion.generation
        companion.generation += 1  # The user pressed again while this was in flight.
        companion._utterance(b"wav", generation)
        self.assertEqual(speech.said, [])

    def test_history_accumulates_for_follow_ups(self):
        gemini = FakeGemini()
        companion, _audio, _ = build(gemini=gemini)
        companion._utterance(b"wav", companion.generation)
        companion._utterance(b"wav", companion.generation)
        self.assertEqual(gemini.calls[0]["history"], [])
        self.assertEqual(gemini.calls[1]["history"],
                         [("what is ahead", "A door ahead.")])


class DeviceActions(unittest.TestCase):
    def result(self, action):
        return {"transcript": "", "answer": "Okay.", "landmark": "", "device_action": action}

    def test_navigation_start_and_stop_use_guidance_gate(self):
        companion, _, speech = build()
        guidance = mock.Mock()
        guidance.start.return_value = "Guidance to the backpack started."
        companion.guidance = guidance
        companion._act(self.result("navigate_backpack"), "", companion.generation)
        guidance.start.assert_called_once()
        self.assertEqual(speech.said[-1][0], "Guidance to the backpack started.")
        companion._act(self.result("stop_navigation"), "", companion.generation)
        guidance.stop.assert_called_once()
        self.assertEqual(speech.said[-1][0], "Guidance stopped.")

    def test_stop_speaks_nothing(self):
        companion, _audio, speech = build()
        companion._act(self.result("stop"), "", companion.generation)
        self.assertEqual(speech.said, [])

    def test_louder_raises_gain(self):
        companion, audio, _ = build()
        companion._act(self.result("louder"), "", companion.generation)
        self.assertGreater(audio.gain, 1.0)

    def test_quieter_lowers_gain_but_stays_audible(self):
        companion, audio, _ = build()
        for _ in range(10):
            companion._act(self.result("quieter"), "", companion.generation)
        self.assertGreater(audio.gain, 0.0)

    def test_repeat_says_the_previous_answer(self):
        companion, _audio, speech = build()
        companion._act(self.result("repeat"), "The earlier answer.", companion.generation)
        self.assertEqual(speech.said[0][0], "The earlier answer.")


class GeminiRequest(unittest.TestCase):
    """The wire format is only exercised for real at demo time, so pin it here."""

    def send(self, image=b"\xff\xd8\xffJPEG", reply=None):
        reply = reply or {"transcript": "what is ahead", "answer": "A door.",
                          "landmark": "Room 204", "device_action": "none"}
        body = {"candidates": [{"finishReason": "STOP",
                                "content": {"parts": [{"text": json.dumps(reply)}]}}]}
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["headers"] = {k.lower(): v for k, v in request.headers.items()}
            captured["payload"] = json.loads(request.data)
            return io.BytesIO(json.dumps(body).encode())

        client = gemini_module.Gemini("test-key", "gemini-3.8-flash")
        with mock.patch.object(gemini_module, "urlopen", fake_urlopen):
            result = client.ask(b"WAVDATA", image, [("earlier", "answer")])
        return captured, result

    def test_audio_and_image_are_both_inline_parts(self):
        captured, _ = self.send()
        parts = captured["payload"]["contents"][-1]["parts"]
        inline = [p["inlineData"] for p in parts if "inlineData" in p]
        self.assertEqual([p["mimeType"] for p in inline], ["image/jpeg", "audio/wav"])
        self.assertEqual(base64.b64decode(inline[1]["data"]), b"WAVDATA")

    def test_request_targets_the_model_with_the_api_key_header(self):
        captured, _ = self.send()
        self.assertIn("gemini-3.8-flash:generateContent", captured["url"])
        self.assertEqual(captured["headers"]["x-goog-api-key"], "test-key")

    def test_history_precedes_the_current_turn(self):
        captured, _ = self.send()
        contents = captured["payload"]["contents"]
        self.assertEqual(contents[0]["role"], "user")
        self.assertEqual(contents[0]["parts"][0]["text"], "earlier")
        self.assertEqual(contents[1]["role"], "model")
        self.assertEqual(len(contents), 3)

    def test_schema_and_system_prompt_are_sent(self):
        captured, _ = self.send()
        config = captured["payload"]["generationConfig"]
        self.assertEqual(config["responseMimeType"], "application/json")
        self.assertIn("device_action", config["responseSchema"]["properties"])
        self.assertIn("blind or low-vision",
                      captured["payload"]["systemInstruction"]["parts"][0]["text"])

    def test_absent_image_is_stated_rather_than_omitted_silently(self):
        captured, _ = self.send(image=None)
        parts = captured["payload"]["contents"][-1]["parts"]
        self.assertNotIn("image/jpeg",
                         [p["inlineData"]["mimeType"] for p in parts if "inlineData" in p])
        self.assertIn("No camera image", parts[0]["text"])

    def test_unknown_device_action_falls_back_to_none(self):
        _, result = self.send(reply={"transcript": "", "answer": "Fine.", "landmark": "",
                                     "device_action": "self_destruct"})
        self.assertEqual(result["device_action"], "none")

    def test_truncated_answer_is_an_error_not_a_partial_answer(self):
        body = {"candidates": [{"finishReason": "MAX_TOKENS",
                                "content": {"parts": [{"text": "{}"}]}}]}
        client = gemini_module.Gemini("test-key", "gemini-3.8-flash")
        with mock.patch.object(gemini_module, "urlopen",
                               lambda *_a, **_k: io.BytesIO(json.dumps(body).encode())):
            with self.assertRaises(AppError):
                client.ask(b"WAV", None, [])

    def test_missing_key_fails_before_any_request(self):
        client = gemini_module.Gemini("", "gemini-3.8-flash")
        with self.assertRaises(AppError):
            client.ask(b"WAV", None, [])


class GeminiRetries(unittest.TestCase):
    """A 503 is demand, not a broken request. Retrying saves the user from
    having to repeat the whole question."""

    def responder(self, codes, reply=None):
        reply = reply or {"transcript": "hi", "answer": "Fine.", "landmark": "",
                          "device_action": "none"}
        body = {"candidates": [{"finishReason": "STOP",
                                "content": {"parts": [{"text": json.dumps(reply)}]}}]}
        attempts = []

        def fake_urlopen(_request, timeout=None):
            attempts.append(timeout)
            if len(attempts) <= len(codes):
                raise gemini_module.HTTPError(
                    "url", codes[len(attempts) - 1], "err", {}, io.BytesIO(b'{"error":{}}'))
            return io.BytesIO(json.dumps(body).encode())

        return fake_urlopen, attempts

    def run_ask(self, codes):
        fake, attempts = self.responder(codes)
        client = gemini_module.Gemini("key", "gemini-3.8-flash")
        with mock.patch.object(gemini_module, "urlopen", fake), \
                mock.patch.object(gemini_module.time, "sleep", lambda _s: None):
            try:
                return client.ask(b"WAV", None, []), attempts, None
            except AppError as error:
                return None, attempts, error

    def test_overload_then_success(self):
        result, attempts, error = self.run_ask([503])
        self.assertIsNone(error)
        self.assertEqual(result["answer"], "Fine.")
        self.assertEqual(len(attempts), 2)

    def test_persistent_overload_reports_busy_not_misconfiguration(self):
        _result, attempts, error = self.run_ask([503, 503, 503])
        self.assertEqual(len(attempts), 3, "should stop after the retry budget")
        self.assertEqual(error.status, 503)
        self.assertIn("busy", str(error).lower())
        self.assertNotIn("api key", str(error).lower())

    def test_rate_limit_is_retried(self):
        result, attempts, error = self.run_ask([429])
        self.assertIsNone(error)
        self.assertEqual(len(attempts), 2)
        self.assertIsNotNone(result)

    def test_bad_request_is_not_retried(self):
        _result, attempts, error = self.run_ask([400])
        self.assertEqual(len(attempts), 1, "a malformed request will fail again")
        self.assertIn("rejected", str(error).lower())

    def test_auth_failure_is_not_retried(self):
        _result, attempts, error = self.run_ask([403])
        self.assertEqual(len(attempts), 1)
        self.assertIsNotNone(error)


class Help(unittest.TestCase):
    def test_help_speaks_status_locally_then_arms_locator(self):
        companion, audio, speech = build()
        companion._help(companion.generation)
        text, local = speech.said[0]
        self.assertTrue(local, "status must use the offline engine")
        self.assertIn("Camera:", text)
        self.assertIn("locator", text)
        self.assertEqual(audio.played, 0)

    def test_second_help_plays_the_locator(self):
        companion, audio, _ = build()
        companion._help(companion.generation)
        companion._help(companion.generation)
        self.assertEqual(audio.played, 1)


if __name__ == "__main__":
    unittest.main()
