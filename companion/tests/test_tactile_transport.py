"""Exercise the real HTTP poller and UDP sender without a Pi or ESP32."""
import signal
import socket
import subprocess
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

from companion.voice.pi_bridge import Handler


class TactileTransportTests(unittest.TestCase):
    def test_http_state_reaches_udp_and_bridge_loss_sends_neutral(self):
        state = {"active": True, "path_valid": True, "direction": 3}
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.guidance = Mock()
        server.guidance.snapshot.side_effect = lambda: dict(state)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(0.1)
        # Both logical hands use one stand-in socket with an ephemeral port.
        # The sender still makes both unicast sends; no venue devices are used.
        code = ("from companion.voice import tactile_link as link; "
                "import sys; link.PORT=int(sys.argv[1]); "
                "link.run(sys.argv[2], '127.0.0.1', '127.0.0.1')")
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(receiver.getsockname()[1]),
             f"http://127.0.0.1:{server.server_port}"],
            cwd=Path(__file__).parents[2], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        def expect(flags, timeout=2):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    packet = receiver.recv(16)
                except socket.timeout:
                    continue
                self.assertEqual(len(packet), 3)
                self.assertEqual(packet[0], 0xA5)
                if packet[2] == flags:
                    return packet
            self.fail(f"No UDP flags {flags} within {timeout}s; sender status {process.poll()}")

        try:
            left = expect(2)
            duplicate = expect(2)
            self.assertEqual(left, duplicate, "Both hands must receive the same packet")
            state["direction"] = 4
            expect(4)
            state["path_valid"] = False
            expect(0)
            state["path_valid"] = True
            state["direction"] = 0
            expect(1)
            state["active"] = False
            expect(0)
            state["active"] = True
            expect(1)
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            expect(0)
            # A live sender must keep sending neutral after upstream disappears.
            for _ in range(4):
                self.assertEqual(receiver.recv(16)[2], 0)
            process.send_signal(signal.SIGINT)
            output, errors = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 0, errors.decode())
            self.assertIn(b"Tactile direction: neutral", output)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=3)
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            receiver.close()
