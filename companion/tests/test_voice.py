"""State machine tests. No audio device, no network, no Gemini calls."""
import base64
import io
import json
import queue
import unittest
from unittest import mock

import numpy as np

from companion.errors import AppError
from companion.voice import gemini as gemini_module
from companion.voice.__main__ import HOLD, SHORT_PRESS, Companion
from companion.voice.audio import ANSWER, HELP, URGENT, Audio
from companion.voice.session import Session


class FakeAudio:
    capture_rate = 16000

    def __init__(self, peak=5000):
        self.gain = 1.0
        self.earcons = []
        self.recording = False
        self.peak = peak
        self.stops = 0

    def earcon(self, name, wait=True):
        self.earcons.append(name)

    def record_start(self):
        self.recording = True

    def record_stop(self):
        self.recording = False
        return b"RIFFfake", 2.0, self.peak

    def stop(self):
        self.stops += 1


class FakeSpeech:
    def __init__(self):
        self.said = []
        self.priorities = []
        self.stops = 0
        self.key = ""
        self.voice_id = ""

    def say(self, text, local=False, priority=ANSWER):
        self.said.append((text, local))
        self.priorities.append(priority)

    def stop(self):
        self.stops += 1


class FakeHazards:
    def __init__(self):
        self.warnings = []

    def due(self, at):
        return not self.warnings or at - self.warnings[-1] >= 2.0

    def warn(self, at):
        self.warnings.append(at)


class FakeGemini:
    key = "test-key"

    def __init__(self, result=None, error=None):
        self.result = result or {"transcript": "what is ahead", "answer": "A door ahead.",
                                 "landmark": "", "device_action": "none"}
        self.error = error
        self.calls = []

    def ask(self, audio, image, history, stream=False, on_answer_chunk=None, **kwargs):
        self.calls.append({"audio": audio, "image": image, "history": list(history), "stream": stream})
        if self.error:
            raise self.error
        if stream and on_answer_chunk and "answer" in self.result:
            on_answer_chunk(self.result["answer"])
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


def build(gemini=None, camera=None, audio=None, hazards=None):
    audio = audio or FakeAudio()
    speech = FakeSpeech()
    companion = Companion(audio, speech, gemini or FakeGemini(), camera or FakeCamera(),
                          Session(), button=None, events=queue.Queue(),
                          hazards=hazards or FakeHazards())
    return companion, audio, speech


def queued(companion):
    """Every task waiting on any worker lane, as (kind, payload)."""
    tasks = []
    for lane in companion.lanes.values():
        while not lane.empty():
            kind, _generation, payload = lane.get_nowait()
            tasks.append((kind, payload))
    return tasks


def tap(companion, times=1, at=0.0):
    for _ in range(times):
        companion._press(at)
        companion._release(at + SHORT_PRESS + 0.05)
    if companion.tap_timer is not None:
        companion.tap_timer.cancel()
    companion._taps_due(at)


class PressGestures(unittest.TestCase):
    def test_hold_then_release_dispatches_utterance(self):
        companion, audio, _ = build()
        companion._press(0.0)
        self.assertTrue(audio.recording)
        self.assertIn("listening", audio.earcons)
        companion._release(HOLD + 0.5)
        self.assertEqual(queued(companion), [("utterance", b"RIFFfake")])
        self.assertEqual(companion.state, "busy")

    def test_press_under_threshold_is_ignored(self):
        companion, audio, _ = build()
        companion._press(0.0)
        companion._release(SHORT_PRESS / 2)
        self.assertIn("too_brief", audio.earcons)
        self.assertEqual(queued(companion), [])
        self.assertEqual(companion.state, "idle")
        self.assertIsNone(companion.tap_timer)

    def test_short_press_waits_for_the_window_then_repeats(self):
        companion, _audio, _ = build()
        companion._press(0.0)
        companion._release(SHORT_PRESS + 0.05)
        self.assertEqual(queued(companion), [])  # Still waiting for more taps.
        self.assertIsNotNone(companion.tap_timer)
        companion.tap_timer.cancel()
        companion._taps_due(0.0)
        self.assertEqual(queued(companion), [("repeat", None)])

    def test_double_tap_opens_help(self):
        companion, _audio, _ = build()
        tap(companion, times=2)
        self.assertEqual(queued(companion), [("help", None)])

    def test_triple_tap_opens_guardian(self):
        companion, _audio, _ = build()
        tap(companion, times=3)
        self.assertEqual(queued(companion), [("guardian", None)])

    def test_help_runs_on_its_own_lane_not_behind_cloud_work(self):
        companion, _audio, _ = build()
        companion.lanes["cloud"].put(("utterance", companion.generation, b"slow"))
        tap(companion, times=2)
        kind, _generation, _payload = companion.lanes["local"].get_nowait()
        self.assertEqual(kind, "help")

    def test_hold_resets_a_pending_tap_chain(self):
        companion, _audio, _ = build()
        companion._press(0.0)
        companion._release(SHORT_PRESS + 0.05)
        companion._press(1.0)
        companion._release(1.0 + HOLD + 0.5)
        self.assertEqual(companion.taps, 0)
        self.assertEqual(queued(companion), [("utterance", b"RIFFfake")])

    def test_chain_timer_firing_mid_press_does_not_lose_taps(self):
        companion, _audio, _ = build()
        companion._press(0.0)
        companion._release(SHORT_PRESS + 0.05)
        companion._press(0.4)
        companion._taps_due(0.5)  # Queued just before the press cancelled it.
        companion._release(0.4 + SHORT_PRESS + 0.05)
        companion.tap_timer.cancel()
        companion._taps_due(1.0)
        self.assertEqual(queued(companion), [("help", None)])

    def test_silent_capture_is_not_sent_to_gemini(self):
        companion, _audio, _ = build(audio=FakeAudio(peak=10))
        companion._press(0.0)
        companion._release(HOLD + 0.5)
        [(kind, payload)] = queued(companion)
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
        companion._disarm_cap()  # Its 30 s cap timers would hold the test run open.

    def test_tap_while_busy_cancels_without_repeat(self):
        companion, _audio, speech = build()
        companion.state = "busy"
        companion._press(0.0)
        companion._release(SHORT_PRESS + 0.05)
        self.assertEqual(speech.stops, 1)
        self.assertEqual(companion.state, "idle")
        self.assertIsNone(companion.tap_timer)
        self.assertEqual(queued(companion), [])

    def test_hold_while_busy_still_asks_a_new_question(self):
        companion, _audio, _ = build()
        companion.state = "busy"
        companion._press(0.0)
        companion._release(HOLD + 0.5)
        self.assertEqual(queued(companion), [("utterance", b"RIFFfake")])

    def test_superseded_task_is_skipped_by_the_worker(self):
        companion, _audio, speech = build()
        lane = companion.lanes["local"]
        lane.put(("help", companion.generation - 1, None))
        lane.put(None)
        companion._worker(lane)
        self.assertEqual(speech.said, [])
        self.assertEqual(companion.events.get_nowait()[0], "done")

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

    def target_result(self, box):
        result = self.result("navigate_target")
        result.update(answer="I don't see a water fountain.", target="water fountain", box_2d=box)
        return result

    def test_navigate_target_sends_the_box_with_the_frame_stamp(self):
        camera = FakeCamera()
        camera.frame_info = lambda jpeg: ({"stamp": "12.000000003", "width": 640, "height": 480}
                                          if jpeg is camera.image else None)
        companion, _, speech = build(camera=camera)
        companion.session.set_image(camera.image, None)
        companion.guidance = mock.Mock()
        companion.guidance.go_to.return_value = "I think I see the water fountain."
        companion._act(self.target_result([1, 2, 3, 4]), "", companion.generation)
        companion.guidance.go_to.assert_called_once_with(
            "water fountain", [1, 2, 3, 4], "12.000000003", 640, 480)
        self.assertEqual(speech.said[-1], ("I think I see the water fountain.", True))

    def test_navigate_target_without_a_box_speaks_geminis_answer(self):
        companion, _, speech = build()
        companion.guidance = mock.Mock()
        companion._act(self.target_result(None), "", companion.generation)
        companion.guidance.go_to.assert_not_called()
        self.assertEqual(speech.said[-1], ("I don't see a water fountain.", False))

    def test_navigate_target_refuses_a_frame_without_a_ros_stamp(self):
        companion, _, speech = build()
        companion.guidance = mock.Mock()
        companion._act(self.target_result([1, 2, 3, 4]), "", companion.generation)
        companion.guidance.go_to.assert_not_called()
        self.assertEqual(speech.said[-1][0], "Guidance needs the live camera.")

    def test_navigate_target_without_ros_says_unavailable(self):
        companion, _, speech = build()
        companion._act(self.target_result([1, 2, 3, 4]), "", companion.generation)
        self.assertEqual(speech.said[-1][0], "Guidance is unavailable here.")

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

    def navigate(self, box, action="navigate_target", target="water fountain"):
        _, result = self.send(reply={"transcript": "", "answer": "Okay.", "landmark": "",
                                     "device_action": action, "target": target,
                                     "box_2d": box})
        return result

    def test_navigate_target_keeps_a_valid_box(self):
        result = self.navigate([100, 200, 300, 400])
        self.assertEqual(result["box_2d"], [100, 200, 300, 400])
        self.assertEqual(result["target"], "water fountain")

    def test_malformed_boxes_become_none(self):
        for box in ([1, 2, 3], [300, 0, 100, 10], [0, 500, 10, 500], [0, 0, 10, 1001],
                    [0.5, 0, 10, 10], [True, 0, 10, 10], "0,0,10,10", None):
            self.assertIsNone(self.navigate(box)["box_2d"], box)

    def test_box_and_target_are_dropped_for_other_actions(self):
        result = self.navigate([100, 200, 300, 400], action="none")
        self.assertIsNone(result["box_2d"])
        self.assertEqual(result["target"], "")

    def test_target_label_cannot_break_the_planner_message(self):
        result = self.navigate([1, 2, 3, 4], target='the "exit" \\ sign' + "x" * 60)
        self.assertNotIn('"', result["target"])
        self.assertNotIn("\\", result["target"])
        self.assertLessEqual(len(result["target"]), 40)

    def test_non_json_response_reports_http_metadata(self):
        class BadResponse(io.BytesIO):
            status = 200
            headers = {"Content-Type": "text/html"}

        client = gemini_module.Gemini("test-key", "gemini-3.5-flash-lite")
        with mock.patch.object(gemini_module, "urlopen", return_value=BadResponse(b"<html>")):
            with self.assertRaises(AppError) as caught:
                client.ask(b"WAVDATA", b"\xff\xd8\xffJPEG", [])
        self.assertIn("non-JSON", str(caught.exception))

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

    def test_streaming_extracts_answer_incrementally(self):
        chunks = [
            b'data: {"candidates":[{"content":{"parts":[{"text":"{\\n  \\\"answer\\\": \\\"A brown door\\\""}]}}]}\n\n',
            b'data: {"candidates":[{"content":{"parts":[{"text":",\\n  \\\"transcript\\\": \\\"what is ahead\\\",\\n  \\\"landmark\\\": \\\"\\\",\\n  \\\"device_action\\\": \\\"none\\\"\\n}"}]}}]}\n\n'
        ]
        received = []
        client = gemini_module.Gemini("test-key", "gemini-3.8-flash")
        with mock.patch.object(gemini_module, "urlopen",
                               lambda *_a, **_k: io.BytesIO(b"".join(chunks))):
            res = client.ask(b"WAV", None, [], stream=True,
                             on_answer_chunk=lambda c: received.append(c))
        self.assertEqual("".join(received), "A brown door")
        self.assertEqual(res["answer"], "A brown door")
        self.assertEqual(res["transcript"], "what is ahead")


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
    def setUp(self):
        patcher = mock.patch("companion.voice.__main__.network_up", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_help_speaks_status_locally_at_help_priority(self):
        companion, _audio, speech = build()
        companion._help(companion.generation)
        text, local = speech.said[0]
        self.assertTrue(local, "status must use the offline engine")
        self.assertIn("Camera:", text)
        self.assertEqual(speech.priorities[0], HELP)

    def test_repeated_help_never_mentions_or_plays_a_locator(self):
        companion, _audio, speech = build()
        companion._help(companion.generation)
        companion._help(companion.generation)
        self.assertEqual(len(speech.said), 2)
        for text, _local in speech.said:
            self.assertNotIn("locator", text.lower())

    def test_guardian_action_is_announced_not_silent(self):
        companion, _audio, speech = build()
        companion._act({"transcript": "", "answer": "", "landmark": "",
                        "device_action": "guardian"}, "", companion.generation)
        self.assertIn("Guardian", speech.said[0][0])


class HazardPreemption(unittest.TestCase):
    def test_idle_hazard_plays_the_warning(self):
        companion, _audio, _ = build()
        companion._hazard(10.0)
        self.assertEqual(companion.hazards.warnings, [10.0])
        self.assertEqual(companion.state, "idle")

    def test_hazard_while_busy_invalidates_and_stops_speech(self):
        companion, audio, _ = build()
        companion.state = "busy"
        gen_before = companion.generation
        companion._hazard(10.0)
        self.assertEqual(audio.stops, 1)
        self.assertGreater(companion.generation, gen_before)
        self.assertEqual(companion.state, "idle")
        self.assertEqual(len(companion.hazards.warnings), 1)

    def test_hazard_while_recording_discards_the_question(self):
        companion, audio, _ = build()
        companion._press(10.0)
        companion._hazard(10.5)
        self.assertFalse(audio.recording)
        self.assertEqual(companion.cap_timers, [])
        companion._release(10.0 + HOLD + 1.0)  # The interrupted press lets go.
        self.assertEqual(queued(companion), [])
        self.assertEqual(companion.state, "idle")

    def test_hazard_cancels_a_pending_tap_chain(self):
        companion, _audio, _ = build()
        companion._press(10.0)
        companion._release(10.0 + SHORT_PRESS + 0.05)
        companion._hazard(10.3)
        self.assertIsNone(companion.tap_timer)
        companion._taps_due(10.8)
        self.assertEqual(queued(companion), [])

    def test_repeated_hazard_inside_cooldown_changes_nothing(self):
        companion, _audio, _ = build()
        companion._hazard(10.0)
        generation = companion.generation
        companion._hazard(11.0)
        self.assertEqual(companion.generation, generation)
        self.assertEqual(companion.hazards.warnings, [10.0])
        companion._hazard(12.5)
        self.assertEqual(len(companion.hazards.warnings), 2)

    def test_stale_answer_after_hazard_is_not_spoken(self):
        companion, _audio, speech = build()
        generation = companion.generation
        companion._hazard(10.0)
        companion._utterance(b"wav", generation)
        self.assertEqual(speech.said, [])


def tone(value, count):
    return np.full(count, value, dtype=np.int16)


class AudioOwner(unittest.TestCase):
    """Drives the output owner's mixer directly; no device is opened."""

    def setUp(self):
        self.audio = Audio(start_output=False)

    def test_clip_plays_then_finishes(self):
        playback = self.audio.submit(tone(1000, 300))
        self.assertTrue((self.audio._render(300) == 1000).all())
        self.audio._render(10)
        self.assertTrue(playback.done.is_set())
        self.assertFalse(playback.cancelled)

    def test_more_important_speech_goes_first_and_the_rest_waits(self):
        answer = self.audio.submit(tone(1, 100), priority=ANSWER)
        warning = self.audio.submit(tone(9, 100), priority=URGENT)
        self.assertTrue((self.audio._render(100) == 9).all())
        self.audio._render(1)
        self.assertTrue(warning.done.is_set())
        self.assertFalse(answer.cancelled, "unstarted speech waits its turn")

    def test_warning_cancels_a_started_answer_for_good(self):
        answer = self.audio.stream(ANSWER)
        answer.feed(tone(1, 100))
        self.audio._render(50)
        self.audio.submit(tone(9, 100), priority=URGENT)
        self.assertTrue((self.audio._render(100) == 9).all())
        self.assertTrue(answer.cancelled)
        self.assertFalse(answer.feed(tone(1, 100)), "late cloud chunks are refused")
        self.assertTrue((self.audio._render(50) == 0).all(), "the answer never resumes")

    def test_stop_spares_warnings(self):
        warning = self.audio.submit(tone(9, 100), priority=URGENT)
        answer = self.audio.submit(tone(1, 100), priority=ANSWER)
        help_speech = self.audio.submit(tone(2, 100), priority=HELP)
        self.audio.stop()
        self.assertFalse(warning.cancelled)
        self.assertTrue(answer.cancelled and help_speech.cancelled)

    def test_open_stream_holds_the_lane_until_closed(self):
        warning = self.audio.stream(URGENT)
        self.audio.submit(tone(1, 100), priority=ANSWER)
        self.assertTrue((self.audio._render(100) == 0).all())
        warning.feed(tone(9, 50))
        warning.close()
        rendered = self.audio._render(150)
        self.assertTrue((rendered[:50] == 9).all())
        self.assertTrue((rendered[50:] == 1).all())

    def test_cues_mix_over_speech(self):
        self.audio.submit(tone(100, 100), priority=ANSWER)
        done = self.audio.cue(tone(5, 50))
        rendered = self.audio._render(100)
        self.assertTrue((rendered[:50] == 105).all())
        self.assertTrue((rendered[50:] == 100).all())
        self.assertTrue(done.is_set())

    def test_gain_scales_and_clips(self):
        self.audio.gain = 2.0
        self.audio.submit(tone(30000, 10))
        self.assertTrue((self.audio._render(10) == 32767).all())

    def test_other_rates_are_resampled(self):
        playback = self.audio.stream(ANSWER)
        playback.feed(tone(7, 16000), 16000)
        playback.close()
        self.assertEqual(sum(len(chunk) for chunk in playback.chunks), 22050)


if __name__ == "__main__":
    unittest.main()
