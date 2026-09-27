"""One laptop operator session, shared by scene questions and Guardian."""
import os
import threading
import time
import uuid

from companion.errors import AppError
from companion.voice.session import Session
from companion.voice.camera import NoCamera


class DemoRuntime:
    def __init__(self, server, watchdog=True):
        self.server = server
        self.session = Session()
        self.guardian = None
        self.audio = None
        self.lock = threading.RLock()
        self.actions = threading.Lock()
        self.generation = 0
        self.busy = False
        self.selection = None
        self.heartbeat_at = 0.0
        self.closed = threading.Event()
        self.guardian_error = ""
        self.notice = ""
        self.notice_id = 0
        if watchdog:
            threading.Thread(target=self._watch, daemon=True).start()

    def begin(self, generation=None):
        with self.lock:
            if generation is not None and generation != self.generation:
                raise AppError("Request cancelled. Please ask again.", 409)
            if self.busy or self.guardian_active():
                raise AppError("Finish or stop the current conversation first.", 409)
            self.busy = True
            return self.generation

    def current(self, generation):
        with self.lock:
            return generation == self.generation and not self.closed.is_set()

    def select_target(self, generation, label):
        with self.lock:
            if not self.current(generation):
                raise AppError("Request cancelled.", 409)
            self.selection = {"id": uuid.uuid4().hex, "label": label,
                              "expires_at": time.monotonic() + 60}
            return self.selection["id"]

    def clear_selection(self):
        with self.lock:
            self.selection = None

    def consume_selection(self, selection_id):
        with self.lock:
            selected = self.selection
            if (not selected or selected["id"] != selection_id
                    or selected["expires_at"] <= time.monotonic()):
                self.selection = None
                raise AppError("Find the object again before requesting guidance.", 409)
            self.selection = None
            return selected["label"]

    def finish(self, generation):
        with self.lock:
            if generation == self.generation:
                self.busy = False

    def guardian_active(self):
        return self.guardian is not None and self.guardian.state != "closed"

    def apply(self, generation, fn):
        # Serialize only local actions; never hold this lock across cloud calls.
        with self.actions:
            if not self.current(generation):
                raise AppError("Request cancelled.", 409)
            return fn()

    def remember(self, generation, result, image, captured_at):
        with self.lock:
            if not self.current(generation):
                return
            self.session.set_image(image, captured_at)
            self.session.add_exchange(result.get("transcript"), result["answer"])
            if image and result.get("landmark"):
                self.session.save_landmark(result["landmark"], captured_at)

    def stop(self):
        with self.lock:
            self.generation += 1
            self.busy = False
            self.selection = None
        if self.guardian_active():
            self.guardian.close()
        if self.audio:
            self.audio.stop()
        # A target request already in flight is drained before the final stop.
        with self.actions:
            if self.server.guidance:
                self.server.guidance.stop()
        return "Stopped. Guidance is off."

    def status_text(self):
        camera = self.server.ros_camera
        view = camera.status() if camera else "No live depth camera connected"
        return (f"Beacon laptop demo. Camera: {view}. "
                "Use hold to talk or type a scene question. "
                "Movement guidance requires fresh hazard sensing and warning audio. "
                + self.session.describe_landmark())

    def _guardian_closed(self, reason):
        from companion.guardian.session import exit_line
        self.notice = exit_line(reason)
        self.notice_id += 1

    def open_guardian(self):
        self.stop()
        token = self.generation
        if self.guardian is None:
            if not (os.environ.get("ELEVENLABS_API_KEY") and os.environ.get("ELEVENLABS_AGENT_ID")):
                raise AppError("Guardian unavailable: configure ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID.", 503)
            try:
                from companion.guardian.session import GuardianController
                from companion.guardian.sms import SmsGate, FakeSender
                from companion.voice.audio import Audio, sd, np
                from companion.voice.speech import Speech
                import elevenlabs  # Check the optional SDK before opening a session.
                if sd is None or np is None:
                    raise RuntimeError("Install numpy, sounddevice and PortAudio")
                sd.check_input_settings(channels=1, dtype="int16", samplerate=16000)
                sd.check_output_settings(channels=1, dtype="int16", samplerate=22050)
                self.audio = Audio()
                speech = Speech(self.audio, key=self.server.el_key,
                                voice_id=self.server.el_voice, model_id=self.server.el_model)
                self.guardian = GuardianController(
                    self.audio, speech, self.server.ros_camera or NoCamera(),
                    self.server.gemini, self.session, self.status_text,
                    network_up=lambda: True, notify=self._guardian_closed,
                    api_key=self.server.el_key, agent_id=os.environ["ELEVENLABS_AGENT_ID"])
                # Demo never constructs a live SMS sender, even with Twilio env vars.
                self.guardian.sms = SmsGate(
                    FakeSender(), contact_name="Demo contact", contact_number="+15555550100",
                    user_name="Beacon demo", speak=self.guardian.speak_local,
                    update=self.guardian.update_agent)
            except Exception as error:
                self.guardian = None
                if self.audio:
                    self.audio.close()
                    self.audio = None
                self.guardian_error = str(error)
                raise AppError("Guardian audio setup failed: " + str(error), 503) from None
        with self.lock:
            if not self.current(token):
                raise AppError("Guardian opening cancelled.", 409)
            self.heartbeat_at = time.monotonic()
            self.guardian.open()
        return "Opening Guardian on the laptop microphone and speakers. Text messages are simulated."

    def control(self, action):
        if action == "stop":
            return self.stop()
        if action == "status":
            return self.status_text()
        if action == "repeat":
            return self.session.last_answer or "No answer to repeat yet."
        if action == "guardian_start":
            return self.open_guardian()
        if action == "guardian_end":
            return self.stop()
        if action in ("guardian_press", "guardian_release"):
            if not self.guardian or self.guardian.state != "active":
                raise AppError("Guardian is not ready yet.", 409)
            getattr(self.guardian, "press" if action.endswith("press") else "release")(time.monotonic())
            return ""
        raise AppError("Unknown demo control.", 400)

    def snapshot(self, touch=False):
        if touch:
            self.heartbeat_at = time.monotonic()
        with self.lock:
            selected = self.selection
            selection = ({"id": selected["id"], "label": selected["label"]}
                         if selected and selected["expires_at"] > time.monotonic() else None)
        return {"generation": self.generation, "busy": self.busy,
                "guardian": self.guardian.snapshot() if self.guardian else {"state": "closed"},
                "guardian_configured": bool(os.environ.get("ELEVENLABS_API_KEY") and os.environ.get("ELEVENLABS_AGENT_ID")),
                "guardian_error": self.guardian_error, "sms_mode": "simulated",
                "notice": self.notice, "notice_id": self.notice_id,
                "selection": selection,
                "trail": self.session.trail()}

    def _watch(self):
        while not self.closed.wait(0.5):
            if self.heartbeat_at and time.monotonic() - self.heartbeat_at > 3:
                self.heartbeat_at = 0
                self.stop()

    def close(self):
        self.closed.set()
        self.stop()
        if self.audio:
            self.audio.close()
