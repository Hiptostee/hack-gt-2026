"""Contract, lifespan, audio and all-state integration tests; no hardware/network."""
import json
import unittest
from unittest.mock import Mock, patch

import numpy as np

from companion.voice.hazard_state import Alert, HazardState, parse_snapshot
from companion.voice.audio import Audio, ANSWER, URGENT
from companion.voice.hazards import HazardVoice
from companion.voice.speech import Speech
from companion.tests.test_voice import build

NS = 100_000_000_000


def snapshot(sequence=1, session="boot", now=NS, severity="urgent"):
    return {"schema_version": 1, "source_session": session, "sequence": sequence,
            "published_stamp_ns": now,
            "health": {"depth": "ok", "body_pose": "ok", "floor": "unavailable", "labels": "unavailable"},
            "events": [{"id": "obstacle-1", "observed_stamp_ns": now, "ttl_ms": 250,
                        "kind": "upper_body_obstacle", "severity": severity,
                        "direction": "left", "height_band": "head", "distance_m": 0.6,
                        "label": None, "evidence": "depth_cluster"}]}


class ContractTests(unittest.TestCase):
    def test_valid_and_expired_evidence(self):
        data, events = parse_snapshot(json.dumps(snapshot()), NS, 10)
        self.assertEqual(events[0].phrase, "urgent:head:left")
        self.assertEqual(events[0].expires_at, 10.25)
        value = snapshot(now=NS + 300_000_000)
        value["events"][0]["observed_stamp_ns"] = NS
        self.assertEqual(parse_snapshot(json.dumps(value), NS + 300_000_000, 10.3)[1], [])

    def test_reject_bad_schema_timestamps_units_and_enums(self):
        patches = [("ttl_ms", 251), ("ttl_ms", True), ("distance_m", 0), ("distance_m", float("nan")),
                   ("distance_m", True), ("severity", "safe"), ("kind", "drop_off"),
                   ("observed_stamp_ns", NS + 1), ("direction", "back"), ("label", "door"),
                   ("evidence", "rgb_box")]
        for key, value in patches:
            doc = snapshot(); doc["events"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                parse_snapshot(json.dumps(doc), NS, 10)
        for key, value in [("schema_version", True), ("sequence", -1), ("published_stamp_ns", NS + 1),
                           ("events", [{}] * 9), ("health", {"depth": "ok"})]:
            doc = snapshot(); doc[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                parse_snapshot(json.dumps(doc), NS, 10)
        with self.assertRaises(ValueError):
            parse_snapshot('{"schema_version":1,"schema_version":1}', NS, 10)

    def test_duplicate_reorder_and_restart(self):
        state = HazardState()
        self.assertTrue(state.receive(json.dumps(snapshot(2)), NS, 10))
        self.assertFalse(state.receive(json.dumps(snapshot(2)), NS, 10))
        self.assertFalse(state.receive(json.dumps(snapshot(1)), NS, 10))
        self.assertTrue(state.receive(json.dumps(snapshot(1, "new")), NS, 10))
        self.assertFalse(state.receive(json.dumps(snapshot(3)), NS, 10))

    def test_loss_clock_jump_and_bad_traffic_fail_closed(self):
        state = HazardState()
        for n in range(3):
            doc = snapshot(n); doc["events"] = []
            state.receive(json.dumps(doc), NS, 10)
        self.assertTrue(state.poll(NS, 10)[0])
        self.assertFalse(state.receive("{}", NS + 490_000_000, 10.49))
        self.assertFalse(state.poll(NS + 510_000_000, 10.51)[0])
        self.assertFalse(state.poll(NS - 1_000_000_000, 10.52)[0])
        self.assertEqual(state.good_frames, 0)

    def test_urgent_inhibits_caution_repeats_escalation_bypasses(self):
        state = HazardState()
        for n in range(3):
            state.receive(json.dumps(snapshot(n, severity="caution")), NS, 10)
        permitted, alert = state.poll(NS, 10)
        self.assertTrue(permitted)
        self.assertEqual(alert.priority, 2)
        state.spoken_alert(alert, 10)
        state.receive(json.dumps(snapshot(4)), NS + 10_000_000, 10.01)
        permitted, urgent = state.poll(NS + 10_000_000, 10.01)
        self.assertFalse(permitted)
        self.assertEqual(urgent.priority, 0)
        state.spoken_alert(urgent, 10.01)
        self.assertNotEqual(state.poll(NS + 20_000_000, 10.02)[1].priority, 0)

    def test_output_failure_inhibits_even_with_live_sensing(self):
        state = HazardState()
        for n in range(3):
            doc = snapshot(n); doc["events"] = []
            state.receive(json.dumps(doc), NS, 10)
        self.assertFalse(state.poll(NS, 10, output_ready=False)[0])

    def test_delayed_snapshot_does_not_get_a_new_full_health_lease(self):
        state = HazardState()
        for n in range(3):
            doc = snapshot(n); doc["events"] = []
            state.receive(json.dumps(doc), NS + 400_000_000, 10.4)
        self.assertTrue(state.poll(NS + 400_000_000, 10.4)[0])
        self.assertFalse(state.poll(NS + 510_000_000, 10.51)[0])


class AudioTests(unittest.TestCase):
    def test_button_cues_do_not_mask_or_follow_hazard_speech(self):
        audio = Audio(start_output=False)
        audio.submit(np.ones(100, np.int16), priority=URGENT)
        done = audio.cue(np.full(200, 1000, np.int16))
        self.assertTrue((audio._render(100) == 1).all())
        self.assertTrue(done.is_set())
        self.assertFalse(audio._render(200).any())

    def test_network_open_returning_after_warning_cannot_create_late_speech(self):
        audio = Audio(start_output=False)
        speech = Speech(audio, key="test", voice_id="test")
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        def late_response(*_args, **_kwargs):
            audio.revoke(URGENT)
            return response
        with patch("companion.voice.speech.urlopen", side_effect=late_response):
            self.assertFalse(speech.say("This answer was cancelled by the warning."))
        response.read.assert_not_called()
        self.assertFalse(audio._playbacks)

    def test_generation_prevents_fallback_from_reviving_cancelled_answer(self):
        audio = Audio(start_output=False)
        generation = audio.generation(ANSWER)
        audio.revoke(URGENT)
        playback = audio.stream(ANSWER, generation=generation)
        self.assertTrue(playback.cancelled)
        self.assertFalse(playback.feed(np.ones(10, np.int16)))

    def test_expired_queued_warning_does_not_consume_cooldown(self):
        audio = Audio(start_output=False)
        renderer = Mock(); renderer.render_local.return_value = np.ones(100, np.int16)
        voice = HazardVoice(audio, renderer)
        companion, _, _ = build(audio=audio, hazards=voice)
        companion.camera.hazard_clock = Mock(return_value=NS)
        companion.camera.permit_guidance = Mock()
        companion.hazard_state = HazardState()
        companion.hazard_state.receive(json.dumps(snapshot()), NS, 10)
        with patch("companion.voice.__main__.time.monotonic", return_value=10):
            companion._poll_hazards()
        with patch("companion.voice.audio.time.monotonic", return_value=10.3):
            audio._render(20)
        self.assertTrue(voice.current.cancelled)
        companion.camera.hazard_clock.return_value = NS + 300_000_000
        companion.hazard_state.receive(json.dumps(snapshot(2, now=NS + 300_000_000)), NS + 300_000_000, 10.3)
        with patch("companion.voice.__main__.time.monotonic", return_value=10.3):
            companion._poll_hazards()
        self.assertFalse(voice.current.cancelled)
        self.assertFalse(companion.hazard_state.spoken)

    def test_expired_before_output_is_never_rendered(self):
        audio = Audio(start_output=False)
        with patch("companion.voice.audio.time.monotonic", return_value=10):
            clip = audio.submit(np.ones(20, np.int16), priority=URGENT, expires_at=9.9)
            self.assertFalse(audio._render(20).any())
            self.assertTrue(clip.cancelled)

    def test_preemption_rejects_late_cloud_audio(self):
        audio = Audio(start_output=False)
        speech = Mock(); speech.render_local.return_value = np.ones(100, np.int16)
        hazards = HazardVoice(audio, speech)
        answer = audio.stream(ANSWER); answer.feed(np.ones(100, np.int16))
        alert = Alert("test", "urgent:head:left", URGENT, 1000, 2)
        self.assertTrue(hazards.announce(alert, 10))
        self.assertFalse(answer.feed(np.ones(10, np.int16)))
        self.assertFalse(hazards.announce(Alert("fault", "unavailable", 1, 1000, 15), 10))

    def test_valid_snapshot_interrupts_every_voice_state(self):
        for mode in ("idle", "recording", "busy", "guardian"):
            with self.subTest(mode=mode):
                companion, audio, _ = build()
                companion.state = mode
                companion.guardian = Mock()
                companion.camera.hazard_clock = lambda: NS
                companion.camera.permit_guidance = Mock()
                audio.output_ready = lambda: True
                companion.hazards.phrase_ready = True
                companion.hazards.announce = Mock(return_value=True)
                companion.hazard_state = HazardState()
                companion.hazard_state.receive(json.dumps(snapshot()), NS, 10)
                old = companion.generation
                with patch("companion.voice.__main__.time.monotonic", return_value=10):
                    companion._poll_hazards()
                self.assertGreater(companion.generation, old)
                companion.camera.permit_guidance.assert_called_with(False)
                self.assertEqual(companion.state, "guardian" if mode == "guardian" else "idle")
                if mode == "guardian":
                    companion.guardian.on_hazard.assert_called_once()
                if mode == "recording":
                    companion._release(11)
                    self.assertTrue(all(lane.empty() for lane in companion.lanes.values()))


if __name__ == "__main__":
    unittest.main()
