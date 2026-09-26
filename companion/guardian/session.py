"""Guardian session lifecycle (spec §2, §4, §6).

Driven from the companion's main loop (open, press, release, close, on_hazard)
and from ElevenLabs SDK threads (connected, transcripts, heartbeats, tool
calls, end of session). Every session gets an id; anything that arrives for an
older session is ignored so it can never speak or act.
"""
import os
import sys
import threading
import time

from companion.errors import AppError
from companion.guardian.audio import GuardianAudio
from companion.voice.audio import HELP

CANCEL_WINDOW = 2.0
CONNECT_TIMEOUT = 8.0
SCENE_DEADLINE = 8.0
# The server pings every few seconds; the SDK reports each as a latency sample.
HEARTBEAT_TIMEOUT = 15.0
REPLY_WORKING = 5.0
REPLY_GIVE_UP = 12.0
HAZARD_NOTE_EVERY = 30.0
MAX_TALK = 30.0
# Matches the companion's HOLD: shorter presses are taps.
TALK_AFTER = 0.6

EXIT_LINES = {
    "cancelled": "Cancelled.",
    "exit": "Guardian ended.",
    "ended": "Guardian ended.",
    "lost": "I lost the connection to guardian mode.",
    "unavailable": "Guardian isn't available right now.",
    "offline": "Guardian needs the network.",
}


def connect_elevenlabs(api_key, agent_id, audio_interface, tools, dynamic_variables,
                       on_transcript, on_heartbeat, on_end, on_agent_response=None):
    """Real connection. Raises if the signed URL cannot be fetched."""
    from elevenlabs.client import ElevenLabs
    from elevenlabs.conversational_ai.conversation import (
        ClientTools, Conversation, ConversationInitiationData)

    client_tools = ClientTools()
    for name, handler in tools.items():
        client_tools.register(name, handler)
    conversation = Conversation(
        ElevenLabs(api_key=api_key), agent_id, requires_auth=True,
        audio_interface=audio_interface, client_tools=client_tools,
        config=ConversationInitiationData(dynamic_variables=dynamic_variables),
        callback_user_transcript=on_transcript,
        callback_agent_response=on_agent_response,
        callback_latency_measurement=lambda _ms: on_heartbeat(),
        callback_end_session=on_end)
    conversation.start_session()
    return conversation


def from_env(audio, speech, camera, gemini, session, status, network_up, notify):
    """Controller configured from the environment (spec §3)."""
    from companion.guardian.sms import FakeSender, SmsGate

    controller = GuardianController(
        audio, speech, camera, gemini, session, status, network_up, notify,
        api_key=os.environ.get("ELEVENLABS_API_KEY", ""),
        agent_id=os.environ.get("ELEVENLABS_AGENT_ID", ""))
    mode = os.environ.get("GUARDIAN_SMS", "").lower()
    sender = FakeSender() if mode == "fake" else None
    if mode and mode != "fake":
        print(f"GUARDIAN_SMS={mode!r}: only 'fake' is implemented; texting disabled.",
              file=sys.stderr)
    controller.sms = SmsGate(
        sender,
        contact_name=os.environ.get("GUARDIAN_CONTACT_NAME", ""),
        contact_number=os.environ.get("GUARDIAN_CONTACT_NUMBER", ""),
        user_name=os.environ.get("GUARDIAN_USER_NAME", ""),
        speak=controller.speak_local,
        update=controller.update_agent)
    return controller


class GuardianController:
    def __init__(self, audio, speech, camera, gemini, session, status, network_up, notify,
                 sms=None, api_key="", agent_id="", connect=connect_elevenlabs,
                 cancel_window=CANCEL_WINDOW, connect_timeout=CONNECT_TIMEOUT):
        self.audio = audio
        self.speech = speech
        self.camera = camera
        self.gemini = gemini
        self.session = session
        self.status = status            # () -> local status text
        self.network_up = network_up
        self.notify = notify            # notify(reason): session closed, back to normal
        self.sms = sms
        self.api_key = api_key
        self.agent_id = agent_id
        self.connect = connect
        self.cancel_window = cancel_window
        self.connect_timeout = connect_timeout
        self.lock = threading.Lock()
        self.state = "closed"
        self.session_id = 0
        self.conversation = None
        self.voice = None
        self.connected = threading.Event()
        self.cancelled = threading.Event()
        self.talk_timer = None
        self.talk_started_at = None
        self.last_turn_started_at = None
        self.last_heartbeat = None
        self.last_hazard_note = None
        self.close_reason = None
        self.tools_running = 0
        self.last_activity = None
        self.last_reply_text_at = None

    @property
    def configured(self):
        return bool(self.api_key and self.agent_id)

    def describe(self):
        texting = "fake texting" if self.sms and self.sms.available else "no texting"
        return f"agent {self.agent_id}, {texting}"

    # ---- entry -----------------------------------------------------------

    def open(self):
        """Main loop. Returns immediately; the opening runs on its own thread."""
        with self.lock:
            self.session_id += 1
            sid = self.session_id
            self.state = "opening"
            self.close_reason = None
            self.connected = threading.Event()
            self.cancelled = threading.Event()
            self.last_heartbeat = None
            self.last_turn_started_at = None
            self.voice = GuardianAudio(self.audio, on_started=self.connected.set)
        threading.Thread(target=self._open, args=(sid,), daemon=True).start()

    def _open(self, sid):
        if not self.configured or not self.network_up():
            self._finish(sid, "offline" if self.configured else "unavailable")
            return
        self.audio.earcon("guardian_on")
        threading.Thread(target=self._connect, args=(sid,), daemon=True).start()
        started = time.monotonic()
        self.speech.say("Opening guardian mode. Tap to cancel.", local=True, priority=HELP)
        if self.cancelled.wait(self.cancel_window):
            return
        if not self.connected.wait(max(0.0, self.connect_timeout - (time.monotonic() - started))):
            print("Guardian: no connection within the timeout", file=sys.stderr)
            self._close(sid, "unavailable")
            return
        with self.lock:
            if sid != self.session_id or self.state != "opening":
                return
            self.state = "active"
        print("Guardian: active", file=sys.stderr, flush=True)
        self.voice.enable()
        threading.Thread(target=self._watch, args=(sid,), daemon=True).start()

    def _connect(self, sid):
        variables = {
            "last_observation": self.session.observation_text(),
            "status": self.status(),
            # The agent's first message names the contact, so never send it empty.
            "contact_name": self.sms.contact_name if self.sms else "your contact",
            "sms_available": "yes" if self.sms and self.sms.available else "no",
            "time": time.strftime("%I:%M %p").lstrip("0"),
        }
        try:
            conversation = self.connect(
                self.api_key, self.agent_id, self.voice, self._tools(sid), variables,
                on_transcript=lambda text: self._on_transcript(sid, text),
                on_heartbeat=lambda: self._on_heartbeat(sid),
                on_end=lambda: self._on_end(sid),
                on_agent_response=lambda _text: self._on_agent_response(sid))
        except Exception as error:
            print(f"Guardian: could not connect ({error!r})", file=sys.stderr)
            if self._current(sid) and self.state == "opening":
                self._close(sid, "unavailable")
            return
        with self.lock:
            stale = sid != self.session_id or self.state in ("closing", "closed")
            if not stale:
                self.conversation = conversation
        if stale:
            conversation.end_session()

    # ---- gestures (main loop) --------------------------------------------

    def press(self, _at):
        if self.state == "opening":
            self.cancel()
            return
        if self.state != "active":
            return
        if self.talk_timer is not None:
            self.talk_timer.cancel()
        self.voice.press()
        self.talk_started_at = time.monotonic()
        self.talk_timer = threading.Timer(TALK_AFTER, self._talk_due, args=(self.session_id,))
        self.talk_timer.start()

    def _talk_due(self, sid):
        if self._current(sid) and self.state == "active":
            self.voice.talk()
            self.audio.earcon("listening", wait=False)
            self.talk_timer = threading.Timer(MAX_TALK - TALK_AFTER, self._talk_cap,
                                              args=(sid,))
            self.talk_timer.start()

    def _talk_cap(self, sid):
        if self._current(sid):
            self.voice.end_talk()
            self.audio.earcon("working", wait=False)

    def release(self, _at):
        """Returns "turn", "tap" or None."""
        if self.state != "active":
            return None
        if self.talk_timer is not None:
            self.talk_timer.cancel()
            self.talk_timer = None
        if self.voice.release():
            self.last_turn_started_at = self.talk_started_at
            threading.Thread(target=self._await_reply,
                             args=(self.session_id, time.monotonic()), daemon=True).start()
            return "turn"
        return "tap"

    def cancel(self):
        """Tap during the cancel window."""
        self.cancelled.set()
        self._close(self.session_id, "cancelled")

    def close(self, reason="exit"):
        """Double tap, or the companion shutting down."""
        self._close(self.session_id, reason)

    def on_hazard(self, warning):
        """The companion already revoked Guardian speech and queued the warning."""
        if self.state not in ("opening", "active"):
            return
        self.voice.mute_until_turn()
        if self.sms is not None:
            self.sms.cancel("a hazard warning played")
        sid = self.session_id

        def note():
            if warning is not None:
                warning.wait(10.0)
            now = time.monotonic()
            if (self._current(sid) and self.state == "active"
                    and (self.last_hazard_note is None
                         or now - self.last_hazard_note >= HAZARD_NOTE_EVERY)):
                self.last_hazard_note = now
                self._update(sid, "A hazard warning just played on the device. "
                                  "When you next speak, ask once if the user is okay.")
        threading.Thread(target=note, daemon=True).start()

    # ---- closing ---------------------------------------------------------

    def _close(self, sid, reason):
        with self.lock:
            if sid != self.session_id or self.state in ("closing", "closed"):
                return
            self.state = "closing"
            self.close_reason = reason
            conversation, self.conversation = self.conversation, None
        self.cancelled.set()  # Wakes an opening still inside its cancel window.
        if self.talk_timer is not None:
            self.talk_timer.cancel()
        if self.voice is not None:
            self.voice.stop()
        if self.sms is not None:
            self.sms.cancel("guardian closed")
        if conversation is not None:
            threading.Thread(target=conversation.end_session, daemon=True).start()
        self._finish(sid, reason)

    def _finish(self, sid, reason):
        with self.lock:
            if sid != self.session_id or self.state == "closed":
                return
            self.state = "closed"
            self.close_reason = reason
        if self.voice is not None:
            self.voice.stop()
        sent = self.voice.sent_seconds if self.voice is not None else 0.0
        print(f"Guardian: closed ({reason}); {sent:.1f}s of mic audio sent",
              file=sys.stderr, flush=True)
        self.notify(reason)

    def _on_end(self, sid):
        """SDK: the socket closed. Ours, the server's, or the network's."""
        if not self._current(sid) or self.state in ("closing", "closed"):
            return
        self._close(sid, "ended" if self.network_up() else "lost")

    def shutdown(self):
        self.close("exit")

    # ---- SDK callbacks ---------------------------------------------------

    def _current(self, sid):
        return sid == self.session_id

    def _on_heartbeat(self, sid):
        if self._current(sid):
            self.last_heartbeat = time.monotonic()

    def _on_transcript(self, sid, text):
        if not self._current(sid):
            return
        self.last_activity = time.monotonic()
        if self.state == "active" and self.sms is not None:
            self.sms.on_transcript(text, self.last_turn_started_at)

    def _on_agent_response(self, sid):
        if self._current(sid):
            self.last_reply_text_at = time.monotonic()

    def update_agent(self, text):
        """Tell the current session's agent something without prompting it to speak."""
        self._update(self.session_id, text)

    def _update(self, sid, text):
        conversation = self.conversation
        if not self._current(sid) or conversation is None:
            return
        try:
            conversation.send_contextual_update(text)
        except Exception as error:
            print(f"Guardian: contextual update failed ({error!r})", file=sys.stderr)

    def _watch(self, sid):
        """A socket can stay open while the connection is dead; heartbeats tell."""
        while self._current(sid) and self.state == "active":
            time.sleep(1.0)
            beat = self.last_heartbeat
            if beat is not None and time.monotonic() - beat > HEARTBEAT_TIMEOUT:
                print("Guardian: heartbeat lost", file=sys.stderr)
                self._close(sid, "lost")
                return

    def _await_reply(self, sid, released_at):
        turn_started = self.last_turn_started_at
        waiting_since = released_at

        def replied():
            audio_at = self.voice.last_output_at if self.voice else None
            text_at = self.last_reply_text_at
            return any(at is not None and at > released_at for at in (audio_at, text_at))

        for wait, action in ((REPLY_WORKING, "working"), (REPLY_GIVE_UP, "give_up")):
            while time.monotonic() - waiting_since < wait:
                time.sleep(0.25)
                if not self._current(sid) or self.state != "active" or replied():
                    return
                if self.last_turn_started_at != turn_started:
                    return  # A newer turn owns the wait now.
                heard = self.last_activity is not None and self.last_activity > waiting_since
                if heard or self.tools_running or self.audio.local_sound_playing():
                    # The server heard the turn, a tool is running, or the device
                    # itself is talking: the reply is in progress.
                    waiting_since = time.monotonic()
            if action == "working":
                self.audio.earcon("working", wait=False)
            else:
                self.speech.say("Guardian isn't responding. Double tap to leave.",
                                local=True, priority=HELP)

    # ---- tools -----------------------------------------------------------

    def _tools(self, sid):
        def guarded(handler):
            def run(parameters):
                if not self._current(sid) or self.state != "active":
                    return "Guardian mode is not active. Do not use tools."
                with self.lock:
                    self.tools_running += 1
                try:
                    result = handler(parameters or {})
                finally:
                    with self.lock:
                        self.tools_running -= 1
                if not self._current(sid) or self.state != "active":
                    return "Guardian mode ended before this finished."
                return result
            return run

        return {"get_status": guarded(lambda _p: self.status()),
                "describe_scene": guarded(lambda _p: self.describe_scene()),
                "prepare_sms": guarded(self._prepare_sms)}

    def describe_scene(self):
        try:
            image, captured_at = self.camera.capture()
        except AppError:
            return "The camera isn't sending images right now, so I can't describe what's around you."
        box = {}

        def ask():
            try:
                box["answer"] = self.gemini.ask(None, image, [])["answer"]
            except Exception as error:
                box["error"] = error

        worker = threading.Thread(target=ask, daemon=True)
        worker.start()
        worker.join(SCENE_DEADLINE)
        if "answer" not in box:
            print(f"Guardian: scene description failed ({box.get('error', 'timeout')!r})",
                  file=sys.stderr)
            return "The image service didn't respond, so I can't describe the scene right now."
        when = time.strftime("%I:%M %p", time.localtime(captured_at or time.time())).lstrip("0")
        return f"As of {when}, the camera shows: {box['answer']}"

    def _prepare_sms(self, parameters):
        if self.sms is None:
            return "Texting isn't available on this device right now. Tell the user."
        return self.sms.prepare(parameters.get("note", ""), self.session.observation_text())

    def speak_local(self, text):
        """SMS previews and results: the device's own voice, above the agent."""
        return self.speech.say(text, local=True, priority=HELP)
