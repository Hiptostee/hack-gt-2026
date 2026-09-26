"""Guidance codes must match the ESP32 packet flags and fail neutral."""
import time
import unittest

from companion.voice.tactile_link import GuidanceFeed, encode_packet, flags_for_state


class TactileLinkTests(unittest.TestCase):
    def test_direction_mapping_and_gate(self):
        for direction, flags in [(0, 1), (1, 2), (2, 4), (3, 2), (4, 4)]:
            self.assertEqual(flags_for_state({"active": True, "path_valid": True,
                                              "direction": direction}), flags)
        for state in (None, {}, {"active": False, "path_valid": True, "direction": 0},
                      {"active": True, "path_valid": False, "direction": 1},
                      {"active": True, "path_valid": True, "direction": None},
                      {"active": True, "path_valid": True, "direction": 9}):
            self.assertEqual(flags_for_state(state), 0)

    def test_stale_pi_state_is_neutral(self):
        feed = GuidanceFeed("http://127.0.0.1:8081")
        feed.state = {"active": True, "path_valid": True, "direction": 0}
        feed.updated_at = time.monotonic() - 2
        self.assertEqual(feed.flags(), 0)
        feed.updated_at = time.monotonic()
        self.assertEqual(feed.flags(), 1)

    def test_packet_matches_shared_esp32_format(self):
        self.assertEqual(encode_packet(200, 2), bytes.fromhex("a5 c8 02"))
        with self.assertRaises(ValueError):
            encode_packet(0, 3)
