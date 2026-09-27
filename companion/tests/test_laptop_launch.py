"""Transport-only startup must not require cloud keys or start a second voice UI."""
import unittest
from unittest.mock import Mock, patch

from scripts import laptop_launch


class LaptopLaunchTests(unittest.TestCase):
    def test_transport_only_starts_tunnel_hands_and_viewer(self):
        processes = []

        def launch(*args, **kwargs):
            process = Mock()
            process.poll.return_value = None
            processes.append(process)
            return process

        with patch("sys.argv", ["laptop_launch.py", "--no-voice"]), \
                patch.dict(laptop_launch.os.environ, {}, clear=True), \
                patch.object(laptop_launch, "port_open", return_value=False) as port, \
                patch.object(laptop_launch, "wait_for_bridge"), \
                patch.object(laptop_launch.subprocess, "Popen", side_effect=launch) as popen, \
                patch.object(laptop_launch.subprocess, "run"), \
                patch.object(laptop_launch, "stop_process"), \
                patch.object(laptop_launch.getpass, "getpass") as key, \
                patch.object(laptop_launch.webbrowser, "open") as browser, \
                patch.object(laptop_launch.time, "sleep", side_effect=KeyboardInterrupt):
            self.assertEqual(laptop_launch.main(), 0)
        key.assert_not_called()
        browser.assert_not_called()
        port.assert_called_once_with(8081)
        commands = [call.args[0] for call in popen.call_args_list]
        self.assertEqual(len(commands), 3)
        self.assertEqual(commands[0][0], "ssh")
        self.assertIn("companion.voice.tactile_link", commands[1])
        self.assertEqual(commands[2][:2], ["docker", "compose"])
