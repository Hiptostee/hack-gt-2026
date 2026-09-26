"""Guardian tests. No microphone, speaker, network or ElevenLabs connection."""
import threading
import time
import unittest
import unittest.mock

import numpy as np

from companion.errors import AppError
from companion.guardian.audio import GuardianAudio
from companion.guardian.session import GuardianController
from companion.guardian.sms import SmsGate
from companion.tests.test_voice import (FakeAudio, FakeCamera, FakeGemini, FakeHazards,
                                        FakeSpeech, build, queued, tap)
from companion.voice.__main__ import SHORT_PRESS
from companion.voice.audio import ANSWER, HELP, URGENT, Audio
from companion.voice.session import Session


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class RecordingSender:
    def __init__(self, status="submitted", error=None):
        self.sent = []
        self.status = status
        self.error = error

    def send(self, to_number, text):
        self.sent.append((to_number, text))
        if self.error:
            raise self.error
        return {"status": self.status, "id": "SM1"}


class SmsGateTests(unittest.TestCase):
    def gate(self, sender=None):
        self.clock = Clock()
        self.spoken = []
        self.updates = []
        self.sender = sender if sender is not None else RecordingSender()
        return SmsGate(self.sender, "Sarah", "+15550100", "Jae",
                       speak=self.spoken.append, update=self.updates.append,
                       clock=self.clock, spawn=lambda fn: fn())

    def prepared(self, note="I'm okay, just lost"):
        gate = self.gate()
        reply = gate.prepare(note, "The camera last saw Room 204 sign at 3:52 PM.")
        self.assertIn("waiting for the user's answer", reply)
        return gate

    def test_preview_is_read_by_the_device_with_the_facts_from_code(self):
        self.prepared()
        preview = self.spoken[0]
        self.assertIn("I'll text Sarah", preview)
        self.assertIn("Room 204 sign at 3:52 PM", preview)
        self.assertIn("I'm okay, just lost", preview)
        self.assertIn("not an emergency service", preview)

    def test_yes_after_the_preview_sends_exactly_once(self):
        gate = self.prepared()
        self.clock.now += 3
        gate.on_transcript("Yes, send it.", turn_started_at=self.clock.now - 1)
        gate.on_transcript("Yes.", turn_started_at=self.clock.now)
        self.assertEqual(len(self.sender.sent), 1)
        self.assertEqual(self.sender.sent[0][0], "+15550100")
        self.assertIn("Your message was submitted.", self.spoken)

    def test_a_turn_started_before_the_preview_ended_cannot_confirm(self):
        gate = self.prepared()
        gate.on_transcript("yes", turn_started_at=self.clock.now - 5)
        self.assertEqual(self.sender.sent, [])
        self.assertIsNotNone(gate.draft, "the draft still waits for a real answer")

    def test_no_or_anything_unclear_cancels_without_sending(self):
        for answer in ("No, don't.", "Yes, wait, no.", "What's around me?", ""):
            gate = self.prepared()
            self.clock.now += 2
            gate.on_transcript(answer, turn_started_at=self.clock.now)
            self.assertEqual(self.sender.sent, [], answer)
            self.assertIsNone(gate.draft)

    def test_expired_draft_cannot_be_confirmed(self):
        gate = self.prepared()
        self.clock.now += 61
        gate.on_transcript("yes", turn_started_at=self.clock.now)
        self.assertEqual(self.sender.sent, [])

    def test_second_prepare_while_waiting_is_refused(self):
        gate = self.prepared()
        reply = gate.prepare("again", "obs")
        self.assertIn("already waiting", reply)
        self.assertEqual(len(self.spoken), 1)

    def test_cancel_drops_the_draft_and_tells_the_agent(self):
        gate = self.prepared()
        gate.cancel("a hazard warning played")
        self.clock.now += 2
        gate.on_transcript("yes", turn_started_at=self.clock.now)
        self.assertEqual(self.sender.sent, [])
        self.assertIn("not sent", self.updates[-1])

    def test_send_limit(self):
        gate = self.gate()
        for _ in range(3):
            gate.prepare("", "obs")
            self.clock.now += 2
            gate.on_transcript("yes", turn_started_at=self.clock.now)
        self.assertIn("limit", gate.prepare("", "obs"))
        self.assertEqual(len(self.sender.sent), 3)

    def test_error_after_sending_is_reported_as_unknown_and_not_retried(self):
        gate = self.gate(RecordingSender(error=TimeoutError("read timed out")))
        gate.prepare("", "obs")
        self.clock.now += 2
        gate.on_transcript("yes", turn_started_at=self.clock.now)
        self.assertEqual(len(self.sender.sent), 1)
        self.assertIn("I couldn't confirm whether it was sent.", self.spoken)

    def test_unavailable_without_a_sender(self):
        gate = SmsGate(None, "Sarah", "", "", speak=print, update=print)
        self.assertIn("isn't available", gate.prepare("", "obs"))

    def test_note_is_trimmed(self):
        gate = self.gate()
        self.assertLessEqual(len(gate.compose("x" * 500, "")), 160 + 120)


class TwilioTests(unittest.TestCase):
    def sender(self):
        from companion.guardian.sms import TwilioSender
        return TwilioSender("AC123", "secret", "+15550001")

    def reply(self, body):
        import io
        import json as json_module
        return io.BytesIO(json_module.dumps(body).encode())

    def test_request_shape(self):
        from urllib.parse import parse_qs
        captured = {}

        def fake(request, timeout=None):
            captured["url"] = request.full_url
            captured["auth"] = request.headers["Authorization"]
            captured["form"] = parse_qs(request.data.decode())
            return self.reply({"sid": "SM9", "status": "queued"})

        with unittest.mock.patch("companion.guardian.sms.urlopen", fake):
            result = self.sender().send("+15550100", "hello")
        self.assertEqual(result, {"status": "submitted", "id": "SM9"})
        self.assertTrue(captured["url"].endswith("/Accounts/AC123/Messages.json"))
        self.assertTrue(captured["auth"].startswith("Basic "))
        self.assertEqual(captured["form"], {"To": ["+15550100"], "From": ["+15550001"],
                                            "Body": ["hello"]})

    def test_refusal_is_a_definite_failure(self):
        import io
        from urllib.error import HTTPError
        error = HTTPError("url", 400, "bad", {}, io.BytesIO(b'{"code":21608}'))
        with unittest.mock.patch("companion.guardian.sms.urlopen", side_effect=error):
            self.assertEqual(self.sender().send("+1555", "x")["status"], "failed")

    def test_server_error_or_timeout_is_unknown(self):
        import io
        from urllib.error import HTTPError, URLError
        for error in (HTTPError("url", 503, "busy", {}, io.BytesIO(b"")),
                      TimeoutError("read timed out"), URLError(TimeoutError("timed out"))):
            with unittest.mock.patch("companion.guardian.sms.urlopen", side_effect=error):
                self.assertEqual(self.sender().send("+1555", "x")["status"], "unknown", error)

    def test_unreachable_before_sending_is_a_failure(self):
        from urllib.error import URLError
        with unittest.mock.patch("companion.guardian.sms.urlopen",
                                 side_effect=URLError("nodename nor servname provided")):
            self.assertEqual(self.sender().send("+1555", "x")["status"], "failed")

    def test_delivery_check(self):
        sender = self.sender()
        for status, expected in (("delivered", "delivered"), ("undelivered", "failed"),
                                 ("sent", None)):
            with unittest.mock.patch("companion.guardian.sms.urlopen",
                                     return_value=self.reply({"status": status})):
                self.assertEqual(sender.check("SM9"), expected)

    def test_env_selects_sender_and_refuses_partial_config(self):
        from companion.guardian.sms import FakeSender, TwilioSender, sender_from_env
        self.assertIsInstance(sender_from_env({"GUARDIAN_SMS": "fake"}), FakeSender)
        self.assertIsNone(sender_from_env({"GUARDIAN_SMS": "twilio",
                                           "TWILIO_ACCOUNT_SID": "AC1"}))
        full = {"GUARDIAN_SMS": "twilio", "TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "t",
                "TWILIO_FROM_NUMBER": "+1", "GUARDIAN_CONTACT_NUMBER": "+2"}
        self.assertIsInstance(sender_from_env(full), TwilioSender)
        self.assertIsNone(sender_from_env({}))


class TrailTests(unittest.TestCase):
    def at(self, hour, minute):
        return time.mktime((2026, 9, 26, hour, minute, 0, 0, 0, -1))

    def session(self):
        session = Session()
        session.save_landmark("Room 204 sign", self.at(15, 52))
        session.save_landmark("elevator sign", self.at(15, 55))
        return session

    def test_newest_first_with_capture_times(self):
        with unittest.mock.patch("time.time", return_value=self.at(16, 0)):
            text = self.session().trail_text()
        self.assertEqual(text, "Recent camera observations: elevator sign at 3:55 PM; "
                               "Room 204 sign at 3:52 PM.")

    def test_repeat_sighting_moves_to_front_and_size_is_capped(self):
        session = self.session()
        for label, minute in (("exit sign", 56), ("water fountain", 57), ("Room 204 sign", 58)):
            session.save_landmark(label, self.at(15, minute))
        labels = [o["label"] for o in session.observations]
        self.assertEqual(labels, ["Room 204 sign", "water fountain", "exit sign"])

    def test_old_observations_drop_out(self):
        with unittest.mock.patch("time.time", return_value=self.at(16, 24)):
            self.assertEqual(self.session().trail_text(),
                             "The camera last saw elevator sign at 3:55 PM.")
        with unittest.mock.patch("time.time", return_value=self.at(17, 0)):
            self.assertEqual(self.session().trail_text(), "No landmark has been observed.")

    def test_greeting_keeps_only_the_newest(self):
        with unittest.mock.patch("time.time", return_value=self.at(16, 0)):
            self.assertEqual(self.session().observation_text(),
                             "The camera last saw elevator sign at 3:55 PM.")

    def test_sms_and_status_carry_the_trail(self):
        audio = Audio(start_output=False)
        audio.earcon = lambda *_a, **_k: None
        session = Session()
        session.save_landmark("Room 204 sign", time.time() - 120)
        session.save_landmark("elevator sign", time.time() - 60)
        spoken = []
        controller = GuardianController(audio, FakeSpeech(), FakeCamera(), FakeGemini(),
                                        session, lambda: "Network reachable.", lambda: True,
                                        print)
        controller.sms = SmsGate(RecordingSender(), "Sarah", "", "Jae", speak=spoken.append,
                                 update=lambda _t: None)
        controller.state = "active"
        tools = controller._tools(controller.session_id)
        self.assertIn("Recent camera observations: elevator sign", tools["get_status"]({}))
        tools["prepare_sms"]({"note": ""})
        self.assertIn("Recent camera observations: elevator sign", spoken[0])


class GuardianAudioTests(unittest.TestCase):
    def setUp(self):
        self.audio = Audio(start_output=False)
        self.voice = GuardianAudio(self.audio, open_mic=False)
        self.sent = []
        self.voice.start(self.sent.append)
        self.voice.enable()

    def chunk(self, value=1000):
        return np.full(4000, value, dtype=np.int16).tobytes()

    def test_mic_sends_silence_unless_held_past_the_threshold(self):
        self.voice.capture(self.chunk())
        self.voice.press()
        self.voice.capture(self.chunk())
        self.assertEqual(self.sent, [bytes(8000), bytes(8000)])

    def test_talk_sends_the_audio_captured_before_the_threshold(self):
        self.voice.press()
        self.voice.capture(self.chunk(1))
        self.voice.talk()
        self.voice.capture(self.chunk(2))
        self.assertEqual(self.sent[1:], [self.chunk(1), self.chunk(2)])

    def test_mic_is_silent_while_the_device_plays_a_local_sound(self):
        self.voice.press()
        self.voice.talk()
        self.audio.submit(np.ones(100, np.int16), priority=URGENT)
        self.voice.capture(self.chunk())
        self.assertEqual(self.sent[-1], bytes(8000))

    def test_press_stops_agent_speech_and_a_tap_keeps_it_quiet(self):
        self.voice.output(self.chunk())
        playback = self.voice.playback
        self.voice.press()
        self.assertTrue(playback.cancelled)
        self.assertFalse(self.voice.release(), "a tap is not a turn")
        self.voice.output(self.chunk())
        self.assertIsNone(self.voice.playback, "late reply audio is dropped")

    def test_a_turn_lets_the_next_reply_play(self):
        self.voice.press()
        self.voice.talk()
        self.voice.output(self.chunk())
        self.assertIsNone(self.voice.playback, "agent muted while the user holds")
        self.assertTrue(self.voice.release())
        self.voice.output(self.chunk())
        self.assertIsNotNone(self.voice.playback)
        self.assertEqual(self.voice.playback.priority, ANSWER)

    def test_talk_cap_stops_sending_but_is_still_a_turn(self):
        self.voice.press()
        self.voice.talk()
        self.voice.end_talk()
        self.voice.capture(self.chunk())
        self.assertEqual(self.sent[-1], bytes(8000))
        self.assertTrue(self.voice.release())

    def test_greeting_waits_for_the_cancel_window(self):
        voice = GuardianAudio(self.audio, open_mic=False)
        voice.output(self.chunk())
        self.assertIsNone(voice.playback)
        voice.enable()
        self.assertIsNotNone(voice.playback)

    def test_warning_cuts_agent_speech_for_good(self):
        self.voice.output(self.chunk())
        self.audio._render(100)
        self.audio.stop()  # What the companion does on a hazard.
        self.voice.mute_until_turn()
        self.voice.output(self.chunk())
        self.assertIsNone(self.voice.playback)


class FakeConversation:
    def __init__(self, voice, tools, variables, callbacks):
        self.voice = voice
        self.tools = tools
        self.variables = variables
        self.callbacks = callbacks
        self.updates = []
        self.ended = 0

    def send_contextual_update(self, text):
        self.updates.append(text)

    def end_session(self):
        self.ended += 1


class ControllerTests(unittest.TestCase):
    def controller(self, connect_ok=True, network=True, camera=None, gemini=None):
        self.audio = Audio(start_output=False)
        self.audio.earcon = lambda *_args, **_kwargs: None  # Nothing renders cues here.
        self.speech = FakeSpeech()
        self.closed = []
        self.conversations = []

        def connect(api_key, agent_id, voice, tools, variables, on_transcript, on_heartbeat,
                    on_end, on_agent_response=None):
            if not connect_ok:
                raise ConnectionError("no route")
            conversation = FakeConversation(voice, tools, variables, dict(
                transcript=on_transcript, heartbeat=on_heartbeat, end=on_end))
            self.conversations.append(conversation)
            voice.send = lambda _chunk: None
            voice.on_started()
            return conversation

        session = Session()
        self.seen_at = time.time() - 60
        session.save_landmark("Room 204 sign", self.seen_at)
        controller = GuardianController(
            self.audio, self.speech, camera or FakeCamera(), gemini or FakeGemini(), session,
            status=lambda: "Network reachable.", network_up=lambda: network,
            notify=self.closed.append, api_key="key", agent_id="agent", connect=connect,
            cancel_window=0.05, connect_timeout=1.0)
        return controller

    def opened(self, **kwargs):
        controller = self.controller(**kwargs)
        controller.open()
        self.assertTrue(wait_for(lambda: controller.state == "active"))
        return controller

    def test_opens_after_the_window_with_the_observation_as_context(self):
        controller = self.opened()
        variables = self.conversations[0].variables
        clock = time.strftime("%I:%M %p", time.localtime(self.seen_at)).lstrip("0")
        self.assertEqual(variables["last_observation"],
                         f"The camera last saw Room 204 sign at {clock}.")
        self.assertEqual(variables["sms_available"], "no")
        self.assertIn(("Opening guardian mode. Tap to cancel.", True), self.speech.said)
        self.assertTrue(controller.voice.enabled)

    def test_tap_during_the_window_cancels(self):
        controller = self.controller()
        controller.cancel_window = 1.0
        controller.open()
        self.assertTrue(wait_for(lambda: self.conversations))
        controller.press(0.0)
        self.assertEqual(controller.state, "closed")
        self.assertEqual(self.closed, ["cancelled"])
        self.assertTrue(wait_for(lambda: self.conversations[0].ended == 1))
        self.assertFalse(controller.voice.enabled, "no mic audio ever went out")

    def test_no_network_falls_back_without_connecting(self):
        controller = self.controller(network=False)
        controller.open()
        self.assertTrue(wait_for(lambda: self.closed == ["offline"]))
        self.assertEqual(self.conversations, [])

    def test_connect_failure_is_announced(self):
        controller = self.controller(connect_ok=False)
        controller.open()
        self.assertTrue(wait_for(lambda: self.closed == ["unavailable"]))

    def test_double_tap_exit_closes_locally(self):
        controller = self.opened()
        controller.close("exit")
        self.assertEqual(self.closed, ["exit"])
        self.assertTrue(wait_for(lambda: self.conversations[0].ended == 1))

    def test_server_end_is_not_reported_as_a_failure(self):
        controller = self.opened()
        self.conversations[0].callbacks["end"]()
        self.assertEqual(self.closed, ["ended"])

    def test_socket_end_without_network_is_a_lost_connection(self):
        controller = self.opened()
        controller.network_up = lambda: False
        self.conversations[0].callbacks["end"]()
        self.assertEqual(self.closed, ["lost"])

    def test_tools_are_refused_when_not_active(self):
        controller = self.opened()
        tools = self.conversations[0].tools
        controller.close("exit")
        self.assertIn("not active", tools["get_status"]({}))

    def test_describe_scene_keeps_its_time(self):
        controller = self.opened()
        reply = self.conversations[0].tools["describe_scene"]({})
        self.assertTrue(reply.startswith("As of "))
        self.assertIn("A door ahead.", reply)

    def test_describe_scene_without_camera(self):
        controller = self.opened(camera=FakeCamera(error=AppError("No recent frame", 503)))
        reply = self.conversations[0].tools["describe_scene"]({})
        self.assertIn("camera isn't sending images", reply)

    def test_scene_result_after_exit_is_discarded(self):
        release = threading.Event()

        class SlowGemini(FakeGemini):
            def ask(self, *args, **kwargs):
                release.wait(2)
                return super().ask(*args, **kwargs)

        controller = self.opened(gemini=SlowGemini())
        tool = self.conversations[0].tools["describe_scene"]
        result = []
        worker = threading.Thread(target=lambda: result.append(tool({})))
        worker.start()
        controller.close("exit")
        release.set()
        worker.join(3)
        self.assertIn("ended before", result[0])

    def test_hazard_mutes_agent_and_notes_it_once(self):
        controller = self.opened()
        controller.on_hazard(None)
        self.assertTrue(controller.voice.drop_output)
        self.assertTrue(wait_for(lambda: len(self.conversations[0].updates) == 1))
        controller.on_hazard(None)
        time.sleep(0.1)
        self.assertEqual(len(self.conversations[0].updates), 1, "rate-limited")

    def test_no_reply_warning_waits_while_a_tool_runs(self):
        from unittest import mock
        controller = self.opened()
        controller.last_turn_started_at = time.monotonic()
        controller.tools_running = 1
        with mock.patch("companion.guardian.session.REPLY_WORKING", 0.3), \
                mock.patch("companion.guardian.session.REPLY_GIVE_UP", 0.6):
            worker = threading.Thread(target=controller._await_reply,
                                      args=(controller.session_id, time.monotonic()))
            worker.start()
            time.sleep(1.0)
            self.assertNotIn(("Guardian isn't responding. Double tap to leave.", True),
                             self.speech.said)
            controller.tools_running = 0
            worker.join(3)
        self.assertIn(("Guardian isn't responding. Double tap to leave.", True),
                      self.speech.said)

    def test_old_session_callbacks_are_ignored(self):
        controller = self.opened()
        old = self.conversations[0]
        controller.close("exit")
        controller.open()
        self.assertTrue(wait_for(lambda: controller.state == "active"))
        old.callbacks["end"]()
        self.assertEqual(controller.state, "active")
        self.assertEqual(self.closed, ["exit"])


class FakeGuardian:
    def __init__(self):
        self.calls = []

    def open(self):
        self.calls.append("open")

    def press(self, at):
        self.calls.append("press")

    def release(self, at):
        self.calls.append("release")
        return "tap"

    def close(self, reason="exit"):
        self.calls.append(("close", reason))

    def on_hazard(self, warning):
        self.calls.append("hazard")

    def shutdown(self):
        pass


class CompanionGuardianTests(unittest.TestCase):
    def build(self):
        companion, audio, speech = build()
        companion.guardian = FakeGuardian()
        return companion, speech

    def test_triple_tap_enters_guardian(self):
        companion, _ = self.build()
        tap(companion, times=3)
        self.assertEqual(companion.state, "guardian")
        self.assertEqual(companion.guardian.calls, ["open"])

    def test_double_tap_inside_guardian_exits(self):
        companion, _ = self.build()
        tap(companion, times=3)
        tap(companion, times=2)
        self.assertIn(("close", "exit"), companion.guardian.calls)

    def test_presses_inside_guardian_never_record(self):
        companion, _ = self.build()
        tap(companion, times=3)
        companion._press(5.0)
        self.assertFalse(companion.audio.recording)
        companion._release(5.0 + SHORT_PRESS + 0.05)
        self.assertEqual(companion.state, "guardian")
        companion.tap_timer.cancel()

    def test_hazard_inside_guardian_keeps_the_mode(self):
        companion, _ = self.build()
        tap(companion, times=3)
        companion._hazard(10.0)
        self.assertEqual(companion.state, "guardian")
        self.assertIn("hazard", companion.guardian.calls)
        self.assertEqual(companion.hazards.warnings, [10.0])

    def test_closing_returns_to_normal_and_speaks_locally(self):
        companion, _ = self.build()
        tap(companion, times=3)
        companion._guardian_closed("exit")
        self.assertEqual(companion.state, "busy")
        self.assertEqual(queued(companion), [("guardian_exit", "exit")])

    def test_spoken_request_enters_guardian(self):
        companion, _ = self.build()
        companion._act({"transcript": "", "answer": "", "landmark": "",
                        "device_action": "guardian"}, "", companion.generation)
        kind, _ = companion.events.get_nowait()
        self.assertEqual(kind, "guardian_request")
        companion._guardian_request(0.0)
        self.assertEqual(companion.state, "guardian")

    def test_exit_announcement_is_local_and_includes_status(self):
        companion, speech = self.build()
        from unittest import mock
        with mock.patch("companion.voice.__main__.network_up", return_value=True):
            companion._guardian_exit("lost", companion.generation)
        text, local = speech.said[0]
        self.assertTrue(local)
        self.assertTrue(text.startswith("I lost the connection"))
        self.assertIn("Camera:", text)
        self.assertEqual(speech.priorities[0], HELP)


if __name__ == "__main__":
    unittest.main()
