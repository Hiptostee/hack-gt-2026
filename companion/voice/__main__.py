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
from companion.voice.audio import HELP, INFO, Audio
from companion.voice.button import open_button
from companion.voice.camera import open_camera
from companion.voice.gemini import Gemini, DEFAULT_MODEL
from companion.voice.hazards import HazardVoice
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
         "help": "local", "guardian": "local"}


def network_up():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=1.5).close()
        return True
    except OSError:
        return False


def battery_text():
    for path in glob.glob("/sys/class/power_supply/*/capacity"):
        try:
            with open(path) as handle:
                return f"Battery {handle.read().strip()} percent."
        except OSError:
            continue
    return "Battery level unknown."


def status_text(camera, gemini, session):
    """Local device status. Shared by double-tap help and Guardian."""
    return " ".join([
        "Network reachable." if network_up() else "No network.",
        "Camera: " + camera.status() + ".",
        "Gemini key configured." if gemini.key else "No Gemini key.",
        battery_text(),
        session.describe_landmark(),
    ])


class Companion:
    def __init__(self, audio, speech, gemini, camera, session, button, events,
                 guidance=None, hazards=None):
        self.audio = audio
        self.speech = speech
        self.gemini = gemini
        self.camera = camera
        self.session = session
        self.button = button
        self.events = events
        self.guidance = guidance
        self.hazards = hazards
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
        self.speech.say("Ready.", priority=INFO)
        while self.running:
            try:
                kind, at = self.events.get(timeout=0.5)
            except queue.Empty:
                continue
            if kind == "quit":
                break
            handler = {"press": self._press, "release": self._release,
                       "taps_due": self._taps_due, "done": self._done,
                       "hazard": self._hazard}.get(kind)
            if handler:
                handler(at)
        self.shutdown()

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
        self.state = "idle"
        if self.hazards is not None:
            self.hazards.warn(at)
        else:
            self.audio.earcon("stopped", wait=False)
        print("Hazard warning: companion speech interrupted", file=sys.stderr)

    def _press(self, at):
        if self.state == "recording":
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
                self._dispatch("say", "I did not hear anything.")
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
        if self.state != "idle" or taps == 0:
            return
        self._dispatch({1: "repeat", 2: "help"}.get(taps, "guardian"), None)

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
                self._speak("Something went wrong.", generation, local=True)
            finally:
                self.events.put(("done", generation))

    def _run(self, kind, generation, payload):
        if kind == "utterance":
            self._utterance(payload, generation)
        elif kind == "repeat":
            self._speak(self.session.last_answer
                        or "There is nothing to repeat yet.", generation)
        elif kind == "help":
            self._help(generation)
        elif kind == "guardian":
            self._guardian(generation)
        elif kind == "say":
            self._speak(payload, generation)

    def _utterance(self, wav, generation):
        image = None
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
        self.session.save_landmark(result["landmark"])
        self._act(result, previous, generation)

    def _act(self, result, previous, generation):
        action = result["device_action"]
        if action == "navigate_backpack":
            if self.guidance is None:
                self._speak("Backpack guidance is unavailable here.", generation, local=True)
            else:
                self._speak(self.guidance.start(), generation, local=True)
            return
        if action == "stop_navigation":
            if self.guidance is not None:
                self.guidance.stop()
            self._speak("Guidance stopped.", generation, local=True)
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
            self._guardian(generation)
            return
        if action == "louder":
            self.audio.gain = min(2.5, self.audio.gain + 0.3)
        elif action == "quieter":
            self.audio.gain = max(0.3, self.audio.gain - 0.3)
        self._speak(result["answer"], generation)

    def _help(self, generation):
        self._speak(status_text(self.camera, self.gemini, self.session), generation,
                    local=True, priority=HELP)

    def _guardian(self, generation):
        # Entry point for companion/guardian/specs.md §2; the session is not built yet.
        self._speak("Guardian mode isn't available on this device yet.", generation,
                    local=True, priority=HELP)

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
        self._disarm_cap()
        self._cancel_taps()
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
    parser.add_argument("--gain", type=float, default=1.0, help="Output volume multiplier")
    args = parser.parse_args()

    audio = Audio(gain=args.gain)
    try:
        import sounddevice

        sounddevice.query_devices(kind="output")
    except Exception as error:
        # A spoken error is useless with no speaker, so this is a hard failure.
        print(f"FATAL: no audio output device ({error}).", file=sys.stderr)
        return 1

    button, events = open_button(args.pin)

    speech = Speech(audio,
                    key=os.environ.get("ELEVENLABS_API_KEY", ""),
                    voice_id=os.environ.get("ELEVENLABS_VOICE_ID", ""),
                    model_id=os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5"))
    hazards = HazardVoice(audio, speech)
    gemini = Gemini(os.environ.get("GEMINI_API_KEY", ""),
                    os.environ.get("GEMINI_MODEL", DEFAULT_MODEL))
    try:
        camera = open_camera(args.topic if args.ros else None, args.image,
                             hazard_topic=args.hazard_topic if args.ros else None,
                             on_hazard=lambda: events.put(("hazard", time.monotonic())))
    except AppError as error:
        print(f"FATAL: {error}", file=sys.stderr)
        return 1

    guidance = None
    if args.ros:
        from companion.voice.guidance import RosGuidance
        guidance = RosGuidance()
    print(f"Voice companion on {platform.system()}. "
          f"Gemini: {'configured' if gemini.key else 'MISSING KEY'}. "
          f"Speech: {'ElevenLabs' if speech.key and speech.voice_id else 'local engine'}. "
          f"Hazard phrase: {'ready' if hazards.phrase_ready else 'tone only'}. "
          f"Camera: {camera.status()}.", flush=True)

    companion = Companion(audio, speech, gemini, camera, Session(), button, events,
                          guidance, hazards)
    try:
        companion.run()
    except KeyboardInterrupt:
        companion.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
