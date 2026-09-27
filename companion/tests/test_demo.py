"""Laptop integration: real HTTP handlers, stubbed cloud/hardware, no live messages."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from companion.demo.runtime import DemoRuntime
from companion.errors import AppError
from companion.voice.gemini import Gemini
from companion.voice.web_test import Handler


class LaptopDemo(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        s = self.server
        s.gemini = Mock(key='test', model='test')
        s.gemini.ask.return_value = dict(answer='A chair beside Room 101.', transcript='what is ahead',
                                        landmark='Room 101', device_action='none', target='', box_2d=None)
        s.guidance = Mock()
        s.ros_camera = Mock()
        s.ros_camera.capture_frame.return_value = dict(jpeg=b'jpeg', stamp='1790000000.000000042', width=640, height=480)
        s.el_key = s.el_voice = s.el_model = s.el_voice_source = ''
        s.fallback_image = None
        s.demo = DemoRuntime(s, watchdog=False)
        self.thread = threading.Thread(target=s.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.demo.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, body=None):
        req = Request(f'http://127.0.0.1:{self.server.server_port}'+path,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={'Content-Type':'application/json'})
        with urlopen(req, timeout=5) as response:
            return json.load(response)

    def found_chair(self):
        self.server.gemini.ask.return_value = dict(answer="A chair.", transcript="chair", landmark="",
            device_action="navigate_target", target="chair", box_2d=[100,200,600,800])
        return self.request('/find', {'text':'chair', 'generation':0})

    def test_find_highlights_without_starting_guidance(self):
        found = self.found_chair()
        self.assertEqual(found['workflow'], 'found')
        self.assertTrue(found['selection_id'])
        self.assertEqual(found['box_2d'], [100,200,600,800])
        self.assertTrue(self.server.gemini.ask.call_args.kwargs['locate_only'])
        self.server.guidance.go_to.assert_not_called()
        self.server.guidance.start.assert_not_called()

    def test_guide_recaptures_and_reidentifies_before_routing(self):
        found = self.found_chair()
        self.server.ros_camera.capture_frame.return_value = dict(jpeg=b'new-frame',
            stamp='1790000001.000000043', width=640, height=480)
        self.server.gemini.ask.return_value['box_2d'] = [300,400,700,900]
        self.server.guidance.go_to.return_value = 'Stationary route preview.'
        guided = self.request('/guide', {'selection_id':found['selection_id'], 'generation':0})
        self.assertEqual(self.server.ros_camera.capture_frame.call_count,2)
        self.assertEqual(self.server.gemini.ask.call_args.args[1],b'new-frame')
        self.server.guidance.go_to.assert_called_once_with('chair', [300,400,700,900],
            '1790000001.000000043',640,480)
        self.assertEqual(guided['answer'],'Stationary route preview.')
        self.assertIsNone(self.server.demo.selection)

    def test_missing_target_on_recheck_never_uses_old_box(self):
        found = self.found_chair()
        self.server.gemini.ask.return_value.update(box_2d=None,answer="I cannot see the chair now.")
        guided = self.request('/guide', {'selection_id':found['selection_id']})
        self.assertIn('cannot see',guided['answer'])
        self.server.guidance.go_to.assert_not_called()

    def test_find_no_match_clears_prior_selection(self):
        self.found_chair()
        self.server.gemini.ask.return_value.update(box_2d=None,answer='No bottle is visible.')
        result=self.request('/find', {'text':'bottle'})
        self.assertEqual(result['workflow'],'not_found')
        self.assertIsNone(self.server.demo.selection)
        self.server.guidance.go_to.assert_not_called()

    def test_expired_or_stopped_selection_cannot_guide(self):
        found=self.found_chair()
        self.server.demo.selection['expires_at']=time.monotonic()-1
        self.assertIn('again',self.request('/guide',{'selection_id':found['selection_id']})['error'])
        found=self.found_chair()
        self.request('/demo/control',{'action':'stop'})
        self.assertIn('again',self.request('/guide',{'selection_id':found['selection_id']})['error'])
        self.server.guidance.go_to.assert_not_called()

    def test_both_urls_serve_the_unified_dashboard(self):
        pages=[]
        for path in ('/','/demo'):
            with urlopen(f'http://127.0.0.1:{self.server.server_port}'+path) as response:
                pages.append(response.read())
        self.assertEqual(*pages)
        self.assertIn(b'Find an object & guide',pages[0])
        self.assertIn(b'Judge Telemetry',pages[0])
        self.assertIn(b'guardian-open',pages[0])
        with urlopen(f'http://127.0.0.1:{self.server.server_port}/dashboard-controls.js') as response:
            self.assertIn(b'submitOperator',response.read())

    def test_typed_question_and_camera_observation_reach_shared_session(self):
        reply = self.request('/ask', {'text':'Read the sign', 'generation':0})
        self.assertEqual(reply['transcript'], 'Read the sign')
        self.assertEqual(self.server.gemini.ask.call_args.kwargs['text'], 'Read the sign')
        self.assertEqual(self.server.demo.session.trail()[0]['label'], 'Room 101')
        self.assertEqual(self.request('/demo/control', {'action':'repeat'})['answer'], reply['answer'])
        self.assertIn('Room 101', self.request('/demo/control', {'action':'status'})['answer'])

    def test_stop_discards_cloud_reply_before_navigation_and_memory(self):
        entered, release = threading.Event(), threading.Event()
        def ask(*args, **kwargs):
            entered.set()
            release.wait(3)
            return dict(answer='Okay', transcript='guide me', landmark='chair', device_action='navigate_target',
                        target='chair', box_2d=[100,200,600,800])
        self.server.gemini.ask.side_effect = ask
        result = {}
        worker = threading.Thread(target=lambda: result.update(self.request('/ask', {'text':'Guide me', 'generation':0})))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            stopped = self.request('/demo/control', {'action':'stop'})
            self.assertEqual(stopped['generation'], 1)
        finally:
            release.set()
            worker.join(3)
        self.assertTrue(result['cancelled'])
        self.server.guidance.go_to.assert_not_called()
        self.assertEqual(self.server.demo.session.trail(), [])

    def test_old_browser_generation_cannot_begin_request(self):
        self.request('/demo/control', {'action':'stop'})
        reply = self.request('/ask', {'text':'go', 'generation':0})
        self.assertIn('cancelled', reply['error'])
        self.server.gemini.ask.assert_not_called()

    def test_scene_request_rejected_while_guardian_active(self):
        self.server.demo.guardian = SimpleNamespace(state='active', close=Mock())
        reply = self.request('/ask', {'text':'go'})
        self.assertIn('conversation', reply['error'])
        self.server.gemini.ask.assert_not_called()

    def test_missing_live_frame_does_not_use_still_or_remember_landmark(self):
        self.server.ros_camera.capture_frame.side_effect = AppError('No frame',503)
        self.server.fallback_image = b'old-image'
        self.request('/ask', {'text':'what is ahead'})
        self.assertIsNone(self.server.gemini.ask.call_args.args[1])
        self.assertEqual(self.server.demo.session.trail(), [])

    def test_missing_guardian_config_is_recoverable(self):
        with patch.dict(os.environ, {}, clear=True):
            result = self.request('/demo/control', {'action':'guardian_start'})
        self.assertIn('configure', result['error'])
        self.server.guidance.stop.assert_called()
        self.assertIn('answer',self.request('/ask', {'text':'hello'}))

    def test_debug_snapshot_does_not_extend_operator_lease(self):
        runtime = self.server.demo
        runtime.heartbeat_at = 12
        runtime.snapshot()
        self.assertEqual(runtime.heartbeat_at,12)
        runtime.snapshot(touch=True)
        self.assertNotEqual(runtime.heartbeat_at,12)

    def test_lost_browser_stops_guidance(self):
        runtime = self.server.demo
        runtime.heartbeat_at = time.monotonic()-4
        thread=threading.Thread(target=runtime._watch,daemon=True)
        thread.start()
        try:
            deadline=time.monotonic()+2
            while runtime.generation == 0 and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertGreater(runtime.generation,0)
            self.server.guidance.stop.assert_called()
        finally:
            runtime.closed.set()
            thread.join(2)

    def test_guardian_snapshot_uses_real_session_trail(self):
        from companion.guardian.session import GuardianController
        session = self.server.demo.session
        session.save_landmark('Room 101')
        controller=GuardianController(Mock(), Mock(), Mock(), Mock(), session, lambda:'ready', lambda:True, Mock())
        self.assertEqual(controller.snapshot()['trail_count'],1)
        self.assertEqual(controller.snapshot()['last_observation']['label'],'Room 101')

    def test_typed_gemini_request_uses_same_multimodal_payload(self):
        gemini=Gemini('test')
        with patch.object(gemini,'_send',return_value=dict(answer='A chair',device_action='none')) as send:
            gemini.ask(None,b'jpeg',[],text='Find the chair')
        parts=send.call_args.args[1]['contents'][-1]['parts']
        self.assertTrue(any('Find the chair' in p.get('text','') for p in parts))
        self.assertTrue(any('inlineData' in p for p in parts))
        self.assertFalse(any('Describe the important' in p.get('text','') for p in parts))


class Launcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=Path(__file__).resolve().parents[2]/'scripts/laptop_launch.py'
        spec=importlib.util.spec_from_file_location('laptop_launch',path)
        cls.launch=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.launch)

    def test_default_demo_does_not_require_docker(self):
        args=self.launch.parser_for_demo().parse_args([])
        self.assertFalse(args.viewer)
        self.assertFalse(args.standalone)

    def test_env_loading_is_literal_and_preserves_exported_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'.env'
            path.write_text('export A="two words"\nB=$(touch /tmp/never-run)\n')
            with self.assertRaises(ValueError):
                self.launch.load_env(path,{})
            path.write_text('A="two words"\nB=literal\n')
            env={'B':'already set'}
            self.launch.load_env(path,env)
            self.assertEqual(env,{'A':'two words','B':'already set'})
