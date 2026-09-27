"""Run with: python3 -m companion.voice

All state transitions happen on the main event loop. Worker threads only do
slow work and report completion back as an event, so there is one place where
state changes and no lock around it.
"""
import argparse
import glob
import os
import platform
import queue
import socket
import sys
import threading
import time

from companion.errors import AppError
from companion.i18n import get as i18n, DEFAULT as I18N_DEFAULT
from companion.voice.audio import HELP, INFO, Audio
from companion.voice.button import open_button
from companion.voice.camera import open_camera
from companion.voice.gemini import Gemini, DEFAULT_MODEL
from companion.voice.hazards import HazardVoice
from companion.voice.hazard_state import HazardState
from companion.voice.session import Session
from companion.voice.speech import Speech

SHORT_PRESS = 0.15
HOLD = 0.6
# A tap chain resolves this long after its last tap: one tap repeats, two open
# help, three open guardian. Help therefore waits one window for a third tap.
TAP_WINDOW = 0.5
MAX_RECORD = 30
WARN_RECORD = 25
MIN_SPEECH = 0.25
# int16 peak below this is treated as nothing captured. Tune on the real mic:
# a false "I did not hear anything" costs the user a whole repeated question,
# so err low. A silent capture on macOS usually means mic permission, not quiet.
MIN_PEAK = 200
WORKING_AFTER = 5
WORKING_EVERY = 4

# Which worker runs each task. Help has its own so it never waits behind a
# cloud request; spoken replies never wait behind Gemini.
LANES = {"utterance": "cloud", "repeat": "speech", "say": "speech",
         "help": "local", "guardian": "local", "guardian_exit": "local"}


def network_up():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=1.5).close()
        return True
    except OSError:
        return False


def battery_text(lang=None):
    for path in glob.glob("/sys/class/power_supply/*/capacity"):
        try:
            with open(path) as handle:
                return i18n("battery_level", lang).format(percent=handle.read().strip())
        except OSError:
            continue
    return i18n("battery_unknown", lang)


def status_text(camera, gemini, session, landmark=True, lang=None):
    """Local device status. Shared by double-tap help and Guardian, which adds
    its own observation trail instead of the single landmark."""
    parts = [
        i18n("network_up", lang) if network_up() else i18n("network_down", lang),
        i18n("camera_status", lang).format(status=camera.status()),
        i18n("gemini_configured", lang) if gemini.key else i18n("gemini_missing", lang),
        battery_text(lang),
    ]
    if landmark:
        parts.append(session.describe_landmark())
    return " ".join(parts)


class Companion:
    def __init__(self, audio, speech, gemini, camera, session, button, events,
                 guidance=None, hazards=None, guardian=None, hazard_state=None,
                 lang=None):
        self.audio = audio
        self.guardian = guardian
        self.speech = speech
        self.gemini = gemini
        self.camera = camera
        self.session = session
        self.button = button
        self.events = events
        self.guidance = guidance
        self.hazards = hazards
        self.hazard_state = hazard_state
        self.lang = lang
        self.pending_hazard = None
        self.lanes = {name: queue.Queue() for name in set(LANES.values())}
        self.state = "idle"
        self.generation = 0
        self.press_at = 0.0
        self.press_cancels = False
        self.taps = 0
        self.tap_timer = None
        self.cap_timers = []
        self.running = True

    # ---- main loop -------------------------------------------------------

    def run(self):
        for lane in self.lanes.values():
            threading.Thread(target=self._worker, args=(lane,), daemon=True).start()
        self.speech.say(i18n("ready", self.lang), priority=INFO)
        while self.running:
            self._poll_hazards()
            try:
                kind, at = self.events.get(timeout=0.05)
            except queue.Empty:
                continue
            if kind == "quit":
                break
            handler = {"press": self._press, "release": self._release,
                       "taps_due": self._taps_due, "done": self._done,
                       "hazard": self._hazard, "guardian_request": self._guardian_request,
                       "guardian_closed": self._guardian_closed}.get(kind)
            if handler:
                handler(at)
        self.shutdown()

    def _poll_hazards(self):
        if self.hazard_state is None:
            return
        now = time.monotonic()
        if self.pending_hazard is not None:
            pending, playback = self.pending_hazard
            if playback.started:
                self.hazard_state.spoken_alert(pending, now)
                self.pending_hazard = None
            elif playback.cancelled:
                # An expired queued clip was never heard: do not impose a
                # cooldown before trying the next fresh observation.
                self.pending_hazard = None
        output_ready = self.audio.output_ready()
        permitted, alert = self.hazard_state.poll(self.camera.hazard_clock(), now,
            output_ready=output_ready and self.hazards.phrase_ready)
        # Published by the event loop, not an independent timer: a hung/crashed
        # companion or dead output device cannot keep the movement gate alive.
        self.camera.permit_guidance(permitted)
        if not output_ready:
            print("FATAL: audio output lost; guidance inhibited. Stopping supervised demo.", file=sys.stderr)
            self.running = False
            return
        if alert is None:
            return
        if alert.phrase == "alive" and (self.state != "idle" or self.audio.local_sound_playing()):
            return
        if not self.hazards.announce(alert, now):
            return
        if self.hazards.current is not None:
            self.pending_hazard = (alert, self.hazards.current)
        if alert.priority > 2:
            return
        self.generation += 1
        self._cancel_taps()
        if self.state == "recording":
            self._disarm_cap()
            self.audio.record_stop()
        self.audio.stop()
        if self.state == "guardian":
            self.guardian.on_hazard(self.hazards.current)
        else:
            self.state = "idle"

    def _hazard(self, at):
        """Interrupts every state (hazard spec §7). The warning is queued, not
        awaited, so the loop stays responsive while it plays."""
        if self.hazards is not None and not self.hazards.due(at):
            return
        # Bump first so no worker speaks an answer that was already on its way.
        self.generation += 1
        self._cancel_taps()
        if self.state == "recording":
            # Discard the question; its release finds state idle and is ignored.
            self._disarm_cap()
            self.audio.record_stop()
        self.audio.stop()
        if self.state != "guardian":
            self.state = "idle"
        warning = None
        if self.hazards is not None:
            self.hazards.warn(at)
            warning = self.hazards.current
        else:
            self.audio.earcon("stopped", wait=False)
        if self.state == "guardian":
            self.guardian.on_hazard(warning)
        print("Hazard warning: companion speech interrupted", file=sys.stderr)

    # ---- guardian --------------------------------------------------------

    def _enter_guardian(self):
        if self.guardian is None:
            self._dispatch("guardian", None)
            return
        self.generation += 1
        self._cancel_taps()
        self.audio.stop()
        self.state = "guardian"
        if self.guidance:
            self.guidance.stop()
        self.guardian.open()

    def _guardian_request(self, _at):
        """Gemini returned device_action "guardian" for the current question."""
        if self.state in ("idle", "busy"):
            self._enter_guardian()

    def _guardian_closed(self, reason):
        if self.state != "guardian":
            return
        self.generation += 1
        self._cancel_taps()
        self.audio.stop()
        self.state = "idle"
        self._dispatch("guardian_exit", reason)

    def _press(self, at):
        if self.state == "recording":
            return
        if self.state == "guardian":
            if self.tap_timer is not None:
                self.tap_timer.cancel()
                self.tap_timer = None
            self.press_at = at
            self.guardian.press(at)
            return
        # A press only pauses a pending tap chain; its release decides.
        if self.tap_timer is not None:
            self.tap_timer.cancel()
            self.tap_timer = None
        # Bump first: workers check the generation before they speak, so any
        # answer already on its way is invalidated before playback is cut.
        self.generation += 1
        self.press_cancels = self.state == "busy"
        if self.press_cancels:
            self.speech.stop()
            self.audio.earcon("stopped")
        self.press_at = at
        self.state = "recording"
        self.audio.earcon("listening")
        self.audio.record_start()
        self._arm_cap()

    def _release(self, at):
        if self.state == "guardian":
            if self.guardian.release(at) == "tap" and at - self.press_at >= SHORT_PRESS:
                self.taps += 1
                self._restart_taps()
            return
        if self.state != "recording":
            return
        self._disarm_cap()
        wav, seconds, peak = self.audio.record_stop()
        duration = at - self.press_at
        self.state = "idle"
        if duration < HOLD and self.press_cancels:
            return  # A tap while thinking or speaking only cancels.
        if duration < SHORT_PRESS:
            self.audio.earcon("too_brief")
            if self.taps:
                self._restart_taps()
        elif duration < HOLD:
            self.taps += 1
            self._restart_taps()
        else:
            self.taps = 0
            if seconds < MIN_SPEECH or not wav or peak < MIN_PEAK:
                print(f"nothing captured (peak {peak}, {seconds:.1f}s)", file=sys.stderr)
                self._dispatch("say", i18n("no_audio", self.lang))
            else:
                self._dispatch("utterance", wav)

    def _restart_taps(self):
        if self.tap_timer is not None:
            self.tap_timer.cancel()
        self.tap_timer = threading.Timer(
            TAP_WINDOW, lambda: self.events.put(("taps_due", time.monotonic())))
        self.tap_timer.start()

    def _cancel_taps(self):
        if self.tap_timer is not None:
            self.tap_timer.cancel()
            self.tap_timer = None
        self.taps = 0

    def _taps_due(self, _at):
        self.tap_timer = None
        if self.state == "recording":
            return  # Fired just as another press began; that release resolves the chain.
        taps, self.taps = self.taps, 0
        if self.state == "guardian":
            if taps >= 2:
                self.guardian.close("exit")  # One tap already stopped the agent at press.
            return
        if self.state != "idle" or taps == 0:
            return
        if taps >= 3:
            self._enter_guardian()
        else:
            self._dispatch({1: "repeat", 2: "help"}[taps], None)

    def _done(self, generation):
        if generation == self.generation and self.state == "busy":
            self.state = "idle"

    def _dispatch(self, kind, payload):
        self.state = "busy"
        self.lanes[LANES[kind]].put((kind, self.generation, payload))

    # ---- recording cap ---------------------------------------------------

    def _arm_cap(self):
        warn = threading.Timer(WARN_RECORD, lambda: self.audio.earcon("working"))
        stop = threading.Timer(
            MAX_RECORD,
            lambda: self.events.put(("release", self.press_at + MAX_RECORD)))
        self.cap_timers = [warn, stop]
        for timer in self.cap_timers:
            timer.start()

    def _disarm_cap(self):
        for timer in self.cap_timers:
            timer.cancel()
        self.cap_timers = []

    # ---- workers ---------------------------------------------------------

    def _worker(self, lane):
        while True:
            task = lane.get()
            if task is None:
                return
            kind, generation, payload = task
            try:
                if generation != self.generation:
                    continue  # Superseded before it started; coalesces repeated help.
                self._run(kind, generation, payload)
            except AppError as error:
                self.audio.earcon("error")
                self._speak(str(error), generation, local=True)
            except Exception as error:  # A crash here must never kill the device.
                print(f"Unexpected failure: {error!r}", file=sys.stderr)
                self.audio.earcon("error")
                self._speak(i18n("something_wrong", self.lang), generation, local=True)
            finally:
                self.events.put(("done", generation))

    def _run(self, kind, generation, payload):
        if kind == "utterance":
            self._utterance(payload, generation)
        elif kind == "repeat":
            self._speak(self.session.last_answer
                        or i18n("nothing_to_repeat", self.lang), generation)
        elif kind == "help":
            self._help(generation)
        elif kind == "guardian":
            self._guardian(generation)
        elif kind == "guardian_exit":
            self._guardian_exit(payload, generation)
        elif kind == "say":
            self._speak(payload, generation)

    def _utterance(self, wav, generation):
        image = None
        captured_at = None
        try:
            image, captured_at = self.camera.capture()
            self.session.set_image(image, captured_at)
        except AppError as error:
            # Never answer from a stale frame; let the model say it cannot see.
            print(f"Camera unavailable: {error}", file=sys.stderr)
        self.audio.earcon("thinking")
        ticker = self._ticker(generation)
        try:
            result = self.gemini.ask(wav, image, self.session.history, stream=True)
        finally:
            ticker.set()
        if generation != self.generation:
            return
        previous = self.session.last_answer
        if result["transcript"]:
            print(f"heard: {result['transcript']}", flush=True)
        print(f"answer: {result['answer']}", flush=True)
        self.session.add_exchange(result["transcript"], result["answer"])
        self.session.save_landmark(result["landmark"], captured_at)
        self._act(result, previous, generation)

    def _act(self, result, previous, generation):
        action = result["device_action"]
        if action == "navigate_target":
            if result.get("box_2d") is None:
                self._speak(result["answer"], generation)
            else:
                self._speak(self._go_to(result), generation, local=True)
            return
        if action == "navigate_backpack":
            if self.guidance is None:
                self._speak(i18n("guidance_unavailable", self.lang), generation, local=True)
            else:
                self._speak(self.guidance.start(), generation, local=True)
            return
        if action == "stop_navigation":
            if self.guidance is not None:
                self.guidance.stop()
            self._speak(i18n("guidance_stopped", self.lang), generation, local=True)
            return
        if action == "stop":
            return
        if action == "repeat":
            self._speak(previous or result["answer"], generation)
            return
        if action == "help":
            self._help(generation)
            return
        if action == "guardian":
            if self.guardian is None:
                self._guardian(generation)
            elif generation == self.generation:
                self.events.put(("guardian_request", time.monotonic()))
            return
        if action == "louder":
            self.audio.gain = min(2.5, self.audio.gain + 0.3)
        elif action == "quieter":
            self.audio.gain = max(0.3, self.audio.gain - 0.3)
        self._speak(result["answer"], generation)

    def _go_to(self, result):
        if self.guidance is None:
            return "Guidance is unavailable here."
        frame_info = getattr(self.camera, "frame_info", None)
        frame = frame_info(self.session.image) if frame_info else None
        if frame is None:
            return "Guidance needs the live camera."
        return self.guidance.go_to(result["target"] or "object", result["box_2d"],
                                   frame["stamp"], frame["width"], frame["height"])

    def _help(self, generation):
        self._speak(status_text(self.camera, self.gemini, self.session, lang=self.lang),
                    generation, local=True, priority=HELP)

    def _guardian(self, generation):
        """Guardian was asked for but is not configured on this device."""
        self._speak(i18n("guardian_not_configured", self.lang), generation,
                    local=True, priority=HELP)

    def _guardian_exit(self, reason, generation):
        """Every way out of Guardian ends in the device's own voice (spec §2)."""
        from companion.guardian.session import exit_line

        if reason != "offline":
            self.audio.earcon("guardian_off")
        if reason == "cancelled":
            self._speak(exit_line(reason, self.lang), generation, local=True, priority=HELP)
            return
        status = status_text(self.camera, self.gemini, self.session, lang=self.lang)
        if reason == "offline":
            text = f"{status} {exit_line(reason, self.lang)}"
        else:
            text = f"{exit_line(reason, self.lang)} {status}"
        self._speak(text, generation, local=True, priority=HELP)

    def _speak(self, text, generation, local=False, priority=None):
        if generation != self.generation:
            return
        if priority is None:
            self.speech.say(text, local=local)
        else:
            self.speech.say(text, local=local, priority=priority)

    def _ticker(self, generation):
        done = threading.Event()

        def tick():
            if done.wait(WORKING_AFTER):
                return
            while not done.wait(WORKING_EVERY):
                if generation != self.generation:
                    return
                self.audio.earcon("working")

        threading.Thread(target=tick, daemon=True).start()
        return done

    def shutdown(self):
        self.running = False
        if self.hazard_state is not None:
            self.camera.permit_guidance(False)
        self._disarm_cap()
        self._cancel_taps()
        if self.guardian is not None:
            self.guardian.shutdown()
        self.speech.stop()
        for lane in self.lanes.values():
            lane.put(None)
        if self.button is not None:
            self.button.close()
        if self.guidance is not None:
            self.guidance.close()
        self.camera.close()
        close = getattr(self.audio, "close", None)
        if close:
            close()


def main():
    parser = argparse.ArgumentParser(prog="python3 -m companion.voice",
                                     description="On-device voice companion.")
    parser.add_argument("--pin", type=int,
                        help="GPIO pin for the push-to-talk button. Omit for keyboard dev mode.")
    parser.add_argument("--ros", action="store_true", help="Take frames from the ROS camera")
    parser.add_argument("--topic", default="/camera/color/image_raw")
    parser.add_argument("--hazard-topic", default="/hazard_warning",
                        help="ROS topic for obstacle alerts that preempt speech")
    parser.add_argument("--image", help="Dev: use a static JPEG instead of a camera")
    parser.add_argument("--pi-url",
                        help="Laptop: take frames and guidance from the Pi bridge through an SSH "
                             "tunnel, e.g. http://127.0.0.1:8081")
    parser.add_argument("--gain", type=float, default=1.0, help="Output volume multiplier")
    args = parser.parse_args()
    if args.ros and args.pi_url:
        parser.error("Use --ros or --pi-url, not both; the HTTP fallback does not carry hazards.")

    audio = Audio(gain=args.gain)
    try:
        import sounddevice

        sounddevice.query_devices(kind="output")
    except Exception as error:
        # A spoken error is useless with no speaker, so this is a hard failure.
        print(f"FATAL: no audio output device ({error}).", file=sys.stderr)
        return 1

    button, events = open_button(args.pin)

    device_lang = os.environ.get("DEVICE_LANG", I18N_DEFAULT)
    from companion.i18n import LANGUAGES
    if device_lang not in LANGUAGES:
        print(f"Warning: DEVICE_LANG={device_lang!r} not recognised, defaulting to {I18N_DEFAULT!r}",
              file=sys.stderr)
        device_lang = I18N_DEFAULT

    speech = Speech(audio,
                    key=os.environ.get("ELEVENLABS_API_KEY", ""),
                    voice_id=os.environ.get("ELEVENLABS_VOICE_ID", ""),
                    model_id=os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5"))
    hazards = HazardVoice(audio, speech, lang=device_lang)
    gemini = Gemini(os.environ.get("GEMINI_API_KEY", ""),
                    os.environ.get("GEMINI_MODEL", DEFAULT_MODEL))
    guidance = None
    hazard_state = HazardState() if args.ros else None
    if args.pi_url:
        from companion.voice.pi_bridge import RemotePi
        try:
            camera = guidance = RemotePi(args.pi_url)
        except ValueError as error:
            print(f"FATAL: {error}", file=sys.stderr)
            return 1
    else:
        try:
            camera = open_camera(args.topic if args.ros else None, args.image,
                                 hazard_topic=args.hazard_topic if args.ros else None,
                                 on_hazard=hazard_state.receive if hazard_state else None)
        except AppError as error:
            print(f"FATAL: {error}", file=sys.stderr)
            return 1
    if args.ros:
        from companion.voice.guidance import RosGuidance
        def announce(text, priority):
            threading.Thread(target=speech.say, args=(text,),
                             kwargs={"local": True, "priority": priority}, daemon=True).start()
        guidance = RosGuidance(on_event=announce)
    session = Session()
    guardian = None
    if os.environ.get("ELEVENLABS_API_KEY") and os.environ.get("ELEVENLABS_AGENT_ID"):
        from companion.guardian.session import from_env
        guardian = from_env(audio, speech, camera, gemini, session,
                            status=lambda: status_text(camera, gemini, session, landmark=False, lang=device_lang),
                            network_up=network_up,
                            notify=lambda reason: events.put(("guardian_closed", reason)),
                            lang=device_lang)
    print(f"Voice companion on {platform.system()}. "
          f"Language: {device_lang}. "
          f"Gemini: {'configured' if gemini.key else 'MISSING KEY'}. "
          f"Speech: {'ElevenLabs' if speech.key and speech.voice_id else 'local engine'}. "
          f"Hazard phrase: {'ready' if hazards.phrase_ready else 'tone only'}. "
          f"Guardian: {guardian.describe() if guardian else 'not configured'}. "
          f"Camera: {camera.status()}.", flush=True)

    companion = Companion(audio, speech, gemini, camera, session, button, events,
                          guidance, hazards, guardian, hazard_state, lang=device_lang)
    try:
        companion.run()
    except KeyboardInterrupt:
        companion.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
