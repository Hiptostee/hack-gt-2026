"""Guidance codes must match the ESP32 packet flags and fail neutral."""
import socket
import threading
import time
import unittest
from unittest import mock

from companion.voice import tactile_link
from companion.voice.tactile_link import GuidanceFeed, encode_packet, flags_for_state, target_hosts


class TactileLinkTests(unittest.TestCase):
    def test_direction_mapping_and_gate(self):
        for direction, flags in [(0, 1), (1, 4), (2, 2), (3, 0)]:
            self.assertEqual(flags_for_state({"active": True, "path_valid": True,
                                              "direction": direction}), flags)
        for state in (None, {}, {"active": False, "path_valid": True, "direction": 0},
                      {"active": True, "path_valid": False, "direction": 1},
                      {"active": True, "path_valid": True, "direction": None},
                      {"active": True, "path_valid": True, "direction": 4},
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

    def test_single_esp32_target_does_not_require_second_hand(self):
        self.assertEqual(target_hosts("10.89.33.186", "left.local", "right.local"),
                         {"esp32": "10.89.33.186"})
        self.assertEqual(target_hosts(None, "left.local", "right.local"),
                         {"left": "left.local", "right": "right.local"})

    def test_single_esp32_receives_direction_packet(self):
        class FakeFeed:
            def __init__(self, _url):
                self.stop = threading.Event()

            def poll(self):
                self.stop.wait()

            def flags(self):
                return 2

        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(1)
        stop = threading.Event()
        with mock.patch.object(tactile_link, "PORT", receiver.getsockname()[1]), \
                mock.patch.object(tactile_link, "GuidanceFeed", FakeFeed):
            sender = threading.Thread(target=tactile_link.run,
                                      args=("http://127.0.0.1:8081", "unused-left", "unused-right"),
                                      kwargs={"single": "127.0.0.1", "stop_event": stop})
            sender.start()
            try:
                packet, _address = receiver.recvfrom(32)
                self.assertEqual(packet, bytes.fromhex("a5 00 02"))
            finally:
                stop.set()
                sender.join(timeout=2)
                receiver.close()
            self.assertFalse(sender.is_alive())
