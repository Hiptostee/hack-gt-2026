"""Run with: python3 -m companion.voice

All state transitions happen on the main event loop. The worker thread only
does slow work and reports completion back as an event, so there is one place
where state changes and no lock around it.
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
from companion.voice.audio import Audio
from companion.voice.button import open_button
from companion.voice.camera import open_camera
from companion.voice.gemini import Gemini, DEFAULT_MODEL
from companion.voice.session import Session
from companion.voice.speech import Speech

SHORT_PRESS = 0.15
HOLD = 0.6
DOUBLE_WINDOW = 0.5
MAX_RECORD = 30
WARN_RECORD = 25
MIN_SPEECH = 0.25
# int16 peak below this is treated as nothing captured. Tune on the real mic:
# a false "I did not hear anything" costs the user a whole repeated question,
# so err low. A silent capture on macOS usually means mic permission, not quiet.
MIN_PEAK = 200
WORKING_AFTER = 5
WORKING_EVERY = 4
LOCATOR_ARM = 20


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


class Companion:
    def __init__(self, audio, speech, gemini, camera, session, button, events, guidance=None):
        self.audio = audio
        self.speech = speech
        self.gemini = gemini
        self.camera = camera
        self.session = session
        self.button = button
        self.events = events
        self.guidance = guidance
        self.tasks = queue.Queue()
        self.state = "idle"
        self.generation = 0
        self.press_at = 0.0
        self.short_timer = None
        self.cap_timers = []
        # None, not 0.0: time.monotonic() starts near zero, so a numeric
        # sentinel reads as "armed at startup" and fires the locator siren on
        # the first help press instead of speaking status.
        self.locator_armed_at = None
        self.running = True

    # ---- main loop -------------------------------------------------------

    def run(self):
        threading.Thread(target=self._worker, daemon=True).start()
        self.speech.say("Ready.")
        while self.running:
            try:
                kind, at = self.events.get(timeout=0.5)
            except queue.Empty:
                continue
            if kind == "quit":
                break
            handler = {"press": self._press, "release": self._release,
                       "repeat_due": self._repeat_due, "done": self._done}.get(kind)
            if handler:
                handler(at)
        self.shutdown()

    def _press(self, at):
        if self.state == "recording":
            return
        # Bump first: the worker checks the generation before it speaks, so any
        # answer already on its way is invalidated before playback is cut.
        self.generation += 1
        if self.state == "busy":
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
        if duration < SHORT_PRESS:
            self.audio.earcon("too_brief")
        elif duration < HOLD:
            self._short_press()
        elif seconds < MIN_SPEECH or not wav or peak < MIN_PEAK:
            print(f"nothing captured (peak {peak}, {seconds:.1f}s)", file=sys.stderr)
            self._dispatch("say", "I did not hear anything.")
        else:
            self._dispatch("utterance", wav)

    def _short_press(self):
        if self.short_timer is not None:
            self.short_timer.cancel()
            self.short_timer = None
            self._dispatch("help", None)
            return
        self.short_timer = threading.Timer(
            DOUBLE_WINDOW, lambda: self.events.put(("repeat_due", time.monotonic())))
        self.short_timer.start()

    def _repeat_due(self, _at):
        self.short_timer = None
        if self.state == "idle":
            self._dispatch("repeat", None)

    def _done(self, generation):
        if generation == self.generation and self.state == "busy":
            self.state = "idle"

    def _dispatch(self, kind, payload):
        self.state = "busy"
        self.tasks.put((kind, self.generation, payload))

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

    # ---- worker ----------------------------------------------------------

    def _worker(self):
        while True:
            task = self.tasks.get()
            if task is None:
                return
            kind, generation, payload = task
            try:
                if kind == "utterance":
                    self._utterance(payload, generation)
                elif kind == "repeat":
                    self._speak(self.session.last_answer
                                or "There is nothing to repeat yet.", generation)
                elif kind == "help":
                    self._help(generation)
                elif kind == "say":
                    self._speak(payload, generation)
            except AppError as error:
                self.audio.earcon("error")
                self._speak(str(error), generation, local=True)
            except Exception as error:  # A crash here must never kill the device.
                print(f"Unexpected failure: {error!r}", file=sys.stderr)
                self.audio.earcon("error")
                self._speak("Something went wrong.", generation, local=True)
            finally:
                self.events.put(("done", generation))

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
            result = self.gemini.ask(wav, image, self.session.history)
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
        if action == "louder":
            self.audio.gain = min(2.5, self.audio.gain + 0.3)
        elif action == "quieter":
            self.audio.gain = max(0.3, self.audio.gain - 0.3)
        self._speak(result["answer"], generation)

    def _help(self, generation):
        now = time.monotonic()
        if self.locator_armed_at is not None and now - self.locator_armed_at < LOCATOR_ARM:
            self.locator_armed_at = None
            self._speak("Playing the locator sound.", generation, local=True)
            self.audio.play(self.audio.pulsed(), self.audio.capture_rate)
            return
        self.locator_armed_at = now
        status = " ".join([
            "Network reachable." if network_up() else "No network.",
            "Camera: " + self.camera.status() + ".",
            "Gemini key configured." if self.gemini.key else "No Gemini key.",
            battery_text(),
            self.session.describe_landmark(),
            "Press twice again for the locator sound.",
        ])
        self._speak(status, generation, local=True)

    def _speak(self, text, generation, local=False):
        if generation != self.generation:
            return
        self.speech.say(text, local=local)

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
        if self.short_timer:
            self.short_timer.cancel()
        self.speech.stop()
        self.tasks.put(None)
        self.button.close()
        if self.guidance is not None:
            self.guidance.close()
        self.camera.close()


def main():
    parser = argparse.ArgumentParser(prog="python3 -m companion.voice",
                                     description="On-device voice companion.")
    parser.add_argument("--pin", type=int,
                        help="GPIO pin for the push-to-talk button. Omit for keyboard dev mode.")
    parser.add_argument("--ros", action="store_true", help="Take frames from the ROS camera")
    parser.add_argument("--topic", default="/camera/color/image_raw")
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

    speech = Speech(audio,
                    key=os.environ.get("ELEVENLABS_API_KEY", ""),
                    voice_id=os.environ.get("ELEVENLABS_VOICE_ID", ""),
                    model_id=os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5"))
    gemini = Gemini(os.environ.get("GEMINI_API_KEY", ""),
                    os.environ.get("GEMINI_MODEL", DEFAULT_MODEL))
    try:
        camera = open_camera(args.topic if args.ros else None, args.image)
    except AppError as error:
        print(f"FATAL: {error}", file=sys.stderr)
        return 1

    button, events = open_button(args.pin)
    guidance = None
    if args.ros:
        from companion.voice.guidance import RosGuidance
        guidance = RosGuidance()
    print(f"Voice companion on {platform.system()}. "
          f"Gemini: {'configured' if gemini.key else 'MISSING KEY'}. "
          f"Speech: {'ElevenLabs' if speech.key and speech.voice_id else 'local engine'}. "
          f"Camera: {camera.status()}.", flush=True)

    companion = Companion(audio, speech, gemini, camera, Session(), button, events, guidance)
    try:
        companion.run()
    except KeyboardInterrupt:
        companion.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
