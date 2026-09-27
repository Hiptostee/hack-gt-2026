"""The Pi HTTP bridge observes, rather than competes with, on-device voice."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class GuidanceOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.node = Mock()
        modules = {
            "rclpy": Mock(),
            "rclpy.node": SimpleNamespace(Node=Mock(return_value=self.node)),
            "rclpy.executors": SimpleNamespace(
                ExternalShutdownException=RuntimeError, SingleThreadedExecutor=Mock()),
            "std_msgs": Mock(),
            "std_msgs.msg": SimpleNamespace(Bool=SimpleNamespace, UInt8=SimpleNamespace),
        }
        spec = importlib.util.spec_from_file_location(
            "guidance_under_test", Path(__file__).parents[1] / "voice/guidance.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(self.module)
        self.clock = patch.object(self.module.time, "monotonic", return_value=100)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)

    def make_guidance(self, read_only):
        with patch.object(self.module.threading.Thread, "start"):
            return self.module.RosGuidance(read_only=read_only)

    def test_observer_never_publishes_and_expires_owner_heartbeat(self):
        guidance = self.make_guidance(True)
        self.node.create_publisher.assert_not_called()
        self.node.create_timer.assert_not_called()
        self.assertIn("/backpack/guidance_active", [
            call.args[1] for call in self.node.create_subscription.call_args_list])
        guidance._valid(SimpleNamespace(data=True))
        guidance._direction(SimpleNamespace(data=3))
        guidance._active(SimpleNamespace(data=True))
        self.assertEqual(guidance.snapshot(), {"active": True, "path_valid": True,
                                               "direction": 3})
        self.now.return_value = 100.61
        guidance._direction(SimpleNamespace(data=3))
        self.assertEqual(guidance.snapshot(), {"active": False, "path_valid": True,
                                               "direction": None})
        guidance.start()
        guidance.stop()
        guidance._publish()
        self.node.create_publisher.assert_not_called()

    def test_owner_stop_reaches_observer_and_requires_explicit_restart(self):
        owner = self.make_guidance(False)
        observer = self.make_guidance(True)
        owner.publisher.publish.side_effect = observer._active
        for guidance in (owner, observer):
            guidance._valid(SimpleNamespace(data=True))
            guidance._direction(SimpleNamespace(data=0))
        owner.start()
        self.assertEqual(observer.snapshot()["direction"], 0)
        owner.stop()
        self.assertFalse(observer.snapshot()["active"])
        observer._direction(SimpleNamespace(data=2))
        self.assertIsNone(observer.snapshot()["direction"])
        owner.start()
        self.assertEqual(observer.snapshot()["direction"], 2)

    def test_observer_neutralizes_stale_direction_and_path(self):
        guidance = self.make_guidance(True)
        guidance._valid(SimpleNamespace(data=True))
        guidance._direction(SimpleNamespace(data=1))
        self.now.return_value = 100.61
        guidance._active(SimpleNamespace(data=True))
        self.assertIsNone(guidance.snapshot()["direction"])
        self.now.return_value = 101.51
        guidance._active(SimpleNamespace(data=True))
        guidance._direction(SimpleNamespace(data=1))
        self.assertFalse(guidance.snapshot()["path_valid"])
        self.assertIsNone(guidance.snapshot()["direction"])
